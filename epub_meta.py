"""Lightweight EPUB metadata reader: title, author and cover for the interface."""
from __future__ import annotations

import base64
import posixpath
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

COVER_LIMIT = 4_000_000
IMAGE_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
               '.webp': 'image/webp', '.svg': 'image/svg+xml'}


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _cover_href(package):
    manifest = [node.attrib for node in package.iter() if _local(node.tag) == 'item']
    for item in manifest:
        if 'cover-image' in item.get('properties', '').split():
            return item.get('href'), item.get('media-type')
    cover_id = next((node.attrib.get('content') for node in package.iter()
                     if _local(node.tag) == 'meta' and node.attrib.get('name') == 'cover'), None)
    for item in manifest:
        if cover_id and item.get('id') == cover_id:
            return item.get('href'), item.get('media-type')
    for item in manifest:
        if item.get('media-type', '').startswith('image/') and 'cover' in (item.get('id', '') + item.get('href', '')).lower():
            return item.get('href'), item.get('media-type')
    return None, None


def book_info(epub, with_cover=True):
    """Returns {'title', 'author', 'cover'}; cover is a data URI or None. Never raises for bad files."""
    epub = Path(epub)
    info = {'title': epub.stem, 'author': '', 'cover': None}
    try:
        with zipfile.ZipFile(epub) as archive:
            container = ET.fromstring(archive.read('META-INF/container.xml'))
            opf = next(node.attrib['full-path'] for node in container.iter() if _local(node.tag) == 'rootfile')
            package = ET.fromstring(archive.read(opf))
            meta = lambda key: next(((node.text or '').strip() for node in package.iter()
                                     if _local(node.tag) == key and (node.text or '').strip()), '')
            info['title'] = meta('title') or epub.stem
            info['author'] = meta('creator')
            if not with_cover:
                return info
            href, media_type = _cover_href(package)
            if href:
                member = posixpath.normpath(posixpath.join(posixpath.dirname(opf), href))
                entry = archive.getinfo(member)
                if entry.file_size <= COVER_LIMIT:
                    media_type = media_type or IMAGE_TYPES.get(Path(member).suffix.lower(), 'image/jpeg')
                    data = base64.b64encode(archive.read(member)).decode('ascii')
                    info['cover'] = f'data:{media_type};base64,{data}'
    except (OSError, KeyError, StopIteration, ET.ParseError, zipfile.BadZipFile):
        pass
    return info
