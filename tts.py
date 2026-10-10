"""Read-aloud back end built on EMA Lightning (Turkish TTS, Apache-2.0).

The model is optional: ema-lightning pulls in PyTorch, so it is imported only when
first needed and the rest of the application works without it. Loading takes a
while (weights from the Hugging Face cache, first download ~34 MB), so it runs on a
background thread and reports its state through the `emit` callback.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import os
import threading
import time
import traceback
import wave

PLAY_RATE = 24000      # sent to the interface; half of the model's rate keeps the bridge payload small
EXPORT_RATE = 48000    # files keep the model's full quality
SENTENCE_GAP = 0.35    # seconds of silence between sentences in an exported file
WARMUP = 'Merhaba, bu bir deneme cümlesidir.'
BATCH_SIZE = 4          # sentences synthesised together at most (current + prefetch)


def _pcm16(audio):
    import numpy as np
    return (np.clip(audio, -1.0, 1.0) * 32767.0).round().astype('<i2').tobytes()


def wav_bytes(audio, rate):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(_pcm16(audio))
    return buffer.getvalue()


class Speaker:
    """Owns one EMA instance. States: missing, idle, loading, ready, error."""

    def __init__(self, emit=None, factory=None):
        self._emit = emit or (lambda _event, _data: None)
        self._factory = factory          # tests inject a fake model factory
        self._lock = threading.Lock()
        self._model = None
        self._device = None
        self._thread = None
        self.state = 'idle' if self.installed() else 'missing'
        self.message = ''
        self._exporting = False

    def installed(self):
        if self._factory is not None:
            return True
        return importlib.util.find_spec('ema_lightning') is not None and importlib.util.find_spec('torch') is not None

    def status(self):
        return {'state': self.state, 'message': self.message, 'device': self._device, 'exporting': self._exporting}

    def _set(self, state, message=''):
        self.state, self.message = state, message
        self._emit('tts', self.status())

    def load(self, device='auto'):
        """Starts loading in the background; returns the current status immediately."""
        with self._lock:
            if self.state == 'missing':
                return self.status()
            wanted = device or 'auto'
            if self._model is not None and wanted in ('auto', self._requested):
                return self.status()
            if self._thread and self._thread.is_alive():
                return self.status()
            self._requested = wanted
            self._model = None
            self._set('loading', 'Ses modeli yükleniyor…')
            self._thread = threading.Thread(target=self._load, args=(wanted,), name='tts-load', daemon=True)
            self._thread.start()
        return self.status()

    _requested = None

    def _stage(self, message, started):
        print(f'[sesli okuma] {time.perf_counter() - started:6.1f} sn  {message}', flush=True)
        self._set('loading', message)

    def _load(self, device):
        started = time.perf_counter()
        try:
            if self._factory is not None:
                model = self._factory(device)
            else:
                os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')
                self._stage('PyTorch yükleniyor…', started)
                import torch
                from ema_lightning import EMA
                if device == 'cuda' and not torch.cuda.is_available():
                    raise RuntimeError('NVIDIA ekran kartı (CUDA) bulunamadı. Ayarlardan "Otomatik" veya "CPU" seçin '
                                       'ya da PyTorch\'un CUDA sürümünü kurun.')
                self._stage('Model dosyaları alınıyor ve yükleniyor…', started)
                model = EMA(device=device)
                # lightning() (torch.compile + CUDA graphs for every shape) is skipped on purpose: it can take
                # minutes or stall on Windows, and the plain GPU/CPU path is already far faster than real time.
                # A fixed small batch also skips the throughput benchmark; reading needs at most a few at once.
                model._batch_size = BATCH_SIZE
            self._stage('Isınıyor…', started)
            model.say(WARMUP, sample_rate=PLAY_RATE, seed=0)
            self._model = model
            self._device = str(getattr(model, 'device', device))
            print(f'[sesli okuma] {time.perf_counter() - started:6.1f} sn  Hazır ({self._device})', flush=True)
            self._set('ready', 'Hazır')
        except Exception as error:
            traceback.print_exc()
            self._set('error', f'Ses modeli yüklenemedi: {error}')

    def _ready_model(self):
        if self._model is None:
            raise RuntimeError('Ses modeli henüz hazır değil.')
        return self._model

    def say(self, text, speed=1.0):
        """One sentence as a base64 WAV (PLAY_RATE) with its duration."""
        model = self._ready_model()
        text = ' '.join(str(text).split())
        if not text:
            return {'audio': '', 'duration': 0.0}
        speech = model.say(text, speed=float(speed), seed=0, sample_rate=PLAY_RATE)
        return {'audio': base64.b64encode(wav_bytes(speech.audio, PLAY_RATE)).decode('ascii'),
                'duration': speech.duration}

    def export(self, sentences, speed, path, progress=None):
        """Writes all sentences to one WAV file at EXPORT_RATE; blocking (run on a worker thread)."""
        import numpy as np
        model = self._ready_model()
        texts = [' '.join(str(s).split()) for s in sentences if str(s).strip()]
        if not texts:
            raise ValueError('Kaydedilecek metin yok.')
        gap = np.zeros(int(EXPORT_RATE * SENTENCE_GAP), np.float32)
        self._exporting = True
        try:
            with wave.open(str(path), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(EXPORT_RATE)
                for index, text in enumerate(texts):
                    speech = model.say(text, speed=float(speed), seed=0, sample_rate=EXPORT_RATE)
                    stream.writeframes(_pcm16(speech.audio))
                    stream.writeframes(_pcm16(gap))
                    if progress:
                        progress(index + 1, len(texts))
        finally:
            self._exporting = False
        return len(texts)
