"""Saved venue photos with explicit reuse licenses and source-level identity checks."""
import hashlib
import json
import os
import re
import time
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .documents import normalize
from .pipeline import safe_url
from .resource_usage import ResourceRate
from .retrieval import SourceError
from .venue_identity import matches_place, name_matches, street_text, identity_text


def venue_photo_mode():
    value=os.environ.get('VENUE_PHOTO_PROVIDER','source_first')
    if value not in ('google','licensed','source_first'):raise SourceError('Unknown venue photo provider')
    return value


def licensed_photo_mode():
    return venue_photo_mode()=='licensed'


def plain(value):
    value=str(value or '')
    return normalize(BeautifulSoup(value,'html.parser').get_text(' ',strip=True)) if '<' in value else normalize(value)


def license_info(value):
    """Accept commercial reuse and resizing; reject NC/ND or ambiguous terms."""
    parts=urlsplit(value if isinstance(value,str) else '')
    if parts.scheme not in ('http','https') or parts.username or parts.password or parts.hostname not in ('creativecommons.org','www.creativecommons.org'):return None
    match=re.fullmatch(r'/(licenses/(by|by-sa)|publicdomain/(zero|mark))/(1\.0|2\.0|2\.5|3\.0|4\.0)/?(?:deed(?:\.[a-z-]+)?/?)?',parts.path)
    if not match:return None
    kind=match[2] or match[3]
    name={'by':'CC BY','by-sa':'CC BY-SA','zero':'CC0','mark':'Public domain'}[kind]
    return {'license_url':'https://creativecommons.org/'+match[1]+'/'+match[4]+'/',
            'license_name':name+' '+match[4], 'requires_attribution':kind not in ('zero','mark')}


def website_photo_licenses(soup, base, fetcher=None, permission_lookup=None):
    """Only image-specific licenses; a page footer license is insufficient."""
    from .visuals import image_identity
    from .photo_permissions import EditorialPhotoPermissions
    lookup=permission_lookup or (EditorialPhotoPermissions(fetcher) if fetcher else None)
    found={}
    for script in soup.find_all('script',type='application/ld+json',limit=20):
        try:pending=[json.loads(script.string or script.get_text())]
        except (ValueError,TypeError):continue
        for _ in range(300):
            if not pending:break
            node=pending.pop()
            if isinstance(node,list):pending.extend(node[:100]);continue
            if not isinstance(node,dict):continue
            pending.extend(v for v in node.values() if isinstance(v,(dict,list)))
            kind=node.get('@type',[]);kind=[kind] if isinstance(kind,str) else kind
            if 'ImageObject' not in kind:continue
            image=safe_url(urljoin(base,node.get('contentUrl') or node.get('url') or ''))
            creator=node.get('creator') or node.get('author') or node.get('copyrightHolder') or ''
            if isinstance(creator,dict):creator=creator.get('name','')
            if not isinstance(creator,str):continue
            creator=plain(creator)
            declared=node.get('license')
            info=license_info(declared)
            if not info and lookup and image and isinstance(declared,str):
                policy=safe_url(urljoin(base,declared))
                if policy and (urlsplit(policy).hostname or '').removeprefix('www.')==(urlsplit(base).hostname or '').removeprefix('www.'):
                    info=lookup.image(policy,image,creator)
            if info and image and (creator or info.get('attribution') or not info['requires_attribution']):
                found[image_identity(image,base)]={**info,'attribution':info.get('attribution') or creator or 'Public domain',
                    'permission_source_url':info.get('permission_source_url',base),'permission_basis':info.get('permission_basis','image_specific_license')}
    for figure in soup.find_all('figure',limit=60):
        anchor=figure.find('a',rel=lambda value:value and 'license' in value)
        info=license_info(urljoin(base,anchor.get('href',''))) if anchor else None
        credit=figure.find(attrs={'itemprop':'creator'})
        creator=plain(credit.get_text(' ')) if credit else ''
        if not info or info['requires_attribution'] and not creator:continue
        for image in figure.find_all('img',limit=10):
            url=image.get('data-src') or image.get('src','')
            key=image_identity(url,base)
            if key:found[key]={**info,'attribution':creator or 'Public domain','permission_source_url':base,'permission_basis':'image_specific_license'}
    if lookup:
        for key,permission in lookup.inline(soup,base).items():found.setdefault(key,permission)
        site=lookup.site(soup,base)
        if site:
            for image in soup.find_all('img',limit=100):
                if image.find_parent(['nav','header','footer','aside']):continue
                target=safe_url(urljoin(base,image.get('data-src') or image.get('src','')))
                if target:found.setdefault(image_identity(target,base),{**site,'permission_image_urls':[target]})
    return found


