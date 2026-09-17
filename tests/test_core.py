import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import app_core
import cevir
from app_core import (Client, DEFAULT_CONFIG, FileLock, PauseController, TranslationEngine,
                      _target_search, analysis_terms, book_data, chapters_containing, first_heading,
                      image_only_source, load_glossary, parse_analysis, reset_chapter, rows, split_source,
                      restore_missing_epub_anchors, translation_requires_completion_marker, validate_rerun,
                      validate_translation_structure)
from epub_import import prepare
from epub_output import build_translated_epub, inline_markup, run_epubcheck, validate_epub_archive


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


def make_epub_with_duplicate_spine_and_bad_toc(path):
    """EPUB3 nav'da bir dis/mutlak girdi, spine'da tekrarlanan HTML bulunur."""
    container = '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    opf = '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">9780000000001</dc:identifier><dc:title>Dup</dc:title><dc:language>en</dc:language></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/><item id="pic" href="/images/outside.png" media-type="image/png"/></manifest>
<spine><itemref idref="c1"/><itemref idref="c1"/></spine></package>'''
    nav = '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol><li><a href="c1.xhtml">One</a></li><li><a href="/outside.xhtml">Bad</a></li></ol></nav></body></html>'
    c1 = '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>One</h1><p>Hello.</p></body></html>'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml', container)
        archive.writestr('OPS/book.opf', opf)
        archive.writestr('OPS/nav.xhtml', nav)
        archive.writestr('OPS/c1.xhtml', c1)
        archive.writestr('OPS/images/outside.png', b'PNGDATA')


def make_epub_adobe_rc_encrypted(path):
    container = '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    opf = '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">9780000000002</dc:identifier><dc:title>Enc</dc:title><dc:language>en</dc:language></metadata>
<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="c1"/></spine></package>'''
    c1 = '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Enc</h1><p>Hi.</p></body></html>'
    encryption = '<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><encrypted><EncryptionMethod Algorithm="http://ns.adobe.com/pdf/enc#RC"/><CipherData><CipherReference URI="OPS/c1.xhtml"/></CipherData></encrypted></encryption>'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml', container)
        archive.writestr('META-INF/encryption.xml', encryption)
        archive.writestr('OPS/book.opf', opf)
        archive.writestr('OPS/c1.xhtml', c1)


def make_structured_epub(path):
    """Kodlanmis/parantezli gorsel, tablo ve bolumler arasi dipnot baglantisi."""
    container = '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    opf = '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">structured</dc:identifier><dc:title>Structured</dc:title><dc:language>en</dc:language></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/><item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/><item id="pic" href="images/My%20Cover(1).jpg" media-type="image/jpeg"/></manifest>
