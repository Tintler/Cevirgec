import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from bridge import Bridge
from reader import EpubBook, Library
from test_core import make_epub

PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                    '1f15c4890000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082')


def make_rich_epub(path, encrypted=False):
    opf = '''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="u">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">x</dc:identifier><dc:title>Rich</dc:title><dc:creator>Writer</dc:creator></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/><item id="a" href="text/a.xhtml" media-type="application/xhtml+xml"/>
<item id="b" href="text/b.xhtml" media-type="application/xhtml+xml"/><item id="img" href="img/p.png" media-type="image/png"/>
<item id="css" href="style.css" media-type="text/css"/></manifest>
<spine><itemref idref="cover"/><itemref idref="a"/><itemref idref="b"/></spine></package>'''
    nav = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>'
           '<li><a href="text/a.xhtml">Part One</a><ol><li><a href="text/a.xhtml#s2">Second scene</a></li></ol></li>'
           '<li><a href="text/b.xhtml">Notes</a></li></ol></nav></body></html>')
    cover = ('<html xmlns="http://www.w3.org/1999/xhtml"><body><svg xmlns="http://www.w3.org/2000/svg" '
             'xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="img/p.png"/></svg></body></html>')
    a = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>T</title>'
         '<style>p{color:red}</style><script>alert(1)</script></head><body>'
         '<h1 style="text-align: center" onclick="evil()">One</h1>'
         '<p class="x" onmouseover="evil()">Hello <b>world</b> <a href="b.xhtml#n1">1</a> <a href="https://example.com">site</a>'
         ' <a href="javascript:evil()">bad</a></p><script>evil()</script><iframe src="x"></iframe>'
         '<p id="s2">Scene <img src="../img/p.png" alt="pic" onerror="evil()"/> <img src="http://tracker/x.png"/></p>'
         '<p>Unclosed <em>emphasis</p></body></html>')
    b = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
         '<aside epub:type="footnote" id="n1"><p>A note.</p></aside><a id="end"/></body></html>')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml', '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        if encrypted:
            archive.writestr('META-INF/encryption.xml', '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData><EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/></EncryptedData></encryption>')
        for name, content in (('OPS/book.opf', opf), ('OPS/nav.xhtml', nav), ('OPS/cover.xhtml', cover),
                              ('OPS/text/a.xhtml', a), ('OPS/text/b.xhtml', b), ('OPS/style.css', 'p{}')):
            archive.writestr(name, content)
        archive.writestr('OPS/img/p.png', PNG)


class EpubBookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.epub = self.root / 'rich.epub'
        make_rich_epub(self.epub)

    def tearDown(self):
        self.temp.cleanup()

    def test_spine_and_nested_toc(self):
        book = EpubBook(self.epub)
        self.assertEqual([s['path'] for s in book.sections], ['OPS/cover.xhtml', 'OPS/text/a.xhtml', 'OPS/text/b.xhtml'])
        self.assertEqual([(e['section'], e['anchor'], e['title'], e['depth']) for e in book.toc],
                         [(1, '', 'Part One', 0), (1, 'r-s2', 'Second scene', 1), (2, '', 'Notes', 0)])
        self.assertEqual(book.section_title(1), 'Part One')
        self.assertEqual(book.section_title(0), '')

    def test_sanitises_scripts_styles_and_event_handlers(self):
        html = EpubBook(self.epub).section_html(1)
        for forbidden in ('<script', 'alert', 'evil', '<style', 'color:red', '<iframe', 'onclick', 'onmouseover',
                          'onerror', 'class="x"', 'javascript:', 'tracker', '<title'):
            self.assertNotIn(forbidden, html)
        self.assertIn('<h1 class="align-center">One</h1>', html)
        self.assertIn('<b>world</b>', html)
        self.assertIn('<a data-section="2" data-anchor="r-n1">1</a>', html)
        self.assertIn('<a data-ext="https://example.com">site</a>', html)
        self.assertIn('<p id="r-s2">', html)
        self.assertIn('src="data:image/png;base64,', html)
        self.assertIn('alt="pic"', html)
        # Unclosed tags are closed so the fragment stays well formed.
        ET.fromstring('<div>' + html.replace('<br>', '<br/>').replace('" alt="pic">', '" alt="pic"/>') + '</div>')

    def test_svg_cover_image_and_notes(self):
        book = EpubBook(self.epub)
        self.assertIn('<img src="data:image/png;base64,', book.section_html(0))
        notes = book.section_html(2)
        self.assertIn('<aside id="r-n1" data-note="1">', notes)
        self.assertIn('<span id="r-end"></span>', notes)

    def test_rejects_drm_and_missing_sections(self):
        drm = self.root / 'drm.epub'
        make_rich_epub(drm, encrypted=True)
        with self.assertRaises(ValueError):
            EpubBook(drm)
        with self.assertRaises(ValueError):
            EpubBook(self.epub).section_html(9)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.epub = self.root / 'rich.epub'
        make_rich_epub(self.epub)
        self.library = Library(self.root / 'app')
        (self.root / 'app').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_add_list_remove_and_errors(self):
        other = self.root / 'notes.txt'
        other.write_text('x', encoding='utf-8')
        broken = self.root / 'broken.epub'
        broken.write_bytes(b'not a zip')
        result = self.library.add([str(self.epub), str(self.epub), str(other), str(broken)])
        self.assertEqual(len(set(result['added'])), 1)
        self.assertEqual(len(result['errors']), 2)
        books = self.library.list()
        self.assertEqual([(b['title'], b['author'], b['percent'], b['exists']) for b in books], [('Rich', 'Writer', 0, True)])
        stored = json.loads((self.root / 'app' / 'library.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['books'][0]['path'], str(self.epub.resolve()))
        self.library.remove(books[0]['id'])
        self.assertEqual(self.library.list(), [])

    def test_open_section_position_and_bookmarks(self):
        book_id = self.library.add([str(self.epub)])['added'][0]
        opened = self.library.open(book_id)
        self.assertEqual(opened['position'], {'section': 0, 'offset': 0})
        self.assertEqual(len(opened['sections']), 3)
        self.assertIsNotNone(self.library.list()[0]['opened'])
        self.assertIn('Hello', self.library.section(book_id, 1)['html'])
        percent = self.library.save_position(book_id, 1, 0.5)
        self.assertTrue(0 < percent < 100)
        self.assertEqual(self.library.save_position(book_id, 2, 1.0), 100)
        self.assertEqual(self.library.open(book_id)['position'], {'section': 2, 'offset': 1.0})
        marks = self.library.add_bookmark(book_id, 1, 0.25, 'Scene start')
        marks = self.library.add_bookmark(book_id, 0, 0.0, 'Cover')
        self.assertEqual([m['label'] for m in marks], ['Cover', 'Scene start'])
        marks = self.library.remove_bookmark(book_id, marks[0]['id'])
        self.assertEqual([m['label'] for m in marks], ['Scene start'])
        self.assertEqual(self.library.list()[0]['bookmarks'], 1)

    def test_missing_file_is_reported(self):
        book_id = self.library.add([str(self.epub)])['added'][0]
        self.epub.unlink()
        self.assertFalse(self.library.list()[0]['exists'])
        self.assertIsNone(self.library.cover(book_id))
        with self.assertRaises(ValueError):
            self.library.open(book_id)


class BridgeReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        patcher = patch('bridge.APP_DIR', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.epub = self.root / 'book.epub'
        make_epub(self.epub)
        self.events = []
        self.bridge = Bridge(emit=lambda event, data: self.events.append((event, data)))

    def tearDown(self):
        self.temp.cleanup()

    def test_add_emits_library_and_errors_are_returned(self):
        result = self.bridge.library_add([str(self.epub)])
        self.assertEqual(len(result['books']), 1)
        self.assertEqual(self.events[-1][0], 'library')
        book_id = result['books'][0]['id']
        self.assertTrue(self.bridge.library_cover(book_id).startswith('data:image/png'))
        self.assertIn('error', self.bridge.reader_open('missing'))
        self.assertIn('error', self.bridge.reader_section(book_id, 99))
        self.assertEqual(self.bridge.library_remove(book_id), [])

    def test_open_external_only_allows_web_links(self):
        self.assertIn('error', self.bridge.open_external('file:///C:/Windows/notepad.exe'))
        with patch('bridge.webbrowser.open') as opened:
            self.assertTrue(self.bridge.open_external('https://example.com'))
        opened.assert_called_once_with('https://example.com')


if __name__ == '__main__':
    unittest.main()
