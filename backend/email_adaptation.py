"""Preserve complete, short email recommendations without another drafting call."""
import re

from .documents import normalize
from .locations import email_places, labelled_address, place_prose
from .venue_facts import venue_facts


def preserve_short_email(document):
    if document.metadata.get('format') != 'email':
        return None
    if document.metadata.get('venue_reviews'):
        # New review evidence needs a grounded draft and an independent check.
        return None
    blocks=[]
    evidence=[]
    venue = bool(document.metadata.get('places'))
    metadata_continuation = False
    for span in document.spans:
        text=span['text']
        prefix='Email subject: '+document.metadata.get('email_subject','')+'\n'
        if text.startswith(prefix):text=text[len(prefix):]
        section=span.get('section','')
        if section and text.startswith(section+'\n'):text=text[len(section)+1:]
        kept=[]
        for line in text.splitlines():
            line=normalize(line)
            if venue:
                if line.casefold() == document.title.casefold():continue
                plain=re.sub(r'^[-–—•]\s*','',line)
                if re.match(r'^(?:rating|address|postal address|where|location|website(?:\s*/\s*location)?|hours|phone|telephone)\s*:',plain,re.I):
                    metadata_continuation=True;continue
                if metadata_continuation and re.match(r'^(?:\d|mon\b|tue\b|wed\b|thu\b|fri\b|sat\b|sun\b)',plain,re.I):continue
                metadata_continuation=False
                if plain.endswith(':') and len(plain.split())<=10:continue
                if re.match(r"^(?:today.s pick|pick|recommendation)\s*:",plain,re.I) and re.search(r'\s[-–—•]\s*(?:rating|address|website|hours|phone)\s*:',plain,re.I):continue
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
    # Once a venue is identified, its name/address live in the title/actions;
    # a short, complete descriptive sentence should not need padding.
    minimum = 3 if any(place.get('name') and place.get('identity_known') is not False for place in document.metadata.get('places', [])) else 25
    if not minimum <= len(body.split()) <= 160 or not re.search(r'[.!?]',body):return None
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
                        'venue_facts':venue_facts(document),
                        'pipeline_version':PIPELINE_VERSION,'content_mode':'source_preserved',
                        'verification':{'method':'exact_source_passages','independently_fact_checked':False},
                        'usage':{'calls':0,'input_tokens':0,'output_tokens':0,'estimated_cost_usd':0,'stages':[]},
                        'reading':{'core_words':len((title+' '+body).split()),'target_seconds':120}}}
