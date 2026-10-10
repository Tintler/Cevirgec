"""Reading tab back end: a personal EPUB library and a sanitising chapter renderer.

The library is independent of translation projects. It stores references to the
user's EPUB files (library.json next to config.json) with reading position and
bookmarks. Chapters are rendered to a small, allow-listed HTML subset so the
interface can show them with its own typography; scripts, styles and external
resources are never passed through.
"""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import posixpath
import re
import threading
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from app_core import atomic, read
from epub_import import decode_document, local, target, toc_entries
from epub_meta import book_info

LIBRARY_FILE = 'library.json'
IMAGE_LIMIT = 8_000_000
DOCUMENT_TYPES = ('application/xhtml+xml', 'text/html')
IMAGE_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
               '.webp': 'image/webp', '.svg': 'image/svg+xml', '.bmp': 'image/bmp'}

# Allowed elements; anything else is unwrapped (its text kept), DROP elements lose their content too.
ALLOWED = {
    'p', 'div', 'section', 'article', 'aside', 'header', 'footer', 'main', 'nav', 'blockquote', 'pre', 'code',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'em', 'strong', 'i', 'b', 'u', 's', 'small', 'sub', 'sup', 'span',
    'br', 'hr', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th',
    'caption', 'figure', 'figcaption', 'img', 'a', 'abbr', 'cite', 'q', 'mark', 'del', 'ins', 'ruby', 'rt',
}
VOID = {'br', 'hr', 'img'}
DROP = {'script', 'style', 'head', 'title', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'select',
        'textarea', 'audio', 'video', 'canvas', 'noscript', 'template', 'math', 'link', 'meta'}
ALIGN = re.compile(r'text-align\s*:\s*(center|right)', re.I)


def _book_id(path):
    return hashlib.sha1(str(Path(path).resolve()).casefold().encode('utf-8')).hexdigest()[:16]


class EpubBook:
    """Spine, table of contents and resources of one EPUB file."""

    def __init__(self, path):
        self.path = Path(path)
        with zipfile.ZipFile(self.path) as archive:
            names = set(archive.namelist())
            if 'META-INF/encryption.xml' in names:
                encryption = ET.fromstring(archive.read('META-INF/encryption.xml'))
                methods = [node.attrib.get('Algorithm', '') for node in encryption.iter() if local(node.tag) == 'EncryptionMethod']
                if any(method != 'http://www.idpf.org/2008/embedding' for method in methods):
                    raise ValueError('Şifreli/DRM korumalı EPUB okunamaz.')
            container = ET.fromstring(archive.read('META-INF/container.xml'))
            self.opf = next(node.attrib['full-path'] for node in container.iter() if local(node.tag) == 'rootfile')
            package = ET.fromstring(archive.read(self.opf))
            manifest = {node.attrib['id']: node.attrib for node in package.iter() if local(node.tag) == 'item'}
            self.language = next(((node.text or '').strip().lower() for node in package.iter()
                                  if local(node.tag) == 'language' and (node.text or '').strip()), '')
            self.media = {}
            for item in manifest.values():
                try:
                    path, _ = target(self.opf, item['href'])
                except ValueError:
                    continue
                self.media[path] = item.get('media-type', '')
            self.sections = []
            for node in package.iter():
                if local(node.tag) != 'itemref' or node.attrib.get('idref') not in manifest:
                    continue
                item = manifest[node.attrib['idref']]
                if item.get('media-type') not in DOCUMENT_TYPES:
                    continue
                path, _ = target(self.opf, item['href'])
                if path in names and path not in (s['path'] for s in self.sections):
                    self.sections.append({'path': path, 'size': archive.getinfo(path).file_size})
            if not self.sections:
                raise ValueError('EPUB içinde okunabilir bölüm bulunamadı.')
            self.index = {section['path']: number for number, section in enumerate(self.sections)}
            try:
                entries, _mode = toc_entries(archive, self.opf, manifest, package)
            except (ValueError, KeyError, ET.ParseError):
                entries = []
        self.toc = []
        for path, anchor, label in entries:
            if path in self.index:
                parts = [part for part in label.split(' — ') if part]
                self.toc.append({'section': self.index[path], 'anchor': ('r-' + anchor) if anchor else '',
                                 'title': parts[-1] if parts else label, 'depth': max(0, len(parts) - 1)})
        if not self.toc:
            self.toc = [{'section': number, 'anchor': '', 'title': f'Bölüm {number + 1}', 'depth': 0}
                        for number in range(len(self.sections))]

    def section_html(self, number):
        if not 0 <= number < len(self.sections):
            raise ValueError('Bölüm bulunamadı.')
        path = self.sections[number]['path']
        with zipfile.ZipFile(self.path) as archive:
            renderer = _Renderer(self, archive, path)
            renderer.feed(decode_document(archive.read(path), path))
            renderer.close()
        return renderer.html()

    def section_title(self, number):
        title = ''
        for entry in self.toc:
            if entry['section'] == number and not title:
                title = entry['title']
            if entry['section'] > number:
                break
        if not title:
            previous = [entry for entry in self.toc if entry['section'] < number]
            title = previous[-1]['title'] if previous else ''
        return title


