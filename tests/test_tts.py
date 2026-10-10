import base64
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave
import zipfile

from bridge import Bridge
from tts import Engines, Worker, language_key

REPO = Path(__file__).resolve().parents[1]

FAKE_WORKER = '''
import os, time
import numpy as np
from engines.protocol import Engine, serve

class Fake(Engine):
    def load(self, voice, device):
        self.stage('Hazırlanıyor…')
        with open(os.path.join(os.environ['CEVIRGEC_ENGINE_DATA'], 'loaded.txt'), 'w') as f:
            f.write(voice)
        self.voice = voice
        return {'device': 'cpu', 'voice': voice}

    def say(self, text, speed, rate):
        if text == 'ÇÖK':
            os._exit(3)
        rate = rate or 16000
        return np.full(int(rate * 0.2 / speed), 0.1, np.float32), rate

serve(Fake())
'''


def make_engine(root, engine_id, languages=('tr',), module=True):
    folder = root / 'engines' / engine_id.replace('-', '_')
    folder.mkdir(parents=True)
    (folder / '__init__.py').write_text('', encoding='utf-8')
    (folder / 'worker.py').write_text(FAKE_WORKER, encoding='utf-8')
    manifest = {'id': engine_id, 'name': engine_id.title(), 'version': '1.0', 'protocol': 1, 'entry': 'worker.exe',
                'python_modules': [], 'speed': True,
                'voices': [{'id': f'{language}-voice', 'name': f'{language} ses', 'language': language,
                            'files': [f'voices/{language}.onnx']} for language in languages]}
    if module:
        manifest['module'] = f'engines.{folder.name}.worker'
    (folder / 'motor.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest


def wait_for(predicate, timeout=20):
    end = time.time() + timeout
    while not predicate() and time.time() < end:
        time.sleep(0.05)
    return predicate()


class EnginesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'src'
        (self.root / 'engines').mkdir(parents=True)
        shutil.copy(REPO / 'engines' / 'protocol.py', self.root / 'engines' / 'protocol.py')
        (self.root / 'engines' / '__init__.py').write_text('', encoding='utf-8')
        self.app = Path(self.temp.name) / 'app'
        self.app.mkdir()
        self.prefs = {}
        self.events = []
        self.engines = Engines(self.app, self.root / 'engines', emit=lambda e, d: self.events.append((e, dict(d))),
                               prefs=lambda: self.prefs)
        self.addCleanup(self.engines.shutdown)

    def tearDown(self):
        self.engines.shutdown()
        self.temp.cleanup()

    def load(self, language):
        self.engines.load(language)
        self.assertTrue(wait_for(lambda: self.engines.state != 'loading'), self.engines.status())
        return self.engines.status()

    def test_language_key(self):
        self.assertEqual([language_key(v) for v in ('tr', 'en-US', 'en_GB', '', None, 'TR')], ['tr', 'en', 'en', 'tr', 'tr', 'tr'])

    def test_choice_prefers_saved_voice_then_defaults(self):
        make_engine(self.root, 'ema-lightning', ('tr',))
        make_engine(self.root, 'piper', ('tr', 'en'))
        listed = {item['id']: item for item in self.engines.engines()}
        self.assertEqual(listed['piper']['languages'], ['en', 'tr'])
        self.assertEqual(listed['piper']['mode'], 'source')
        self.assertFalse(listed['piper']['voices'][0]['downloaded'])
        self.assertEqual(self.engines.choice('tr'), ('ema-lightning', 'tr-voice'))
        self.assertEqual(self.engines.choice('en-US'), ('piper', 'en-voice'))
        self.assertEqual(self.engines.choice('de'), (None, None))
        self.prefs['voices'] = {'tr': 'piper/tr-voice'}
        self.assertEqual(self.engines.choice('tr'), ('piper', 'tr-voice'))
        self.prefs['voices'] = {'tr': 'gone/voice'}
        self.assertEqual(self.engines.choice('tr'), ('ema-lightning', 'tr-voice'))

    def test_load_say_export_and_switch(self):
        make_engine(self.root, 'ema-lightning', ('tr',))
        make_engine(self.root, 'piper', ('tr', 'en'))
        status = self.load('tr')
        self.assertEqual((status['state'], status['engine'], status['voice']), ('ready', 'ema-lightning', 'tr-voice'))
        self.assertIn(('tts', 'Hazırlanıyor…'), [(e, d['message']) for e, d in self.events if e == 'tts'])
        self.assertEqual((self.app / 'ses-motorlari' / 'ema-lightning' / 'data' / 'loaded.txt').read_text(), 'tr-voice')
        result = self.engines.say('  Merhaba \n dünya. ', 2.0)
        with wave.open(io.BytesIO(base64.b64decode(result['audio']))) as clip:
            self.assertEqual(clip.getframerate(), 24000)
            self.assertAlmostEqual(clip.getnframes() / 24000, 0.1, places=2)
        self.assertEqual(self.engines.say('   ')['audio'], '')
        first = self.engines._worker.process.pid
        self.assertEqual(self.load('tr')['state'], 'ready')                 # same choice reuses the worker
        self.assertEqual(self.engines._worker.process.pid, first)
        status = self.load('en')
        self.assertEqual((status['engine'], status['voice'], status['language']), ('piper', 'en-voice', 'en'))
        self.assertNotEqual(self.engines._worker.process.pid, first)
        path = self.app / 'out.wav'
        progress = []
        self.assertEqual(self.engines.export(['Bir.', ' ', 'İki.'], 1.0, path, lambda d, t: progress.append((d, t))), 2)
        self.assertEqual(progress, [(1, 2), (2, 2)])
        with wave.open(str(path)) as clip:
            self.assertEqual(clip.getframerate(), 48000)
            self.assertAlmostEqual(clip.getnframes() / 48000, 2 * (0.2 + 0.35), places=2)
        self.assertEqual(self.load('de')['state'], 'missing')

    def test_worker_crash_is_reported(self):
        make_engine(self.root, 'ema-lightning', ('tr',))
        self.load('tr')
        with self.assertRaises(RuntimeError) as caught:
            self.engines.say('ÇÖK')
        self.assertIn('kapandı', str(caught.exception))
        with self.assertRaises(RuntimeError):
            self.engines.say('Merhaba.')
        self.assertEqual(self.load('tr')['state'], 'ready')                 # loading again restarts it

    def test_engine_without_runnable_source_is_not_usable(self):
        make_engine(self.root, 'piper', ('en',), module=False)
        self.assertIsNone(self.engines.engines()[0]['mode'])
        self.assertEqual(self.load('en')['state'], 'missing')

    def package(self, engine_id='paket', entry=True, extra=None, protocol=1, top='paket/'):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr(top + 'motor.json', json.dumps({'id': engine_id, 'name': 'Paket', 'version': '2.0',
                                                             'protocol': protocol, 'entry': 'worker.exe',
                                                             'voices': [{'id': 'v', 'name': 'V', 'language': 'tr'}]}))
            if entry:
                archive.writestr(top + 'worker.exe', b'MZ')
                archive.writestr(top + '_internal/lib.dll', b'x' * 10)
            for name, data in (extra or {}).items():
                archive.writestr(name, data)
        path = Path(self.temp.name) / f'{engine_id}-{time.time_ns()}.zip'
        path.write_bytes(buffer.getvalue())
        return path

    def test_install_zip_keeps_data_and_validates(self):
        self.assertEqual(self.engines.install_zip(self.package()), 'paket')
        folder = self.app / 'ses-motorlari' / 'paket'
        self.assertTrue((folder / 'worker.exe').exists() and (folder / '_internal' / 'lib.dll').exists())
        listed = {item['id']: item for item in self.engines.engines()}['paket']
        self.assertEqual((listed['mode'], listed['installed_version'], listed['removable']), ('package', '2.0', True))
        (folder / 'data' / 'voices').mkdir(parents=True)
        (folder / 'data' / 'voices' / 'v.onnx').write_bytes(b'voice')
        self.engines.install_zip(self.package(top=''))                       # reinstall (flat layout)
        self.assertEqual((folder / 'data' / 'voices' / 'v.onnx').read_bytes(), b'voice')
        for bad, message in ((self.package(entry=False), 'eksik'), (self.package(protocol=9), 'uyumlu'),
                             (self.package(engine_id='../kaçak'), 'geçersiz'),
                             (self.package(engine_id='zip', extra={'zip/../../dışarı.txt': b'x'}, top='zip/'), 'güvenli')):
            with self.assertRaises(ValueError) as caught:
                self.engines.install_zip(bad)
            self.assertIn(message, str(caught.exception))
        self.assertFalse((self.app / 'dışarı.txt').exists())
        not_package = Path(self.temp.name) / 'x.zip'
        with zipfile.ZipFile(not_package, 'w') as archive:
            archive.writestr('readme.txt', 'x')
        with self.assertRaises(ValueError):
            self.engines.install_zip(not_package)
        self.engines.remove('paket')
        self.assertFalse(folder.exists())
        with self.assertRaises(ValueError):
            self.engines.remove('paket')

    def test_install_refuses_too_long_paths_and_cleans_up_failures(self):
        with patch('tts.MAX_PATH', 40):
            with self.assertRaises(RuntimeError) as caught:
                self.engines.install_zip(self.package())
        self.assertIn('çok uzun', str(caught.exception))
        self.assertFalse((self.app / 'ses-motorlari' / 'paket').exists())
        with patch('tts.shutil.copyfileobj', side_effect=OSError('disk dolu')):
            with self.assertRaises(OSError):
                self.engines.install_zip(self.package())
        root = self.app / 'ses-motorlari'
        self.assertEqual([p.name for p in root.iterdir()], [])            # no half-extracted staging folder
        self.assertIsNone({item['id']: item for item in self.engines.engines()}.get('paket'))
        self.assertEqual(self.engines._busy, {})


class Dialogs:
    def __init__(self, path):
        self.path = path

    def save_file(self, *_):
        return self.path


class BridgeTtsTests(unittest.TestCase):
    def test_export_runs_in_background(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            (temp / 'src' / 'engines').mkdir(parents=True)
            shutil.copy(REPO / 'engines' / 'protocol.py', temp / 'src' / 'engines' / 'protocol.py')
            (temp / 'src' / 'engines' / '__init__.py').write_text('', encoding='utf-8')
            make_engine(temp / 'src', 'ema-lightning', ('tr',))
            done = threading.Event()
            events = []

            def emit(event, data):
                events.append((event, data))
                if event == 'tts_export' and data.get('finished'):
                    done.set()

            with patch('bridge.APP_DIR', temp), patch('bridge.ROOT', temp / 'src'):
                api = Bridge(emit=emit, dialogs=Dialogs(str(temp / 'bolum')))
                self.assertIn('error', api.tts_export('Kitap', ['Bir.'], 1.0))
                api.tts_load('tr')
                self.assertTrue(wait_for(lambda: api.tts_status()['state'] == 'ready'), api.tts_status())
                self.assertIn('audio', api.tts_say('Merhaba.'))
                self.assertTrue(api.tts_export('Kitap: Bölüm/1', ['Bir.', 'İki.'], 1.0)['started'])
                self.assertTrue(done.wait(20))
                self.assertEqual([e['id'] for e in api.tts_engines()], ['ema-lightning'])
                api.shutdown()
            finished = [d for e, d in events if e == 'tts_export' and d.get('finished')][0]
            self.assertEqual(finished['count'], 2)
            self.assertTrue(finished['path'].endswith('bolum.wav') and Path(finished['path']).exists())


if __name__ == '__main__':
    unittest.main()
