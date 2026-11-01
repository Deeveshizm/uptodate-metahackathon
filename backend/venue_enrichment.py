"""Fill venue gaps in source order; Google content stays live and attributed."""
import json
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .documents import normalize
from .google_places_photos import GooglePlacesPhotos, parse_reference
from .locations import labelled_fields, maps_search
from .pipeline import safe_url
from .retrieval import SourceError
from .venue_facts import venue_facts
from .venue_identity import matches_place

GOOGLE_FIELDS={'website':'websiteUri','phone':'internationalPhoneNumber','location':'formattedAddress',
               'rating':'rating','hours':'regularOpeningHours','reviews':'reviews'}


def newsletter_fields(document):
    places=document.metadata.get('places',[])
    if len(places)!=1:return {}
    place=places[0]
    text=(document.metadata.get('email_text') if '#' not in document.url else None) or '\n'.join(span['text'] for span in document.spans if not span.get('source_kind'))
    labels=labelled_fields(text)
    phone=next((value for label,value in labels if label in ('phone','phone number','telephone','call')),None)
    review=next((value for label,value in labels if label in ('reviews','review summary','review highlights','review')),None)
    facts=venue_facts(document) or {}
    website=place.get('website_url')
    if not website:
        from .email_content import map_link
        links=[link for link in document.links if safe_url(link.get('url','')) and not map_link(link['url'])
               and not re.search(r'unsubscribe|confirm|activate|login|sign.?in',link['url'],re.I)
               and not re.search(r'\.(?:jpg|jpeg|png|webp|gif)(?:\?|$)',link['url'],re.I)]
        labelled=next((link for link in links if re.search(r'\b(?:website|official site)\b',link.get('context','')+' '+link.get('title',''),re.I)),None)
        if labelled:website=labelled['url']
        elif len(links)==1 and urlsplit(links[0]['url']).path in ('','/'):website=links[0]['url']
    return {'website':website,'phone':phone,'location':place.get('address'),
            'rating':facts.get('rating'),'hours':facts.get('hours'),
            'reviews':review if review and len(review.split())>=4 else None,
            'image':bool(document.figures)}


def google_values(found, missing):
    values={}
    if 'website' in missing and safe_url(found.get('websiteUri','')):values['website']=found['websiteUri']
    if 'phone' in missing and isinstance(found.get('internationalPhoneNumber'),str):values['phone']=found['internationalPhoneNumber']
    if 'location' in missing and isinstance(found.get('formattedAddress'),str):values['location']=found['formattedAddress']
    if 'rating' in missing and isinstance(found.get('rating'),(int,float)):
        count=found.get('userRatingCount')
        values['rating']=f"Rating: {found['rating']:g}/5"+(f' · {count} ratings' if isinstance(count,int) else '')
    if 'hours' in missing:
        descriptions=(found.get('regularOpeningHours') or {}).get('weekdayDescriptions',[])
        if isinstance(descriptions,list):
            text='\n'.join(value for value in descriptions[:7] if isinstance(value,str))
            if text:values['hours']=text
    if 'reviews' in missing:
        summary=found.get('reviewSummary') or {}
        text=(summary.get('text') or {}).get('text')
        reviews=found.get('reviews',[])
        if isinstance(text,str) and text:
            values['reviews']={'summary':text,'disclosure':(summary.get('disclosureText') or {}).get('text'),
                               'source_url':summary.get('reviewsUri') or found.get('googleMapsUri')}
        elif isinstance(reviews,list):
            selected=[]
            for review in reviews[:2]:
                if not isinstance(review,dict):continue
                body=(review.get('text') or {}).get('text')
                author=review.get('authorAttribution') or {}
                if isinstance(body,str) and body:
                    selected.append({'text':body,'author':author.get('displayName'), 'author_url':author.get('uri'),
                                     'avatar_url':author.get('photoUri'),'source_url':review.get('googleMapsUri') or found.get('googleMapsUri')})
            if selected:values['reviews']={'items':selected,'source_url':found.get('googleMapsUri')}
    return values


