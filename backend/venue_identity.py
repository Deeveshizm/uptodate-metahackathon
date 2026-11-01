"""Match a venue's supplied identity without confusing branches or generic names."""
import re
import unicodedata


def identity_text(value):
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ' '.join(re.findall(r'[a-z0-9]+', value))


def street_text(value):
    aliases = {'north':'n', 'south':'s', 'east':'e', 'west':'w', 'street':'st',
               'road':'rd', 'avenue':'ave', 'boulevard':'blvd', 'drive':'dr',
               'lane':'ln', 'court':'ct', 'parkway':'pkwy', 'highway':'hwy'}
    value = str(value or '').split(',')[0]
    value = re.split(r'(?:#|\b(?:suite|ste|unit|apt)\.?\s*)\s*[\w-]+', value, maxsplit=1, flags=re.I)[0]
    return ' '.join(aliases.get(word, word) for word in identity_text(value).split())


def name_matches(name, text, *, address_matches=False):
    normalized = ' ' + identity_text(text) + ' '
    wanted = identity_text(name)
    if wanted and ' ' + wanted + ' ' in normalized:
        return True
    # Company, punctuation and category suffixes vary across official listings.
    # A distinctive brand token is useful only with a verified street address.
    generic = {'the','and','co','company','inc','llc','coffee','cafe','house','shop',
               'bar','restaurant','restaurants','kitchen','food','brewing','brewery',
               'bistro','grill','pub','midtown','downtown','northside','southside'}
    tokens = [word for word in wanted.split() if word not in generic]
    return bool(address_matches and tokens and any(len(word) >= 4 for word in tokens)
                and all(' ' + word + ' ' in normalized for word in tokens))


def address_matches(address, text):
    street = street_text(address)
    if not street or not re.search(r'\d', street):
        return False
    aliases = {'north':'n','south':'s','east':'e','west':'w','street':'st','road':'rd',
               'avenue':'ave','boulevard':'blvd','drive':'dr','lane':'ln','court':'ct',
               'parkway':'pkwy','highway':'hwy'}
    normalized = ' ' + ' '.join(aliases.get(word,word) for word in identity_text(text).split()) + ' '
    return ' ' + street + ' ' in normalized


def matches_place(place, text):
    matched = address_matches(place.get('address',''), text)
    return name_matches(place.get('name',''), text, address_matches=matched) and (
        matched if place.get('address') else True)
