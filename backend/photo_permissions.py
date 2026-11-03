"""Recognize explicit publisher grants for editorial photo reuse.

This is intentionally a small parser for unambiguous grants, not a model making
legal assumptions. Keep the actual permission, source, scope and restrictions.
"""
import re
from urllib.parse import urljoin,urlsplit

from bs4 import BeautifulSoup

from .documents import normalize
from .pipeline import safe_url
from .retrieval import SourceError

PHOTO=r'(?:photographs?|photos?|images?|pictures?)'
USE=r'(?:used|reproduced|republished|published|downloaded|distributed)'
GRANT=re.compile(r'\b'+PHOTO+r'\b.{0,180}?(?:may\s+be|can\s+be|are\s+(?:freely\s+)?(?:available|provided)|is\s+(?:freely\s+)?(?:available|provided)|are\s+free|is\s+free|permission\s+is\s+granted).{0,120}?\beditorial\b|\b(?:you\s+)?may\s+(?:download|use|reproduce|publish)\s+(?:these|the\s+following|our)\s+'+PHOTO+r'\b.{0,140}?\beditorial\b',re.I)
GATED=re.compile(r'(?:prior|written|express|explicit)\s+(?:written\s+)?(?:permission|approval|consent)|(?:request|obtain|seek|requires?|subject\s+to)\s+(?:our\s+|the\s+|a\s+|written\s+|prior\s+)*(?:permission|approval|authorization)|\bon\s+request\b|\bupon\s+approval\b|\baccredited\b|\bregistered\s+(?:press|media|journalists?)\b|\bcase.by.case\b',re.I)
INCOMPATIBLE=re.compile(r'\bnon[- ]?commercial\b|\b(?:no|not|never)\s+(?:be\s+)?(?:download(?:ed|ing)?|stor(?:ed|age)|cach(?:ed|ing)|rehost(?:ed|ing)?|archiv(?:ed|ing)|alter(?:ed|ations?)|resiz(?:ed|ing))\b|\b(?:may|must|can)\s+not\s+be\s+'+USE+r'|\b(?:expires?|expiration|valid\s+until|time.limited)\b|\bthird.party\b|\bexcept(?:ions?|\s+where)\b',re.I)


def text_of(node):
    return normalize(node.get_text(' ',strip=True))


def editorial_permission(text,url,*,scope,creator='',image_urls=()):
    """A link or the label 'press kit' alone never establishes permission."""
    text=normalize(text)
    if re.search(r'\b(?:do\s+not|don.t|may\s+not|must\s+not)\s+(?:use|download|reproduce|publish|store).{0,90}\bwithout\b',text,re.I):return None
    conditions=re.sub(r'\bwithout\s+(?:(?:any|prior|written|express)\s+)*(?:permission|approval)\b|\bno\s+(?:(?:prior|written|express)\s+)*(?:permission|approval)\s+(?:is\s+)?(?:required|necessary)\b','',text,flags=re.I)
    if not GRANT.search(text) or GATED.search(conditions) or INCOMPATIBLE.search(text):return None
    if re.search(r'\b(?:permission|approval|authorization|consent)\s+(?:(?:is|may\s+be|must\s+be)\s+)?(?:required|necessary|needed)\b|\b(?:fees?|payment|purchase)\s+(?:(?:is|are)\s+)?(?:required|necessary|appl(?:y|ies))\b|\bmust\s+(?:first\s+)?(?:contact|register|request|sign\s+in)\b',conditions,re.I):return None
    if re.search(r'\b(?:approved|professional)\s+(?:press|media|journalists?)\b|\b(?:example|hypothetical|sample\s+wording)\b',text,re.I):return None
    if re.search(r'\b(?:cannot|can.t|mustn.t)\s+(?:be\s+)?(?:used|stored|downloaded|rehosted|archived)|\bnot\s+(?:freely\s+)?(?:available|permitted|allowed|authorized)\b',text,re.I):return None
    # The declaration must identify an allowed act, not only say 'editorial'.
    if not re.search(r'\b'+USE+r'\b|\b(?:download|use|reproduce|publish)\s+(?:these|the\s+following|our)\b|\bavailable\s+for\b|\bfree\s+for\b',text,re.I):return None
    if scope=='page' and not re.search(r'\b(?:all|these|following|provided|included)\s+'+PHOTO+r'\b|\b'+PHOTO+r'\s+(?:in|on|from)\s+(?:this|the|our)\s+(?:press|media|photo|image|page|gallery)',text,re.I):return None
    if scope=='site' and not re.search(r'\ball\s+(?:of\s+the\s+)?'+PHOTO+r'\s+on\s+(?:this|our)\s+(?:web\s*site|site)\b',text,re.I):return None
    credit=re.search(r'\b(?:photo\s+)?credit(?:\s+(?:must\s+be\s+given\s+to|should\s+be\s+given\s+to|to))?\s*:\s*["\u201c]?([^.;\n"]{2,180})',text,re.I)
    if not credit:credit=re.search(r'\bcredit\s+(?:must\s+be\s+given\s+to|should\s+be\s+given\s+to|to|as)\s+["\u201c]?([^.;\n"]{2,180})',text,re.I)
    if not credit:credit=re.search(r'\b(?:[Pp]lease\s+)?[Cc]redit\s+(?!is\b|required\b|must\b|the\s+source\b)([A-Z][^.;\n]{1,120})',text)
    required=re.search(r'\bcredit\b|\battribution\b',text,re.I)
    if required and not credit and re.search(r'\bcredit\s+(?:must|should|to|as|line)\b',text,re.I):return None
    attribution=normalize(credit[1]).rstrip('"\u201d') if credit else normalize(creator)
    if required and not attribution:return None
    if not attribution:attribution='Source: '+(urlsplit(url).hostname or 'publisher')
    return {'license_url':url,'license_name':'Editorial use permission','requires_attribution':bool(required),
            'attribution':attribution,'permission_source_url':url,'permission_basis':'explicit_editorial_permission',
            'permission_quote':text[:1600],'permission_scope':scope,'rights_status':'documented_editorial_permission',
            'usage_restrictions':['editorial_use_only'], 'permission_image_urls':list(image_urls)[:30]}