def website_values(resource, place, missing):
    if 'html' not in resource.content_type:return {}
    soup=BeautifulSoup(resource.body,'html.parser')
    values={}
    for script in soup.find_all('script',type='application/ld+json',limit=20):
        try:pending=[json.loads(script.string or script.get_text())]
        except (ValueError,TypeError):continue
        for _ in range(150):
            if not pending:break
            node=pending.pop()
            if isinstance(node,list):pending.extend(node[:50]);continue
            if not isinstance(node,dict):continue
            pending.extend(value for value in node.values() if isinstance(value,(dict,list)))
            if not isinstance(node.get('name'),str) or not node.get('address'):continue
            context=node['name']+' '+json.dumps(node['address'])
            if not matches_place(place,context):continue
            if 'phone' in missing and isinstance(node.get('telephone'),str):values['phone']=node['telephone'][:80]
            if 'website' in missing and safe_url(node.get('url','')):values['website']=node['url']
            if 'location' in missing:
                address=node['address']
                if isinstance(address,str):values['location']=address
                elif isinstance(address,dict):values['location']=', '.join(str(address[key]) for key in ('streetAddress','addressLocality','addressRegion','postalCode') if address.get(key))
            if 'hours' in missing and isinstance(node.get('openingHours'),(str,list)):
                hours=node['openingHours'];values['hours']='\n'.join(hours) if isinstance(hours,list) and all(isinstance(v,str) for v in hours) else hours if isinstance(hours,str) else ''
            if 'rating' in missing and isinstance(node.get('aggregateRating'),dict):
                from .venue_reviews import number
                rating=node['aggregateRating'];value=number(rating.get('ratingValue'))
                if value is not None and 0<=value<=5:values['rating']=f'Rating: {value:g}/5'
    if not matches_place(place,soup.get_text(' ',strip=True)) and not values:return {}
    if 'phone' in missing and not values.get('phone'):
        anchor=soup.find('a',href=re.compile(r'^tel:',re.I))
        if anchor:values['phone']=anchor['href'][4:][:80]
    return {key:value for key,value in values.items() if value}


class VenueEnrichment:
    def __init__(self, fetcher, google=None):
        self.fetcher,self.google=fetcher,google

    def enrich(self, document, budget=None):
        places=document.metadata.get('places',[])
        if len(places)!=1:return document
        own=newsletter_fields(document)
        if not own:return document
        place=places[0]
        if own.get('website') and not place.get('website_url'):place['website_url']=own['website']
        missing={key for key,value in own.items() if not value}
        document.metadata['venue_reviews_needed']=not bool(own.get('reviews'))
        document.metadata['venue_missing_fields']=sorted(missing)
        if not missing:return document
        found=None
        if self.google:
            fields={GOOGLE_FIELDS[key] for key in missing if key in GOOGLE_FIELDS}
            if 'image' in missing:fields.add('photos')
            if 'reviews' in missing:fields.add('reviewSummary')
            if 'rating' in missing:fields.add('userRatingCount')
            try:found=self.google.lookup(place,fields,budget,document.metadata.get('google_venue_id'))
            except SourceError:pass
        available=google_values(found,missing) if found else {}
        if found:
            document.metadata['google_venue_id']=found['id']
            if 'image' in missing:
                document.metadata['google_photo_checked']=True
                gallery=self.google.selected_references(place,found)
                document.metadata['google_photo_indices']=[parse_reference(data)['index'] for _,data in gallery]
            if available:
                document.metadata['live_venue']={'place_id':found['id'],'fields':sorted(available)}
        # Google fields are deliberately not copied into source spans or saved
        # as newsletter facts. Clients fetch them live with source attribution.
        remaining=missing-set(available)-{'image'}
        website=own.get('website') or (found.get('websiteUri') if found else None)
        supplement={}
        if remaining and website:
            try:
                resource=self.fetcher.fetch(website)
                supplement=website_values(resource,place,remaining)
                document.metadata['venue_website_fields']={'values':supplement,'source_url':resource.url}
            except SourceError:pass
        document.metadata['venue_reviews_needed']='reviews' in remaining
        return document


def live_venue_details(reference_data, place, google, budget=None):
    requested=set(reference_data.get('fields',[])) & set(GOOGLE_FIELDS)
    if not requested:return {'fields':{},'sources':{}}
    fields={GOOGLE_FIELDS[key] for key in requested}
    fields.add('googleMapsUri')
    if 'reviews' in requested:fields.add('reviewSummary')
    if 'rating' in requested:fields.add('userRatingCount')
    found=google.lookup(place,fields,budget,reference_data.get('place_id'))
    if not found:return {'fields':{},'sources':{}}
    values=google_values(found,requested)
    return {'fields':values,'sources':{key:'Google Maps' for key in values},'source_url':found.get('googleMapsUri')}
