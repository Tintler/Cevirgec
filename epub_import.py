"""EPUB spine extraction with stdlib. No scripts, network or ZIP extraction."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import zipfile


class Markdown(HTMLParser):
    def __init__(self, document_path):
        super().__init__(convert_charrefs=True)
        self.out=[]; self.skip=0; self.pre=False; self.anchors={}
        self.document_path=document_path; self.links=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag in ('script','style','head'):
            self.skip+=1; return
        if self.skip:return
        for key in ('id', 'name'):
            if attrs.get(key):self.anchors[attrs[key]]=len(''.join(self.out))
        if tag in ('p','div','section','article','blockquote','ul','ol','table','tr'):
            self.out.append('\n\n')
        elif re.fullmatch('h[1-6]',tag):self.out.append('\n\n'+'#'*int(tag[1])+' ')
        elif tag=='br':self.out.append('\n')
        elif tag in ('em','i'):self.out.append('*')
        elif tag in ('strong','b'):self.out.append('**')
        elif tag=='li':self.out.append('\n- ')
        elif tag=='img' and attrs.get('src'):
            resource,_=target(self.document_path,attrs['src'])
            self.out.append('\n\n!['+attrs.get('alt','')+'](epub-resource:'+resource+')\n\n')
        elif tag=='a' and attrs.get('href'):
            self.out.append('['); self.links.append(attrs['href'])
        elif tag in ('td','th'):self.out.append(' | ')
        elif tag=='pre':self.pre=True;self.out.append('\n\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','head'):
            self.skip=max(0,self.skip-1);return
        if self.skip:return
        if tag in ('em','i'):self.out.append('*')
        elif tag in ('strong','b'):self.out.append('**')
        elif tag in ('p','div','section','article','blockquote','table','tr') or re.fullmatch('h[1-6]',tag):self.out.append('\n\n')
        elif tag=='pre':self.pre=False;self.out.append('\n\n')
        elif tag=='a' and self.links:self.out.append(']('+self.links.pop()+')')
    def handle_data(self,data):
        if not self.skip:self.out.append(data if self.pre else re.sub(r'\s+',' ',data))
    def result(self):
        text=''.join(self.out)
        text=re.sub(r' *\n *','\n',text)
        return re.sub(r'\n{3,}','\n\n',text).strip()+'\n'


def local(tag):
    return tag.rsplit('}',1)[-1]


def target(basefile, href):
    from urllib.parse import urlsplit
    url=urlsplit(href)
    if url.scheme or url.netloc:raise ValueError('EPUB TOC dis baglanti iceriyor: '+href)
    path=posixpath.normpath(posixpath.join(posixpath.dirname(basefile),unquote(url.path))) if url.path else basefile
    if path.startswith('../') or path.startswith('/'):raise ValueError('Gecersiz EPUB yolu.')
    return path,unquote(url.fragment)


def toc_entries(z, opf, manifest, package):
    nav=next((item for item in manifest.values() if 'nav' in item.get('properties','').split()),None)
    entries=[]
    if nav:
        navpath,_=target(opf,nav['href'])
        root=ET.fromstring(z.read(navpath))
        toc=next((n for n in root.iter() if local(n.tag)=='nav' and
            ('toc' in next((v for k,v in n.attrib.items() if local(k)=='type'),'').split() or n.attrib.get('role')=='doc-toc')),None)
        if toc is not None:
            def walk(node, parents):
                if local(node.tag)=='li':
                    link=next((c for c in node if local(c.tag) in ('a','span')),None)
                    label=' '.join(''.join(link.itertext()).split()) if link is not None else ''
                    labels=parents+([label] if label else [])
                    if link is not None and link.attrib.get('href'):
                        path,anchor=target(navpath,link.attrib['href'])
                        entries.append((path,anchor,' — '.join(labels)))
                    for child in node:
                        if local(child.tag) not in ('a','span'):walk(child,labels)
                else:
                    for child in node:walk(child,parents)
            walk(toc,[])
    if entries:return entries,'EPUB3 nav'
    spine=next((n for n in package.iter() if local(n.tag)=='spine'),None)
    ncx=manifest.get(spine.attrib.get('toc','')) if spine is not None else None
    if not ncx:ncx=next((i for i in manifest.values() if i.get('media-type')=='application/x-dtbncx+xml'),None)
    if ncx:
        ncxpath,_=target(opf,ncx['href']);root=ET.fromstring(z.read(ncxpath))
        def walk_ncx(node,parents):
            if local(node.tag)=='navPoint':
                labelnode=next((c for c in node if local(c.tag)=='navLabel'),None)
                label=' '.join(''.join(labelnode.itertext()).split()) if labelnode is not None else ''
                labels=parents+([label] if label else [])
                content=next((c for c in node if local(c.tag)=='content'),None)
                if content is not None and content.attrib.get('src'):
                    path,anchor=target(ncxpath,content.attrib['src']);entries.append((path,anchor,' — '.join(labels)))
                for child in node:
                    if local(child.tag)=='navPoint':walk_ncx(child,labels)
            else:
                for child in node:walk_ncx(child,parents)
        walk_ncx(root,[])
    return entries,'EPUB2 NCX' if entries else 'Spine fallback (TOC yok)'


def chapter_files(z,opf,manifest,spine,package):
    entries,mode=toc_entries(z,opf,manifest,package)
    documents={};full='';starts=[]
    for key in spine:
        item=manifest[key]
        if item.get('media-type') not in ('application/xhtml+xml','text/html'):continue
        path,_=target(opf,item['href'])
        if path in documents:raise ValueError('Spine icinde ayni HTML iki kez var: '+path)
        parser=Markdown(path);parser.feed(z.read(path).decode('utf-8-sig'))
        raw=''.join(parser.out)
        documents[path]=(len(full),parser.anchors)
        starts.append((len(full),PurePosixPath(path).stem))
        full+=raw+'\n\n'
    boundaries={}
    for path,anchor,label in entries:
        if path not in documents:raise ValueError('TOC hedefi spine icinde yok: '+path)
        offset,anchors=documents[path]
        if anchor:
            if anchor not in anchors:raise ValueError('TOC bolum isareti bulunamadi: '+path+'#'+anchor)
            offset+=anchors[anchor]
        boundaries[offset]=label
    if not boundaries:
        boundaries=dict(starts)
    elif min(boundaries)>0:
        first=min(boundaries)
        for offset,label in starts:
            if offset<first:boundaries[offset]=label
    if full[:min(boundaries,default=0)].strip():boundaries[0]='Front matter'
    result=[]; ordered=sorted(boundaries.items())
    for index,(offset,label) in enumerate(ordered):
        end=ordered[index+1][0] if index+1<len(ordered) else len(full)
        text=full[offset:end]
        text=re.sub(r' *\n *','\n',text)
        text=re.sub(r'\n{3,}','\n\n',text).strip()
        text=re.sub(r'^#{1,6}\s*$', '', text, flags=re.M).strip()
        if not text:continue
        if entries:text='# '+label+'\n\n'+text
        import unicodedata
        slug=unicodedata.normalize('NFKD',label).encode('ascii','ignore').decode()
        slug=re.sub(r'[^A-Za-z0-9]+','-',slug).strip('-')[:120] or 'Section'
        name=f'{len(result)+1:03d}-{slug}.md'
        result.append((name,text+'\n'))
    return result,mode


def prepare(epub, workspace_parent=None):
    epub=Path(epub).resolve()
    raw=epub.read_bytes(); fingerprint=hashlib.sha256(raw).hexdigest()
    parent=Path(workspace_parent).resolve() if workspace_parent else epub.parent
    parent.mkdir(parents=True,exist_ok=True)
    base_book=parent/(epub.stem+'-ceviri')
    book=base_book
    marker=book/'epub-import.json'
    if marker.exists() and json.loads(marker.read_text(encoding='utf-8')).get('import_version') != 3:
        print('Eski parca duzeni korunuyor. Bolum duzeni icin ayri klasor olusturulacak.')
        book=parent/(epub.stem+'-ceviri-bolumler')
        marker=book/'epub-import.json'
    if marker.exists():
        state=json.loads(marker.read_text(encoding='utf-8'))
        if state.get('sha256')==fingerprint and state.get('import_version')==3:return book
    if book.exists():
        index=2
        while (parent/(epub.stem+f'-ceviri-{index}')).exists():index+=1
        book=parent/(epub.stem+f'-ceviri-{index}')
        marker=book/'epub-import.json'
    with zipfile.ZipFile(epub) as z:
        if any(i.file_size>50_000_000 for i in z.infolist()):raise ValueError('EPUB icinde 50 MB ustu oge var.')
        if 'META-INF/encryption.xml' in z.namelist():
            enc=ET.fromstring(z.read('META-INF/encryption.xml'))
            methods=[n.attrib.get('Algorithm','') for n in enc.iter() if n.tag.endswith('EncryptionMethod')]
            if any(a not in ('http://www.idpf.org/2008/embedding','http://ns.adobe.com/pdf/enc#RC') for a in methods):
                raise ValueError('Sifreli/DRM EPUB desteklenmiyor.')
        root=ET.fromstring(z.read('META-INF/container.xml'))
        opf=next(n.attrib['full-path'] for n in root.iter() if n.tag.endswith('rootfile'))
        package=ET.fromstring(z.read(opf)); base=posixpath.dirname(opf)
        manifest={n.attrib['id']:n.attrib for n in package.iter() if n.tag.endswith('}item')}
        spine=[n.attrib['idref'] for n in package.iter() if n.tag.endswith('}itemref')]
        meta=lambda key:next((n.text or '' for n in package.iter() if n.tag.endswith('}'+key)), '')
        title=meta('title') or epub.stem; author=meta('creator')
        chapters,mode=chapter_files(z,opf,manifest,spine,package)
        print('Bolum kaynagi: '+mode,flush=True)
        if not chapters:raise ValueError('EPUB icinde okunabilir metin bulunamadi; taranmis kitap/OCR desteklenmiyor.')
    book.mkdir();(book/'source').mkdir();(book/'translation').mkdir()
    for name,text in chapters:(book/'source'/name).write_text(text,encoding='utf-8')
    context=f'''# 00-CONTEXT
## Metadata
- **Title:** {title}
- **Author:** {author}
- **Direction:** English → Turkish
## Workflow State
- **Style approved:** yes
- **Last completed file:** none
## Files
| File | Status |
|---|---|
'''
    context+='\n'.join(f'| `{n}` | {"next" if i==0 else ""} |' for i,(n,_) in enumerate(chapters))
    context+='''
## Tone and Style
- Follow the source narrator, tense, register and tone; use natural literary Turkish.
- Preserve profanity, ambiguity and deliberate repetition. No unit conversion.
- Address form: infer from the source relationship, then keep it consistent.
- Automatic initial defaults for unattended translation; not a recorded human style review.
## Proper Nouns
Preserve original names unless a conventional Turkish form is clearly established.
## Glossary
No initial glossary. Follow decisions recorded in Translation notes.
## Recurring Phrases
Preserve consistency.
## Section Summaries
'''
    (book/'00-CONTEXT.md').write_text(context,encoding='utf-8')
    marker.write_text(json.dumps({'import_version':3,'toc_mode':mode,'sha256':fingerprint,'epub':str(epub),'files':[n for n,t in chapters]},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'EPUB ayrildi: {len(chapters)} metin dosyasi. Klasor: {book}',flush=True)
    return book
