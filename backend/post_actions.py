"""Move source contact details and links into bounded, deterministic post actions."""
import re
from urllib.parse import unquote, urlsplit

from .documents import normalize
from .locations import labelled_fields, map_link, maps_search, text_address
from .pipeline import safe_url

PHONE_LABEL = r'(?:phone|telephone|tel\.?|call(?: us)?|contact number)'
PHONE = re.compile(r'(?<![\w])(?:\+\d[\d ().-]{6,23}\d|\(\d{2,4}\)[ .-]*\d{3,4}[ .-]\d{3,4}|\d{3}[ .-]\d{3}[ .-]\d{4})(?![\w])')
URL = re.compile(r'https?://[^\s<>"\]]+|\bwww\.[^\s<>"\]]+', re.I)
EXCLUDED = re.compile(r'unsubscribe|manage.?preferences|email.?preferences|view.?in.?browser|/(?:privacy|terms|signin|login)(?:[/?#]|$)|reset.?password|verify.?email|confirmation.?token|tracking.?pixel', re.I)
LINK_LABELS = {'website', 'web site', 'official website', 'website / location', 'website/location', 'github', 'repository', 'repo', 'source code', 'project website', 'link'}
CONTACT_LABELS = LINK_LABELS | {'phone', 'telephone', 'tel', 'call', 'call us', 'contact number', 'address', 'postal address', 'location', 'where', 'map', 'maps', 'directions', 'location link'}


def phone_url(value):
    value = unquote(re.sub(r'^tel:', '', value, flags=re.I)).strip()
    # A phone action can contain a number, never a command, pause, or URL query.
    if not re.fullmatch(r'\+?[\d ().-]+', value):
        return None
    digits = re.sub(r'\D', '', value)
    if not 7 <= len(digits) <= 15:
        return None
    return 'tel:' + ('+' if value.startswith('+') else '') + digits


def web_url(value):
    value = value.strip().rstrip('.,;!')
    while value.endswith(')') and value.count(')') > value.count('('):
        value = value[:-1]
    if value.lower().startswith('www.'):
        value = 'https://' + value
    return safe_url(value)


def source_actions(document, analysis, visible=()):
    spans = document.spans if document else []
    text = '\n'.join(span['text'] for span in spans)
    fields = labelled_fields(text)
    links = document.links if document else []
    metadata = document.metadata if document else {}
    actions, seen = [], set()

    def add(kind, label, url, **extra):
        if not url:
            return
        if (kind, url.rstrip('/')) in seen:
            if extra:
                current = next(item for item in actions if item['kind'] == kind and item['url'].rstrip('/') == url.rstrip('/'))
                current.update({key: value for key, value in extra.items() if value})
                if label:
                    current['label'] = normalize(label)[:180]
            return
        limits = {'website': 8, 'github': 8, 'phone': 8, 'map': 12}
        if sum(item['kind'] == kind for item in actions) >= limits[kind]:
            return
        seen.add((kind, url.rstrip('/')))
        actions.append({'kind': kind, 'label': normalize(label)[:180], 'url': url, **extra})

    def add_web(value, label=''):
        url = web_url(value)
        if not url or EXCLUDED.search(url + ' ' + label):
            return
        if re.search(r'\.(?:png|jpe?g|gif|webp|svg|ico|woff2?)(?:$|\?)', url, re.I):
            return
        if map_link(url):
            add('map', label or 'Open source map', url)
        else:
            host = (urlsplit(url).hostname or '').lower().removeprefix('www.')
            kind = 'github' if host == 'github.com' else 'website'
            add(kind, label or host, url)

    for link in links[:100]:
        url, label = link.get('url', ''), link.get('title', '')
        # Split editions retain parent links: require evidence in this story.
        related = not document or '#' not in document.url or url in text or (link.get('context') and normalize(link['context']) in normalize(text))
        if related:
            add_web(url, label)
    for match in URL.finditer(text):
        add_web(match[0])
    for label, value in fields:
        if label in LINK_LABELS:
            for match in URL.finditer(value):
                add_web(match[0])
            if not URL.search(value) and re.fullmatch(r'(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?', value, re.I):
                add_web('https://' + value)
        if label in ('phone', 'telephone', 'tel', 'call', 'call us', 'contact number'):
            url = phone_url(value)
            if url:
                add('phone', 'Call ' + value, url)
    for match in PHONE.finditer(text):
        add('phone', 'Call ' + match[0], phone_url(match[0]))
    for match in re.finditer(r'\b' + PHONE_LABEL + r'\s*:?\s*(\+?[\d ().-]{6,25}\d)\b', text, re.I):
        add('phone', 'Call ' + match[1], phone_url(match[1]))
    for item in metadata.get('contact_links', [])[:30]:
        context = normalize(item.get('context', ''))
        if document and '#' in document.url and context not in normalize(text):
            continue
        number = item.get('url', '')
        add('phone', 'Call ' + number.removeprefix('tel:'), phone_url(number))

    action = analysis.get('action') or {}
    places = action.get('data', []) if action.get('kind') == 'places' else metadata.get('places', [])
    addresses = []
    for place in places:
        if place.get('website_url'):
            add_web(place['website_url'], place.get('name', 'Website'))
        address = place.get('address', '')
        if address:
            addresses.append(address)
        if place.get('map_url') or address:
            add('map', place.get('name') or address, place.get('map_url') or maps_search(place.get('name', ''), address),
                name=place.get('name', ''), address=address)
    for label, value in fields:
        if label in ('address', 'postal address') or (label in ('where', 'location') and re.search(r'\d', value)):
            if len(value) <= 400 and not URL.search(value) and re.search(r'[A-Za-z]', value):
                addresses.append(value)
    if not addresses and (metadata.get('format') == 'email' or action.get('kind') == 'places'):
        address = text_address(text)
        if address:
            addresses.append(address)
    mapped = {normalize(item.get('address', '')).casefold() for item in actions if item['kind'] == 'map'}
    for address in dict.fromkeys(addresses):
        if normalize(address).casefold() not in mapped:
            add('map', address, maps_search('', address), name=address, address=address)
            mapped.add(normalize(address).casefold())

    # Legacy excerpt posts may have no retained document. Preserve their links
    # as actions rather than silently discarding access to them.
    if document is None:
        for value in visible:
            for match in URL.finditer(value):
                add_web(match[0])
            for match in PHONE.finditer(value):
                add('phone', 'Call ' + match[0], phone_url(match[0]))
    return actions


