import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from app_core import Client, DEFAULT_CONFIG, PauseController, TranslationEngine, book_data, first_heading, parse_analysis, split_source
from epub_import import prepare
from epub_output import build_translated_epub


def make_epub(path):
    container = '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    opf = '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">9780000000000</dc:identifier><dc:title>Example</dc:title><dc:creator>Author</dc:creator><dc:language>en</dc:language><dc:publisher>Publisher</dc:publisher></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/><item id="pic" href="images/p.png" media-type="image/png" properties="cover-image"/></manifest>
<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>'''
    nav = '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol><li><a href="c1.xhtml">One</a></li><li><a href="c2.xhtml">Two</a></li></ol></nav></body></html>'
    c1 = '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>One</h1><p>Hello world.</p><img src="images/p.png" alt="cover"/></body></html>'
    c2 = '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Two</h1><p>Goodbye.</p></body></html>'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml', container)
        archive.writestr('OPS/book.opf', opf)
        archive.writestr('OPS/nav.xhtml', nav)
        archive.writestr('OPS/c1.xhtml', c1)
        archive.writestr('OPS/c2.xhtml', c2)
        archive.writestr('OPS/images/p.png', b'PNGDATA')


class CoreTests(unittest.TestCase):
    def test_split_round_trip_and_long_paragraph(self):
        text = 'a' * 15 + '\n\nshort\n'
        chunks = split_source(text, 8)
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(len(chunk) <= 8 for chunk in chunks))

    def test_first_heading_fallback(self):
        self.assertEqual(first_heading('# Türkçe Başlık\n\nMetin', 'x'), 'Türkçe Başlık')
        self.assertEqual(first_heading('Metin', 'Dosya'), 'Dosya')

    def test_pause_waits_and_resumes(self):
        states = []
        control = PauseController(states.append)
        control.request_pause()
        finished = []
        thread = threading.Thread(target=lambda: (control.checkpoint(), finished.append(True)))
        thread.start(); time.sleep(.05)
        self.assertFalse(finished)
        control.resume(); thread.join(1)
        self.assertTrue(finished)
        self.assertIn('paused', states)

    def test_analysis_accepts_an_exact_json_code_fence(self):
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False
        client = Client(cfg, lambda _message: None)
        def fake_request(_route, payload=None):
            marker = re.search(r'(\[\[END_[0-9a-f]+\]\])', payload['system_prompt']).group(1)
            return {'output': [{'type': 'message', 'content': '```json\n{"summary":"ok"}\n```\n' + marker}], 'stats': {}}
        client.request = fake_request
        self.assertEqual(client.generate('system', 'user', 100, allow_json_fence=True), '{"summary":"ok"}')

    def test_missing_analysis_scope_falls_back_to_chapter(self):
        value = parse_analysis(json.dumps({'summary': 'x', 'characters': [{'name': 'A', 'facts': ['f']}],
                                          'terms': [{'source': 'T', 'suggested_target': 'K'}], 'style': ['yerel'],
                                          'changes': [], 'ambiguities': []}))
        self.assertEqual(value['characters'][0]['scope'], 'chapter')
        self.assertEqual(value['terms'][0]['scope'], 'chapter')
        self.assertEqual(value['style'][0]['scope'], 'chapter')

    def test_markerless_analysis_requires_a_complete_json_schema(self):
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False
        client = Client(cfg, lambda _message: None)
        payload = {'summary': 'Tamam.', 'characters': [], 'terms': [], 'style': [],
                   'changes': [], 'ambiguities': []}
        def fake_request(_route, request_payload=None):
            self.assertNotIn('[[END_', request_payload['system_prompt'])
            return {'output': [{'type': 'message', 'content': json.dumps(payload, ensure_ascii=False)}], 'stats': {}}
        client.request = fake_request
        text = client.generate('system', 'user', 100, require_marker=False, allow_json_fence=True)
        self.assertEqual(parse_analysis(text)['summary'], 'Tamam.')
        with self.assertRaisesRegex(ValueError, 'eksik alanlar'):
            parse_analysis('{"summary":"yarim"}')

    def test_analysis_resumes_after_the_fourth_of_eight_parts_fails(self):
        class FakeClient:
            calls = 0
            fail_on_fourth = True
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                self.assertions(require_marker, allow_json_fence)
                FakeClient.calls += 1
                if FakeClient.fail_on_fourth and 4 <= FakeClient.calls <= 6:
                    raise ValueError('Yanit tamamlanma isareti tasimiyor; sonuc kaydedilmedi.')
                return json.dumps({'summary': 'Parca.', 'characters': [], 'terms': [], 'style': [],
                                   'changes': [], 'ambiguities': []}, ensure_ascii=False)
            @staticmethod
            def assertions(require_marker, allow_json_fence):
                if require_marker or not allow_json_fence:
                    raise AssertionError('Ön analiz marker istememeli ve JSON fence kabul etmeli.')

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('a' * 80, encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 10
            with patch('app_core.Client', FakeClient):
                engine = TranslationEngine(lambda: cfg, log=lambda _message: None)
                with self.assertRaisesRegex(ValueError, 'tamamlanma isareti'):
                    engine.run_analysis(book, ['001.md'])
                state_path = book / '_python_analysis' / '001.md.v2.state.json'
                self.assertEqual(len(json.loads(state_path.read_text(encoding='utf-8'))['parts']), 3)
                FakeClient.fail_on_fourth = False; FakeClient.calls = 0
                engine.run_analysis(book, ['001.md'])
            state = json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(FakeClient.calls, 5)
            self.assertEqual(len(state['parts']), 8)
            self.assertTrue(state['complete'])

    def test_analysis_retries_an_incomplete_json_response_automatically(self):
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.calls += 1
                payload = {'summary': 'Parca.', 'characters': [], 'terms': [], 'style': [], 'changes': []}
                if FakeClient.calls > 1:
                    payload['ambiguities'] = []
                    self.assert_retry_notice(user)
                return json.dumps(payload, ensure_ascii=False)
            @staticmethod
            def assert_retry_notice(user):
                if 'RETRY NOTICE' not in user:
                    raise AssertionError('Yeniden deneme uyarısı modele gönderilmedi.')

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('Kisa bolum.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            logs = []
            with patch('app_core.Client', FakeClient):
                TranslationEngine(lambda: cfg, log=logs.append).run_analysis(book, ['001.md'])
            state = json.loads((book / '_python_analysis' / '001.md.v2.state.json').read_text(encoding='utf-8'))
            self.assertEqual(FakeClient.calls, 2)
            self.assertTrue(state['complete'])
            self.assertTrue(any('Otomatik yeniden deneme 1/2' in line for line in logs))

    def test_import_and_output_epub(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'sample.epub'; work_parent = root / 'work'
            make_epub(source)
            book = prepare(source, work_parent)
            marker = json.loads((book / 'epub-import.json').read_text(encoding='utf-8'))
            self.assertEqual(marker['import_version'], 3)
            self.assertEqual(len(marker['files']), 2)
            first_source = (book / 'source' / marker['files'][0]).read_text(encoding='utf-8')
            self.assertIn('epub-resource:OPS/images/p.png', first_source)
            for index, filename in enumerate(marker['files'], 1):
                text = (book / 'source' / filename).read_text(encoding='utf-8')
                text = text.replace('# One', '# Bir').replace('# Two', '# İki')
                (book / 'translation' / filename).write_text(text, encoding='utf-8')
            output = build_translated_epub(book, lambda _message: None)
            self.assertEqual(output.name, 'sample TR.epub')
            with output.open('rb') as stream:
                self.assertEqual(stream.read(4), b'PK\x03\x04')
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.infolist()[0].filename, 'mimetype')
                self.assertEqual(archive.infolist()[0].compress_type, zipfile.ZIP_STORED)
                self.assertNotIn('OEBPS/text/cover.xhtml', archive.namelist())
                self.assertEqual(archive.read('OEBPS/assets/OPS/images/p.png'), b'PNGDATA')
                package = ET.fromstring(archive.read('OEBPS/content.opf'))
                languages = [node.text for node in package.iter() if node.tag.endswith('language')]
                self.assertEqual(languages, ['tr'])
                titles = [node.text for node in package.iter() if node.tag.endswith('title')]
                self.assertIn('Example TR', titles)
                nav_text = archive.read('OEBPS/nav.xhtml').decode('utf-8')
                self.assertIn('Bir', nav_text)
                first_chapter = archive.read('OEBPS/text/chapter-0001.xhtml').decode('utf-8')
                self.assertIn('../assets/OPS/images/p.png', first_chapter)
                self.assertEqual(first_chapter.count('<h1>Bir</h1>'), 1)
            second = build_translated_epub(book, lambda _message: None)
            self.assertEqual(second.name, 'sample TR-2.epub')

    def test_engine_writes_checkpoint_context_and_summary_flags(self):
        class FakeClient:
            def __init__(self, cfg, log=print): self.cfg = cfg; self.log = log
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                if 'SOURCE TO TRANSLATE' in user: return '# Bölüm\n\nKatilbot geldi.'
                if 'Compress only' in system: return 'Kısa olay özeti.'
                return '- Murderbot -> Katilbot\n- Bölüm özeti.'

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp)
            (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('# One\n\nMurderbot arrived.', encoding='utf-8')
            (book / 'glossary.json').write_text('[{"source":"Murderbot","target":"Katilbot"}]', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# 00-CONTEXT
## Metadata
- **Title:** Test
## Workflow State
- **Last completed file:** none
## Files
| File | Status |
|---|---|
| `001.md` | next |
## Tone and Style
- Test
## Proper Nouns
- Test
## Glossary
- Test
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            events = []
            with patch('app_core.Client', FakeClient):
                TranslationEngine(lambda: cfg, log=lambda _message: None, progress=lambda **data: events.append(data)).run_book(book, build_epub=False)
            self.assertIn('Katilbot', (book / 'translation' / '001.md').read_text(encoding='utf-8'))
            state = json.loads((book / '_python_translation' / '001.md' / 'state.json').read_text(encoding='utf-8'))
            self.assertTrue(state['complete'])
            book_state = json.loads((book / '_python_translation' / 'book-state.json').read_text(encoding='utf-8'))
            self.assertEqual(book_state['compressed_at'], [25, 50, 75])
            self.assertTrue(any(event.get('phase') == 'chapter_complete' for event in events))

    def test_translation_retries_and_preserves_the_previous_part_checkpoint(self):
        class FakeClient:
            fail_second_part = True
            part_calls = {1: 0, 2: 0}
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                if 'SOURCE TO TRANSLATE' not in user:
                    return '- Bölüm notu.'
                part = 2 if 'PART 2/2' in user else 1
                FakeClient.part_calls[part] += 1
                if part == 2 and FakeClient.fail_second_part:
                    if FakeClient.part_calls[part] > 1 and 'previous response' not in system:
                        raise AssertionError('Çeviri tekrar uyarısı system promptuna eklenmedi.')
                    raise ValueError('Yanit tamamlanma isareti tasimiyor; sonuc kaydedilmedi.')
                return f'Türkçe parça {part}.'

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('a' * 20, encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# 00-CONTEXT
## Metadata
- **Title:** Test
## Workflow State
- **Last completed file:** none
## Files
| File | Status |
|---|---|
| `001.md` | next |
## Tone and Style
- Test
## Proper Nouns
- Test
## Glossary
- Test
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 10
            logs = []
            with patch('app_core.Client', FakeClient):
                engine = TranslationEngine(lambda: cfg, log=logs.append)
                with self.assertRaisesRegex(ValueError, 'tamamlanma isareti'):
                    engine.run_file(book, '001.md')
                state_path = book / '_python_translation' / '001.md' / 'state.json'
                first_state = json.loads(state_path.read_text(encoding='utf-8'))
                self.assertEqual(len(first_state['parts']), 1)
                self.assertEqual(FakeClient.part_calls, {1: 1, 2: 3})
                FakeClient.fail_second_part = False
                engine.run_file(book, '001.md')
            final_state = json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(FakeClient.part_calls, {1: 1, 2: 4})
            self.assertTrue(final_state['complete'])
            self.assertTrue(any('Otomatik yeniden deneme 2/2' in line for line in logs))

    def test_translation_does_not_retry_an_output_token_limit_error(self):
        class FakeClient:
            translation_calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.translation_calls += 1
                raise ValueError('Cikti token sinirina ulasti; eksik sonuc kaydedilmedi.')

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('Kısa kaynak.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            with patch('app_core.Client', FakeClient):
                with self.assertRaisesRegex(ValueError, 'Cikti token sinirina ulasti'):
                    TranslationEngine(lambda: cfg, log=lambda _message: None).run_file(book, '001.md')
            self.assertEqual(FakeClient.translation_calls, 1)
            state = json.loads((book / '_python_translation' / '001.md' / 'state.json').read_text(encoding='utf-8'))
            self.assertEqual(state['parts'], [])

    def test_optional_analysis_checkpoints_without_changing_glossary(self):
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg; self.log = log
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.calls += 1
                first = '001.md' in user
                return json.dumps({
                    'summary': 'İlk bölüm özeti.' if first else 'İkinci bölüm özeti.',
                    'characters': [
                        {'name': 'Murderbot', 'facts': ['Bir SecUnit.'], 'voice': [], 'scope': 'book'},
                        {'name': 'Murderbot', 'facts': [], 'voice': ['Kuru konuşur.' if first else 'Daha yumuşak konuşur.'], 'scope': 'chapter'},
                    ],
                    'terms': [{'source': 'SecUnit', 'suggested_target': 'GüvBirim', 'reason': 'Kısa ve tutarlı.', 'scope': 'book'}],
                    'style': [
                        {'observation': 'Birinci tekil şahıs anlatım.', 'scope': 'book'},
                        {'observation': 'Gerilimli.' if first else 'Daha sakin.', 'scope': 'chapter'},
                    ],
                    'changes': ['Mesafeli davranır.' if first else 'Yakınlık kurar.'],
                    'ambiguities': ['SecUnit terimi bağlama göre özel ad olabilir.'],
                }, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('# One\n\nMurderbot is a SecUnit.', encoding='utf-8')
            (book / 'source' / '002.md').write_text('# Two\n\nThe SecUnit answers.', encoding='utf-8')
            glossary = '[{"source":"Murderbot","target":"Katilbot"}]'
            (book / 'glossary.json').write_text(glossary, encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
| `002.md` | |
## Tone and Style
- Test
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            with patch('app_core.Client', FakeClient):
                engine = TranslationEngine(lambda: cfg, log=lambda _message: None)
                engine.run_analysis(book)
                calls = FakeClient.calls
                engine.run_analysis(book)
            self.assertEqual(FakeClient.calls, calls)
            self.assertEqual((book / 'glossary.json').read_text(encoding='utf-8'), glossary)
            self.assertIn('GüvBirim', (book / 'BOOK-ANALYSIS.md').read_text(encoding='utf-8'))
            global_reference = (book / '_python_translation' / 'pre-analysis-reference.md').read_text(encoding='utf-8')
            self.assertIn('SecUnit -> GüvBirim', global_reference)
            self.assertIn('Birinci tekil şahıs anlatım.', global_reference)
            self.assertNotIn('Kuru konuşur.', global_reference)
            self.assertNotIn('İlk bölüm özeti.', global_reference)
            first_reference = (book / '_python_analysis' / '001.md.reference.md').read_text(encoding='utf-8')
            second_reference = (book / '_python_analysis' / '002.md.reference.md').read_text(encoding='utf-8')
            self.assertIn('Kuru konuşur.', first_reference)
            self.assertNotIn('Daha yumuşak konuşur.', first_reference)
            self.assertIn('Daha yumuşak konuşur.', second_reference)
            combined = book_data(book, (book / '00-CONTEXT.md').read_text(encoding='utf-8'), '001.md')
            self.assertIn('Kuru konuşur.', combined)
            self.assertNotIn('Daha yumuşak konuşur.', combined)
            state = json.loads((book / '_python_analysis' / '001.md.v2.state.json').read_text(encoding='utf-8'))
            self.assertTrue(state['complete'])

    def test_analysis_selection_excludes_unselected_chapters(self):
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.calls += 1
                return json.dumps({'summary': 'Yalnız seçilen bölüm.', 'characters': [], 'terms': [],
                                   'style': [{'observation': 'Seçili üslup.', 'scope': 'chapter'}],
                                   'changes': [], 'ambiguities': []}, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001-cover.md').write_text('# Cover\n', encoding='utf-8')
            (book / 'source' / '002-story.md').write_text('# Story\n\nText.', encoding='utf-8')
            context = '''# Context
## Files
| File | Status |
|---|---|
| `001-cover.md` | next |
| `002-story.md` | |
## Tone and Style
- Test
'''
            (book / '00-CONTEXT.md').write_text(context, encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            events = []
            with patch('app_core.Client', FakeClient):
                TranslationEngine(lambda: cfg, log=lambda _message: None, progress=lambda **data: events.append(data)).run_analysis(book, ['002-story.md'])
            self.assertEqual(FakeClient.calls, 1)
            selection = json.loads((book / '_python_analysis' / 'selection.json').read_text(encoding='utf-8'))
            self.assertEqual(selection['filenames'], ['002-story.md'])
            self.assertNotIn('Seçili üslup.', book_data(book, context, '001-cover.md'))
            self.assertIn('Seçili üslup.', book_data(book, context, '002-story.md'))
            self.assertTrue(any(event.get('phase') == 'analysis_saved' for event in events))


if __name__ == '__main__':
    unittest.main()
