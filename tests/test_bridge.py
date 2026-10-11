import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import app_core
import bridge
from bridge import Bridge, validate_glossary_rows
from epub_meta import book_info
from test_core import make_epub


class FakeClient:
    gate = None

    def __init__(self, cfg, log=print):
        self.cfg = cfg

    def context_length(self):
        return 32768, 'test'

    def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
        if FakeClient.gate is not None:
            FakeClient.gate.wait(10)
        if 'SOURCE TO TRANSLATE' in user:
            # Echo the source with a Turkish marker so the EPUB structure checks pass.
            source = user.split('SOURCE TO TRANSLATE', 1)[1].split(':\n', 1)[1]
            return source.replace('Hello world.', 'Merhaba dünya.')
        if 'Compress only' in system:
            return 'Kısa olay özeti.'
        return '- Bölüm özeti.'


class Recorder:
    def __init__(self):
        self.events = []
        self.cond = threading.Condition()

    def __call__(self, event, data):
        with self.cond:
            self.events.append((event, data))
            self.cond.notify_all()

    def wait_for(self, event, timeout=20):
        deadline = time.time() + timeout
        with self.cond:
            while True:
                found = [data for name, data in self.events if name == event]
                if found:
                    return found[-1]
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise AssertionError(f'{event} olayı gelmedi: {[name for name, _ in self.events]}')
                self.cond.wait(remaining)


