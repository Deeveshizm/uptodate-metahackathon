"""Private original-email documents and explicit location references."""
import copy
import hashlib
import re
from urllib.parse import urlsplit

from .documents import Document, make_spans, normalize, parse_document
from .retrieval import Resource
from .locations import email_places, map_link, labelled_fields, NAME_LABELS


def single_venue_sections(document, sections, places):
    """Supporting headings in one recommendation are not separate stories."""
    if len(places) != 1 or places[0].get('identity_known') is False:return False
    name=normalize(places[0].get('name','')).casefold()
    if not name or not places[0].get('address'):return False
    text='\n'.join(span['text'] for span in document.spans)
    fields=labelled_fields(text)
    # Repeated contact blocks or named venues indicate an actual list/digest.
    addresses={normalize(value).casefold() for label,value in fields if label in ('address','where','location')}
    names={normalize(value).casefold() for label,value in fields if label in NAME_LABELS}
    if len(addresses)>1 or len(names)>1:return False
    for heading in sections:
        value=normalize(heading).casefold()
        if name in value:continue
        if re.fullmatch(r'(?:why (?:it|this|we)\b.*|(?:good morning[!,. ]*)?here is today.s\b.*|description|about(?: the (?:place|venue|recommendation))?|what to (?:try|expect)|(?:opening )?hours|contact(?: details)?|rating|reviews?|highlights?|details|the pick)',value):continue
        return False
    return True


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
                             email_text=payload['text'],email_subject=payload['subject'],email_sender=payload.get('sender',''))
    for index, image_url in enumerate(payload.get('image_urls',[])[:8]):
        document.figures.append({'id':f'email-photo-{index}','url':image_url,'caption':'Photo linked in '+payload['subject'],
                                'license_url':'','page':None,'selection_basis':'email_photo_reference'})
    document.links=payload.get('links') or [{'url':u,'title':u} for u in payload.get('urls',[])]
    locations=email_places(document)
    if locations:document.metadata['places']=locations[:12]
    document.metadata['location_links']=[link for link in document.links if map_link(link['url'])][:20]
    document.metadata['email_images_pending']=bool(payload.get('email_images_pending') or re.search(r'<img[^>]+src=[\"\']cid:',payload.get('html',''),re.I)) and not document.figures
    groups={}
    for span in document.spans:
        groups.setdefault(span['section'],[]).append(span)
    substantial={name:spans for name,spans in groups.items() if name not in ('Body','Email',payload['subject']) and not re.search(r'^(?:sources?|references?|footer|disclaimer|introduction|overview)$',name,re.I)
                 and sum(len(s['text']) for s in spans)>=200 and sum(len(s['text'].split()) for s in spans)>=30}
    originals=[document]
    if single_venue_sections(document,substantial,locations):
        document.metadata['story_grouping']='single_venue_recommendation'
    if len(substantial)>1 and document.metadata.get('story_grouping')!='single_venue_recommendation':
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
    # A venue's website supplies contact details/photos, not a replacement for
    # a recommendation already written in the email. This includes deep URLs.
    if document.metadata.get('places') or document.metadata.get('location_links'):return True
    if not document.links:return True
    if any(urlsplit(link['url']).hostname in ('arxiv.org','doi.org') for link in document.links):return False
    eligible=[link for link in document.links if not map_link(link['url']) and urlsplit(link['url']).path.rstrip('/')]
    return not eligible
