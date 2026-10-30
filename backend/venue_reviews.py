"""Optional, bounded venue enrichment from fetched, branch-matched evidence.

Search only discovers pages. Model-written search answers are never reviews.
Unsupported pages, unavailable providers and failed retrieval leave the original
recommendation usable; none proves that the venue has few reviews.
"""
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .documents import normalize
from .pipeline import safe_url
from .retrieval import FETCH_DEADLINE, SourceError
from .visuals import venue_street


def number(value):
    if isinstance(value, bool):return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):return None


def identity(value):
    return ''.join(re.findall(r'[a-z0-9]+', normalize(value).casefold()))


def review_evidence(resource, place):
    """Accept explicit structured reviews only for a matching name AND street."""
    if 'html' not in resource.content_type:return []
    soup = BeautifulSoup(resource.body, 'html.parser')
    nodes = []
    for script in soup.find_all('script', attrs={'type':'application/ld+json'}, limit=8):
        raw = script.get_text()
        if len(raw) > 100_000:continue
        try:data = json.loads(raw)
        except (ValueError, RecursionError):continue
        for item in (data if isinstance(data, list) else [data])[:60]:
            if isinstance(item, dict):
                nodes.append(item)
                graph = item.get('@graph', [])
                if isinstance(graph, list):nodes.extend(obj for obj in graph[:60] if isinstance(obj, dict))
    by_id = {node['@id']:node for node in nodes if isinstance(node.get('@id'), str)}
    def resolve(value):
        return by_id.get(value.get('@id'), value) if isinstance(value, dict) else value
    host = urlsplit(resource.url).hostname or 'the review source'
    records = []
    for business in nodes:
        if not isinstance(business.get('name'), str) or identity(business['name']) != identity(place['name']):continue
        address = resolve(business.get('address'))
        if not isinstance(address, dict):continue
        street = address.get('streetAddress', '')
        expected = venue_street(place.get('address', ''))
        if not isinstance(street, str) or not expected or venue_street(street) != expected:continue
        # Street names can repeat across cities. Check supplied city/postcode
        # when available, and require at least one matching locality field.
        locality = address.get('addressLocality', '')
        postcode = address.get('postalCode', '')
        supplied = identity(place.get('address', ''))
        if not any(isinstance(v, str) and identity(v) and identity(v) in supplied for v in (locality, postcode)):continue
        if any(isinstance(v, str) and identity(v) and identity(v) not in supplied for v in (locality,) if v):continue
        expected_postcode = re.search(r'\b\d{5}(?:-\d{4})?\b', place.get('address','').split(',')[-1])
        if expected_postcode and isinstance(postcode,str) and postcode and expected_postcode[0][:5] != postcode[:5]:continue
        prefix = f"{place['name']} at {place['address']}, listed on {host}"
        aggregate = resolve(business.get('aggregateRating'))
        if isinstance(aggregate, dict):
            rating, best, worst = (number(aggregate.get(key, default)) for key, default in (('ratingValue', None), ('bestRating', 5), ('worstRating', 1)))
            count_key = 'reviewCount' if 'reviewCount' in aggregate else 'ratingCount'
            count = number(aggregate.get(count_key))
            if rating is not None and best is not None and worst is not None and worst < best and worst <= rating <= best and count is not None and count > 0 and count.is_integer():
                unit = 'reviews' if count_key == 'reviewCount' else 'ratings'
                records.append({'kind':'rating','text':f'{prefix}: rating {rating:g} out of {best:g}, based on {int(count)} {unit}.',
                                'rating':rating,'scale':best,'count':int(count),'count_kind':unit,'small_sample':count < 10,
                                'url':resource.url})
        reviews = business.get('review', [])
        for review in (reviews if isinstance(reviews, list) else [reviews])[:6]:
            review = resolve(review)
            if not isinstance(review, dict):continue
            content = review.get('reviewBody') or review.get('description')
            if not isinstance(content, str):continue
            body = normalize(BeautifulSoup(content, 'html.parser').get_text(' ', strip=True))
            if len(body) < 20:continue
            author = review.get('author')
            editorial = isinstance(author, dict) and author.get('@type') == 'Organization'
            author = author.get('name') if isinstance(author, dict) else author
            author = normalize(author)[:100] if isinstance(author, str) else 'Unnamed reviewer'
            kind = 'publisher_description' if editorial else 'review'
            label = 'Publisher description' if editorial else 'One published review'
            records.append({'kind':kind,'text':f'{prefix}. {label} by {author}: {body[:600]}',
                            'author':author,'url':resource.url})
    return records[:7]


class VenueReviews:
    def __init__(self, fetcher, search=None):
        self.fetcher, self.search = fetcher, search

    def enrich(self, document, budget=None):
        places = document.metadata.get('places', [])
        # One short recommendation per lookup; a multi-venue edition should
        # first be split into venue stories rather than mixed in one query.
        if len(places) != 1 or len(document.metadata.get('email_text', '').split()) > 160:return document
        place = places[0]
        if not place.get('address') or place.get('identity_known') is False:return document
        pages = [place.get('website_url')] if place.get('website_url') else []
        records, checked = [], []
        def collect(urls):
            deadline = FETCH_DEADLINE.set(min(FETCH_DEADLINE.get() or float('inf'), time.monotonic() + 20))
            try:
                for url in urls:
                    url = safe_url(url or '')
                    if not url or url in checked or len(checked) >= 3 or time.monotonic() >= FETCH_DEADLINE.get():continue
                    # A maps product is not a scrapeable review feed.
                    host = (urlsplit(url).hostname or '').lower()
                    if host in ('google.com','www.google.com','maps.google.com','maps.apple.com','maps.app.goo.gl'):continue
                    checked.append(url)
                    try:records.extend(review_evidence(self.fetcher.fetch(url), place))
                    except SourceError:continue
                    if time.monotonic() >= FETCH_DEADLINE.get():break
            finally:FETCH_DEADLINE.reset(deadline)
        collect(pages)
        if not records and self.search:
            try:pages = self.search.pages([place], budget)
            except SourceError:pages = []
            document.metadata['venue_reference_pages'] = pages[:3]
            collect(pages)
        unique = list({(record['kind'], record['text']):record for record in records}.values())[:7]
        document.metadata['review_lookup'] = {'status':'verified_source' if unique else 'unavailable',
            'checked_at':datetime.now(timezone.utc).isoformat(), 'pages_checked':len(checked)}
        if not unique:return document
        document.metadata['venue_reviews'] = unique
        for index, record in enumerate(unique, 1):
            document.spans.append({'id':f'venue-review-{index}', 'section':'Retrieved venue '+record['kind'],
                'page':None, 'text':record['text'], 'source_url':record['url'], 'source_kind':record['kind']})
        document.metadata['source_content_hash'] = hashlib.sha256(json.dumps(
            document.spans, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return document
