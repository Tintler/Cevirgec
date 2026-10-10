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


def _flat_download(repo, filename, **_kwargs):
    """Model files straight into data/model/ (short paths) instead of the deeply nested Hub cache."""
    from huggingface_hub import hf_hub_download
    folder = os.path.join(data_dir(), 'model')
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        hf_hub_download(repo, filename, local_dir=folder)
    return path


class EmaEngine(Engine):
    def __init__(self):
        super().__init__()
        self.model = None

    def load(self, voice, device):
        if self.model is not None:
            return {'device': str(self.model.device), 'voice': 'default'}
        os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')
        packaged = getattr(sys, 'frozen', False)
        if packaged and 'HF_HOME' not in os.environ:
            # Packaged engine: keep downloads next to the program, not in the user profile.
            os.environ['HF_HOME'] = os.path.join(data_dir(), 'hf')
        self.stage('PyTorch yükleniyor…')
        import torch
        import ema_lightning.api
        from ema_lightning import EMA
        if packaged:
            ema_lightning.api.hf_hub_download = _flat_download
        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('NVIDIA ekran kartı (CUDA) bulunamadı. "Otomatik" veya "CPU" seçin '
                               'ya da PyTorch\'un CUDA sürümünü kurun.')
        self.stage('Model dosyaları alınıyor ve yükleniyor…')
        try:
            model = EMA(device=device if device in ('cpu', 'cuda') else 'auto')
        except OSError as error:
            if getattr(error, 'winerror', None) == 206:
                raise RuntimeError("Klasör yolu Windows için çok uzun. Çevirgeç'i daha kısa yollu bir klasöre "
                                   '(örneğin C:\\Cevirgec) taşıyın.') from error
            raise
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
