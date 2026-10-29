"""Preserve complete, short email recommendations without another drafting call."""
import re

from .documents import normalize
from .locations import email_places, labelled_address, place_prose


def preserve_short_email(document):
    if document.metadata.get('format') != 'email':
        return None
    blocks=[]
    evidence=[]
    for span in document.spans:
        text=span['text']
        prefix='Email subject: '+document.metadata.get('email_subject','')+'\n'
        if text.startswith(prefix):text=text[len(prefix):]
        section=span.get('section','')
        if section and text.startswith(section+'\n'):text=text[len(section)+1:]
        kept=[]
        for line in text.splitlines():
            line=normalize(line)
            if not line or re.fullmatch(r'(?:hi|hello|dear)\b[^.!?]{0,70}[,!]?',line,re.I):continue
            if re.fullmatch(r'(?:enjoy|cheers|best regards|thanks|thank you)[!.]?',line,re.I):continue
            if re.fullmatch(r'(?:(?:photos?|location|website|maps?)\s*:\s*)?https?://\S+',line,re.I):continue
            if re.search(r'unsubscribe|manage.?preferences|verification.?code|reset.?password',line,re.I):continue
            kept.append(line)
        if kept:
            blocks.append(' '.join(kept))
            evidence.append({'field':'summary','span_id':span['id'],'source':document.title,
                             'quote':span['text'],'url':document.url})
    paragraphs=[]
    for block in blocks:
        if paragraphs and not re.search(r'[.!?]$',paragraphs[-1]) and not re.match(r'(?:Where|Address|Location|Rating|Hours)\s*:',block,re.I):
            paragraphs[-1]+=' '+block
        else:paragraphs.append(block)
    body='\n\n'.join(paragraphs)
    # Long or incomplete editions still use story selection and grounded drafting.
    if not 25 <= len(body.split()) <= 160 or not re.search(r'[.!?]',body):return None
    title=document.title
    suffix=title.split(':',1)[-1].strip() if ':' in title else ''
    if suffix and suffix.casefold() in body.casefold():title=suffix
    if not 5 <= len(title) <= 130:return None
    places=list(document.metadata.get('places',[])) or email_places(document)
    if places:body=place_prose(body)
    from .editorial import PIPELINE_VERSION,source_category,source_details
    source,action=source_details(document,places=places)
    return {'title':title,'hook':'','finding':body,'limitations':'','original_url':document.url,
            'evidence':evidence,'generation':'source:preserved-email','verification':'source_preserved',
            'analysis':{'content_type':'places' if places else source_category(document),
                        'summary':body,'sections':[],'source':source,'action':action,
                        'pipeline_version':PIPELINE_VERSION,'content_mode':'source_preserved',
                        'verification':{'method':'exact_source_passages','independently_fact_checked':False},
                        'usage':{'calls':0,'input_tokens':0,'output_tokens':0,'estimated_cost_usd':0,'stages':[]},
                        'reading':{'core_words':len((title+' '+body).split()),'target_seconds':120}}}
