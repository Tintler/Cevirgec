import base64
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

import numpy as np

import tts
from bridge import Bridge
from tts import EXPORT_RATE, PLAY_RATE, Speaker


class FakeSpeech:
    def __init__(self, text, rate, speed):
        seconds = max(0.1, len(text) / 18 / speed)
        self.audio = np.full(int(rate * seconds), 0.25, np.float32)
        self.sample_rate = rate
        self.duration = len(self.audio) / rate


class FakeModel:
    def __init__(self, device):
        self.device = device
        self.calls = []

    def say(self, text, speed=1.0, seed=None, sample_rate=48000):
        self.calls.append((text, speed, sample_rate))
        return FakeSpeech(text, sample_rate, speed)


def wait_for(predicate, timeout=5):
    end = time.time() + timeout
    while not predicate() and time.time() < end:
        time.sleep(0.02)
    return predicate()


class SpeakerTests(unittest.TestCase):
    def test_load_say_and_export(self):
        events = []
        speaker = Speaker(emit=lambda event, data: events.append((event, dict(data))), factory=FakeModel)
        self.assertEqual(speaker.status()['state'], 'idle')
        with self.assertRaises(RuntimeError):
            speaker.say('Merhaba.')
        speaker.load('cpu')
        self.assertTrue(wait_for(lambda: speaker.state == 'ready'))
        self.assertEqual([data['state'] for _event, data in events], ['loading', 'ready'])
        self.assertEqual(speaker._model.calls[0][2], PLAY_RATE)          # warm-up call
        result = speaker.say('  Merhaba,\n dünya.  ', speed=1.5)
        self.assertEqual(speaker._model.calls[-1][:2], ('Merhaba, dünya.', 1.5))
        with wave.open(io.BytesIO(base64.b64decode(result['audio']))) as stream:
            self.assertEqual((stream.getframerate(), stream.getnchannels(), stream.getsampwidth()), (PLAY_RATE, 1, 2))
            self.assertAlmostEqual(stream.getnframes() / PLAY_RATE, result['duration'], places=3)
        self.assertEqual(speaker.say('   ')['audio'], '')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'out.wav'
            progress = []
            count = speaker.export(['Bir.', ' ', 'İki.'], 1.0, path, lambda done, total: progress.append((done, total)))
            self.assertEqual(count, 2)
            self.assertEqual(progress, [(1, 2), (2, 2)])
            with wave.open(str(path)) as stream:
                self.assertEqual(stream.getframerate(), EXPORT_RATE)
                self.assertGreater(stream.getnframes(), EXPORT_RATE * 0.7)
            with self.assertRaises(ValueError):
                speaker.export(['  '], 1.0, path)

    def test_load_failure_reports_error_and_reload_on_device_change(self):
        attempts = []

        def factory(device):
            attempts.append(device)
            if device == 'cuda':
                raise RuntimeError('CUDA yok')
            return FakeModel(device)

        speaker = Speaker(factory=factory)
        speaker.load('cuda')
        self.assertTrue(wait_for(lambda: speaker.state == 'error'))
        self.assertIn('CUDA yok', speaker.status()['message'])
        speaker.load('cpu')
        self.assertTrue(wait_for(lambda: speaker.state == 'ready'))
        speaker.load('auto')                     # an already loaded model is kept
        self.assertEqual(attempts, ['cuda', 'cpu'])

    def test_missing_package(self):
        with patch('tts.importlib.util.find_spec', return_value=None):
            speaker = Speaker()
        self.assertEqual(speaker.load()['state'], 'missing')


class Dialogs:
    def __init__(self, path):
        self.path = path

    def save_file(self, *_):
        return self.path


class BridgeTtsTests(unittest.TestCase):
    def test_export_runs_in_background_and_reports_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            events = []
            done = threading.Event()

            def emit(event, data):
                events.append((event, data))
                if event == 'tts_export' and data.get('finished'):
                    done.set()

            with patch('bridge.APP_DIR', Path(temp)):
                api = Bridge(emit=emit, dialogs=Dialogs(str(Path(temp) / 'bolum')))
                self.assertIn('error', api.tts_export('Kitap', ['Bir.'], 1.0))
                api._speaker_cache = Speaker(emit=emit, factory=FakeModel)
                api.tts_load()
                self.assertTrue(wait_for(lambda: api.tts_status()['state'] == 'ready'))
                result = api.tts_export('Kitap: Bölüm/1', ['Bir.', 'İki.'], 1.0)
                self.assertTrue(result['started'])
                self.assertTrue(done.wait(5))
            finished = [data for event, data in events if event == 'tts_export' and data.get('finished')][0]
            self.assertEqual(finished['count'], 2)
            self.assertTrue(finished['path'].endswith('bolum.wav'))
            self.assertTrue(Path(finished['path']).exists())
            self.assertIn('audio', api.tts_say('Merhaba.'))


if __name__ == '__main__':
    unittest.main()
