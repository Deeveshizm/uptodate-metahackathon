"""Google Places fallback. Persist place IDs, never photo names, URLs or images."""
import json
import os
import re
import hashlib
import time
import uuid
from urllib.parse import urlencode, urlsplit

import httpx

from .retrieval import PublicFetcher, SourceError
from .venue_identity import matches_place

REFERENCE_TYPE = 'application/x-uptodate-google-place'
REFERENCE_PREFIX = b'uptodate-google-place-v1:'
PLACE_ID = re.compile(r'[A-Za-z0-9_-]{10,200}')
PHOTO_NAME = re.compile(r'places/[A-Za-z0-9_-]{10,200}/photos/[A-Za-z0-9_-]{1,1500}')
_CLIENT_FEATURES={}


def supports_live_venue(user_id):
    """Roll out live attribution only after an account has used the new client."""
    try:identifier=str(uuid.UUID(user_id))
    except (ValueError,TypeError):return False
    previous=_CLIENT_FEATURES.get(identifier)
    if previous and previous[0]>time.time():return previous[1]
    origin=os.environ.get('SUPABASE_URL','').rstrip('/')
    key=os.environ.get('SUPABASE_SECRET_KEY','')
    parts=urlsplit(origin)
    if parts.scheme!='https' or not parts.hostname or parts.path or not key:return False
    try:
        with httpx.Client(timeout=15,trust_env=False) as client:
            response=client.get(origin+'/auth/v1/admin/users/'+identifier,headers={'apikey':key,'Authorization':'Bearer '+key})
            if response.status_code!=200:return False
            user=response.json();metadata=user.get('user_metadata') or {}
            enabled=isinstance(metadata,dict) and metadata.get('uptodate_live_venue_client_v1') is True
    except (httpx.HTTPError,ValueError,TypeError):return False
    if len(_CLIENT_FEATURES)>2000:_CLIENT_FEATURES.clear()
    _CLIENT_FEATURES[identifier]=(time.time()+300,enabled)
    return enabled