<spine><itemref idref="c1"/><itemref idref="notes"/></spine></package>'''
    nav = '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol><li><a href="c1.xhtml">One</a></li><li><a href="notes.xhtml">Notes</a></li></ol></nav></body></html>'
    c1 = '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>One</h1><p>Text<a href="notes.xhtml#n1">1</a>.</p><table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>1</td></tr></table><img src="images/My%20Cover(1).jpg" alt="cover"/></body></html>'
    notes = '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Notes</h1><p id="n1">1. Footnote.</p></body></html>'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml', container)
        archive.writestr('OPS/book.opf', opf)
        archive.writestr('OPS/nav.xhtml', nav)
        archive.writestr('OPS/c1.xhtml', c1)
        archive.writestr('OPS/notes.xhtml', notes)
        archive.writestr('OPS/images/My Cover(1).jpg', b'JPEGDATA')


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

    def test_import_tolerates_duplicate_spine_and_bad_toc_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'dup.epub'; work_parent = root / 'work'
            make_epub_with_duplicate_spine_and_bad_toc(source)
            book = prepare(source, work_parent)
            marker = json.loads((book / 'epub-import.json').read_text(encoding='utf-8'))
            # Tekrarlanan spine HTML'i tek bolum olarak islenir; bozuk TOC girdisi atlanir.
            self.assertEqual(marker['files'], ['001-One.md'])
            first = (book / 'source' / marker['files'][0]).read_text(encoding='utf-8')
            self.assertIn('Hello', first)

    def test_import_rejects_adobe_rc_encryption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'enc.epub'; work_parent = root / 'work'
            make_epub_adobe_rc_encrypted(source)
            with self.assertRaisesRegex(ValueError, 'Sifreli/DRM'):
                prepare(source, work_parent)

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

    def test_completion_marker_supports_custom_threshold_and_disabled_mode(self):
        cfg = dict(DEFAULT_CONFIG)
        self.assertFalse(translation_requires_completion_marker('# Copyright\n\nCopyright © 2020 by Martha Wells', cfg))
        self.assertTrue(translation_requires_completion_marker('A' * 401, cfg))
        cfg['completion_marker_exempt_chars'] = 500
        self.assertFalse(translation_requires_completion_marker('A' * 500, cfg))
        self.assertTrue(translation_requires_completion_marker('A' * 501, cfg))
        cfg['completion_marker_enabled'] = False
        self.assertFalse(translation_requires_completion_marker('A' * 50000, cfg))

    def test_disabled_completion_marker_also_applies_to_glossary_fix(self):
        cfg = dict(DEFAULT_CONFIG)
        cfg['token_estimation_enabled'] = False
        cfg['completion_marker_enabled'] = False
        client = Client(cfg, lambda _message: None)
        def fake_request(_route, payload=None):
            self.assertNotIn('[[END_', payload['system_prompt'])
            return {'output': [{'type': 'message', 'content': 'Kül burada.'}], 'stats': {}}
        client.request = fake_request
        engine = TranslationEngine(lambda: cfg, log=lambda _message: None)
        result = engine._enforce_glossary(
            client, 'Ash is here.', 'O burada.', [{'source': 'Ash', 'target': 'Kül'}], 100)
        self.assertEqual(result, 'Kül burada.')

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

    def test_inline_markup_escapes_alt_once_not_twice(self):
        asset_map = {'img/a.png': 'assets/img/a.png'}
        result = inline_markup('![Tom & Jerry <Co>](epub-resource:img/a.png)', asset_map)
        self.assertIn('alt="Tom &amp; Jerry &lt;Co&gt;"', result)

    def test_generate_accepts_thinking_and_response_in_sentence_middle(self):
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False
        client = Client(cfg, lambda _message: None)

        def fake_request(_route, payload=None):
            marker = re.search(r'(\[\[END_[0-9a-f]+\]\])', payload['system_prompt']).group(1)
            return {'output': [{'type': 'message', 'content': 'He was thinking about her.\nShe gave a response.\n' + marker}], 'stats': {}}

        client.request = fake_request
        self.assertEqual(client.generate('system', 'user', 100), 'He was thinking about her.\nShe gave a response.')

    def test_generate_rejects_line_start_tool_and_thought_markers(self):
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False
        client = Client(cfg, lambda _message: None)

        def fake_request(content):
            def _inner(_route, payload=None):
                marker = re.search(r'(\[\[END_[0-9a-f]+\]\])', payload['system_prompt']).group(1)
                return {'output': [{'type': 'message', 'content': content + marker}], 'stats': {}}
            return _inner

        for content in ('\n<tool_call>...', '\n<|tool_call>...', '\nthinking something', '\nresponse text'):
            client.request = fake_request(content)
            with self.assertRaisesRegex(ValueError, 'dusunce/tool'):
                client.generate('system', 'user', 100)

    def test_analysis_retries_transient_connection_errors_automatically(self):
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.calls += 1
                if FakeClient.calls == 1:
                    if 'CONNECTION' in user:
                        raise AssertionError('Ilk denemede henuz retry uyarisi olmamali.')
                elif 'CONNECTION' not in user:
                    raise AssertionError('Baglanti retry uyarisi modele gitmedi.')
                if FakeClient.calls <= 2:
                    raise RuntimeError('LM Studio baglantisi kurulamadi: <urlopen error [Errno 10061] Connection refused>')
                return json.dumps({'summary': 'Parca.', 'characters': [], 'terms': [], 'style': [],
                                   'changes': [], 'ambiguities': []}, ensure_ascii=False)

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
            self.assertEqual(FakeClient.calls, 3)
            self.assertTrue(state['complete'])
            self.assertTrue(any('Otomatik yeniden deneme 2/2' in line for line in logs))

    def test_analysis_does_not_retry_a_non_transient_connection_error(self):
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.calls += 1
                raise RuntimeError('HTTP 403: Yetkisiz istek')  # retryable degil

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
                with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
                    TranslationEngine(lambda: cfg, log=logs.append).run_analysis(book, ['001.md'])
            self.assertEqual(FakeClient.calls, 1)
            self.assertFalse(any('Otomatik yeniden deneme' in line for line in logs))

    def test_analysis_honors_configured_retry_count(self):
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.calls += 1
                return json.dumps({'summary': 'Eksik şema.'}, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('Kısa bölüm.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['auto_retry_count'] = 1
            logs = []
            with patch('app_core.Client', FakeClient):
                with self.assertRaisesRegex(ValueError, 'eksik alanlar'):
                    TranslationEngine(lambda: cfg, log=logs.append).run_analysis(book, ['001.md'])
            self.assertEqual(FakeClient.calls, 2)
            self.assertTrue(any('Otomatik yeniden deneme 1/1' in line for line in logs))

    def test_retry_count_cannot_be_disabled_or_set_below_one(self):
        cfg = dict(DEFAULT_CONFIG); cfg['auto_retry_count'] = 1
        app_core.validate_config(cfg)
        cfg['auto_retry_count'] = 0
        with self.assertRaisesRegex(ValueError, '1 ile 100'):
            app_core.validate_config(cfg)

    def test_import_and_output_epub(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'sample.epub'; work_parent = root / 'work'
            make_epub(source)
            book = prepare(source, work_parent)
            marker = json.loads((book / 'epub-import.json').read_text(encoding='utf-8'))
            self.assertEqual(marker['import_version'], 4)
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
                cover_metas = [node for node in package.iter() if node.tag.endswith('meta') and node.attrib.get('property') == 'cover-image']
                self.assertEqual(len(cover_metas), 1)
                self.assertEqual(cover_metas[0].attrib.get('refines'), '#asset-4')
                cover_items = [node for node in package.iter() if node.tag.endswith('item') and 'cover-image' in node.attrib.get('properties', '').split()]
                self.assertEqual(len(cover_items), 1)
                self.assertEqual(cover_metas[0].attrib['refines'].lstrip('#'), cover_items[0].attrib['id'])
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

    def test_translation_honors_configured_retry_count(self):
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                if 'SOURCE TO TRANSLATE' in user:
                    FakeClient.calls += 1
                    raise ValueError('Yanit tamamlanma isareti tasimiyor; sonuc kaydedilmedi.')
                return '- Not.'

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('# Test\n\n' + ('A' * 450), encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['auto_retry_count'] = 1
            logs = []
            with patch('app_core.Client', FakeClient):
                with self.assertRaisesRegex(ValueError, 'tamamlanma isareti'):
                    TranslationEngine(lambda: cfg, log=logs.append).run_file(book, '001.md')
            self.assertEqual(FakeClient.calls, 2)
            self.assertTrue(any('Otomatik yeniden deneme 1/1' in line for line in logs))

    def test_reset_chapter_backs_up_and_resets(self):
        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('# Bir\n\nMerhaba.', encoding='utf-8')
            (book / 'source' / '002.md').write_text('# İki\n\nDünya.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Metadata
- **Title:** Test
## Workflow State
- **Last completed file:** 001.md
## Files
| File | Status |
|---|---|
| `001.md` | done |
| `002.md` | next |
## Translation notes — 001.md
- Eski not.
## Translation notes — 002.md
- Eski not iki.
''', encoding='utf-8')
            (book / 'translation' / '001.md').write_text('Bir çevirisi.', encoding='utf-8')
            work = book / '_python_translation' / '001.md'; work.mkdir(parents=True)
            (work / 'state.json').write_text(json.dumps({'complete': True}), encoding='utf-8')
            reset_chapter(book, '001.md')
            self.assertFalse((book / 'translation' / '001.md').exists())
            self.assertEqual(len(list(work.glob('recheck-prev-*.md'))), 1)
            self.assertFalse((work / 'state.json').exists())
            self.assertTrue((work / 'recheck.json').exists())
            context = (book / '00-CONTEXT.md').read_text(encoding='utf-8')
            self.assertIn('| `001.md` | next |', context)
            self.assertNotIn('| `002.md` | next |', context)
            self.assertNotIn('Translation notes — 001.md', context)
            self.assertIn('Translation notes — 002.md', context)
            self.assertIn('Last completed file:** 001.md', context)

    def test_book_data_recheck_excludes_future_notes_and_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp)
            context = '''# Context
## Files
| File | Status |
|---|---|
| `001.md` | done |
| `002.md` | done |
| `003.md` | next |
## Tone and Style
- Test
## Translation notes — 001.md
- Bir notu.
## Translation notes — 002.md
- İki notu.
## Translation notes — 003.md
- Üç notu.
'''
            (book / '_python_translation').mkdir(parents=True)
            (book / '_python_translation' / 'continuity-summary.md').write_text('Sonraki bolum ozeti.', encoding='utf-8')
            data = book_data(book, context, '002.md', recheck=True)
            self.assertIn('- Bir notu.', data)
            self.assertNotIn('- İki notu.', data)
            self.assertNotIn('- Üç notu.', data)
            self.assertNotIn('Sonraki bolum ozeti.', data)
            normal = book_data(book, context, '002.md')
            self.assertIn('- İki notu.', normal)
            self.assertIn('- Üç notu.', normal)
            self.assertIn('Sonraki bolum ozeti.', normal)

    def test_run_file_with_recheck_marker_uses_clean_context(self):
        class FakeClient:
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                if 'SOURCE TO TRANSLATE' in user:
                    self.translation_user = user
                return 'Temiz çeviri.'

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('# Bir\n\nKaynak.', encoding='utf-8')
            (book / 'source' / '002.md').write_text('# İki\n\nKaynak.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | done |
| `002.md` | done |
## Tone and Style
- Test
## Translation notes — 001.md
- Bir notu.
## Translation notes — 002.md
- İki notu.
''', encoding='utf-8')
            (book / 'translation' / '002.md').write_text('Eski iki.', encoding='utf-8')
            (book / '_python_translation').mkdir()
            (book / '_python_translation' / 'continuity-summary.md').write_text('Ozet.', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            client = FakeClient(cfg)
            with patch('app_core.Client', lambda *_a, **_kw: client):
                engine = TranslationEngine(lambda: cfg, log=lambda _message: None)
                reset_chapter(book, '002.md')
                engine.run_file(book, '002.md')
            self.assertNotIn('Translation notes — 002.md', client.translation_user)
            self.assertIn('Translation notes — 001.md', client.translation_user)
            self.assertNotIn('Ozet', client.translation_user)
            self.assertFalse((book / '_python_translation' / '002.md' / 'recheck.json').exists())

    def test_analysis_terms_returns_unique_global_and_chapter_terms(self):
        """G16: analysis_terms ayni source'u yalnizca bir kez, global once olacak
        sekilde dondurur; ayni source farkli target ile iki kayit olusturmaz."""
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.calls += 1
                if 'FILE: 001.md' in user:
                    return json.dumps({'summary': 'Bir.', 'characters': [],
                                       'terms': [
                                           {'source': 'Corporation Rim', 'suggested_target': 'Corporation Rim', 'reason': 'ozel ad', 'scope': 'book'},
                                           {'source': 'Corporation Rim', 'suggested_target': 'Kurumsal Çeper', 'reason': 'duplicate', 'scope': 'chapter'},
                                           {'source': 'SecUnit', 'suggested_target': 'GüvBirim', 'reason': 'tutarlılık', 'scope': 'book'},
                                       ],
                                       'style': [], 'changes': [], 'ambiguities': []}, ensure_ascii=False)
                return json.dumps({'summary': 'İki.', 'characters': [],
                                   'terms': [{'source': 'corporate surveillance capitalism', 'suggested_target': 'kurumsal gözetim kapitalizmi', 'reason': '', 'scope': 'chapter'}],
                                   'style': [], 'changes': [], 'ambiguities': []}, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('# Bir\n\nCorporation Rim.', encoding='utf-8')
            (book / 'source' / '002.md').write_text('# İki\n\nKapitalizm.', encoding='utf-8')
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
                TranslationEngine(lambda: cfg, log=lambda _message: None).run_analysis(book)
            terms = analysis_terms(book)
            sources = [item['source'].casefold() for item in terms]
            self.assertEqual(len(sources), len(set(sources)))
            self.assertIn('corporation rim', sources)
            self.assertEqual(sources.count('corporation rim'), 1)
            self.assertIn('secunit', sources)
            self.assertIn('corporate surveillance capitalism', sources)

    def test_glossary_merge_deduplicates_and_keeps_existing_targets(self):
        """G16: _merge_analysis_terms_into_glossary mevcut kararlari korur,
        ayni source yeni karşılik ile gelirse duplicate olusturmaz."""
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.calls += 1
                return json.dumps({'summary': 'Özet.', 'characters': [],
                                   'terms': [
                                       {'source': 'SecUnit', 'suggested_target': 'GüvBirim', 'reason': 'tutarlılık', 'scope': 'book'},
                                       {'source': 'Corporation Rim', 'suggested_target': 'Kurumsal Çeper', 'reason': 'yeni', 'scope': 'chapter'},
                                       {'source': 'Corporation Rim', 'suggested_target': 'Corporation Rim', 'reason': 'duplicate', 'scope': 'chapter'},
                                   ],
                                   'style': [], 'changes': [], 'ambiguities': []}, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('Corporation Rim.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
## Tone and Style
- Test
''', encoding='utf-8')
            existing = [{'source': 'Corporation Rim', 'target': 'Corporation Rim'}]
            (book / 'glossary.json').write_text(json.dumps(existing, ensure_ascii=False), encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            with patch('app_core.Client', FakeClient):
                engine = TranslationEngine(lambda: cfg, log=lambda _message: None)
                engine.run_analysis(book)
                engine._merge_analysis_terms_into_glossary(book)
            merged = json.loads((book / 'glossary.json').read_text(encoding='utf-8'))
            sources = [item['source'].casefold() for item in merged]
            self.assertEqual(len(sources), len(set(sources)))
            self.assertIn('secunit', sources)
            # Mevcut kayit korunur; ayni source yeni karşılik ile gelince
            # yalnizca ilk karşılik (mevcut) kalir.
            rim = [item for item in merged if item['source'].casefold() == 'corporation rim']
            self.assertEqual(len(rim), 1)
            self.assertEqual(rim[0]['target'], 'Corporation Rim')
            self.assertNotIn('Kurumsal Çeper', [item['target'] for item in merged])

    def test_load_glossary_deduplicates_existing_entries(self):
        """G16: glossary.json'da ayni source iki kez varsa load_glossary ilk karşıligi korur."""
        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp)
            glossary = [
                {'source': 'Corporation Rim', 'target': 'Corporation Rim'},
                {'source': 'Corporation Rim', 'target': 'Kurumsal Çeper'},
                {'source': 'corporation rim', 'target': 'Üçüncü'},
            ]
            (book / 'glossary.json').write_text(json.dumps(glossary, ensure_ascii=False), encoding='utf-8')
            loaded = load_glossary(book)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]['target'], 'Corporation Rim')

    def test_run_analysis_does_not_touch_glossary_until_merge(self):
        """G16: run_analysis tek basina glossary.json'u degistirmez; merge
        acikca cagrilinca yazar. Boylece CLI/GUI onay akisi karari verir."""
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.calls += 1
                return json.dumps({'summary': 'Özet.', 'characters': [],
                                   'terms': [{'source': 'SecUnit', 'suggested_target': 'GüvBirim', 'reason': '', 'scope': 'book'}],
                                   'style': [], 'changes': [], 'ambiguities': []}, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('SecUnit.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
## Tone and Style
- Test
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            with patch('app_core.Client', FakeClient):
                TranslationEngine(lambda: cfg, log=lambda _message: None).run_analysis(book)
            self.assertFalse((book / 'glossary.json').exists())
            self.assertTrue(analysis_terms(book))

    def test_chapters_containing_finds_matching_chapters(self):
        """G17: chapters_containing tam kelime eslestirir; alt dize ("Ash" →
        "ashamed") bir bolumu etkilenmis saymaz."""
        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('Murderbot geldi.', encoding='utf-8')
            (book / 'source' / '002.md').write_text('He was ashamed in his bed.', encoding='utf-8')
            (book / 'source' / '003.md').write_text('Ash kapıyı çaldı.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | done |
| `002.md` | next |
| `003.md` | |
## Tone and Style
- Test
''', encoding='utf-8')
            self.assertEqual(chapters_containing(book, ['Murderbot']), ['001.md'])
            self.assertEqual(chapters_containing(book, ['Yok']), [])
            # G17: "Ash" yalnızca tam kelime olarak geçen bolumleri dondurur.
            self.assertEqual(chapters_containing(book, ['Ash'], only_done=False), ['003.md'])
            # G21: varsayilan olarak henuz cevrilmemis bolumler listeye girmez.
            self.assertEqual(chapters_containing(book, ['Ash']), [])

    def test_enforce_glossary_ignores_substring_occurrences(self):
        """G17: "Ash" terimi "ashamed" içinde geçse bile kaynakta tam kelime
        olarak yoksa zorunlu karşılık aranmaz; karşılık arama da alt dizede
        yanlış pozitif üretmez."""
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False
        client = Client(cfg, lambda _message: None)

        def fake_request(_route, payload=None):
            marker = re.search(r'(\[\[END_[0-9a-f]+\]\])', payload['system_prompt']).group(1)
            return {'output': [{'type': 'message', 'content': 'He felt ashamed.\n' + marker}], 'stats': {}}

        client.request = fake_request
        translated = 'Utandı.'
        glossary = [{'source': 'Ash', 'target': 'Kül'}]
        engine = TranslationEngine(lambda: dict(DEFAULT_CONFIG), log=lambda _message: None)
        # Kaynakta "Ash" tam kelime olarak yok (sadece ashamed): denetim
        # düzeltme isteği göndermez, çeviriyi aynen dondurur.
        self.assertEqual(engine._enforce_glossary(client, 'He felt ashamed.', translated, glossary, 100), translated)

    def test_enforce_glossary_matches_whole_words_only_in_translated_too(self):
        """G17: zorunlu terim kaynakta tam kelime olarak varsa karşılık da
        çıktıda tam kelime olarak aranır; "külot" içindeki "kül" sayılmaz."""
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False; cfg['glossary_strict'] = True
        client = Client(cfg, lambda _message: None)

        def fake_request(_route, payload=None):
            marker = re.search(r'(\[\[END_[0-9a-f]+\]\])', payload['system_prompt']).group(1)
            return {'output': [{'type': 'message', 'content': 'Giydiği külot eskiydi.\n' + marker}], 'stats': {}}

        client.request = fake_request
        glossary = [{'source': 'Ash', 'target': 'Kül'}]
        engine = TranslationEngine(lambda: dict(DEFAULT_CONFIG), log=lambda _message: None)
        # Kaynakta "Ash" tam kelime var; çıktıda yalnızca "külot" içinde "kül"
        # geçiyor → karşılık eksik sayılır, düzeltme isteği gider.
        with self.assertRaisesRegex(ValueError, 'Glossary zorunlulugu saglanamadi: Kül'):
            engine._enforce_glossary(client, 'Ash is here.', 'Giydiği külot eskiydi.', glossary, 100)

    def test_enforce_glossary_accepts_turkish_inflected_target(self):
        """G18: 'constructs -> Yapı' (tekil) girisinde model 'yapılar' (coğul)
        üretse de denetim kabul eder (kök + opsiyonel çekim eki)."""
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False
        client = Client(cfg, lambda _message: None)

        def fake_request(_route, payload=None):
            marker = re.search(r'(\[\[END_[0-9a-f]+\]\])', payload['system_prompt']).group(1)
            return {'output': [{'type': 'message', 'content': 'The constructs are free.\n' + marker}], 'stats': {}}

        client.request = fake_request
        glossary = [{'source': 'constructs', 'target': 'Yapı'}]
        engine = TranslationEngine(lambda: dict(DEFAULT_CONFIG), log=lambda _message: None)
        # Kaynakta "constructs" tam kelime var; çeviride "yapılar" çekimli
        # biçimi kabul edilir (G18), düzeltme isteği yok.
        self.assertEqual(engine._enforce_glossary(client, 'The constructs are free.', 'Yapılar özgür.', glossary, 100), 'Yapılar özgür.')

    def test_enforce_glossary_accepts_slash_separated_inflected_forms(self):
        """G18: model 'yapılar/konstrüktler' gibi eğik çizgili üretimde de kök
        çekimli kabul edilir; düzeltme isteği yok."""
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False
        client = Client(cfg, lambda _message: None)

        def fake_request(_route, payload=None):
            marker = re.search(r'(\[\[END_[0-9a-f]+\]\])', payload['system_prompt']).group(1)
            return {'output': [{'type': 'message', 'content': 'The constructs are free.\n' + marker}], 'stats': {}}

        client.request = fake_request
        glossary = [{'source': 'constructs', 'target': 'Yapı'}]
        engine = TranslationEngine(lambda: dict(DEFAULT_CONFIG), log=lambda _message: None)
        self.assertEqual(engine._enforce_glossary(client, 'The constructs are free.', 'Yapılar/konstrüktler özgürdür.', glossary, 100), 'Yapılar/konstrüktler özgürdür.')

    def test_enforce_glossary_accepts_inflected_target_variants(self):
        """G18: 'Yapı' koku icin yönelme (-ya), belirtme (-yı), bulunma (-da),
        ilgi (-nın), ayrılma (-dan) çekimleri de kabul edilir."""
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False
        client = Client(cfg, lambda _message: None)

        def fake_request(content):
            def _inner(_route, payload=None):
                marker = re.search(r'(\[\[END_[0-9a-f]+\]\])', payload['system_prompt']).group(1)
                return {'output': [{'type': 'message', 'content': content + marker}], 'stats': {}}
            return _inner

        glossary = [{'source': 'constructs', 'target': 'Yapı'}]
        engine = TranslationEngine(lambda: dict(DEFAULT_CONFIG), log=lambda _message: None)
        for content in ('Bu yapıya dikkat.', 'Yapının içinde.', 'Yapıda sorun var.', 'Yapıdan çıktı.', 'Yapıyı gördü.'):
            client.request = fake_request(content)
            self.assertEqual(engine._enforce_glossary(client, 'The constructs are dangerous.', content, glossary, 100), content)

    def test_enforce_glossary_accepts_multi_suffix_turkish_forms(self):
        """G18: 'Yapı' koku icin cok ekli cekimler de kabul edilir —
        'Yapıların' (coğul+ilgi), 'Yapısı' (iyelik), 'Yapıları' (coğul+iyelik).
        Gerçek proje logundaki (glossary_fixes.log) kullanimlar."""
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False
        client = Client(cfg, lambda _message: None)

        def fake_request(content):
            def _inner(_route, payload=None):
                marker = re.search(r'(\[\[END_[0-9a-f]+\]\])', payload['system_prompt']).group(1)
                return {'output': [{'type': 'message', 'content': content + marker}], 'stats': {}}
            return _inner

        glossary = [{'source': 'constructs', 'target': 'Yapı'}]
        engine = TranslationEngine(lambda: dict(DEFAULT_CONFIG), log=lambda _message: None)
        for content in (
            'İnsan/bot Yapıları üretmek.',
            'Yapıların farkında değiller.',
            'bir bot/insan Yapısı köleleştirmek.',
            'Yapılarda sorun yok.',
        ):
            client.request = fake_request(content)
            self.assertEqual(engine._enforce_glossary(client, 'The constructs are dangerous.', content, glossary, 100), content)

    def test_enforce_glossary_does_not_accept_unrelated_inflected_form(self):
        """G18: 'kül' karşılığı 'külot' içinde kabul edilmez (kok uzatmasi farkli
        kelime); çekim toleransı yalnızca ekli biçimleri kapsar."""
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False; cfg['glossary_strict'] = True
        client = Client(cfg, lambda _message: None)

        def fake_request(_route, payload=None):
            marker = re.search(r'(\[\[END_[0-9a-f]+\]\])', payload['system_prompt']).group(1)
            return {'output': [{'type': 'message', 'content': 'Giydiği külot eskiydi.\n' + marker}], 'stats': {}}

        client.request = fake_request
        glossary = [{'source': 'Ash', 'target': 'Kül'}]
        engine = TranslationEngine(lambda: dict(DEFAULT_CONFIG), log=lambda _message: None)
        with self.assertRaisesRegex(ValueError, 'Glossary zorunlulugu saglanamadi: Kül'):
            engine._enforce_glossary(client, 'Ash is here.', 'Giydiği külot eskiydi.', glossary, 100)

    def test_enforce_glossary_records_failed_fix(self):
        """G19: düzeltme isteği başarısız olduğunda engine.glossary_fixes
        listesine kayıt düşülür (kaynak + önceki çeviri + denenen düzeltme)."""
        cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False
        client = Client(cfg, lambda _message: None)

        def fake_request(_route, payload=None):
            marker = re.search(r'(\[\[END_[0-9a-f]+\]\])', payload['system_prompt']).group(1)
            return {'output': [{'type': 'message', 'content': 'Melez varlıklar serbesttir.\n' + marker}], 'stats': {}}

        client.request = fake_request
        glossary = [{'source': 'constructs', 'target': 'Yapı'}]
        engine = TranslationEngine(lambda: dict(DEFAULT_CONFIG), log=lambda _message: None)
        # G25: katı mod kapalıyken çeviri durmaz; düzeltme daha iyi değilse ilk çeviri döner.
        self.assertEqual(engine._enforce_glossary(client, 'The constructs are free.', 'Melez varlıklar özgür.', glossary, 100),
                         'Melez varlıklar özgür.')
        # G19: kayıt yazıldı mı?
        self.assertEqual(len(engine.glossary_fixes), 1)
        record = engine.glossary_fixes[0]
        self.assertEqual(record['missing_targets'], ['Yapı'])
        self.assertIn('constructs', record['source'])
        self.assertEqual(record['translated_before'], 'Melez varlıklar özgür.')
        self.assertEqual(record['attempted_fix'], 'Melez varlıklar serbesttir.')

    def test_rerun_chapters_only_reruns_selected(self):
        class FakeClient:
            translation_calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                if 'SOURCE TO TRANSLATE' in user:
                    FakeClient.translation_calls += 1
                return 'Yeni çeviri.'

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('# Bir\n\nMurderbot geldi.', encoding='utf-8')
            (book / 'source' / '002.md').write_text('# İki\n\nMurderbot gitti.', encoding='utf-8')
            (book / 'source' / '003.md').write_text('# Üç\n\nHayat normal.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | done |
| `002.md` | done |
| `003.md` | done |
## Tone and Style
- Test
''', encoding='utf-8')
            for index in (1, 2, 3):
                (book / 'translation' / f'00{index}.md').write_text(f'Eski çeviri {index}.', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            with patch('app_core.Client', FakeClient):
                engine = TranslationEngine(lambda: cfg, log=lambda _message: None)
                engine.rerun_chapters(book, ['001.md'])
            self.assertEqual(FakeClient.translation_calls, 1)
            self.assertIn('Yeni çeviri.', (book / 'translation' / '001.md').read_text(encoding='utf-8'))
            self.assertEqual((book / 'translation' / '002.md').read_text(encoding='utf-8'), 'Eski çeviri 2.')
            self.assertEqual((book / 'translation' / '003.md').read_text(encoding='utf-8'), 'Eski çeviri 3.')

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

    def test_run_book_raises_when_next_never_clears(self):
        class FakeClient:
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                return 'Türkçe bölüm.'

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('# Bölüm\n\nKaynak.', encoding='utf-8')
            (book / 'glossary.json').write_text('[]', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
## Tone and Style
- Test
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            with patch('app_core.Client', FakeClient):
                TranslationEngine(lambda: cfg, log=lambda _message: None).run_book(book, build_epub=False)
            # El ile bozuk context: tamamlanan dosya hala next gorunuyor.
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
## Tone and Style
- Test
''', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'Ilerleme yok'):
                with patch('app_core.Client', FakeClient):
                    TranslationEngine(lambda: cfg, log=lambda _message: None).run_book(book, build_epub=False)

    def test_compression_uses_the_active_chapter_config_snapshot(self):
        class FakeClient:
            compress_models = []
            def __init__(self, cfg, log=print):
                self.cfg = cfg
                self.log = log
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                if 'Compress only' in system:
                    FakeClient.compress_models.append(self.cfg['model'])
                return 'Yanit.'

        calls = []

        def provider():
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            cfg['model'] = 'model-A' if not calls else 'model-B'
            calls.append(True)
            return cfg

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('# Bir\n\nİlk kaynak satır.', encoding='utf-8')
            (book / 'source' / '002.md').write_text('# İki\n\nİkinci kaynak satır.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
| `002.md` | |
## Tone and Style
- Test
''', encoding='utf-8')
            with patch('app_core.Client', FakeClient):
                TranslationEngine(provider, log=lambda _message: None).run_book(book, build_epub=False)
            # 001 sonrasi %50 -> %25 ve %50 esikleri birlikte tetiklenir, ikisi de
            # bolum 1'in config'iyle (model-A) gider; 002 sonrasi %100 -> %75 esigi
            # bolum 2'nin config'iyle (model-B) gider. Aradaki guncel config.json
            # (model-B) bolum 1'in sikistirmasina yansimamali.
            # G22: ayni anda tetiklenen esikler tek sikistirma istegiyle islenir; yeni not
            # yoksa bos istek gonderilmez.
            self.assertEqual(FakeClient.compress_models, ['model-A', 'model-B'])

    def test_analysis_signature_ignores_translation_only_settings(self):
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.calls += 1
                return json.dumps({'summary': 'Özet.', 'characters': [], 'terms': [], 'style': [],
                                   'changes': [], 'ambiguities': []}, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('# One\n\nKısa bölüm.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
## Tone and Style
- Test
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            with patch('app_core.Client', FakeClient):
                engine = TranslationEngine(lambda: cfg, log=lambda _message: None)
                engine.run_analysis(book)
                initial_calls = FakeClient.calls
                # temperature analiz kararlılığını etkilemez; checkpoint korunmalı.
                cfg['temperature'] = 1.0
                engine.run_analysis(book)
                self.assertEqual(FakeClient.calls, initial_calls)
                # chunk_chars analizi gerçekten etkiler; yeniden analiz istenmeli.
                cfg['chunk_chars'] = 500
                with self.assertRaisesRegex(ValueError, 'analiz ayarlar'):
                    engine.run_analysis(book)

    def test_run_file_rejects_a_broken_effective_config(self):
        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('# Bölüm\n\nKaynak.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
## Tone and Style
- Test
''', encoding='utf-8')
            work = book / '_python_translation' / '001.md'
            work.mkdir(parents=True)
            (work / 'state.json').write_text(json.dumps({'effective_config': {}}), encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG)
            with self.assertRaisesRegex(ValueError, 'Checkpoint ayarlari'):
                TranslationEngine(lambda: cfg, log=lambda _message: None).run_file(book, '001.md')
            # Yalnizca base_url olan eksik config de ayni korumaya girmeli.
            (work / 'state.json').write_text(
                json.dumps({'effective_config': {'base_url': 'http://127.0.0.1:1234/v1'}}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Checkpoint ayarlari'):
                TranslationEngine(lambda: cfg, log=lambda _message: None).run_file(book, '001.md')

    def test_cli_logger_writes_translation_log(self):
        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp)
            log = cevir.make_logger(book)
            log('On analiz 001.md 1/2 — yanit bekleniyor...')
            log('Parca 1 kaydedildi.')
            content = (book / 'translation.log').read_text(encoding='utf-8')
            self.assertIn('yanit bekleniyor', content)
            self.assertIn('kaydedildi', content)
            self.assertEqual(content.count('\n'), 2)

    def test_cli_review_analysis_terms_confirms_and_merges(self):
        """G16: CLI onay akisi evet derse merge eder, hayir derse korur."""
        class FakeClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                FakeClient.calls += 1
                return json.dumps({'summary': 'Özet.', 'characters': [],
                                   'terms': [{'source': 'SecUnit', 'suggested_target': 'GüvBirim', 'reason': '', 'scope': 'book'}],
                                   'style': [], 'changes': [], 'ambiguities': []}, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp); (book / 'source').mkdir(); (book / 'translation').mkdir()
            (book / 'source' / '001.md').write_text('SecUnit.', encoding='utf-8')
            (book / '00-CONTEXT.md').write_text('''# Context
## Files
| File | Status |
|---|---|
| `001.md` | next |
## Tone and Style
- Test
''', encoding='utf-8')
            cfg = dict(DEFAULT_CONFIG); cfg['chunk_chars'] = 2000
            with patch('app_core.Client', FakeClient):
                engine = TranslationEngine(lambda: cfg, log=lambda _message: None)
                engine.run_analysis(book)
                # Hayir: glossary degismez.
                cevir.review_analysis_terms(engine, book, confirm=lambda _prompt: 'h')
                self.assertFalse((book / 'glossary.json').exists())
                # Evet: merge edilir.
                cevir.review_analysis_terms(engine, book, confirm=lambda _prompt: 'e')
            self.assertTrue((book / 'glossary.json').exists())
            merged = json.loads((book / 'glossary.json').read_text(encoding='utf-8'))
            self.assertEqual(merged[0]['target'], 'GüvBirim')

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


def make_book(root, statuses, notes=True):
    """Kucuk test projesi: statuses = [('001.md', 'done'), ...]."""
    book = Path(root); (book / 'source').mkdir(exist_ok=True); (book / 'translation').mkdir(exist_ok=True)
    lines = ['# Context', '## Metadata', '- **Title:** Test', '## Workflow State', '- **Last completed file:** none',
             '## Files', '| File | Status |', '|---|---|']
    for name, status in statuses:
        (book / 'source' / name).write_text(f'# {name}\n\nAsh walked.\n\nSecond paragraph.', encoding='utf-8')
        lines.append(f'| `{name}` | {status} |')
        if status == 'done':
            (book / 'translation' / name).write_text(f'Eski {name}.\n', encoding='utf-8')
    lines += ['## Tone and Style', '- Test']
    if notes:
        for name, status in statuses:
            if status == 'done':
                lines += [f'## Translation notes — {name}', f'- Not {name}.']
    (book / '00-CONTEXT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return book


class RecordingClient:
    """Cevirileri, notlari ve sikistirma cagrilarini kaydeden sahte istemci."""
    calls = []
    marker_flags = []
    context_tokens = 32768
    fail_once = set()

    def __init__(self, cfg, log=print):
        self.cfg = cfg; self.log = log

    def context_length(self):
        return RecordingClient.context_tokens, 'test'

    def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
        kind = ('translation' if 'SOURCE TO TRANSLATE' in user else 'decisions' if 'durable translation decisions' in system
                else 'compress' if 'Compress only' in system else 'glossary' if 'MANDATORY GLOSSARY' in user else 'notes')
        RecordingClient.calls.append((kind, user))
        RecordingClient.marker_flags.append((kind, require_marker))
        if kind in RecordingClient.fail_once:
            RecordingClient.fail_once.discard(kind)
            raise RuntimeError('LM Studio baglantisi kurulamadi: test')
        return {'translation': 'Yeni çeviri.', 'decisions': '- Ash -> Kül', 'compress': 'Kısa özet.',
                'glossary': 'Düzeltilmiş.', 'notes': '- Not: Ash -> Kül. ' + 'x' * 50}[kind]


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        RecordingClient.calls = []; RecordingClient.marker_flags = []
        RecordingClient.context_tokens = 32768; RecordingClient.fail_once = set()
        self.cfg = dict(DEFAULT_CONFIG); self.cfg['chunk_chars'] = 2000

    def engine(self, logs=None):
        return TranslationEngine(lambda: dict(self.cfg), log=(logs.append if logs is not None else (lambda _m: None)))

    def test_short_chapter_translation_uses_markerless_validated_path(self):
        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'next')], notes=False)
            with patch('app_core.Client', RecordingClient):
                self.engine().run_file(book, '001.md')
            translation_flags = [flag for kind, flag in RecordingClient.marker_flags if kind == 'translation']
            self.assertEqual(translation_flags, [False])

    def test_marker_settings_update_an_existing_failed_checkpoint(self):
        class MarkerClient:
            fail = True
            flags = []
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                if 'SOURCE TO TRANSLATE' in user:
                    MarkerClient.flags.append(require_marker)
                    if MarkerClient.fail:
                        raise ValueError('Yanit tamamlanma isareti tasimiyor; sonuc kaydedilmedi.')
                    return 'Yeni çeviri.'
                return '- Kısa bölüm notu.'

        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'next')], notes=False)
            (book / 'source' / '001.md').write_text('# Test\n\n' + ('A' * 450), encoding='utf-8')
            logs = []
            with patch('app_core.Client', MarkerClient):
                with self.assertRaisesRegex(ValueError, 'tamamlanma isareti'):
                    self.engine(logs).run_file(book, '001.md')
                self.assertEqual(MarkerClient.flags, [True, True, True])
                self.cfg['completion_marker_exempt_chars'] = 500
                self.cfg['auto_retry_count'] = 1
                MarkerClient.fail = False
                self.engine(logs).run_file(book, '001.md')
            self.assertEqual(MarkerClient.flags[-1], False)
            state = json.loads((book / '_python_translation' / '001.md' / 'state.json').read_text(encoding='utf-8'))
            self.assertEqual(state['effective_config']['completion_marker_exempt_chars'], 500)
            self.assertEqual(state['effective_config']['auto_retry_count'], 1)
            self.assertTrue(any('mevcut yarim bolum' in line for line in logs))

    # --- G24: Turkce eslesme -------------------------------------------------
    def test_turkish_target_search_handles_mutations_and_case(self):
        accepted = [('kitap', 'Kitabı masaya koydu'), ('ev', 'Evde kimse yoktu'), ('at', 'Atı koştu'),
                    ('Işık', 'ışığı gördü'), ('Işık', 'ışıklar yandı'), ('ağaç', 'Ağacın altında'),
                    ('burun', 'Burnunu kaşıdı'), ('renk', 'Rengi soluktu'), ('Kara Kule', "Kara Kule'ye vardılar"),
                    ('Kara Kule', 'Kara Kulesinde'), ('Yapı', 'YAPILAR'), ('Yapılar', 'yapıya'),
                    ('kitap', 'kitaplarımızdan'), ('Kül', 'küller')]
        rejected = [('at', 'atlet giydi'), ('Kara Kule', 'Kara bir gün'), ('Kül', 'külot'), ('Yapı', 'yapılı'),
                    ('Kul', 'kullanmak')]
        for target, text in accepted:
            self.assertTrue(_target_search(target, text), (target, text))
        for target, text in rejected:
            self.assertFalse(_target_search(target, text), (target, text))

    # --- G25: katı olmayan glossary --------------------------------------------
    def test_glossary_miss_logs_and_continues_unless_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'next')], notes=False)
            (book / 'glossary.json').write_text('[{"source":"Ash","target":"Kül"}]', encoding='utf-8')
            logs = []
            with patch('app_core.Client', RecordingClient):
                self.engine(logs).run_file(book, '001.md')
            self.assertTrue((book / 'translation' / '001.md').exists())
            self.assertIn('MISSING TARGETS: Kül', (book / 'glossary_fixes.log').read_text(encoding='utf-8'))
            self.assertTrue(any('UYARI: Glossary' in line for line in logs))
        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'next')], notes=False)
            (book / 'glossary.json').write_text('[{"source":"Ash","target":"Kül"}]', encoding='utf-8')
            self.cfg['glossary_strict'] = True
            with patch('app_core.Client', RecordingClient):
                with self.assertRaisesRegex(ValueError, 'Glossary zorunlulugu'):
                    self.engine().run_file(book, '001.md')

    # --- G21: yeniden ceviri guvenligi -------------------------------------------
    def test_rerun_does_not_lock_a_partially_translated_chapter(self):
        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'done'), ('002.md', 'done'), ('003.md', 'next')])
            self.cfg['chunk_chars'] = 20

            class StopAfterFirstPart(RecordingClient):
                def generate(self, system, user, max_tokens, **kwargs):
                    if 'PART 2/' in user:
                        raise KeyboardInterrupt()
                    return super().generate(system, user, max_tokens, **kwargs)

            with patch('app_core.Client', StopAfterFirstPart):
                with self.assertRaises(KeyboardInterrupt):
                    self.engine().run_file(book, '003.md')
            partial = json.loads((book / '_python_translation' / '003.md' / 'state.json').read_text(encoding='utf-8'))
            self.assertEqual(len(partial['parts']), 1)
            with patch('app_core.Client', RecordingClient):
                self.engine().rerun_chapters(book, ['001.md'])
                self.engine().run_book(book, build_epub=False)
            statuses = dict(rows((book / '00-CONTEXT.md').read_text(encoding='utf-8')))
            self.assertEqual(set(statuses.values()), {'done'})
            finished = json.loads((book / '_python_translation' / '003.md' / 'state.json').read_text(encoding='utf-8'))
            self.assertTrue(finished['complete'])
            self.assertEqual(finished['parts'][0], partial['parts'][0])  # kayitli parca yeniden cevrilmedi

    def test_same_chapter_can_be_rerun_twice_and_outputs_are_rebuilt(self):
        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'done'), ('002.md', 'done')])
            with patch('app_core.Client', RecordingClient):
                self.engine().rerun_chapters(book, ['001.md'])
                self.engine().rerun_chapters(book, ['001.md'])
            work = book / '_python_translation' / '001.md'
            self.assertEqual(len(list(work.glob('recheck-prev-*.md'))), 2)
            self.assertIn('Yeni çeviri.', (book / 'TAM-CEVIRI.md').read_text(encoding='utf-8'))

    def test_rerun_rejects_untranslated_chapters_before_touching_anything(self):
        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'done'), ('002.md', 'next'), ('003.md', '')])
            before = (book / '00-CONTEXT.md').read_text(encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'henuz cevrilmedi'):
                validate_rerun(book, ['001.md', '003.md'])
            with patch('app_core.Client', RecordingClient):
                with self.assertRaisesRegex(ValueError, 'henuz cevrilmedi'):
                    self.engine().rerun_chapters(book, ['001.md', '003.md'])
            self.assertEqual((book / '00-CONTEXT.md').read_text(encoding='utf-8'), before)
            self.assertTrue((book / 'translation' / '001.md').exists())

    def test_compressed_notes_are_tracked_by_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'done'), ('002.md', 'done'), ('003.md', 'done'), ('004.md', 'next')])
            (book / '_python_translation').mkdir()
            # Eski bicim: ilk iki not bolumu sikistirilmis.
            (book / '_python_translation' / 'book-state.json').write_text('{"compressed_at":[25,50],"summary_note_count":2}', encoding='utf-8')
            reset_chapter(book, '001.md')
            context = (book / '00-CONTEXT.md').read_text(encoding='utf-8')
            state = json.loads((book / '_python_translation' / 'book-state.json').read_text(encoding='utf-8'))
            self.assertEqual(state['compressed_notes'], ['002.md'])
            data = book_data(book, context, '004.md')
            self.assertIn('- Not 003.md.', data)      # sira kaysa da sikistirilmamis not gizlenmez
            self.assertNotIn('- Not 002.md.', data)

    # --- G22/G23/G27: baglam butcesi, kalici kararlar, yeniden deneme ----------
    def test_reference_budget_triggers_batched_compression_and_keeps_decisions(self):
        with tempfile.TemporaryDirectory() as temp:
            names = [f'{index:03}.md' for index in range(1, 11)]
            book = make_book(temp, [(name, 'next' if index == 0 else '') for index, name in enumerate(names)], notes=False)
            RecordingClient.context_tokens = 5000  # %40 butce ≈ 2000 token
            self.cfg['notes_max_tokens'] = 500; self.cfg['context_safety_tokens'] = 100; self.cfg['max_tokens'] = 500

            class BigNotes(RecordingClient):
                def generate(self, system, user, max_tokens, **kwargs):
                    result = super().generate(system, user, max_tokens, **kwargs)
                    return result + ' ' + 'n' * 4000 if RecordingClient.calls[-1][0] == 'notes' else result

            with patch('app_core.Client', BigNotes):
                self.engine().run_file(book, names[0])
                self.engine().run_file(book, names[1])
                self.engine()._compress_if_needed(book, BigNotes(self.cfg))
            kinds = [kind for kind, _user in RecordingClient.calls]
            self.assertIn('decisions', kinds); self.assertIn('compress', kinds)
            self.assertLess(kinds.index('decisions'), kinds.index('compress'))
            state = json.loads((book / '_python_translation' / 'book-state.json').read_text(encoding='utf-8'))
            self.assertEqual(state['compressed_at'], [])  # %20: yuzde esigi degil, butce tetikledi
            self.assertEqual(state['compressed_notes'], names[:2])
            data = book_data(book, (book / '00-CONTEXT.md').read_text(encoding='utf-8'), names[2])
            self.assertIn('- Ash -> Kül', data)
            self.assertIn('Kısa özet.', data)
            self.assertNotIn('n' * 4000, data)

    def test_notes_generation_retries_transient_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'next')], notes=False)
            RecordingClient.fail_once = {'notes'}
            logs = []
            with patch('app_core.Client', RecordingClient):
                self.engine(logs).run_file(book, '001.md')
            self.assertTrue((book / 'translation' / '001.md').exists())
            self.assertTrue(any('Bolum notu yaniti alinamadi' in line for line in logs))

    def test_notes_and_compression_helper_honors_configured_retry_count(self):
        class AlwaysFailClient:
            calls = 0
            def __init__(self, cfg): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                AlwaysFailClient.calls += 1
                raise RuntimeError('LM Studio baglantisi kurulamadi: test')

        cfg = dict(DEFAULT_CONFIG); cfg['auto_retry_count'] = 1
        logs = []
        engine = TranslationEngine(lambda: cfg, log=logs.append)
        with self.assertRaisesRegex(RuntimeError, 'baglantisi'):
            engine._generate_retry(AlwaysFailClient(cfg), 'system', 'user', 100, 'Bolum notu')
        self.assertEqual(AlwaysFailClient.calls, 2)
        self.assertTrue(any('Otomatik yeniden deneme 1/1' in line for line in logs))

    # --- G26: on analiz onerileri kapsami ----------------------------------------
    def test_analysis_terms_keep_scope_and_merge_skips_chapter_terms(self):
        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'next')], notes=False)
            analysis_dir = book / '_python_analysis'; analysis_dir.mkdir()
            part = {'summary': '', 'characters': [], 'style': [], 'changes': [], 'ambiguities': [],
                    'terms': [{'source': 'SecUnit', 'suggested_target': 'GüvBirim', 'reason': '', 'scope': 'book'},
                              {'source': 'hub', 'suggested_target': 'merkez', 'reason': '', 'scope': 'chapter'}]}
            (analysis_dir / '001.md.v2.state.json').write_text(json.dumps({'analysis_version': 2, 'parts': [part]}), encoding='utf-8')
            scopes = {item['source']: item['scope'] for item in analysis_terms(book)}
            self.assertEqual(scopes, {'SecUnit': 'book', 'hub': 'chapter'})
            self.engine()._merge_analysis_terms_into_glossary(book)
            merged = json.loads((book / 'glossary.json').read_text(encoding='utf-8'))
            self.assertEqual([item['source'] for item in merged], ['SecUnit'])

    # --- G29: EPUB donusumu ---------------------------------------------------------
    def test_structure_validation_rejects_changed_or_missing_epub_targets(self):
        source = ('Text ![map](epub-resource:OPS/images/map(1).png) '
                  '[note](epub-link:OPS/notes.xhtml%23n1) [[EPUB_ANCHOR:OPS/c1.xhtml%23a]]')
        validate_translation_structure(source, source)
        with self.assertRaisesRegex(ValueError, 'Yapisal EPUB'):
            validate_translation_structure(source, source.replace('map(1).png', 'map(2).png'))
        with self.assertRaisesRegex(ValueError, 'Yapisal EPUB'):
            validate_translation_structure(source, source.replace('[note](epub-link:OPS/notes.xhtml%23n1)', 'note'))
        table = '# H\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n'
        validate_translation_structure(table, '# T\n\n| X | Y |\n| --- | --- |\n| 3 | 4 |\n')
        with self.assertRaisesRegex(ValueError, 'tablo yapisi'):
            validate_translation_structure(table, '# T\n\nX ve Y\n')
        self.assertTrue(app_core.retryable_translation_error(
            ValueError('Yapisal EPUB isaretleri korunmadi: gorsel')))

    def test_missing_standalone_epub_anchor_is_restored_at_source_block(self):
        source = ('# Chapter\n\nFirst paragraph.\n\n'
                  '[[EPUB_ANCHOR:OPS/c1.xhtml%23page_68]]\n\n'
                  'Second paragraph.\n\nThird paragraph.')
        translated = '# Bölüm\n\nİlk paragraf.\n\nİkinci paragraf.\n\nÜçüncü paragraf.'
        restored, count = restore_missing_epub_anchors(source, translated)
        self.assertEqual(count, 1)
        self.assertIn(
            'İlk paragraf.\n\n[[EPUB_ANCHOR:OPS/c1.xhtml%23page_68]]\n\nİkinci paragraf.',
            restored,
        )
        validate_translation_structure(source, restored)

    def test_translation_pipeline_restores_anchor_without_retry(self):
        class AnchorDroppingClient:
            translation_calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, system, user, max_tokens, require_marker=True, allow_json_fence=False):
                if 'SOURCE TO TRANSLATE' in user:
                    AnchorDroppingClient.translation_calls += 1
                    return '# Bölüm\n\nİlk paragraf.\n\nİkinci paragraf.'
                return '- Kısa bölüm notu.'

        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'next')], notes=False)
            anchor = '[[EPUB_ANCHOR:OPS/c1.xhtml%23page_68]]'
            (book / 'source' / '001.md').write_text(
                '# Chapter\n\nFirst paragraph.\n\n' + anchor + '\n\nSecond paragraph.',
                encoding='utf-8',
            )
            logs = []
            with patch('app_core.Client', AnchorDroppingClient):
                self.engine(logs).run_file(book, '001.md')
            translated = (book / 'translation' / '001.md').read_text(encoding='utf-8')
            self.assertEqual(AnchorDroppingClient.translation_calls, 1)
            self.assertIn(anchor, translated)
            self.assertTrue(any('otomatik geri yerlestirildi: 1' in line for line in logs))

    def test_ambiguous_or_changed_epub_anchor_is_not_silently_repaired(self):
        source = 'First.\n\n[[EPUB_ANCHOR:OPS/c1.xhtml%23a]]\n\nSecond.'
        merged = 'Birinci ve ikinci paragraf birleştirildi.'
        self.assertEqual(restore_missing_epub_anchors(source, merged), (merged, 0))
        changed = 'Birinci.\n\n[[EPUB_ANCHOR:OPS/c1.xhtml%23b]]\n\nİkinci.'
        self.assertEqual(restore_missing_epub_anchors(source, changed), (changed, 0))
        with self.assertRaisesRegex(ValueError, 'Yapisal EPUB'):
            validate_translation_structure(source, changed)

    def test_image_only_chapter_is_copied_without_model_request(self):
        class NoRequestClient:
            calls = 0
            def __init__(self, cfg, log=print): self.cfg = cfg
            def context_length(self): return 32768, 'test'
            def generate(self, *args, **kwargs):
                NoRequestClient.calls += 1
                raise AssertionError('Yalnizca gorsel bolum modele gitmemeli.')

        with tempfile.TemporaryDirectory() as temp:
            book = make_book(temp, [('001.md', 'next')], notes=False)
            source = '# Cover\n\n![cover](epub-resource:OPS/images/cover(1).jpg)\n'
            (book / 'source' / '001.md').write_text(source, encoding='utf-8')
            self.assertTrue(image_only_source(source))
            cfg = dict(DEFAULT_CONFIG); cfg['token_estimation_enabled'] = False
            with patch('app_core.Client', NoRequestClient):
                TranslationEngine(lambda: cfg, log=lambda _message: None).run_file(book, '001.md')
            self.assertEqual(NoRequestClient.calls, 0)
            self.assertEqual((book / 'translation' / '001.md').read_text(encoding='utf-8'), source)
            state = json.loads((book / '_python_translation' / '001.md' / 'state.json').read_text(encoding='utf-8'))
            self.assertTrue(state['complete'])

    def test_encoded_parenthesized_asset_table_and_internal_note_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'structured.epub'; work = root / 'work'
            make_structured_epub(source)
            book = prepare(source, work)
            marker = json.loads((book / 'epub-import.json').read_text(encoding='utf-8'))
            self.assertEqual(marker['import_version'], 4)
            first = (book / 'source' / marker['files'][0]).read_text(encoding='utf-8')
            second = (book / 'source' / marker['files'][1]).read_text(encoding='utf-8')
            self.assertIn('epub-resource:OPS/images/My Cover(1).jpg', first)
            self.assertIn('epub-link:OPS/notes.xhtml%23n1', first)
            self.assertIn('| Name | Value |', first)
            self.assertIn('EPUB_ANCHOR:OPS/notes.xhtml%23n1', second)
            for filename in marker['files']:
                text = (book / 'source' / filename).read_text(encoding='utf-8')
                (book / 'translation' / filename).write_text(text, encoding='utf-8')
            output = build_translated_epub(book, lambda _message: None)
            validate_epub_archive(output)
            with zipfile.ZipFile(output) as archive:
                self.assertIn('OEBPS/assets/OPS/images/My Cover(1).jpg', archive.namelist())
                page1 = archive.read('OEBPS/text/chapter-0001.xhtml').decode('utf-8')
                page2 = archive.read('OEBPS/text/chapter-0002.xhtml').decode('utf-8')
                self.assertIn('<table>', page1)
                self.assertIn('href="chapter-0002.xhtml#', page1)
                target_id = re.search(r'href="chapter-0002.xhtml#([^"]+)', page1).group(1)
                self.assertIn(f'id="{target_id}"', page2)
                self.assertIn('My Cover(1).jpg', page1)

    def test_optional_epubcheck_jar_command(self):
        with tempfile.TemporaryDirectory() as temp:
            jar = Path(temp) / 'epubcheck.jar'; jar.write_bytes(b'jar')
            epub = Path(temp) / 'book.epub'; epub.write_bytes(b'epub')
            completed = __import__('subprocess').CompletedProcess([], 0, 'No errors', '')
            logs = []
            with patch('epub_output.shutil.which', return_value='java'), \
                    patch('epub_output.subprocess.run', return_value=completed) as runner:
                run_epubcheck(epub, jar, logs.append)
            self.assertEqual(runner.call_args.args[0][:3], ['java', '-jar', str(jar)])
            self.assertTrue(any('basarili' in line for line in logs))

    def test_epub_markdown_round_trip_keeps_structure(self):
        from epub_import import Markdown, decode_document
        from epub_output import markdown_xhtml
        parser = Markdown('OPS/c1.xhtml')
        parser.feed('<p>One.</p><hr/><p>Two<sup><a href="notes.xhtml#f1">1</a></sup>. <a id="x">anchor</a> '
                    '<a href="https://a.com/?a=1&amp;b=2">site</a></p><ol><li>a</li><li>b</li></ol>'
                    '<blockquote><p>Quote<br/>two</p></blockquote>')
        markdown = parser.result()
        self.assertIn('* * *', markdown)
        self.assertIn('epub-link:OPS/notes.xhtml%23f1', markdown)
        self.assertIn('EPUB_ANCHOR:OPS/c1.xhtml%23x', markdown)
        self.assertIn('1. a\n2. b', markdown)
        self.assertIn('> Quote\n> two', markdown)
        links = {'OPS/notes.xhtml#f1': {'href': 'text/chapter-2.xhtml', 'id': 'ref-note'},
                 'OPS/c1.xhtml#x': {'href': 'text/chapter-1.xhtml', 'id': 'ref-x'}}
        page = markdown_xhtml(markdown, 't', {}, link_map=links, current_href='text/chapter-1.xhtml')
        ET.fromstring(page.split('\n', 2)[2])  # gecerli XHTML
        self.assertIn('<p class="scene-break">* * *</p>', page)
        self.assertIn('Two<sup><a href="chapter-2.xhtml#ref-note">1</a></sup>.', page)
        self.assertIn('href="https://a.com/?a=1&amp;b=2"', page)
        self.assertIn('href="chapter-2.xhtml#ref-note"', page)
        self.assertIn('id="ref-x"', page)
        self.assertNotIn('&amp;amp;', page)
        self.assertIn('<ol><li>a</li><li>b</li></ol>', page)
        self.assertIn('<blockquote><p>Quote<br/>two</p></blockquote>', page)
        # Eski projelerde kalmis ham ic baglanti metin olarak cikar.
        self.assertIn('<p>Bak 3.</p>', markdown_xhtml('Bak [3](notes.xhtml#f3).', 't', {}))
        self.assertEqual(decode_document(b'<?xml version="1.0" encoding="iso-8859-9"?><p>Kad\xfdn</p>', 'x')[-12:], '<p>Kadın</p>')
        self.assertIn('café', decode_document(b'<p>caf\xe9</p>', 'x'))


@unittest.skipIf(app_core.msvcrt is None and app_core.fcntl is None, 'OS dosya kilidi yok')
class FileLockTests(unittest.TestCase):
    def test_acquire_release_reacquire(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'proje.lock'
            with FileLock(path):
                pass
            with FileLock(path):
                pass
            self.assertFalse(path.exists())

    def test_second_owner_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'proje.lock'
            first = FileLock(path).acquire()
            try:
                with self.assertRaisesRegex(RuntimeError, 'baska bir Cevirgec'):
                    FileLock(path).acquire()
            finally:
                first.release()
            # Yayinlaninca ayni kilit yeniden alinabilmeli.
            with FileLock(path):
                pass


if __name__ == '__main__':
    unittest.main()