class _Renderer(HTMLParser):
    def __init__(self, book, archive, path):
        super().__init__(convert_charrefs=True)
        self.book, self.archive, self.path = book, archive, path
        self.out, self.stack = [], []
        self.drop = 0
        self.in_body = False
        self.seen_body = False

    def html(self):
        return ''.join(self.out).strip()

    def _attrs(self, tag, attrs):
        values = dict(attrs)
        result = []
        element_id = values.get('id') or (values.get('name') if tag == 'a' else None)
        if element_id:
            result.append(('id', 'r-' + element_id))
        align = ALIGN.search(values.get('style', '') or '')
        if align:
            result.append(('class', 'align-' + align.group(1).lower()))
        epub_type = next((value for key, value in attrs if key.endswith('type') and ':' in key), '') or ''
        if 'footnote' in epub_type or 'endnote' in epub_type or 'note' in epub_type.split():
            result.append(('data-note', '1'))
        if tag in ('td', 'th'):
            for key in ('colspan', 'rowspan'):
                if str(values.get(key, '')).isdigit():
                    result.append((key, values[key]))
        if tag == 'a':
            href = values.get('href') or ''
            if re.match(r'^(?:https?:|mailto:)', href, re.I):
                result.append(('data-ext', href))
            elif href:
                try:
                    path, fragment = target(self.path, href)
                except ValueError:
                    path, fragment = None, ''
                if path in self.book.index:
                    result.append(('data-section', str(self.book.index[path])))
                    if fragment:
                        result.append(('data-anchor', 'r-' + fragment))
        if tag == 'img':
            source = self._image(values.get('src') or values.get('href') or values.get('xlink:href') or '')
            if not source:
                return None
            result.append(('src', source))
            result.append(('alt', values.get('alt', '') or ''))
        return result

    def _image(self, href):
        if not href or re.match(r'^[a-z]+:', href, re.I):
            return ''
        try:
            path, _ = target(self.path, href)
            info = self.archive.getinfo(path)
        except (ValueError, KeyError):
            return ''
        if info.file_size > IMAGE_LIMIT:
            return ''
        media = self.book.media.get(path) or IMAGE_TYPES.get(Path(path).suffix.lower(), '')
        if not media.startswith('image/'):
            return ''
        return f'data:{media};base64,' + base64.b64encode(self.archive.read(path)).decode('ascii')

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == 'body':
            self.in_body = self.seen_body = True
            return
        if self.drop or tag in DROP:
            if tag in DROP and tag not in VOID:
                self.drop += 1
            return
        if tag == 'image':  # <svg><image xlink:href=...> covers
            tag = 'img'
        if tag not in ALLOWED:
            return
        attributes = self._attrs(tag, attrs)
        if attributes is None:
            return
        rendered = ''.join(f' {key}="{escape(value, quote=True)}"' for key, value in attributes)
        if tag in VOID:
            self.out.append(f'<{tag}{rendered}>')
        else:
            self.out.append(f'<{tag}{rendered}>')
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in VOID or tag == 'image':
            self.handle_starttag(tag, attrs)
        elif tag in ALLOWED and not self.drop:
            # Empty element such as <a id="x"/>: keep anchors, drop the rest.
            attributes = self._attrs(tag, attrs) or []
            if any(key == 'id' for key, _ in attributes):
                rendered = ''.join(f' {key}="{escape(value, quote=True)}"' for key, value in attributes)
                self.out.append(f'<span{rendered}></span>')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'body':
            self.in_body = False
            return
        if tag in DROP and tag not in VOID:
            self.drop = max(0, self.drop - 1)
            return
        if self.drop or tag not in ALLOWED or tag in VOID:
            return
        if tag in self.stack:
            # Close anything left open inside it so the output stays well formed.
            while self.stack:
                open_tag = self.stack.pop()
                self.out.append(f'</{open_tag}>')
                if open_tag == tag:
                    break

    def handle_data(self, data):
        if self.drop or (self.seen_body and not self.in_body):
            return
        self.out.append(escape(data, quote=False))

    def close(self):
        super().close()
        while self.stack:
            self.out.append(f'</{self.stack.pop()}>')