class FakeDialogs:
    def __init__(self, file=None, folder=None):
        self.file, self.folder = file, folder

    def open_file(self, _title, _types):
        return self.file

    def open_folder(self, _title):
        return self.folder


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app_dir = self.root / 'app'
        self.app_dir.mkdir()
        for target in ('app_core.APP_DIR', 'bridge.APP_DIR'):
            patcher = patch(target, self.app_dir)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.epub = self.root / 'book.epub'
        make_epub(self.epub)
        self.work = self.root / 'work'
        self.events = Recorder()
        self.bridge = Bridge(emit=self.events, dialogs=FakeDialogs(file=str(self.epub), folder=str(self.work)))
        FakeClient.gate = None

    def tearDown(self):
        FakeClient.gate = None
        thread = self.bridge._thread
        if thread:
            thread.join(10)
        self.temp.cleanup()

    def prepare(self):
        snapshot = self.bridge.choose_epub()
        self.assertEqual(snapshot['book']['title'], 'Example')
        self.bridge.choose_folder()
        prepared = self.bridge.prepare_project()
        self.assertTrue(prepared['glossary_required'])
        result = self.bridge.confirm_glossary([{'source': 'Hello', 'target': 'Merhaba'}])
        self.assertNotIn('error', result)
        return result

    def test_glossary_rows_validation(self):
        self.assertEqual(validate_glossary_rows([{'source': ' A ', 'target': ' B '}, {'source': '', 'target': ''}]),
                         [{'source': 'A', 'target': 'B'}])
        with self.assertRaises(ValueError):
            validate_glossary_rows([{'source': 'A', 'target': ''}])
        with self.assertRaises(ValueError):
            validate_glossary_rows([{'source': 'A', 'target': 'B'}, {'source': 'a', 'target': 'C'}])

    def test_book_info_reads_title_author_and_cover(self):
        info = book_info(self.epub)
        self.assertEqual((info['title'], info['author']), ('Example', 'Author'))
        self.assertTrue(info['cover'].startswith('data:image/png;base64,'))
        self.assertEqual(book_info(self.root / 'missing.epub')['title'], 'missing')

    def test_start_requires_prepared_project(self):
        self.assertIn('error', self.bridge.prepare_project())
        self.assertIn('error', self.bridge.start(None))

    def test_full_translation_run_emits_progress_and_finishes(self):
        snapshot = self.prepare()
        self.assertEqual(len([c for c in snapshot['chapters'] if c['type'] == 'chapter']), 2)
        self.assertEqual(json.loads((Path(snapshot['book']['project']) / 'glossary.json').read_text(encoding='utf-8')),
                         [{'source': 'Hello', 'target': 'Merhaba'}])
        with patch('app_core.Client', FakeClient):
            status = self.bridge.start(None)
            self.assertEqual(status['runtime'], 'running')
            finished = self.events.wait_for('finished')
        self.assertEqual(finished['outcome'], 'done', finished['message'])
        chapters = [c for c in finished['snapshot']['chapters'] if c['type'] == 'chapter']
        self.assertTrue(all(c['kind'] == 'done' for c in chapters), chapters)
        self.assertEqual(finished['snapshot']['status']['progress']['percent'], 100)
        self.assertTrue(any(name == 'status' for name, _ in self.events.events))
        self.assertTrue(any(name == 'log' for name, _ in self.events.events))
        preview = self.bridge.preview(chapters[0]['filename'])
        self.assertTrue(preview['translated'])
        self.assertIn('Merhaba', preview['translation'])
        self.assertTrue((Path(finished['snapshot']['book']['project']) / 'translation.log').exists())

    def test_pause_and_resume_while_running(self):
        self.prepare()
        FakeClient.gate = threading.Event()
        with patch('app_core.Client', FakeClient):
            self.bridge.start(None)
            self.assertEqual(self.bridge.control()['runtime'], 'pausing')
            self.assertIn('error', self.bridge.choose_epub())
            FakeClient.gate.set()
            deadline = time.time() + 10
            while self.bridge._runtime != 'paused' and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(self.bridge._runtime, 'paused')
            self.assertEqual(self.bridge.control()['runtime'], 'running')
            finished = self.events.wait_for('finished')
        self.assertEqual(finished['outcome'], 'done', finished['message'])

    def test_close_request_stops_worker_gracefully(self):
        self.prepare()
        FakeClient.gate = threading.Event()
        closed = threading.Event()
        with patch('app_core.Client', FakeClient):
            self.bridge.start(None)
            self.assertFalse(self.bridge.request_close(closed.set))
            FakeClient.gate.set()
            self.assertTrue(closed.wait(10))
        self.assertEqual(self.events.wait_for('finished')['outcome'], 'stopped')
        self.assertTrue(self.bridge.request_close(lambda: None))

    def test_review_round_trip(self):
        self.prepare()
        book = self.bridge._book
        result = {}
        pending = [{'source': 'Hello', 'suggested_target': 'Selam'},
                   {'source': 'Mage', 'suggested_target': 'Büyücü', 'scope': 'book', 'reason': 'sık geçiyor'}]
        worker = threading.Thread(target=lambda: result.setdefault('values', self.bridge._ask_review(book, pending)))
        worker.start()
        review = self.events.wait_for('review')
        self.assertEqual([p['source'] for p in review['proposed']], ['Mage'])
        self.assertEqual(review['current'], [{'source': 'Hello', 'target': 'Merhaba'}])
        self.assertTrue(self.bridge.submit_review(review['current'] + [{'source': 'Mage', 'target': 'Büyücü'}]))
        worker.join(5)
        self.assertEqual(result['values'], [{'source': 'Hello', 'target': 'Merhaba'}, {'source': 'Mage', 'target': 'Büyücü'}])

    def test_glossary_edit_reports_changes(self):
        self.prepare()
        self.assertEqual(self.bridge.save_glossary([{'source': 'Hello', 'target': 'Merhaba'}]),
                         {'changed': False, 'affected': []})
        result = self.bridge.save_glossary([{'source': 'Hello', 'target': 'Selam'}])
        self.assertTrue(result['changed'])
        self.assertIn('error', self.bridge.save_glossary([{'source': 'Hello', 'target': ''}]))

    def test_settings_are_validated_and_merged(self):
        self.assertIn('error', self.bridge.save_settings({'base_url': 'http://example.com/v1'}))
        with patch.object(self.bridge, 'refresh_models'):
            saved = self.bridge.save_settings({'chunk_chars': '3000', 'glossary_strict': True})
        self.assertEqual(saved['config']['chunk_chars'], 3000)
        stored = json.loads((self.app_dir / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual((stored['chunk_chars'], stored['glossary_strict']), (3000, True))
        self.bridge._runtime = 'running'
        with patch.object(self.bridge, 'refresh_models'):
            saved = self.bridge.save_settings({'chunk_chars': 1234, 'auto_retry_count': 5})
        self.assertEqual((saved['config']['chunk_chars'], saved['config']['auto_retry_count']), (3000, 5))
        self.bridge._runtime = 'idle'

    def test_refresh_models_lists_unloaded_models(self):
        models = [
            {'type': 'llm', 'key': 'gemma-4', 'loaded_instances': [{'id': 'gemma-4', 'config': {'context_length': 8192}}]},
            {'type': 'llm', 'key': 'qwen3.6-35b-a3b', 'loaded_instances': []},
            {'type': 'embedding', 'key': 'text-embedding-nomic', 'loaded_instances': []},
        ]

        class ModelClient:
            def __init__(self, cfg, log=print):
                pass

            def model_info(self):
                return None, models

        recorder = Recorder()
        self.bridge._emit = recorder
        with patch.object(bridge, 'Client', ModelClient):
            config = app_core.load_config()
            config['model'] = 'qwen3.6-35b-a3b'
            (self.app_dir / 'config.json').write_text(json.dumps(config), encoding='utf-8')
            self.bridge.refresh_models()
            event = recorder.wait_for('models')
            while event.get('state') != 'ok':
                time.sleep(0.05)
                event = recorder.wait_for('models')
        self.assertEqual(event['available'], ['gemma-4', 'qwen3.6-35b-a3b'])
        self.assertEqual(event['model'], 'qwen3.6-35b-a3b')       # an unloaded choice is kept
        self.assertIn('warn', self.bridge.model_check())
        self.assertNotIn('block', self.bridge.model_check())

    def test_open_project_validates_folder(self):
        self.assertIn('error', self.bridge.open_project(str(self.root)))
        snapshot = self.prepare()
        other = Bridge(emit=lambda *_: None)
        reopened = other.open_project(snapshot['book']['project'])
        self.assertEqual(reopened['book']['epub'], str(self.epub.resolve()))
        self.assertEqual(reopened['status']['phase'], 'Mevcut proje hazır')
        self.assertEqual(other.prepare_project(), {'ok': True, 'glossary_required': False})

    def test_ui_prefs_round_trip(self):
        self.assertEqual(self.bridge.ui_prefs(), {})
        self.assertEqual(self.bridge.save_ui_prefs({'theme': 'light'}), {'theme': 'light'})
        self.assertEqual(self.bridge.ui_prefs(), {'theme': 'light'})


if __name__ == '__main__':
    unittest.main()