def action_prose(text, actions):
    """Presentation only: source evidence and stored prose remain unchanged."""
    text = re.sub(r'\[([^\]]+)\]\((?:https?://|tel:)[^)]*\)', r'\1', text)
    for item in sorted(actions, key=lambda item: len(item.get('address', '')), reverse=True):
        if item['kind'] == 'map' and item.get('address'):
            address = re.escape(item['address'])
            text = re.sub(r'(?i)\b(?:address|postal address|where|location)\s*:\s*' + address, '', text)
            text = re.sub(address, '', text, flags=re.I)
        elif item['kind'] == 'phone':
            number = item['url'].removeprefix('tel:')
            for match in reversed(list(PHONE.finditer(text))):
                if phone_url(match[0]) == item['url']:
                    text = text[:match.start()] + text[match.end():]
            text = re.sub(re.escape(number) + r'(?!\d)', '', text)
        elif item['kind'] in ('website', 'github'):
            parts = urlsplit(item['url'])
            bare = parts.netloc + parts.path.rstrip('/')
            if parts.query:
                bare += '?' + parts.query
            text = re.sub(r'(?<![\w@./-])' + re.escape(bare) + r'(?![\w/-]|\.\w)', '', text, flags=re.I)
    # Strip whole labeled link/number fields, then remove links in prose while
    # retaining their human-readable anchor text.
    label_pattern = '|'.join(re.escape(label) for label in sorted(CONTACT_LABELS, key=len, reverse=True))
    text = re.sub(r'(?i)\b(' + label_pattern + r')\s*:\s*(?=(?:[-–—•|]\s|\n|$|[.!?](?:\s|$)))', '', text)
    text = URL.sub('', text)
    for label, value in labelled_fields(text):
        if label in LINK_LABELS and re.fullmatch(r'(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?', value, re.I):
            text = text.replace(value, '')
    if any(item['kind'] == 'phone' for item in actions):
        text = re.sub(r'\b' + PHONE_LABEL + r'\s*:\s*', '', text, flags=re.I)
    text = re.sub(r'(?i)\b(' + '|'.join(re.escape(label) for label in sorted(LINK_LABELS, key=len, reverse=True)) + r')\s*:\s*', '', text)
    text = re.sub(r'(?i)\b(' + label_pattern + r')\s*:\s*(?=(?:[-–—•|]\s|\n|$|[.!?](?:\s|$)))', '', text)
    text = re.sub(r'(?:\s+[-–—•|]){2,}\s*', ' - ', text)
    text = re.sub(r'(?m)^[ \t]*[-–—•|][ \t]*|[ \t]+[-–—•|][ \t]*$', '', text)
    return '\n\n'.join(normalize(part) for part in text.split('\n\n') if normalize(part))
