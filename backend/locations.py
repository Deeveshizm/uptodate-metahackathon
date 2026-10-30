"""Source-only venue fields and map references; no invented geocoding."""
import re
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit

from .documents import normalize
from .pipeline import safe_url

GENERIC = re.compile(r"(?:(?:open|view|get|see|show|find|click)\s+)?(?:(?:in|on|to|the)\s+)?(?:(?:google|apple)\s+)?(?:maps?|directions|location|here|website)",re.I)
NAME_LABELS={"pick","today's pick","today’s pick","recommendation","recommended venue","venue","place","restaurant","cafe","café","coffee shop","name"}


def venue_name(value):
    value=normalize(value)
    alias=re.fullmatch(r'(.+?)\s+\(([^()]+)\)',value)
    if alias:
        primary=normalize(alias[1])
        if re.search(r'(?<!\w)'+re.escape(primary)+r'(?!\w)',normalize(alias[2]),re.I):
            return primary
    return value


def generic_map_label(value):
    return bool(GENERIC.fullmatch(value) or re.match(
        r'^(?:(?:open|view|see|get)\s+)?(?:(?:google|apple)\s+)?maps?\s+(?:for|to|of|at)\b',value,re.I))


def labelled_fields(text):
    # Match each field's starting boundary independently so adjacent inline
    # fields are not swallowed by the previous value.
    starts=list(re.finditer(r"(?:(?:^|\n)[ \t]*(?:[-–—•|][ \t]+)?|(?<=[.!?])[ \t]+|[ \t]+[-–—•|][ \t]+)([A-Za-z][A-Za-z /'’]{0,40})\s*:[ \t]*",text))
    result=[]
    for i,match in enumerate(starts):
        newline=text.find('\n',match.end())
        end=starts[i+1].start() if i+1<len(starts) else len(text)
        if newline>=0:end=min(end,newline)
        value=normalize(text[match.end():end]).strip()
        if value:result.append((match[1].casefold(),value))
    return result


def labelled_address(text):
    addresses=[value for label,value in labelled_fields(text) if label in ('where','address','location') and not value.lower().startswith(('http:','https:'))]
    return addresses[0] if len(addresses)==1 and len(addresses[0])<=400 else None


def text_address(text):
    labelled=labelled_address(text)
    if labelled:return labelled
    street=r"\d{1,6}\s+(?:[\w.'’/-]+\s+){1,8}(?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Parkway|Pkwy)\b\.?"
    matches=list(re.finditer(street+r"(?:\s*(?:#|Suite|Ste|Unit)\s*[\w-]+)?(?:,\s*[A-Za-z][A-Za-z .'-]{1,45})?(?:,\s*[A-Z]{2}\s*\d{5}(?:-\d{4})?)?",text,re.I))
    if len(matches)!=1:return None
    return re.split(r'[.!?]\s+(?=[A-Z])',matches[0][0],maxsplit=1)[0].strip()


def map_link(url):
    if not safe_url(url):return False
    parts=urlsplit(url);host=(parts.hostname or '').lower().removeprefix('www.')
    return host in ('maps.google.com','maps.app.goo.gl','maps.apple.com','openstreetmap.org') or (host=='google.com' and parts.path.startswith('/maps')) or (host=='goo.gl' and parts.path.startswith('/maps'))


def maps_search(name,address):
    return 'https://maps.apple.com/?'+urlencode({'q':', '.join(value for value in (name,address) if value)})


def website_reference(document,text):
    for label,value in labelled_fields(text):
        if label in ('website','web site','official website','website / location','website/location'):
            found=re.search(r'https?://[^\s<>]+',value)
            value=safe_url(found[0].rstrip('.,);')) if found else ''
            if value and not map_link(value):return value
    for link in document.links:
        value=safe_url(link.get('url',''))
        if value and not map_link(value) and (
            re.fullmatch(r'(?:visit |official |our )?website',link.get('title','').strip(),re.I)
            or re.match(r'^(?:[-–—•]\s*)?(?:official |our )?web\s*site\s*:',link.get('context','').strip(),re.I)
        ):return value
    return None


def email_places(document):
    text='\n'.join(span['text'] for span in document.spans)
    fields=labelled_fields(text)
    suffix=document.title.split(':',1)[-1].strip() if ':' in document.title else ''
    explicit_name=next((venue_name(value) for label,value in fields if label in NAME_LABELS),None)
    name=explicit_name
    if not name and suffix and suffix.casefold() in text.casefold():name=suffix
    if not name:
        headings=list(dict.fromkeys(normalize(span.get('section','')) for span in document.spans
            if span.get('section') and span['section'] not in ('Body','Email',document.title)
            and not re.fullmatch(r'sources?|references?|footer|disclaimer|introduction|overview|ingredients|instructions|hours|contact(?: details)?',normalize(span['section']),re.I)))
        if len(headings)==1 and len(headings[0])<=200:name=venue_name(headings[0])
    address=text_address(text) or ''
    website=website_reference(document,text)
    links=[link for link in document.links if map_link(link.get('url',''))]
    places=[]
    for link in links[:12]:
        parts=urlsplit(link['url']);query=parse_qs(parts.query)
        label=normalize(link.get('title',''))
        label=re.sub(r'^(?:get |open |view )?directions to\s+','',label,flags=re.I)
        value=(query.get('query') or query.get('q') or query.get('name') or [''])[0]
        if not value:
            path=re.search(r'/maps/(?:place|search)/([^/]+)',parts.path)
            if path:value=unquote(path[1]).replace('+',' ')
        if re.fullmatch(r'[-+\d.,\s]+',value) or value.startswith('place_id:'):value=''
        if explicit_name and len(links)==1:
            identity=explicit_name
        else:
            identity=label if label and not generic_map_label(label) and not label.startswith('http') else name or value
        linked_address=(query.get('address') or [''])[0] or text_address(link.get('context','')) or (address if len(links)==1 else '')
        if not identity:identity=linked_address or 'Location from email'
        place={'name':identity[:200],'address':linked_address[:400],'map_url':urlunsplit(parts._replace(scheme='https')),'source_url':document.url}
        if identity=='Location from email':place['identity_known']=False
        if website and len(links)==1:place['website_url']=website
        places.append(place)
    venue_context=re.search(r'coffee|caf[eé]|restaurant|dining|venue|places?|travel|visit|pick',document.title,re.I) or any(label in NAME_LABELS-{'name'} for label,_ in fields)
    if not places and name and address and venue_context:
        place={'name':name[:200],'address':address,'map_url':maps_search(name,address),'source_url':document.url}
        if website:place['website_url']=website
        places.append(place)
    seen=set();unique=[]
    for place in places:
        key=(place['name'].casefold(),place['address'].casefold())
        if key not in seen:unique.append(place);seen.add(key)
    return unique[:12]


def place_prose(text):
    text=re.sub(r'(?:(?:^|\n)[ \t]*(?:[-–—•|][ \t]+)?|[ \t]+[-–—•|][ \t]+)(?:website|official website|maps?|directions|location link)\s*:\s*https?://\S+','',text,flags=re.I)
    text=re.sub(r'https?://[^\s<>]+','',text)
    text=re.sub(r'(?m)^\s*(?:website|maps?|directions)\s*:?\s*$','',text,flags=re.I)
    return '\n\n'.join(normalize(paragraph) for paragraph in text.split('\n\n') if normalize(paragraph))
