"""Create a translated EPUB while retaining source metadata and binary assets."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import html
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile

from app_core import first_heading, read

OPF = 'http://www.idpf.org/2007/opf'
DC = 'http://purl.org/dc/elements/1.1/'
XHTML = 'http://www.w3.org/1999/xhtml'
ET.register_namespace('', OPF)
ET.register_namespace('dc', DC)


def local(tag):
    return tag.rsplit('}', 1)[-1]


def safe_member(name):
    value = posixpath.normpath(name).lstrip('/')
    if value == '..' or value.startswith('../'):
        raise ValueError('EPUB icinde gecersiz yol: ' + name)
    return value


def inline_markup(text, asset_map):
    escaped = html.escape(text, quote=False)

    def image(match):
        alt = html.escape(match.group(1), quote=True)
        source = match.group(2)
        if not source.startswith('epub-resource:'):
            return html.escape(match.group(0))
        original = safe_member(source[len('epub-resource:'):])
        href = asset_map.get(original)
        if not href:
            return f'<span class="missing-image">[{alt or "Resim"}]</span>'
        return f'<img src="../{html.escape(href, quote=True)}" alt="{alt}" />'

    escaped = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', image, escaped)
    escaped = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', escaped)
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
    escaped = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<em>\1</em>', escaped)
    return escaped


def without_first_heading(markdown):
    """Remove the importer-only TOC heading while preserving native headings."""
    return re.sub(r'\A\s*#{1,6}[^\n]*(?:\n[ \t]*)?\n?', '', markdown, count=1)


def markdown_xhtml(markdown, title, asset_map, hide_first_heading=False):
    if hide_first_heading:
        markdown = without_first_heading(markdown)
    blocks = re.split(r'\n\s*\n', markdown.strip())
    body = []
    for block in blocks:
        heading = re.match(r'^(#{1,6})\s+(.+)$', block.strip(), re.S)
        if heading and '\n' not in block.strip():
            level = len(heading.group(1))
            body.append(f'<h{level}>{inline_markup(heading.group(2), asset_map)}</h{level}>')
            continue
        if re.fullmatch(r'!\[[^\]]*\]\([^)]+\)', block.strip()):
            body.append('<div class="image">' + inline_markup(block.strip(), asset_map) + '</div>')
            continue
        lines = block.splitlines()
        if lines and all(line.startswith('- ') for line in lines):
            body.append('<ul>' + ''.join(f'<li>{inline_markup(line[2:], asset_map)}</li>' for line in lines) + '</ul>')
            continue
        body.append('<p>' + '<br/>'.join(inline_markup(line, asset_map) for line in lines) + '</p>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            f'<html xmlns="{XHTML}" lang="tr" xml:lang="tr"><head><title>{html.escape(title)}</title>'
            '<link rel="stylesheet" type="text/css" href="../style.css"/></head><body>'
            + ''.join(body) + '</body></html>')


def output_path(book, original):
    base = book / f'{original.stem} TR.epub'
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = book / f'{original.stem} TR-{index}.epub'
        if not candidate.exists():
            return candidate
        index += 1


def build_translated_epub(book, log=print):
    book = Path(book)
    marker = json.loads(read(book / 'epub-import.json'))
    source_epub = Path(marker['epub'])
    if not source_epub.is_file():
        raise ValueError('Kaynak EPUB bulunamadi: ' + str(source_epub))
    legacy_hidden_headings = marker.get('import_version') == 3 and marker.get('toc_mode') in ('EPUB3 nav', 'EPUB2 NCX')
    translated = []
    for index, filename in enumerate(marker['files'], 1):
        path = book / 'translation' / filename
        if not path.exists():
            raise ValueError('EPUB olusturulamadi; ceviri eksik: ' + filename)
        text = read(path)
        translated.append({
            'href': f'text/chapter-{index:04}.xhtml',
            'title': first_heading(text, Path(filename).stem),
            'text': text,
            'hide_heading': legacy_hidden_headings,
        })

    with zipfile.ZipFile(source_epub) as source:
        container = ET.fromstring(source.read('META-INF/container.xml'))
        opf_path = next(node.attrib['full-path'] for node in container.iter() if local(node.tag) == 'rootfile')
        package = ET.fromstring(source.read(opf_path))
        opf_dir = posixpath.dirname(opf_path)
        source_manifest = [node for node in package.iter() if local(node.tag) == 'item']
        document_types = {'application/xhtml+xml', 'text/html', 'application/x-dtbncx+xml'}
        asset_entries = []
        asset_map = {}
        for number, item in enumerate(source_manifest, 1):
            media = item.attrib.get('media-type', 'application/octet-stream')
            if media in document_types or 'nav' in item.attrib.get('properties', '').split():
                continue
            original = safe_member(posixpath.join(opf_dir, item.attrib['href']))
            if original not in source.namelist():
                continue
            target = 'assets/' + original
            properties = item.attrib.get('properties', '')
            asset_entries.append((f'asset-{number}', target, media, properties, original))
            asset_map[original] = target

        metadata_source = next(node for node in package if local(node.tag) == 'metadata')
        metadata = ET.Element(f'{{{OPF}}}metadata')
        has_language = False
        has_identifier = False
        source_titles = [node for node in metadata_source if node.tag == f'{{{DC}}}title']
        subtitle_ids = {
            node.attrib.get('refines', '').lstrip('#') for node in metadata_source
            if local(node.tag) == 'meta' and node.attrib.get('property') == 'title-type'
            and (node.text or '').strip().lower() == 'subtitle'
        }
        title_target_id = next((node.attrib.get('id') for node in reversed(source_titles)
                                if node.attrib.get('id') in subtitle_ids),
                               source_titles[0].attrib.get('id') if source_titles else None)
        title_updated = False
        for child in metadata_source:
            cloned = copy.deepcopy(child)
            if local(cloned.tag) == 'language':
                cloned.text = 'tr'
                has_language = True
            if local(cloned.tag) == 'identifier':
                has_identifier = True
            is_target_title = cloned.tag == f'{{{DC}}}title' and (
                (title_target_id and cloned.attrib.get('id') == title_target_id) or
                (not title_target_id and not title_updated)
            )
            if is_target_title:
                original_title = (cloned.text or '').strip()
                cloned.text = original_title + ('' if original_title.endswith(' TR') else ' TR')
                title_updated = True
            if local(cloned.tag) == 'meta' and cloned.attrib.get('property') == 'dcterms:modified':
                cloned.text = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            metadata.append(cloned)
        if not has_language:
            ET.SubElement(metadata, f'{{{DC}}}language').text = 'tr'
        unique_id = package.attrib.get('unique-identifier', 'book-id')
        identifiers = [node for node in metadata if local(node.tag) == 'identifier']
        if not has_identifier:
            identifier = ET.SubElement(metadata, f'{{{DC}}}identifier', {'id': 'book-id'})
            identifier.text = 'urn:uuid:' + str(uuid.uuid4())
            unique_id = 'book-id'
        elif not any(node.attrib.get('id') == unique_id for node in identifiers):
            identifiers[0].set('id', 'book-id')
            unique_id = 'book-id'

        new_package = ET.Element(f'{{{OPF}}}package', {'version': '3.0', 'unique-identifier': unique_id})
        new_package.append(metadata)
        manifest = ET.SubElement(new_package, f'{{{OPF}}}manifest')
        ET.SubElement(manifest, f'{{{OPF}}}item', {'id': 'nav', 'href': 'nav.xhtml', 'media-type': 'application/xhtml+xml', 'properties': 'nav'})
        ET.SubElement(manifest, f'{{{OPF}}}item', {'id': 'ncx', 'href': 'toc.ncx', 'media-type': 'application/x-dtbncx+xml'})
        ET.SubElement(manifest, f'{{{OPF}}}item', {'id': 'style', 'href': 'style.css', 'media-type': 'text/css'})
        spine = ET.SubElement(new_package, f'{{{OPF}}}spine', {'toc': 'ncx'})
        for index, chapter in enumerate(translated, 1):
            ET.SubElement(manifest, f'{{{OPF}}}item', {'id': f'chapter-{index}', 'href': chapter['href'], 'media-type': 'application/xhtml+xml'})
            ET.SubElement(spine, f'{{{OPF}}}itemref', {'idref': f'chapter-{index}'})
        for item_id, href, media, properties, _original in asset_entries:
            attrs = {'id': item_id, 'href': href, 'media-type': media}
            if properties:
                attrs['properties'] = properties
            ET.SubElement(manifest, f'{{{OPF}}}item', attrs)

        toc_chapters = translated
        navigation = '<?xml version="1.0" encoding="utf-8"?><html xmlns="%s" xmlns:epub="http://www.idpf.org/2007/ops" lang="tr"><head><title>İçindekiler</title></head><body><nav epub:type="toc" id="toc"><h1>İçindekiler</h1><ol>%s</ol></nav></body></html>' % (XHTML, ''.join(f'<li><a href="{chapter["href"]}">{html.escape(chapter["title"])}</a></li>' for chapter in toc_chapters))
        identifier_text = next((node.text for node in metadata if local(node.tag) == 'identifier' and node.text), 'urn:uuid:' + str(uuid.uuid4()))
        ncx = '<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="%s"/></head><docTitle><text>İçindekiler</text></docTitle><navMap>%s</navMap></ncx>' % (html.escape(identifier_text, quote=True), ''.join(f'<navPoint id="nav-{index}" playOrder="{index}"><navLabel><text>{html.escape(chapter["title"])}</text></navLabel><content src="{chapter["href"]}"/></navPoint>' for index, chapter in enumerate(toc_chapters, 1)))
        container_xml = '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
        style = 'body{font-family:serif;line-height:1.45;margin:5%;}h1,h2,h3{text-align:left;}img{max-width:100%;height:auto;}.image{text-align:center;}p{text-indent:1.2em;margin:.35em 0;}h1+p,h2+p,h3+p{ text-indent:0;}'
        destination = output_path(book, source_epub)
        temporary = destination.with_suffix('.epub.tmp')
        with zipfile.ZipFile(temporary, 'w') as output:
            output.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            output.writestr('META-INF/container.xml', container_xml)
            output.writestr('OEBPS/content.opf', ET.tostring(new_package, encoding='utf-8', xml_declaration=True))
            output.writestr('OEBPS/nav.xhtml', navigation)
            output.writestr('OEBPS/toc.ncx', ncx)
            output.writestr('OEBPS/style.css', style)
            for chapter in translated:
                output.writestr('OEBPS/' + chapter['href'], markdown_xhtml(
                    chapter['text'], chapter['title'], asset_map, chapter['hide_heading']))
            for _item_id, target, _media, _properties, original in asset_entries:
                output.writestr('OEBPS/' + target, source.read(original))
        temporary.replace(destination)
    log(f'EPUB metadata dili tr olarak ayarlandi; {len(asset_entries)} kaynak varlik kopyalandi.')
    return destination