class LicensedVenuePhotos:
    def __init__(self,store=None,fetcher=None,transport=None):
        from .retrieval import PublicFetcher
        self.store,self.fetcher,self.transport=store,fetcher or PublicFetcher(),transport
        self.last_report=[]
        self.identity_pages={}
        self.unavailable_until={}

    def _match(self,place,text):
        city=identity_text(place['address'].split(',')[1]) if ',' in place['address'] else ''
        if city and ' '+city+' ' not in ' '+identity_text(text)+' ':return None
        if matches_place(place,text):return 'exact_name_and_street_address'
        # A photo may name an intersection/street without its building number.
        # Verify that the official site lists exactly one branch on that street.
        # A matching city or brand alone never establishes the photo's identity.
        from .visuals import venue_address_text
        address=place['address'];street=street_text(address)
        number,separator,road=street.partition(' ')
        context=' '+venue_address_text(text)+' '
        compact=lambda value:identity_text(value).replace(' ','')
        named=name_matches(place['name'],text,address_matches=True) or compact(place['name']) in compact(text)
        direction,_,tail=road.partition(' ')
        has_direction=direction in ('n','s','e','w')
        short_road=tail if has_direction else road
        if not named or not separator or not number.isdigit() or not city or ' '+city+' ' not in ' '+identity_text(text)+' ' or ' '+short_road+' ' not in context:return None
        mentioned_directions=set(re.findall(r'\b([nsew])\s+'+re.escape(short_road)+r'\b',context))
        if has_direction and mentioned_directions and mentioned_directions!={direction}:return None
        road_pattern=(r'(?:[nsew]\s+)?' if has_direction else '')+re.escape(short_road)
        reported=set(re.findall(r'\b(\d{1,6})\s+'+road_pattern+r'\b',context))
        if reported and reported!={number}:return None
        website=safe_url(place.get('identity_page_url') or place.get('website_url',''))
        if not website:return None
        if website not in self.identity_pages:
            if len(self.identity_pages)>=64:self.identity_pages.pop(next(iter(self.identity_pages)))
            try:
                resource=self.fetcher.fetch(website)
                self.identity_pages[website]=BeautifulSoup(resource.body,'html.parser').get_text(' ',strip=True)[:100000] if 'html' in resource.content_type else ''
            except SourceError:self.identity_pages[website]=''
        official=self.identity_pages[website]
        if not matches_place(place,official):return None
        branches=set(re.findall(r'\b(\d{1,6})\s+'+road_pattern+r'\b',' '+venue_address_text(official)+' '))
        return 'official_unique_branch_street_and_city' if branches=={number} else None

    def _get(self,provider,url,params,budget):
        rate=ResourceRate(provider,'image_search',None,0,'UTC')
        reservation=budget.reserve_resource(rate) if budget else None
        try:
            with httpx.Client(timeout=httpx.Timeout(15,connect=5),trust_env=False,transport=self.transport,
                              headers={'User-Agent':'UpToDate/0.2 (https://up2-date.app; venue photo discovery)'}) as client:
                with client.stream('GET',url,params=params) as response:
                    if response.status_code!=200:raise SourceError(provider+' photo search is temporarily unavailable')
                    body=bytearray()
                    for block in response.iter_bytes():
                        body.extend(block)
                        if len(body)>2_000_000:raise SourceError('Photo search response exceeds the limit')
            result=json.loads(body)
            if not isinstance(result,dict):raise ValueError()
            return result
        except (httpx.HTTPError,ValueError):raise SourceError(provider+' photo search could not be read') from None
        finally:
            if budget:budget.settle_resource(reservation)

    def _commons(self,place,budget):
        # Names, street and city are public venue fields; never send email text.
        city=place['address'].split(',')[1].strip() if ',' in place['address'] else ''
        params={'action':'query','format':'json','generator':'search','gsrsearch':'"'+place['name']+'" '+city,
                'gsrnamespace':6,'gsrlimit':8,'prop':'imageinfo','iiprop':'url|extmetadata','iiurlwidth':1200}
        data=self._get('wikimedia_commons','https://commons.wikimedia.org/w/api.php',params,budget)
        pages=data.get('query',{}).get('pages',{})
        if not pages:
            generic={'the','coffee','cafe','co','company','house','restaurant','bar','bistro','pub'}
            distinctive=[word for word in identity_text(place['name']).split() if word not in generic]
            if distinctive:
                params['gsrsearch']=' '.join(distinctive)+' '+city
                data=self._get('wikimedia_commons','https://commons.wikimedia.org/w/api.php',params,budget)
                pages=data.get('query',{}).get('pages',{})
        if not isinstance(pages,dict):return []
        candidates=[]
        for page in list(pages.values())[:8]:
            image=next(iter(page.get('imageinfo') or []),{})
            meta=image.get('extmetadata') or {}
            value=lambda name:plain((meta.get(name) or {}).get('value',''))
            info=license_info((meta.get('LicenseUrl') or {}).get('value',''))
            text=' '.join([page.get('title',''),*[value(key) for key in ('ImageDescription','ObjectName','Categories')]])
            creator=value('Artist')
            if not info or info['requires_attribution'] and not creator:continue
            if value('Restrictions'):continue
            candidates.append({**info,'image_url':image.get('thumburl') or image.get('url',''),
                'source_url':image.get('descriptionurl',''),'attribution':creator or 'Public domain',
                'caption':text,'identity_text':text,'provider':'wikimedia_commons',
                'permission_basis':'commons_file_license','permission_source_url':image.get('descriptionurl','')})
        return candidates

    def _openverse(self,place,budget):
        city=place['address'].split(',')[1].strip() if ',' in place['address'] else ''
        data=self._get('openverse','https://api.openverse.org/v1/images/',
            {'q':place['name']+' '+city,'license':'by,by-sa,cc0,pdm','page_size':8,'mature':'false'},budget)
        candidates=[]
        results=data.get('results',[])
        if not isinstance(results,list):return []
        for item in results[:8]:
            info=license_info(item.get('license_url'))
            creator=plain(item.get('creator'))
            if not info or info['requires_attribution'] and not creator:continue
            tags=' '.join(plain(tag.get('name','')) for tag in item.get('tags',[]) if isinstance(tag,dict))
            candidates.append({**info,'image_url':item.get('url',''),'source_url':item.get('foreign_landing_url',''),
                'attribution':creator or 'Public domain','caption':plain(item.get('title')),
                'identity_text':plain(item.get('title'))+' '+tags,'provider':'openverse',
                'permission_basis':'original_photo_license','permission_source_url':item.get('foreign_landing_url','')})
        return candidates

    def _verify_original(self,candidate,place):
        if candidate['provider']=='wikimedia_commons':
            matched=self._match(place,candidate['identity_text'])
            if matched:candidate['identity_basis']=matched
            return bool(matched)
        # Openverse is a discovery index. Verify its license against the actual
        # photo page, rather than trusting a search result's license label.
        resource=self.fetcher.fetch(candidate['source_url'])
        if 'html' not in resource.content_type:return False
        soup=BeautifulSoup(resource.body,'html.parser')
        licenses=website_photo_licenses(soup,resource.url,self.fetcher)
        from .visuals import image_identity
        declared=licenses.get(image_identity(candidate['image_url'],resource.url))
        if not declared or declared['license_url']!=candidate['license_url']:return False
        parts=[candidate['identity_text']]
        for node in soup.find_all('meta',attrs={'property':re.compile(r'^og:(?:title|description)$')},limit=4):parts.append(node.get('content',''))
        matched=self._match(place,' '.join(parts))
        if matched:candidate['identity_basis']=matched
        return bool(matched)

    def _candidates(self,provider,place,budget):
        if self.unavailable_until.get(provider,0)>time.time():raise SourceError(provider+' photo search is temporarily unavailable')
        key=hashlib.sha256(('licensed-venue-v2:'+provider+json.dumps({k:place[k] for k in ('name','address')},sort_keys=True).casefold()).encode()).hexdigest()
        if self.store:
            with self.store.connect() as db:
                row=db.execute("SELECT pages FROM venue_photo_searches WHERE cache_key=? AND status='ready' AND expires_at>?",(key,time.time())).fetchone()
            if row:return json.loads(row['pages'])
        try:candidates=(self._commons if provider=='wikimedia_commons' else self._openverse)(place,budget)
        except SourceError:
            self.unavailable_until[provider]=time.time()+300
            raise
        if self.store:
            with self.store.connect() as db:
                db.execute("INSERT INTO venue_photo_searches(cache_key,status,pages,expires_at) VALUES(?,'ready',?,?) ON CONFLICT(cache_key) DO UPDATE SET status='ready',pages=excluded.pages,expires_at=excluded.expires_at",(key,json.dumps(candidates),time.time()+(86400*7 if candidates else 43200)))
        return candidates

    def gallery(self,places,budget=None,limit=3):
        from .visuals import decode_image
        self.last_report=[]
        for place in places[:3]:
            if not place.get('name') or not place.get('address') or place.get('identity_known') is False:continue
            gallery=[];seen=set()
            for provider in ('wikimedia_commons','openverse'):
                try:candidates=self._candidates(provider,place,budget)
                except SourceError as exc:
                    self.last_report.append({'provider':provider,'status':'unavailable','detail':str(exc)});continue
                matched=0
                for candidate in candidates[:8]:
                    try:
                        if not safe_url(candidate['image_url']) or not safe_url(candidate['source_url']):continue
                        if re.search(r'\b(?:logo|map|menu|avatar|icon)\b',candidate['caption'],re.I):continue
                        if not self._verify_original(candidate,place):continue
                        matched+=1
                        resource=self.fetcher.fetch(candidate['image_url'])
                        if not resource.content_type.startswith('image/'):continue
                        data=decode_image(resource.body,max_edge=1200)
                        digest=hashlib.sha256(data).hexdigest()
                        if digest in seen:continue
                        seen.add(digest)
                        visual={k:v for k,v in candidate.items() if k not in ('identity_text','image_url','requires_attribution')}
                        visual.update(kind='source',status='ready',image_source_url=candidate['image_url'],
                            caption=place['name']+' · real venue photo',source_role='venue_licensed_photo',
                            rights_status='recognized_reuse_license',modifications='Resized for display',cache_policy='stored')
                        gallery.append((visual,data))
                    except SourceError:continue
                    if len(gallery)>=min(limit,3):break
                self.last_report.append({'provider':provider,'candidates':len(candidates),'identity_matches':matched,'saved':len(gallery)})
                if gallery:return gallery
        return []