class Library:
    """library.json: the user's books with position, progress and bookmarks."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self._lock = threading.RLock()
        self._cache = {}

    @property
    def path(self):
        return self.directory / LIBRARY_FILE

    def _load(self):
        try:
            data = json.loads(read(self.path)) if self.path.exists() else {}
        except (OSError, ValueError):
            data = {}
        data.setdefault('books', [])
        return data

    def _save(self, data):
        atomic(self.path, json.dumps(data, ensure_ascii=False, indent=2))

    def _find(self, data, book_id):
        book = next((item for item in data['books'] if item['id'] == book_id), None)
        if book is None:
            raise ValueError('Kitap kitaplıkta bulunamadı.')
        return book

    @staticmethod
    def _public(book):
        result = {key: book.get(key) for key in ('id', 'path', 'title', 'author', 'added', 'opened', 'percent', 'position')}
        result['exists'] = Path(book['path']).exists()
        result['bookmarks'] = len(book.get('bookmarks', []))
        return result

    def list(self):
        with self._lock:
            books = self._load()['books']
        books.sort(key=lambda item: item.get('opened') or item.get('added') or '', reverse=True)
        return [self._public(book) for book in books]

    def add(self, paths):
        added, errors = [], []
        with self._lock:
            data = self._load()
            known = {book['id'] for book in data['books']}
            for raw in paths:
                path = Path(raw)
                if path.suffix.lower() != '.epub' or not path.is_file():
                    errors.append(f'{path.name}: EPUB dosyası değil.')
                    continue
                book_id = _book_id(path)
                if book_id in known:
                    added.append(book_id)
                    continue
                try:
                    EpubBook(path)
                except Exception as error:
                    errors.append(f'{path.name}: {error}')
                    continue
                info = book_info(path, with_cover=False)
                data['books'].append({'id': book_id, 'path': str(path.resolve()), 'title': info['title'],
                                      'author': info['author'], 'added': datetime.now().isoformat(timespec='seconds'),
                                      'opened': None, 'percent': 0, 'position': None, 'bookmarks': []})
                known.add(book_id)
                added.append(book_id)
            self._save(data)
        return {'added': added, 'errors': errors}

    def remove(self, book_id):
        with self._lock:
            data = self._load()
            data['books'] = [book for book in data['books'] if book['id'] != book_id]
            self._save(data)
            self._cache.pop(book_id, None)

    def cover(self, book_id):
        with self._lock:
            book = self._find(self._load(), book_id)
        return book_info(book['path'])['cover'] if Path(book['path']).exists() else None

    def _book(self, book_id):
        with self._lock:
            entry = self._find(self._load(), book_id)
            cached = self._cache.get(book_id)
            if cached is None or cached.path != Path(entry['path']):
                if not Path(entry['path']).exists():
                    raise ValueError('Kitap dosyası bulunamadı: ' + entry['path'])
                cached = self._cache[book_id] = EpubBook(entry['path'])
            return entry, cached

    def open(self, book_id):
        entry, book = self._book(book_id)
        with self._lock:
            data = self._load()
            stored = self._find(data, book_id)
            stored['opened'] = datetime.now().isoformat(timespec='seconds')
            self._save(data)
        return {'id': book_id, 'title': entry['title'], 'author': entry['author'], 'toc': book.toc,
                'language': book.language,
                'sections': [{'size': section['size'], 'title': book.section_title(number)}
                             for number, section in enumerate(book.sections)],
                'position': entry.get('position') or {'section': 0, 'offset': 0},
                'percent': entry.get('percent', 0), 'bookmarks': entry.get('bookmarks', [])}

    def section(self, book_id, number):
        _entry, book = self._book(book_id)
        return {'section': number, 'html': book.section_html(int(number)), 'title': book.section_title(int(number))}

    def save_position(self, book_id, section, offset):
        _entry, book = self._book(book_id)
        section = max(0, min(int(section), len(book.sections) - 1))
        offset = max(0.0, min(float(offset), 1.0))
        sizes = [item['size'] for item in book.sections]
        total = sum(sizes) or 1
        percent = round((sum(sizes[:section]) + sizes[section] * offset) * 100 / total, 1)
        if section == len(sizes) - 1 and offset >= 0.98:
            percent = 100
        with self._lock:
            data = self._load()
            stored = self._find(data, book_id)
            stored['position'] = {'section': section, 'offset': round(offset, 4)}
            stored['percent'] = percent
            self._save(data)
        return percent

    def add_bookmark(self, book_id, section, offset, label):
        bookmark = {'id': hashlib.sha1(f'{book_id}{section}{offset}{datetime.now()}'.encode()).hexdigest()[:10],
                    'section': int(section), 'offset': round(float(offset), 4), 'label': str(label or '')[:160],
                    'created': datetime.now().isoformat(timespec='seconds')}
        with self._lock:
            data = self._load()
            stored = self._find(data, book_id)
            stored.setdefault('bookmarks', []).append(bookmark)
            stored['bookmarks'].sort(key=lambda item: (item['section'], item['offset']))
            self._save(data)
            return stored['bookmarks']

    def remove_bookmark(self, book_id, bookmark_id):
        with self._lock:
            data = self._load()
            stored = self._find(data, book_id)
            stored['bookmarks'] = [item for item in stored.get('bookmarks', []) if item['id'] != bookmark_id]
            self._save(data)
            return stored['bookmarks']