class EditorialPhotoPermissions:
    """Bounded, same-site permission-page reads; no credentials or form submits."""
    def __init__(self,fetcher):
        self.fetcher=fetcher
        self.pages={}
        self.report=[]

    def _page(self,url):
        if url not in self.pages:
            if len(self.pages)>=4:return None
            try:
                resource=self.fetcher.fetch(url)
                if 'html' not in resource.content_type:return None
                self.pages[url]=(BeautifulSoup(resource.body,'html.parser'),resource.url)
            except SourceError:
                self.pages[url]=None
        return self.pages[url]

    @staticmethod
    def _grants(soup,url,scope,creator=''):
        # Evaluate complete permission paragraphs, including their conditions.
        candidates=soup.find_all(['p','li','figcaption','blockquote'],limit=250)
        if not candidates:candidates=[soup]
        for node in candidates:
            text=text_of(node)
            if len(text)>2500 or not GRANT.search(text):continue
            # Restrictions sometimes follow in the next paragraph.
            following=node.find_next_sibling(['p','li','ul','ol'])
            if following and re.search(r'permission|approval|required|licens|must|only|prohibit|credit|attribution|not\b',text_of(following),re.I):
                if len(text_of(following))>=800:continue
                text+=' '+text_of(following)
            grant=editorial_permission(text,url,scope=scope,creator=creator)
            if grant:return grant
        return None

    def image(self,license_url,image_url,creator):
        page=self._page(license_url)
        if not page:return None
        soup,url=page
        grant=self._grants(soup,url,'image',creator)
        if grant:grant['permission_image_urls']=[image_url]
        return grant

    def inline(self,soup,base):
        from .visuals import image_identity
        found={}
        # Text inside a figure applies to that figure, not other page photos.
        for figure in soup.find_all('figure',limit=60):
            creator=figure.find(attrs={'itemprop':'creator'})
            grant=editorial_permission(text_of(figure),base,scope='image',creator=text_of(creator) if creator else '')
            if not grant:continue
            for image in figure.find_all('img',limit=10):
                target=safe_url(urljoin(base,image.get('data-src') or image.get('src','')))
                if target:found[image_identity(target,base)]={**grant,'permission_image_urls':[target]}
        # A page-wide grant has to explicitly cover its included images.
        grant=self._grants(soup,base,'page')
        if grant and re.search(r'\b(?:our|the)\s+(?:press|media)\s+kit\b',grant['permission_quote'],re.I):
            heading=soup.find('h1') or soup.title
            page_label=(text_of(heading) if heading else '')+' '+urlsplit(base).path
            if not re.search(r'press|media',page_label,re.I):grant=None
        if grant:
            for image in soup.find_all('img',limit=100):
                if image.find_parent(['nav','header','footer','aside']):continue
                target=safe_url(urljoin(base,image.get('data-src') or image.get('src','')))
                if target:found[image_identity(target,base)]={**grant,'permission_image_urls':[target]}
        return found

    def site(self,soup,base):
        # Only an explicit site-wide photo grant applies from a separate terms
        # page. A license for a different press gallery cannot license this page.
        host=(urlsplit(base).hostname or '').removeprefix('www.')
        for anchor in soup.find_all('a',href=True,limit=300):
            target=safe_url(urljoin(base,anchor['href']))
            if not target or (urlsplit(target).hostname or '').removeprefix('www.')!=host:continue
            label=text_of(anchor)+' '+urlsplit(target).path
            if not re.search(r'photo.{0,15}(?:usage|licens|permission)|image.{0,15}(?:usage|licens|permission)|terms.of.use|media.policy|copyright',label,re.I):continue
            page=self._page(target)
            if not page:continue
            terms,url=page
            grant=self._grants(terms,url,'site')
            self.report.append({'url':url,'permitted':bool(grant)})
            if grant:return grant
        return None
