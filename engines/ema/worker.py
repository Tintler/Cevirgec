"""EMA Lightning voice engine (Turkish, Apache-2.0): https://huggingface.co/canberkkkkkk/ema-lightning"""
from __future__ import annotations

import os
import sys

if __package__ in (None, ''):  # frozen worker.exe or run as a script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engines.protocol import Engine, data_dir, serve

WARMUP = 'Merhaba, bu bir deneme cümlesidir.'
BATCH_SIZE = 4          # sentences synthesised together at most (current + prefetch)
RATES = (48000, 24000, 16000, 8000)


class EmaEngine(Engine):
    def __init__(self):
        super().__init__()
        self.model = None

    def load(self, voice, device):
        if self.model is not None:
            return {'device': str(self.model.device), 'voice': 'default'}
        os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')
        if getattr(sys, 'frozen', False) and 'HF_HOME' not in os.environ:
            # Packaged engine: keep the downloaded model next to the program, not in the user profile.
            os.environ['HF_HOME'] = os.path.join(data_dir(), 'huggingface')
        self.stage('PyTorch yükleniyor…')
        import torch
        from ema_lightning import EMA
        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('NVIDIA ekran kartı (CUDA) bulunamadı. "Otomatik" veya "CPU" seçin '
                               'ya da PyTorch\'un CUDA sürümünü kurun.')
        self.stage('Model dosyaları alınıyor ve yükleniyor…')
        model = EMA(device=device if device in ('cpu', 'cuda') else 'auto')
        # lightning() (torch.compile + CUDA graphs for every shape) is skipped on purpose: it can take minutes
        # or stall on Windows, and the plain path is already far faster than real time. A fixed small batch
        # also skips the throughput benchmark.
        model._batch_size = BATCH_SIZE
        self.stage('Isınıyor…')
        model.say(WARMUP, sample_rate=24000, seed=0)
        self.model = model
        return {'device': str(model.device), 'voice': 'default'}

    def say(self, text, speed, rate):
        rate = rate if rate in RATES else 24000
        speech = self.model.say(text, speed=max(0.25, min(4.0, speed)), seed=0, sample_rate=rate)
        return speech.audio, rate


if __name__ == '__main__':
    serve(EmaEngine())
