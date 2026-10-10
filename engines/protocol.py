"""Worker side of the voice engine protocol (version 1).

Every voice engine runs as its own process and talks to Çevirgeç through JSON lines:

    request   {"id": 1, "cmd": "load", "voice": "...", "device": "auto"}
    response  {"id": 1, "ok": true, "result": {...}}   or   {"id": 1, "ok": false, "error": "..."}
    event     {"event": "stage", "message": "..."}      (progress while loading or downloading)

Commands: load {voice, device}, say {text, speed, rate}, ping, shutdown. What an engine offers (name,
languages, voices) is described statically in its motor.json, so Çevirgeç can list it without starting it.
`say` returns a mono 16-bit WAV as base64 with its duration. An engine only implements
load() and say(); see engines/ema/worker.py.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import traceback
import wave

PROTOCOL = 1


def data_dir():
    """Where the engine keeps downloaded models and voices (set by Çevirgeç)."""
    path = os.environ.get('CEVIRGEC_ENGINE_DATA') or os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'data')
    os.makedirs(path, exist_ok=True)
    return path


def wav_base64(audio, rate):
    import numpy as np
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).round().astype('<i2').tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(int(rate))
        stream.writeframes(pcm)
    return base64.b64encode(buffer.getvalue()).decode('ascii')


class Engine:
    """Base class. load() prepares a voice; say() returns (float32 mono audio in [-1, 1], sample rate)."""

    def __init__(self):
        self.out = None

    def stage(self, message):
        self.out({'event': 'stage', 'message': message})

    def load(self, voice, device):
        raise NotImplementedError

    def say(self, text, speed, rate):
        raise NotImplementedError


def serve(engine):
    # Protocol messages own the real stdout; anything libraries print goes to stderr (the worker log).
    stdout = io.TextIOWrapper(os.fdopen(os.dup(sys.stdout.fileno()), 'wb'), encoding='utf-8', newline='\n',
                              line_buffering=True)
    sys.stdout = sys.stderr
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')

    def send(message):
        stdout.write(json.dumps(message, ensure_ascii=False) + '\n')
        stdout.flush()

    engine.out = send
    send({'event': 'ready', 'protocol': PROTOCOL})
    for line in stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        number, command = request.get('id'), request.get('cmd')
        try:
            if command == 'shutdown':
                send({'id': number, 'ok': True, 'result': {}})
                break
            if command == 'load':
                result = engine.load(request.get('voice') or '', request.get('device') or 'auto') or {}
            elif command == 'say':
                text = ' '.join(str(request.get('text', '')).split())
                if not text:
                    result = {'audio': '', 'duration': 0.0}
                else:
                    audio, rate = engine.say(text, float(request.get('speed') or 1.0), int(request.get('rate') or 0))
                    result = {'audio': wav_base64(audio, rate), 'duration': len(audio) / rate, 'rate': rate}
            elif command == 'ping':
                result = {}
            else:
                raise ValueError(f'Bilinmeyen komut: {command}')
            send({'id': number, 'ok': True, 'result': result})
        except Exception as error:
            traceback.print_exc()
            send({'id': number, 'ok': False, 'error': str(error) or type(error).__name__})
