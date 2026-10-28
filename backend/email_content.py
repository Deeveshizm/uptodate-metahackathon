"""Private original-email documents and explicit location references."""
import copy
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from .documents import Document, make_spans, normalize, parse_document
from .retrieval import Resource


def map_link(url):
    parts=urlsplit(url)
    host=(parts.hostname or '').lower().removeprefix('www.')
    return host in ('maps.google.com','maps.app.goo.gl','maps.apple.com','openstreetmap.org') or (host=='google.com' and parts.path.startswith('/maps')) or (host=='goo.gl' and parts.path.startswith('/maps'))


def email_documents(payload):
    url='email:'+payload['fingerprint']
    if payload.get('html'):
        document=parse_document(Resource(url,'text/html',payload['html'].encode()))
        document.title=payload['subject']
    else:
        chunks=[];section='Email'
        for line in payload['text'].splitlines():
            text=normalize(line)
            if not text:continue
            if re.search(r'unsubscribe|manage.?preferences|reset.?password|verification.?code',text,re.I):continue
            if re.search(r'https?://\S+\.(?:jpg|jpeg|png|webp|gif)(?:[?\s]|$)',text,re.I):continue
            if len(text)<100 and (text.endswith(':') or re.match(r'^\d+[.)]\s+',text)):
                section=text.rstrip(':')
            chunks.append((text,section,None))
        document=Document(url,payload['subject'],make_spans(chunks),payload.get('links',[]))
    author=payload.get('source_name') or payload.get('sender','Email sender')
    if author==payload.get('sender','').rsplit('@',1)[-1]:author=payload.get('sender','Email sender')
    document.metadata.update(private=True,format='email',author=author,
                             email_text=payload['text'],email_subject=payload['subject'])
    for index, image_url in enumerate(payload.get('image_urls',[])[:8]):
        document.figures.append({'id':f'email-photo-{index}','url':image_url,'caption':'Photo linked in '+payload['subject'],
                                'license_url':'','page':None,'selection_basis':'email_photo_reference'})
    document.links=payload.get('links') or [{'url':u,'title':u} for u in payload.get('urls',[])]
    locations=[]
    for link in document.links:
        if not map_link(link['url']):continue
        query=parse_qs(urlsplit(link['url']).query)
        name=link.get('title','').strip()
        if not name or re.fullmatch(r'(?:open|view|get|see)?\s*(?:in|on)?\s*(?:google|apple)?\s*(?:maps?|directions|location)',name,re.I) or name.startswith('http'):
            name=(query.get('query') or query.get('q') or [''])[0]
        if name and not re.fullmatch(r'[-+\d.,\s]+',name):
            locations.append({'name':name[:200],'address':'','map_url':link['url'],'source_url':url})
    if locations:document.metadata['places']=locations[:12]
    document.metadata['location_links']=[link for link in document.links if map_link(link['url'])][:20]
    document.metadata['email_images_pending']=bool(payload.get('email_images_pending') or re.search(r'<img[^>]+src=[\"\']cid:',payload.get('html',''),re.I)) and not document.figures
    groups={}
    for span in document.spans:
        groups.setdefault(span['section'],[]).append(span)
    substantial={name:spans for name,spans in groups.items() if name not in ('Body','Email',payload['subject']) and not re.search(r'^(?:sources?|references?|footer|disclaimer|introduction|overview)$',name,re.I)
                 and sum(len(s['text']) for s in spans)>=200 and sum(len(s['text'].split()) for s in spans)>=30}
    originals=[document]
    if len(substantial)>1:
        originals=[]
        for name,spans in substantial.items():
            child=Document(url+'#'+hashlib.sha256(name.encode()).hexdigest()[:12],name,copy.deepcopy(spans),copy.deepcopy(document.links),copy.deepcopy(document.metadata),
                           [f for f in document.figures if f.get('section')==name])
            child.metadata['places']=[place for place in locations if normalize(place['name']).casefold() in normalize(name+' '+' '.join(s['text'] for s in spans)).casefold()]
            originals.append(child)
    for item in originals:
        for span in item.spans:
            if span['section'] not in ('Body','Email') and span['section'] not in span['text']:
                span['text']=span['section']+'\n'+span['text']
            span['text']='Email subject: '+payload['subject']+'\n'+span['text']
        item.metadata['source_content_hash']=hashlib.sha256('\n'.join(s['text'] for s in item.spans).encode()).hexdigest()
        item.metadata['_figures']=[{key:value for key,value in figure.items() if not isinstance(value,bytes)} for figure in item.figures if not any(isinstance(value,bytes) for value in figure.values())]
        item.metadata['_links']=item.links
    return document,originals


def use_email_body(document):
    if not document.links:return True
    if any(urlsplit(link['url']).hostname in ('arxiv.org','doi.org') for link in document.links):return False
    if document.metadata.get('location_links'):return True
    eligible=[link for link in document.links if not map_link(link['url']) and urlsplit(link['url']).path.rstrip('/')]
    return not eligible
