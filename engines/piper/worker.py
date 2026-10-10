"""Piper voice engine (MIT/GPL voices vary, ONNX, many languages): https://github.com/OHF-Voice/piper1-gpl

Voices are downloaded on first use from https://huggingface.co/rhasspy/piper-voices into the engine's
data folder (voices/<id>.onnx and .onnx.json).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

if __package__ in (None, ''):  # frozen worker.exe or run as a script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engines.protocol import Engine, data_dir, serve

VOICE_URL = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}{extension}?download=true'


def manifest():
    base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
    with open(os.path.join(base, 'motor.json'), encoding='utf-8') as stream:
        return json.load(stream)


class PiperEngine(Engine):
    def __init__(self):
        super().__init__()
        self.voice = None
        self.voice_id = None
        self.catalog = {voice['id']: voice for voice in manifest()['voices']}

    def _download(self, voice, folder):
        os.makedirs(folder, exist_ok=True)
        for extension in ('.onnx.json', '.onnx'):
            target = os.path.join(folder, voice['id'] + extension)
            if os.path.exists(target):
                continue
            url = VOICE_URL.format(path=voice['path'], extension=extension)
            partial = target + '.part'
            with urllib.request.urlopen(url, timeout=60) as response, open(partial, 'wb') as out:
                total = int(response.headers.get('Content-Length') or 0)
                done = last = 0
                while chunk := response.read(1 << 16):
                    out.write(chunk)
                    done += len(chunk)
                    if total and extension == ".onnx" and done - last > total / 20:
                        last = done
                        self.stage(f'Ses indiriliyor: {voice["name"]} %{done * 100 // total}')
            os.replace(partial, target)

    def load(self, voice_id, device):
        voice_id = voice_id or next(iter(self.catalog))
        if voice_id not in self.catalog:
            raise ValueError(f'Bilinmeyen Piper sesi: {voice_id}')
        if self.voice is not None and self.voice_id == voice_id:
            return {'device': 'cpu', 'voice': voice_id}
        folder = os.path.join(data_dir(), 'voices')
        model = os.path.join(folder, voice_id + '.onnx')
        if not os.path.exists(model) or not os.path.exists(model + '.json'):
            self.stage(f'Ses indiriliyor: {self.catalog[voice_id]["name"]}')
            self._download(self.catalog[voice_id], folder)
        self.stage('Piper yükleniyor…')
        from piper import PiperVoice
        self.voice = PiperVoice.load(model)
        self.voice_id = voice_id
        self.say('Merhaba.', 1.0, 0)
        return {'device': 'cpu', 'voice': voice_id}

    def say(self, text, speed, rate):
        import numpy as np
        from piper import SynthesisConfig
        config = SynthesisConfig(length_scale=1.0 / max(0.25, min(4.0, speed)))
        chunks = [chunk.audio_float_array for chunk in self.voice.synthesize(text, syn_config=config)]
        audio = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, np.float32)
        return audio, self.voice.config.sample_rate


if __name__ == '__main__':
    serve(PiperEngine())
