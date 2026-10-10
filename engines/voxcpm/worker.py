"""VoxCPM voice engine: Trendyol-TTS (Turkish fine-tune, MIT) and VoxCPM2 voice design / cloning (Apache-2.0).

https://huggingface.co/Trendyol/Trendyol-TTS · https://huggingface.co/openbmb/VoxCPM2
"""
from __future__ import annotations

import hashlib
import os
import sys

if __package__ in (None, ''):  # frozen worker.exe or run as a script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engines.protocol import Engine, data_dir, serve

MODELS = {'trendyol': 'Trendyol/Trendyol-TTS', 'design': 'openbmb/VoxCPM2', 'clone': 'openbmb/VoxCPM2'}
# Settings recommended on the Trendyol-TTS card; VoxCPM2 defaults otherwise.
GENERATE = {'trendyol': {'cfg_value': 2.0, 'inference_timesteps': 16}, 'design': {'cfg_value': 2.0, 'inference_timesteps': 10},
            'clone': {'cfg_value': 2.0, 'inference_timesteps': 10}}
DEFAULT_DESCRIPTION = 'A calm, warm middle-aged male narrator reading an audiobook'
# Spoken once in the designed voice; every sentence is then cloned from it so the narrator stays the same.
ANCHOR_TEXT = 'Bu ses, kitapları sakin ve doğal bir biçimde okumak için hazırlanmış bir anlatıcı sesidir.'


def stretch(audio, rate, n_fft=2048, hop=512):
    """Changes speed without changing pitch (phase vocoder on torch.stft; no first-call compile delay)."""
    import math
    import numpy as np
    import torch
    signal = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))
    if signal.numel() < n_fft:
        return audio
    window = torch.hann_window(n_fft)
    spec = torch.stft(signal, n_fft, hop, window=window, return_complex=True)
    steps = torch.arange(0, spec.shape[-1], rate, dtype=torch.float64)
    advance = torch.linspace(0, math.pi * hop, spec.shape[0], dtype=torch.float64)[:, None]
    padded = torch.nn.functional.pad(spec, (0, 2))
    index = steps.floor().long()
    alpha = (steps - index)[None]
    first, second = padded[:, index], padded[:, index + 1]
    delta = second.angle().double() - first.angle().double() - advance
    delta = delta - 2 * math.pi * torch.round(delta / (2 * math.pi)) + advance
    phase = torch.cumsum(torch.cat([spec[:, :1].angle().double(), delta[:, :-1]], dim=-1), dim=-1)
    magnitude = alpha * second.abs().double() + (1 - alpha) * first.abs().double()
    out = torch.polar(magnitude.float(), phase.float())
    length = int(round(signal.numel() / rate))
    return torch.istft(out, n_fft, hop, window=window, length=length).numpy().astype(np.float32)


class VoxCpmEngine(Engine):
    def __init__(self):
        super().__init__()
        self.model = None
        self.model_id = None
        self.voice = None
        self.prompt = {}
        self.normalizer = None

    def _turkish(self, text):
        """Numbers, dates, times, money and abbreviations read aloud in Turkish. VoxCPM's own normaliser
        only knows Chinese and English ("15" would become "fifteen"), so it stays off."""
        if self.normalizer is None:
            from normalizer_tr import Normalizer
            self.normalizer = Normalizer()
        try:
            return self.normalizer.normalize(text, ambiguity_policy='fallback').normalized_text
        except Exception:
            return text

    def load(self, voice, device):
        voice = voice if voice in MODELS else 'trendyol'
        os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')
        if getattr(sys, 'frozen', False) and 'HF_HOME' not in os.environ:
            os.environ['HF_HOME'] = os.path.join(data_dir(), 'hf')
        self.stage('PyTorch yükleniyor…')
        import torch
        from voxcpm.core import VoxCPM
        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('NVIDIA ekran kartı (CUDA) bulunamadı. "Otomatik" veya "CPU" seçin '
                               "ya da PyTorch'un CUDA sürümünü kurun.")
        if self.model_id != MODELS[voice]:
            self.model = None
            self.stage('Model alınıyor ve yükleniyor (ilk kullanımda ~5 GB iner)…')
            # optimize=False: torch.compile can take minutes or fail on Windows; the plain path is fast enough.
            self.model = VoxCPM.from_pretrained(hf_model_id=MODELS[voice], load_denoiser=False, optimize=False,
                                                device=None if device in ('auto', '', None) else device)
            self.model_id = MODELS[voice]
        self.voice = voice
        self.prompt = self._prompt(voice)
        self.stage('Isınıyor…')
        self.say('Merhaba.', 1.0, 0)
        return {'device': str(getattr(self.model.tts_model, 'device', device)), 'voice': voice}

    def _prompt(self, voice):
        if voice == 'clone':
            reference = (self.options.get('reference') or '').strip()
            if not reference or not os.path.exists(reference):
                raise RuntimeError('Klonlanacak ses kaydı seçilmemiş ya da bulunamadı. Ayarlar → Sesli okuma bölümünden '
                                   'bir ses kaydı (WAV/MP3/FLAC, 5-30 saniye) seçin.')
            text = (self.options.get('reference_text') or '').strip()
            if text:
                return {'reference_wav_path': reference, 'prompt_wav_path': reference, 'prompt_text': text}
            return {'reference_wav_path': reference}
        if voice == 'design':
            description = (self.options.get('description') or '').strip() or DEFAULT_DESCRIPTION
            key = hashlib.sha1(f'{self.model_id}|{description}|{ANCHOR_TEXT}'.encode('utf-8')).hexdigest()[:16]
            anchor = os.path.join(data_dir(), 'tasarim', key + '.wav')
            if not os.path.exists(anchor):
                self.stage('Tarif edilen ses oluşturuluyor…')
                import soundfile
                import torch
                torch.manual_seed(0)
                audio = self.model.generate(text=f'({description}){ANCHOR_TEXT}', normalize=False, **GENERATE['design'])
                os.makedirs(os.path.dirname(anchor), exist_ok=True)
                soundfile.write(anchor, audio, self.model.tts_model.sample_rate)
            return {'reference_wav_path': anchor, 'prompt_wav_path': anchor, 'prompt_text': ANCHOR_TEXT}
        return {}

    def say(self, text, speed, rate):
        import numpy as np
        import torch
        torch.manual_seed(0)
        audio = self.model.generate(text=self._turkish(text), normalize=False, **self.prompt, **GENERATE[self.voice])
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if abs(speed - 1.0) > 0.01 and audio.size:
            audio = stretch(audio, max(0.5, min(2.0, speed)))
        return audio, self.model.tts_model.sample_rate


if __name__ == '__main__':
    serve(VoxCpmEngine())