class PlacesRejected(SourceError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status=status


def reference(place_id, index):
    if not PLACE_ID.fullmatch(place_id) or type(index) is not int or not 0 <= index < 10:
        raise SourceError('Invalid Google Maps photo reference')
    return REFERENCE_PREFIX + json.dumps({'place_id':place_id,'index':index},sort_keys=True).encode()


def parse_reference(data):
    if not data.startswith(REFERENCE_PREFIX):return None
    try:
        value=json.loads(data[len(REFERENCE_PREFIX):])
        if set(value) != {'place_id','index'} or reference(value['place_id'],value['index']) != data:
            raise ValueError()
        return value
    except (TypeError,ValueError,KeyError):
        raise SourceError('Invalid Google Maps photo reference') from None


class GooglePlacesPhotos:
    def __init__(self, key=None, transport=None, fetcher=None, store=None):
        self.key=key if key is not None else os.environ.get('GOOGLE_PLACES_API_KEY','').strip()
        self.transport=transport
        self.fetcher=fetcher or PublicFetcher()
        self.store=store

    @staticmethod
    def enabled():
        return bool(os.environ.get('GOOGLE_PLACES_API_KEY','').strip()) and os.environ.get('GOOGLE_PLACES_PHOTOS_ENABLED','0')=='1'

    def _json(self, method, path, *, fields=None, body=None, params=None):
        if not self.key:raise SourceError('Google Maps photos are not configured')
        headers={'X-Goog-Api-Key':self.key}
        if fields:headers['X-Goog-FieldMask']=fields
        try:
            with httpx.Client(timeout=httpx.Timeout(25,connect=10),trust_env=False,transport=self.transport) as client:
                with client.stream(method,'https://places.googleapis.com/v1/'+path,headers=headers,json=body,params=params) as response:
                    if not response.is_success:
                        message={401:'Google Maps photo credentials need attention',403:'Google Maps photo access needs attention',429:'Google Maps photo quota reached; try later'}.get(response.status_code,'Google Maps photos are temporarily unavailable')
                        raise PlacesRejected(message,response.status_code)
                    data=bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data)>2_000_000:raise SourceError('Google Maps photo response exceeded the size limit')
            result=json.loads(data)
            if not isinstance(result,dict):raise ValueError()
            return result
        except httpx.HTTPError:
            raise SourceError('Google Maps photos could not connect or timed out; no automatic retry was made') from None
        except (ValueError,TypeError):
            raise SourceError('Google Maps photos returned an invalid response') from None

    def _paid_json(self, price, budget, *args, **kwargs):
        reservation=budget.reserve(price) if budget else None
        try:result=self._json(*args,**kwargs)
        except PlacesRejected as error:
            if budget and error.status in (400,401,402,403,404,422,429):budget.settle(reservation,0)
            raise
        if budget:budget.settle(reservation,price)
        return result

    def lookup(self, place, fields, budget=None, place_id=None):
        """Fetch only requested fields. The shared cache contains place IDs only."""
        if not place.get('name') or not place.get('address'):return None
        fields=set(fields)|{'id','displayName','formattedAddress'}
        identity_key=hashlib.sha256(('google-place-id-v1:'+json.dumps({key:place.get(key,'') for key in ('name','address')},sort_keys=True).casefold()).encode()).hexdigest()
        if not place_id and self.store:
            with self.store.connect() as db:
                row=db.execute('SELECT pages FROM venue_photo_searches WHERE cache_key=? AND status=\'ready\' AND expires_at>?',(identity_key,time.time())).fetchone()
            if row:
                values=json.loads(row['pages'])
                if values and isinstance(values[0],str) and PLACE_ID.fullmatch(values[0]):place_id=values[0]
        tier=0.025 if fields & {'reviews','reviewSummary'} else 0.020 if fields & {'rating','userRatingCount','regularOpeningHours','internationalPhoneNumber'} else 0.017
        if place_id and PLACE_ID.fullmatch(place_id):
            found=self._paid_json(tier,budget,'GET','places/'+place_id,fields=','.join(sorted(fields)))
            candidates=[found]
        else:
            price=0.040 if tier==0.025 else 0.035 if tier==0.020 else 0.032
            result=self._paid_json(price,budget,'POST','places:searchText',fields=','.join('places.'+field for field in sorted(fields)),
                                   body={'textQuery':place['name']+', '+place['address'],'pageSize':3})
            candidates=result.get('places',[])
        if not isinstance(candidates,list):raise SourceError('Google Maps returned invalid place results')
        for found in candidates[:3]:
            if not isinstance(found,dict) or not isinstance(found.get('displayName'),dict):continue
            name,address=found['displayName'].get('text',''),found.get('formattedAddress','')
            if not isinstance(name,str) or not isinstance(address,str) or not matches_place(place,name+' '+address):continue
            postal=re.search(r'\b\d{5}(?:-\d{4})?\b',place.get('address',''))
            if postal and postal.group().split('-')[0] not in address:continue
            identifier=found.get('id','')
            if not isinstance(identifier,str) or not PLACE_ID.fullmatch(identifier):continue
            if self.store:
                with self.store.connect() as db:
                    db.execute("INSERT INTO venue_photo_searches(cache_key,status,pages,expires_at) VALUES(?,'ready',?,?) ON CONFLICT(cache_key) DO UPDATE SET status='ready',pages=excluded.pages,expires_at=excluded.expires_at",(identity_key,json.dumps([identifier]),time.time()+86400*30))
            return found
        return None

    @staticmethod
    def selected_references(place, found, limit=3):
        place_id=found.get('id','')
        photos=found.get('photos',[])
        if not isinstance(place_id,str) or not PLACE_ID.fullmatch(place_id) or not isinstance(photos,list):return []
        gallery=[]
        for index,photo in enumerate(photos[:10]):
            if not isinstance(photo,dict):continue
            resource=photo.get('name','')
            if not isinstance(resource,str) or not PHOTO_NAME.fullmatch(resource) or not resource.startswith('places/'+place_id+'/photos/'):continue
            width,height=photo.get('widthPx',0),photo.get('heightPx',0)
            if not isinstance(width,(int,float)) or not isinstance(height,(int,float)) or width<400 or height<240:continue
            source='https://www.google.com/maps/search/?'+urlencode({'api':1,'query':place['name'],'query_place_id':place_id})
            visual={'kind':'source','status':'ready','provider':'google_places','source_role':'venue_listing',
                    'caption':place['name']+' · Google Maps photo','source_url':source,'attribution':'Google Maps',
                    'requires_live_attribution':True,'cache_policy':'no-store'}
            gallery.append((visual,reference(place_id,index)))
            if len(gallery)>=min(limit,3):break
        return gallery

    def gallery(self, places, budget=None, limit=3):
        place=next((p for p in places[:3] if p.get('name') and p.get('address') and p.get('identity_known') is not False),None)
        if not place:return []
        found=self.lookup(place,{'photos'},budget)
        return self.selected_references(place,found,limit) if found else []

    def read(self, stored, budget=None):
        value=parse_reference(stored)
        if value is None:raise SourceError('Invalid Google Maps photo reference')
        place_id,index=value['place_id'],value['index']
        # Photo names expire and must be obtained afresh for each display.
        result=self._json('GET','places/'+place_id,fields='photos')
        photos=result.get('photos',[])
        if not isinstance(photos,list) or index>=len(photos):raise SourceError('This Google Maps photo is no longer available')
        photo=photos[index]
        if not isinstance(photo,dict):raise SourceError('Invalid Google Maps photo response')
        name=photo.get('name','')
        if not isinstance(name,str) or not PHOTO_NAME.fullmatch(name) or not name.startswith('places/'+place_id+'/photos/'):
            raise SourceError('Invalid Google Maps photo response')
        media=self._paid_json(0.007,budget,'GET',name+'/media',params={'maxWidthPx':1200,'maxHeightPx':1200,'skipHttpRedirect':'true'})
        uri=media.get('photoUri','')
        parts=urlsplit(uri) if isinstance(uri,str) else None
        host=parts.hostname if parts else ''
        if not parts or parts.scheme!='https' or parts.username or not host or not (host.endswith('.googleusercontent.com') or host.endswith('.ggpht.com')):
            raise SourceError('Google Maps returned an invalid photo destination')
        resource=self.fetcher.fetch(uri)
        if not resource.content_type.lower().startswith('image/'):raise SourceError('Google Maps photo was unavailable')
        from .visuals import decode_image
        credit={'provider':'google_maps','authors':[], 'source_url':photo.get('googleMapsUri')}
        authors=photo.get('authorAttributions',[])
        if not isinstance(authors,list):authors=[]
        for author in authors[:6]:
            if isinstance(author,dict) and isinstance(author.get('displayName'),str):
                credit['authors'].append({key:author[key] for key in ('displayName','uri','photoUri') if isinstance(author.get(key),str)})
        if not credit['source_url']:
            credit['source_url']='https://www.google.com/maps/search/?'+urlencode({'api':1,'query':place_id,'query_place_id':place_id})
        return decode_image(resource.body),credit
