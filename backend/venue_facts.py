"""Display sourced venue ratings and hours after the recommendation prose."""
import re
from urllib.parse import urlsplit

from .locations import labelled_fields


def venue_facts(document):
    if len(document.metadata.get('places',[]))!=1:return None
    prefix='Email subject: '+document.metadata.get('email_subject','')+'\n'
    text='\n'.join(span['text'].removeprefix(prefix) for span in document.spans if not span.get('source_kind'))
    fields=labelled_fields(text)
    rating=None
    for record in document.metadata.get('venue_reviews',[]):
        if record.get('kind')!='rating' or not {'rating','scale','count','count_kind','url'} <= record.keys():continue
        host=urlsplit(record['url']).hostname or 'the review source'
        rating=f"Rating: {record['rating']:g}/{record['scale']:g} · {record['count']} {record['count_kind']} ({host})"
        break
    if not rating:
        value=next((value for label,value in fields if label in ('rating','ratings')),None)
        if value and len(value)<=200:rating='Rating: '+value
    hours=next((value for label,value in fields if label in ('hours','opening hours','business hours')),None)
    raw=document.metadata.get('email_text',text) if '#' not in document.url else text
    lines=raw.splitlines()
    for index,line in enumerate(lines):
        match=re.match(r'^\s*[-–—•]?\s*(?:hours|opening hours|business hours)\s*:\s*(.*)',line,re.I)
        if not match:continue
        parts=[match[1].strip()]
        for following in lines[index+1:index+8]:
            value=following.strip()
            if not value or not re.match(r'^(?:\d|mon\b|tue\b|wed\b|thu\b|fri\b|sat\b|sun\b|closed\b|open\b|daily\b)',value,re.I):break
            parts.append(value)
        hours=' '.join(parts);break
    if hours:hours=hours.strip() if len(hours)<=800 else None
    return {'rating':rating,'hours':hours} if rating or hours else None
