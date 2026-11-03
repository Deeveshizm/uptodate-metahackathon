"""Source visual first; an explicitly labeled generated illustration is fallback."""
import io
import json
import os
import re
import warnings
import hashlib
from dataclasses import replace
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

from bs4 import BeautifulSoup
from PIL import Image

from .documents import content_root, primary_source_urls
from .pipeline import safe_url
from .retrieval import SourceError
from .artifacts import GenerationPending


def reusable_license(url):
    parts = urlsplit(url)
    return parts.hostname in ("creativecommons.org", "www.creativecommons.org") and (
        parts.path.startswith("/licenses/by/") or parts.path.startswith("/publicdomain/zero/")
    )


BRANDING = re.compile(r"\b(?:logo|logotype|wordmark|masthead|brandmark|favicon|avatar)\b", re.I)


def image_identity(value, base):
    url = safe_url(urljoin(base, value))
    if not url:
        return None
    parts = urlsplit(url)
    # Keep asset-identifying query values on image proxies; ignore renditions.
    query = sorted((k,v) for k,v in parse_qsl(parts.query) if k.lower() not in
                   ('w','width','h','height','q','quality','dpr','fit','crop','auto','fm','format','resize'))
    return parts.netloc.lower(), parts.path, tuple(query)


def branding_images(soup, base):
    result, nodes = set(), []
    for script in soup.find_all('script', attrs={'type':'application/ld+json'}, limit=30):
        try:
            pending = [json.loads(script.string or script.get_text())]
        except (ValueError,TypeError):
            continue
        for _ in range(500):
            if not pending:
                break
            node = pending.pop()
            if isinstance(node,dict):
                nodes.append(node)
                pending.extend(v for v in node.values() if isinstance(v,(dict,list)))
            elif isinstance(node,list):
                pending.extend(node[:100])
    by_id = {n['@id']:n for n in nodes if isinstance(n.get('@id'),str) and (n.get('url') or n.get('contentUrl'))}
    def add(value):
        for _ in range(4):
            if isinstance(value,dict):
                value = by_id.get(value.get('@id'),value)
                value = value.get('url') or value.get('contentUrl') or value.get('@id','')
            elif isinstance(value,str) and value in by_id:
                value = by_id[value].get('url') or by_id[value].get('contentUrl','')
            else:
                break
        if isinstance(value,str) and value and value not in by_id and not value.startswith('#'):
            key = image_identity(value,base)
            if key:result.add(key)
    for node in nodes:
        logo = node.get('logo')
        for value in logo if isinstance(logo,list) else [logo]:
            add(value)
    for image in soup.find_all('img',limit=200):
        label = ' '.join(str(image.get(k,'')) for k in ('alt','title','class','id'))
        if BRANDING.search(label) or image.find_parent(['nav','header','footer']):
            add(image.get('data-src') or image.get('src',''))
    for icon in soup.find_all('link',rel=lambda value:value and 'icon' in str(value).lower(),limit=20):
        add(icon.get('href',''))
    return result


def html_figures(resource, document_license, metadata=None):
    soup = BeautifulSoup(resource.body, "html.parser")
    if metadata is None:
        from .documents import source_metadata
        metadata=source_metadata(soup,resource.url)
    recipe=metadata.get('recipe') or {}
    main = content_root(soup)
    branding = branding_images(soup,resource.url)
    figures, seen = [], set()
    for image in main.find_all(["img", "object", "embed", "svg"], limit=50):
        ancestry = list(image.parents)
        attributes = " ".join(str(node.get("class", "")) + " " + str(node.get("id", "")) for node in [image, *ancestry[:6]])
        if any(node.name in ("nav", "header", "footer", "aside", "form") for node in ancestry) or re.search(r"(?:^|[\W_])(?:ad|ads|advertisement|sponsored|sponsor|promo|related|recommendations|subscribe|avatar|logo|wordmark)(?:$|[\W_])", attributes, re.I):
            continue
        if re.search(r'(?:^|[\W_])(?:affiliate|tasty-links?|tasty-link-card|product-card|shop-the-post)(?:$|[\W_])',attributes,re.I):
            continue
        if recipe and re.search(r'(?:^|[\W_])(?:equipment|cooking-tools|recommended-products)(?:$|[\W_])',attributes,re.I):
            continue
        if image.find_parent('a',rel=lambda rel:rel and 'sponsored' in rel):
            continue
        inline_svg = image.name == "svg"
        if image.name in ("object", "embed") and not image.find_parent("figure"):
            continue
        raw = image.get("data-src") or image.get("data-lazy-src") or image.get("nitro-lazy-src") or image.get("data") or image.get("src", "")
        url = resource.url + "#" + str(image.find_parent("figure").get("id", "figure")) if inline_svg and image.find_parent("figure") else safe_url(urljoin(resource.url, raw))
        if not url or url in seen or image_identity(url,resource.url) in branding or re.search(r"logo|avatar|tracking|pixel|favicon|badge", url, re.I):
            continue
        seen.add(url)
        figure = image.find_parent("figure")
        caption_node = figure.find("figcaption") if figure else None
        caption = caption_node.get_text(" ", strip=True) if caption_node else image.get("alt", "")
        if BRANDING.search(caption):
            continue
        uncaptioned_photo = False
        if len(caption) < 15:
            # Editorial photos often have empty alt text (notably Substack).
            # Require a substantial image inside article content, rejecting
            # avatars, wordmarks and narrow decorative separators by dimensions.
            try:
                width, height = int(image.get("width", 0)), int(image.get("height", 0))
            except (ValueError, TypeError):
                width, height = 0, 0
            if image.name != "img" or min(width, height) < 300 or max(width, height) / min(width, height) > 3:
                continue
            title = next((heading for heading in soup.find_all("h1") if heading.get_text(strip=True)), soup.title)
            caption = "Source photo · " + (title.get_text(" ", strip=True) if title else "Original article")
            uncaptioned_photo = True
        license_node = figure.find("a", rel=lambda x: x and "license" in x) if figure else None
        license_url = safe_url(urljoin(resource.url, license_node.get("href", ""))) if license_node else document_license
        # A page-wide license does not override a third-party credit or exception.
        if not license_node and re.search(r"courtesy|all rights reserved|used with permission|copyright|©|third.party", caption, re.I):
            license_url = ""
        candidate = {"id": f"html-{len(figures) + 1}", "url": url, "caption": caption[:1000],
                     "license_url": license_url, "page": None}
        heading=image.find_previous(['h2','h3'])
        if heading:candidate['section']=heading.get_text(' ',strip=True)
        if uncaptioned_photo:
            candidate["selection_basis"] = "article_photo_dimensions"
            candidate["caption_origin"] = "article_title"
        if inline_svg:
            candidate["svg_data"] = str(image).encode()
        figures.append(candidate)
    hero = soup.find("meta", attrs={"property": "og:image"})
    kind = soup.find("meta", attrs={"property": "og:type"})
    if hero and (main.name == "article" or (kind and kind.get("content") == "article")):
        url = safe_url(urljoin(resource.url, hero.get("content", "")))
        alt = soup.find('meta',attrs={'property':'og:image:alt'})
        if url and image_identity(url,resource.url) not in branding and not BRANDING.search(alt.get('content','') if alt else '') and url not in {figure['url'] for figure in figures} and not re.search(r"logo|avatar|tracking|pixel|favicon|wordmark|badge", url, re.I):
            title = next((heading for heading in soup.find_all("h1") if heading.get_text(strip=True)), soup.title)
            figures.append({"id": "article-hero", "url": url, "caption": "Source photo · " + (title.get_text(" ", strip=True) if title else "Original article"),
                            "license_url": document_license, "page": None, "selection_basis": "article_hero_metadata", "caption_origin": "article_title"})
    for index,value in enumerate(recipe.get('images',[])[:1]):
        url=safe_url(urljoin(resource.url,value))
        if not url or image_identity(url,resource.url) in branding:continue
        existing=next((figure for figure in figures if figure['url']==url),None)
        if existing:
            existing['selection_basis']='recipe_hero_metadata'
        else:
            figures.append({'id':f'recipe-hero-{index}','url':url,'caption':'Finished dish · '+recipe.get('name','Recipe'),
                'license_url':document_license,'page':None,'selection_basis':'recipe_hero_metadata'})
    return figures


def rasterize_svg(data):
    """Render original vector artwork, without scripts or external resources."""
    import pymupdf
    if len(data) > 2_000_000 or re.search(br"<!ENTITY|<\s*(?:script|foreignobject)\b", data, re.I):
        raise SourceError("Source vector requires unsupported markup; inspect the linked PDF")
    clean = re.sub(br"<!DOCTYPE[^>]*>", b"", data, flags=re.I)
    try:
        root = ET.fromstring(clean)
        if root.tag.rsplit("}", 1)[-1].lower() != "svg" or sum(1 for _ in root.iter()) > 50_000:
            raise ValueError("Invalid vector document")
        for node in root.iter():
            for key, value in node.attrib.items():
                key = key.rsplit("}", 1)[-1].lower()
                if key.startswith("on") or (key in ("href", "src") and not value.startswith(("#", "data:image/png;base64,", "data:image/jpeg;base64,"))):
                    raise ValueError("External vector resource")
            style = " ".join(node.attrib.values()) + (node.text or "" if node.tag.rsplit("}", 1)[-1] == "style" else "")
            targets = re.findall(r"url\(([^)]*)\)", style, re.I)
            if re.search(r"@import", style, re.I) or any(not target.strip().strip("'\"").startswith("#") for target in targets):
                raise ValueError("External vector style")
        with pymupdf.open(stream=clean, filetype="svg") as vector:
            page = vector[0]
            if page.rect.width <= 0 or page.rect.height <= 0:
                raise ValueError("Empty vector")
            scale = min(2.0, 1600 / max(page.rect.width, page.rect.height))
            return decode_image(page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png"))
    except SourceError:
        raise
    except Exception:
        raise SourceError("Source vector could not be rendered; inspect the linked PDF") from None


def pdf_figures(resource):
    import pymupdf
    with pymupdf.open(stream=resource.body, filetype="pdf") as pdf:
        metadata_text = (pdf.get_xml_metadata() or "") + " " + str(pdf.metadata)
        first_page = pdf[0].get_text() if len(pdf) else ""
        matches = re.findall(r"https?://(?:www\.)?creativecommons\.org/[^\s<>\"']+", metadata_text + " " + first_page)
        license_url = next((url.rstrip(".,)") for url in matches if reusable_license(url.rstrip(".,)"))), "")
        figures = []
        for index in range(min(len(pdf), 25)):
            page = pdf[index]
            text = page.get_text()
            captions = re.findall(r"(?:Figure|Fig\.)\s*\d+[^\n]*(?:\n(?!\s*\n)[^\n]+)?", text, re.I)
            if not captions:
                continue
            # Discovery/preview is separate from permission to redistribute.
            # A third-party credit must not inherit the document's license.
            figure_license = "" if re.search(r"courtesy|all rights reserved|used with permission|©", " ".join(captions), re.I) else license_url
            # Render the complete source page so vector figures and their labels
            # are retained. Never guess a crop that could cut off a method block.
            scale = min(2.0, 1600 / max(page.rect.width, page.rect.height))
            image = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=False).tobytes("png")
            figures.append({"id": f"pdf-page-{page.number + 1}", "url": resource.url,
                            "caption": "Source page containing " + " ".join(captions)[:850],
                            "license_url": figure_license, "page": page.number + 1, "image_data": image})
            if len(figures) >= 4:
                break
        return figures, license_url


def decode_image(data, max_edge=1600):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.width < 240 or image.height < 160 or image.width * image.height > 20_000_000:
                    raise SourceError("Source image is too small or exceeds the pixel limit")
                image.seek(0)
                image = image.convert("RGB")
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format="PNG")
                return output.getvalue()
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError("Source visual is not a valid supported image") from exc


def words(text):
    stop = {"with", "from", "that", "this", "into", "their", "they", "have", "been", "will", "which", "source", "figure", "image", "paper", "using", "used"}
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in stop}


def rank_figures(document, draft):
    context = words(document.title + " " + draft["title"] + " " + draft["finding"])
    story = words(draft['title'] + ' ' + draft['finding'])
    paper = (draft.get('analysis') or {}).get('content_type') == 'paper' or bool(document.metadata.get('abstract') or document.metadata.get('doi'))
    recipe = (draft.get('analysis') or {}).get('content_type') == 'recipe' or bool(document.metadata.get('recipe'))
    eligible = []
    for figure in document.figures:
        # An unknown license is NOT evidence that the source has no image.
        # Keep rights metadata for review, independently of visual selection.
        caption = figure["caption"]
        if BRANDING.search(caption) or re.search(r'logo|wordmark|favicon|avatar',figure.get('url',''),re.I):
            continue
        overlap = len(words(caption) & context)
        methodological = bool(re.search(r"architectur|method|framework|pipeline|schematic|overview|diagram", caption, re.I))
        generic_method = paper and methodological and len(caption.split()) <= 12 and not re.search(r'baseline|prior work|related work',caption,re.I)
        if overlap < 2 and not generic_method and figure.get("selection_basis") not in ("article_photo_dimensions", "article_hero_metadata", "recipe_hero_metadata", "email_photo_reference", "venue_website_photo"):
            continue
        primary_method = paper and methodological and not re.search(r'baseline|prior work|related work',caption,re.I)
        recipe_hero=(2 if figure.get('selection_basis')=='recipe_hero_metadata' else 1 if figure.get('selection_basis')=='article_hero_metadata' else 0) if recipe else 0
        eligible.append(((bool(primary_method),recipe_hero,overlap + 2 * len(words(caption) & story)), figure))
    return [figure for _, figure in sorted(eligible, key=lambda item: item[0], reverse=True)]


primary_visual_urls = primary_source_urls


def source_visual(document, draft, fetcher, allow_pdf=True):
    for candidate in rank_figures(document, draft)[:4]:
        try:
            data = candidate.get("image_data")
            source_format = "image"
            if candidate.get("svg_data") is not None:
                data = rasterize_svg(candidate["svg_data"])
                source_format = "svg"
            elif data is None:
                resource = fetcher.fetch(candidate["url"])
                if not resource.content_type.lower().startswith("image/"):
                    continue
                if "svg" in resource.content_type.lower() or urlsplit(candidate["url"]).path.lower().endswith(".svg"):
                    data = rasterize_svg(resource.body)
                    source_format = "svg"
                else:
                    data = resource.body
            data = decode_image(data)
            license_url = candidate.get("license_url", "")
            rights_review = not reusable_license(license_url)
            return {"kind": "source", "status": "ready", "caption": candidate["caption"],
                    "source_url": document.url, "image_source_url": candidate["url"],
                    "attribution": ("Photo linked in source email · " + (urlsplit(candidate['url']).hostname or 'source')) if document.metadata.get('format')=='email' else document.metadata.get("author") or document.title,
                    "license_url": license_url, "page": candidate.get("page"),
                    "license_name": document.metadata.get("license_name", "") if license_url == document.metadata.get("license_url") else "",
                    "rights_status": "needs_review" if rights_review else "recognized_reuse_license",
                    "requires_rights_review": rights_review,
                    "selection": candidate.get('selection_basis','caption_relevance'), "source_format": source_format}, data
        except SourceError:
            continue
    pdf_url = document.metadata.get("pdf_url")
    if allow_pdf and pdf_url and pdf_url != document.url:
        try:
            from .documents import parse_document
            pdf = parse_document(fetcher.fetch(pdf_url))
            selected = source_visual(pdf, draft, fetcher, allow_pdf=False)
            if selected:
                visual, data = selected
                return {**visual, "source_url": document.url, "source_format": "pdf",
                        "attribution": document.metadata.get("author") or visual["attribution"]}, data
        except SourceError:
            pass
    return None


def choose_visual(document, draft, fetcher, illustrator=None, stage=lambda _: None, text_model=None, budget=None, photo_search=None, google_photos=None, venue_photos_checked=False, licensed_photos=None):
    from .licensed_venue_photos import venue_photo_mode
    if venue_photo_mode()!='google' and draft.get('analysis',{}).get('action',{}).get('kind')=='places':
        if not venue_photos_checked:
            gallery=source_gallery(document,draft,fetcher,stage,photo_search=photo_search,google_photos=google_photos,licensed_photos=licensed_photos)
            if gallery:return gallery[0]
        # The entire real-photo chain has finished. Do not run a second,
        # differently ordered lookup before the final illustration fallback.
        document=replace(document,figures=[],links=[])
        venue_photos_checked=True
    # Prefer the paper/original linked by a secondary article before using its
    # own hero image. No recursive discovery, signup, or arbitrary site crawl.
    checks = []
    for url in primary_visual_urls(document):
        stage("checking_primary_source_visual")
        try:
            from .documents import parse_document
            original = parse_document(fetcher.fetch(url))
            selected = source_visual(original, draft, fetcher)
            if selected:
                visual, data = selected
                return {**visual, "source_role": "primary", "article_url": document.url}, data
            checks.append({"url": original.url, "status": "no_usable_visual"})
        except SourceError as exc:
            checks.append({"url": url, "status": "unavailable", "detail": str(exc)})
    stage("selecting_source_visual")
    selected = source_visual(document, draft, fetcher)
    if selected:
        visual, data = selected
        return {**visual, "source_role": "article", "primary_source_checks": checks}, data
    venues = [] if venue_photos_checked else google_venue_gallery(draft,stage,google_photos)
    if not venue_photos_checked and not venues:
        venues=linked_venue_gallery(document,draft,fetcher,stage)
    if not venue_photos_checked and not venues and document.metadata.get('venue_reference_pages'):
        venues = linked_venue_gallery(document, draft, fetcher, stage, pages=document.metadata['venue_reference_pages'])
    if not venue_photos_checked and not venues:
        venues = searched_venue_gallery(document,draft,fetcher,stage,photo_search)
    if venues:
        return venues[0]
    style = os.environ.get("POST_IMAGE_STYLE", "Subject-led editorial art: choose a distinctive medium, natural or expressive colors, lighting and texture appropriate to this story; one clear focal subject, readable at small size")
    prompt = ("Editorial illustration of " + draft["title"] + ". " + draft["finding"] +
              "\nStyle: " + style + ". A single focused visual metaphor of this subject, generous whitespace, "
              "simple shapes, wordless artwork. Conceptual illustration, not a source figure or technical schematic. "
              "Only show relationships supported by the supplied context. No charts, numerical results, "
              "measured evidence, invented research architecture, logos, text, or labels.")
    try:
        planner = None
        if illustrator is None:
            provider = os.environ.get("IMAGE_GENERATION_PROVIDER", "meta")
            if provider != "meta":
                raise SourceError("IMAGE_GENERATION_PROVIDER must be meta")
            from .image_generation import MuseImageIllustrator
            planner = MuseImageIllustrator(budget=budget or getattr(text_model, "budget", None))
            prompt, image = planner.illustrate(draft, style, stage)
        else:
            stage("generating_fallback_visual")
            image = illustrator.generate(prompt)
        return {"kind": "generated", "status": "ready", "caption": "AI-generated editorial illustration · not a source figure",
                "attribution": "Generated with Muse Image" if getattr(planner, "model", None) == "muse-image-1.0" else "AI-generated editorial illustration",
                "prompt": prompt, "brief":getattr(planner,'last_brief',None), "planning_usage": getattr(planner, "last_usage", [])}, decode_image(image)
    except SourceError as exc:
        return {"kind": "pending", "status": "needs_image_generator", "caption": "Illustration pending",
                "detail": str(exc), "prompt": prompt}, None


def venue_address_text(value):
    from .documents import normalize
    from .venue_identity import STREET_ORDINALS
    aliases={'north':'n','south':'s','east':'e','west':'w','street':'st','road':'rd','avenue':'ave',
             'boulevard':'blvd','drive':'dr','lane':'ln','court':'ct','parkway':'pkwy','highway':'hwy'}
    aliases.update(STREET_ORDINALS)
    return ' '.join(aliases.get(word,word) for word in re.findall(r'[\w]+',normalize(value).casefold()))


def venue_street(address):
    street=address.split(',')[0]
    street=re.split(r'(?:#|\b(?:suite|ste|unit|apt)\.?\s*)\s*[\w-]+',street,maxsplit=1,flags=re.I)[0]
    return venue_address_text(street)


def structured_venue_photos(soup, places):
    """Use full-size photos declared by the exact business, never nearby listings."""
    from .venue_identity import matches_place
    found, matching = [], []
    pending = []
    for script in soup.find_all('script', type='application/ld+json', limit=25):
        try:
            pending.append(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue
    for _ in range(600):
        if not pending:break
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(node[:100]);continue
        if not isinstance(node, dict):continue
        pending.extend(value for value in node.values() if isinstance(value, (dict, list)))
        address = node.get('address')
        if not address or not isinstance(node.get('name'), str):continue
        identity = node['name'] + ' ' + (json.dumps(address) if isinstance(address, dict) else str(address))
        place = next((place for place in places if matches_place(place, identity)), None)
        if not place:continue
        matching.append(place)
        values = []
        for key in ('image', 'photo'):
            value = node.get(key, [])
            values.extend(value if isinstance(value, list) else [value])
        for value in values[:20]:
            if isinstance(value, dict):value = value.get('contentUrl') or value.get('url')
            if isinstance(value, str):found.append({'url':value, 'alt':place['name']})
    return matching, found


def venue_photo_candidates(soup, region, structured):
    candidates = list(structured)
    # Social-feed mirrors expose recent posts as a "gallery". Those can be
    # menus or notices, rather than photographs of the establishment itself.
    feed_text=region.get_text(' ',strip=True)
    dated_feed=len(re.findall(r'\b\d{1,2}/\d{1,2}/\d{4}\b',feed_text))>=3
    if region is soup and not dated_feed:
        hero = soup.find('meta', attrs={'property':'og:image'})
        if hero:candidates.append({'url':hero.get('content',''), 'alt':''})
    for node in region.find_all(['img','source'], limit=100):
        if any(parent.name in ('nav','header','footer','aside') for parent in node.parents):continue
        attributes = ' '.join(str(node.get(key,'')) for key in ('class','id','alt'))
        if BRANDING.search(attributes):continue
        # Public venue galleries often interleave hiring ads, event flyers and
        # food photos. Keep the nearby caption so an ad cannot become the cover.
        nearby=node.parent
        context=''
        for _ in range(4):
            if nearby is None or nearby is region or nearby.name in ('body','main','html'):break
            text=nearby.get_text(' ',strip=True)
            if len(text)>1600:break
            if text:context=text
            if len(text)>25:context=text;break
            nearby=nearby.parent
        description=node.get('alt','')+' '+context
        if dated_feed and not re.search(r'\b(?:interior|exterior|inside (?:our|the)|dining room|seating area|front of|storefront|patio|terrace|outdoor seating|view (?:of|from))\b',description,re.I):continue
        if re.search(r'\b(?:hiring|job openings?|open positions?|apply (?:now|today|in person)|help wanted|join our team|employment|gift cards?|coupon|discount code|ticket sales?|raffle|giveaway|featured drinks?|new shirt|shirt designs?|karaoke|goth night|trivia night|line.?up|this week at|this month at)\b',description,re.I):continue
        if re.search(r'\bclosed\s+(?:today|tomorrow|(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b|\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+at\b',description,re.I):continue
        parent = node.find_parent('a')
        if parent and re.search(r'\.(?:jpe?g|png|webp)(?:\?|$)', parent.get('href',''), re.I):
            candidates.append({'url':parent['href'], 'alt':node.get('alt','')})
        # Responsive and lazy-loaded markup often exposes only a tiny src.
        variants = []
        for srcset in (node.get('data-srcset',''),node.get('srcset','')):
            for entry in srcset.split(','):
                parts = entry.strip().split()
                if not parts:continue
                try:rank = float(parts[-1].rstrip('wx')) if len(parts)>1 else 0
                except ValueError:rank = 0
                if not re.search(r'[{}]',parts[0]):variants.append((rank,parts[0]))
        for _, url in sorted(set(variants), reverse=True)[:1]:
            candidates.append({'url':url, 'alt':node.get('alt','')})
        for key in ('data-src','data-original','data-lazy-src','nitro-lazy-src','src'):
            raw=node.get(key,'')
            if not raw:continue
            if '{width}' in raw:
                try:widths=json.loads(node.get('data-widths','[]'))
                except (ValueError,TypeError):widths=[]
                widths=[int(value) for value in widths if isinstance(value,(int,float)) and 240<=value<=1600] if isinstance(widths,list) else []
                raw=raw.replace('{width}',str(max(widths) if widths else 1024))
            if not re.search(r'[{}]',raw):candidates.append({'url':raw,'alt':node.get('alt','')})
    for node in region.select('[style*="background"]')[:30]:
        if any(parent.name in ('nav','header','footer','aside') for parent in [node,*node.parents]):continue
        if BRANDING.search(' '.join(str(node.get(key,'')) for key in ('class','id'))):continue
        for url in re.findall(r'url\(\s*[\"\']?([^\"\')]+)', node.get('style','')):
            candidates.append({'url':url.strip(), 'alt':node.get('aria-label','')})
    return candidates


def contains_multiple_venue_addresses(region):
    pattern=r'\b\d{1,6}\s+(?:[A-Za-z0-9.\'-]+\s+){1,7}?(?:St(?:reet)?|Rd|Road|Ave(?:nue)?|Blvd|Boulevard|Dr(?:ive)?|Ln|Lane|Ct|Court|Pkwy|Parkway|Hwy|Highway)\b'
    addresses=re.findall(pattern,region.get_text(' ',strip=True),re.I)
    return len({venue_address_text(value) for value in addresses})>1


def linked_venue_gallery(document, draft, fetcher, stage=lambda _: None, limit=4, pages=None, require_permission=False, follow_media=False):
    """Check explicit venue websites/verified photo pages, without rewriting email content."""
    places=draft.get('analysis',{}).get('action',{}).get('data',[])
    if draft.get('analysis',{}).get('action',{}).get('kind')!='places' or not places:return []
    from .email_content import map_link
    from .documents import Document,normalize
    from .venue_identity import matches_place
    from .photo_permissions import EditorialPhotoPermissions
    permission_lookup=EditorialPhotoPermissions(fetcher) if require_permission else None
    urls=list(pages) if pages is not None else [place['website_url'] for place in places if place.get('website_url')]+[link['url'] for link in document.links if not map_link(link['url'])]
    urls=list(dict.fromkeys(url for url in urls if safe_url(url) and not re.search(r'unsubscribe|confirm|activate|login|sign.?in',url,re.I)))[:4 if pages is not None else 2]
    visited=set()
    def follow_venue_pages(soup,resource):
        if pages is not None and not (require_permission or follow_media):return
        host=(urlsplit(resource.url).hostname or '').removeprefix('www.')
        for anchor in soup.find_all('a',href=True):
            target=safe_url(urljoin(resource.url,anchor['href']))
            if not target or (urlsplit(target).hostname or '').removeprefix('www.')!=host:continue
            label=anchor.get_text(' ',strip=True)+' '+urlsplit(target).path
            locator=re.search(r'locations?|find.?us|our.?cafes?|our.?stores?|coffee.?shops?|contact|photo.?gallery',label,re.I)
            media=(require_permission or follow_media) and re.search(r'press.?kit|media.?kit|press.?photos?|media.?gallery|press.?room',label,re.I)
            if (locator or media) and target not in urls:urls.append(target)
            if len(urls)>=5:break
    for url in urls:
        if url in visited:continue
        visited.add(url)
        if len(visited)>8:break
        stage('checking_venue_website_photos')
        try:
            resource=fetcher.fetch(url)
            if 'html' not in resource.content_type:continue
            soup=BeautifulSoup(resource.body,'html.parser')
            from .licensed_venue_photos import website_photo_licenses
            permissions=website_photo_licenses(soup,resource.url,fetcher,permission_lookup) if require_permission else {}
            if require_permission:
                document.metadata.setdefault('venue_photo_permission_checks',[]).append({'url':resource.url,'permitted_images':len(permissions)})
            text=normalize(soup.get_text(' ',strip=True)).casefold()
            address_text=' '+venue_address_text(text)+' '
            structured_matching, structured = structured_venue_photos(soup, places)
            matching=[place for place in places if matches_place(place,text)]
            matching.extend(place for place in structured_matching if place not in matching)
            if matching:
                document.metadata.setdefault('licensed_venue_identity_pages',{})[matching[0]['address']]=resource.url
            if not matching:
                # A homepage may delegate its address and pictures to Contact.
                follow_venue_pages(soup,resource)
                continue
            # On a multi-location page, use images from the matching address's
            # smallest card/section, rather than a different branch's hero.
            region=soup
            street=venue_street(matching[0].get('address',''))
            if street:
                regions=[]
                for node in soup.find_all(['address','article','section','div','li']):
                    if any(parent.name in ('nav','header','footer','aside') for parent in node.parents):continue
                    if not node.find(['img','source']) and not node.select_one('[style*="background"]'):continue
                    content=normalize(node.get_text(' ',strip=True))
                    if len(content)<=2500 and ' '+street+' ' in ' '+venue_address_text(content)+' ':
                        regions.append((len(content),node))
                if regions:region=min(regions,key=lambda item:item[0])[1]
            figures=[]
            # A brand homepage's product/hero photos cannot establish which
            # location is pictured. Require branch-scoped images on such pages.
            candidates=structured if contains_multiple_venue_addresses(region) else venue_photo_candidates(soup,region,structured)
            branding=branding_images(soup,resource.url)
            seen=set()
            for candidate in candidates:
                image_url=safe_url(urljoin(resource.url,candidate['url']))
                permission=permissions.get(image_identity(image_url,resource.url)) if image_url else None
                if require_permission and not permission:continue
                parts=urlsplit(image_url or '')
                from .licensed_venue_photos import venue_photo_mode
                host=(parts.hostname or '').lower()
                if venue_photo_mode()!='google' and (host.endswith(('.googleusercontent.com','.ggpht.com')) or host=='places.googleapis.com'):continue
                # A directory can repeat the correct business/address while
                # decorating it with an unrelated stock coffee-shop image.
                if host.endswith(('.unsplash.com','.pexels.com','.pixabay.com')) or host in ('picsum.photos','loremflickr.com','placehold.co','via.placeholder.com'):continue
                if re.search(r'(?:^|[/_.-])(?:default|placeholder|stock|categor(?:y|ies)|cuisines?|restaurantmenu|menu|no[-_]?image|coming[-_]?soon)(?:[/_.-]|$)',parts.path,re.I):continue
                image_path=re.sub(r'[-_]\d{2,4}x(?:\d{2,4})?(?=\.(?:png|jpe?g|webp)$)','',parts.path,flags=re.I)
                identity=urlunsplit((parts.scheme,parts.netloc,image_path,urlencode([(key,value) for key,value in parse_qsl(parts.query) if key.lower() not in ('w','h','width','height','q','quality','dpr','fit','crop','auto')]),''))
                if not image_url or identity in seen or image_identity(image_url,resource.url) in branding or re.search(r'logo|avatar|icon|badge|chatgpt|ai.generated|dall.e|midjourney',image_url+' '+candidate['alt'],re.I):continue
                if re.search(r'(?:facebook|instagram|twitter|linkedin)',parts.path+' '+candidate['alt'],re.I) and re.search(r'logo|icon|follow|share|social',parts.path+' '+candidate['alt'],re.I):continue
                if re.search(r'(?:^|[/_.-])(?:maps?|staticmap|openstreetmap|googlemaps)(?:[/_.-]|$)|maps\.(?:google|apple)|\b(?:street map|map of|directions map)\b', image_url+' '+candidate['alt'], re.I):continue
                seen.add(identity)
                figures.append({'id':'venue-photo-'+str(len(figures)), 'url':image_url,'caption':matching[0]['name']+' · photo from '+resource.url,
                                'license_url':permission['license_url'] if permission else '','page':None,'selection_basis':'venue_website_photo',
                                **({'reuse_permission':permission} if permission else {})})
            title=soup.title.get_text(' ',strip=True) if soup.title else matching[0]['name']
            source=Document(resource.url,title,[],metadata={'author':title},figures=figures)
            selected=[];seen_images=set()
            for figure in figures[:6]:
                value=source_visual(Document(source.url,source.title,[],metadata=source.metadata,figures=[figure]),draft,fetcher,allow_pdf=False)
                if value:
                    visual,image=value
                    permission=figure.get('reuse_permission')
                    if permission:
                        visual.update(permission)
                        visual.update(provider='venue_website',rights_status=permission.get('rights_status','recognized_reuse_license'),cache_policy='stored',modifications='Resized for display')
                    else:
                        visual.update(provider='venue_website',cache_policy='stored',modifications='Resized for display')
                    identity=hashlib.sha256(image).hexdigest()
                    if identity not in seen_images:
                        selected.append(({**visual,'source_role':'venue_website','photo_source_page':resource.url},image));seen_images.add(identity)
                if len(selected)>=limit:return selected
            if selected:return selected
            follow_venue_pages(soup,resource)
        except SourceError:
            # Retry a supplied legacy www/HTTP URL through its HTTPS host form.
            # PublicFetcher still validates DNS, redirects, robots and size.
            parts=urlsplit(url)
            if parts.scheme=='http' or (parts.hostname or '').startswith('www.'):
                alternate=urlunsplit(('https',parts.netloc.removeprefix('www.'),parts.path,parts.query,''))
                if alternate not in urls and alternate not in visited:urls.append(alternate)
            continue
    return []


def searched_venue_gallery(document,draft,fetcher,stage,photo_search,limit=4,require_permission=False,retry=True):
    action=draft.get('analysis',{}).get('action',{})
    if not photo_search or action.get('kind')!='places':return []
    stage('searching_real_venue_photos')
    try:
        pages=photo_search(action['data'])
    except GenerationPending:
        raise
    except SourceError:
        return []
    gallery=linked_venue_gallery(document,draft,fetcher,stage,limit,pages=pages,require_permission=require_permission)
    if gallery:return gallery
    if not retry:return []
    stage('retrying_real_venue_photo_search')
    try:
        recovered=photo_search(action['data'],recovery=True,exclude_urls=pages)
    except GenerationPending:
        raise
    except (SourceError,TypeError):
        return []
    return linked_venue_gallery(document,draft,fetcher,stage,limit,pages=recovered,require_permission=require_permission)


def google_venue_gallery(draft,stage,google_photos):
    action=draft.get('analysis',{}).get('action',{})
    if not google_photos or action.get('kind')!='places':return []
    stage('checking_google_maps_photos')
    try:return google_photos(action.get('data',[]))
    except SourceError:return []


def venue_page_groups(document, draft):
    """Keep social/map pages out of the earlier official-website pass."""
    from .email_content import map_link
    places=draft.get('analysis',{}).get('action',{}).get('data',[])
    official=[place['website_url'] for place in places if place.get('website_url')]
    if not official:
        from .venue_enrichment import newsletter_fields
        website=newsletter_fields(document).get('website')
        if website:official.append(website)
    linked=[link['url'] for link in document.links]
    if not official:
        labelled=[link['url'] for link in document.links if re.search(r'\b(?:website|official site)\b',link.get('title','')+' '+link.get('context',''),re.I)]
        official=labelled or linked[:1]
    references=document.metadata.get('venue_reference_pages',[])+document.metadata.get('venue_photo_discovery_pages',[])
    def social(url):
        host=(urlsplit(url).hostname or '').lower().removeprefix('www.').removeprefix('m.')
        return host in ('facebook.com','fb.com','instagram.com')
    valid=list(dict.fromkeys(url for url in official+linked+references if safe_url(url) and not map_link(url)))
    return ([url for url in official if url in valid and not social(url)],
            [url for url in valid if not social(url)],
            [url for url in valid if social(url) and (urlsplit(url).hostname or '').lower().endswith('facebook.com')])


def source_first_venue_gallery(document,draft,fetcher,stage,limit,photo_search,google_photos,licensed_photos):
    official,references,social=venue_page_groups(document,draft)
    stage('checking_official_venue_photos')
    gallery=linked_venue_gallery(document,draft,fetcher,stage,limit,pages=official,follow_media=True)
    if gallery:return gallery
    stage('checking_press_and_reference_photos')
    gallery=linked_venue_gallery(document,draft,fetcher,stage,limit,pages=[url for url in references if url not in official],follow_media=True)
    if gallery:return gallery
    if licensed_photos:
        stage('checking_openly_licensed_venue_photos')
        places=[dict(place) for place in draft['analysis']['action'].get('data',[])]
        for place in places:
            page=document.metadata.get('licensed_venue_identity_pages',{}).get(place.get('address'))
            if page:place['identity_page_url']=page
        if len(places)==1 and not places[0].get('website_url') and official:places[0]['website_url']=official[0]
        try:gallery=licensed_photos(places)
        except SourceError:gallery=[]
        if gallery:return gallery
    if photo_search:
        stage('searching_official_venue_website')
        try:pages=photo_search(draft['analysis']['action']['data'])
        except GenerationPending:raise
        except SourceError:pages=[]
        document.metadata['venue_photo_discovery_pages']=pages
        _,discovered,facebook=venue_page_groups(document,draft)
        gallery=linked_venue_gallery(document,draft,fetcher,stage,limit,pages=[url for url in discovered if url not in references],follow_media=True)
        if gallery:return gallery
        social=list(dict.fromkeys(social+facebook))
        if pages:
            stage('retrying_real_venue_photo_search')
            try:recovered=photo_search(draft['analysis']['action']['data'],recovery=True,exclude_urls=pages)
            except GenerationPending:raise
            except (SourceError,TypeError):recovered=[]
            document.metadata['venue_photo_discovery_pages']=list(dict.fromkeys(pages+recovered))
            _,discovered,facebook=venue_page_groups(document,draft)
            gallery=linked_venue_gallery(document,draft,fetcher,stage,limit,pages=[url for url in discovered if url not in references+pages],follow_media=True)
            if gallery:return gallery
            social=list(dict.fromkeys(social+facebook))
    gallery=google_venue_gallery(draft,stage,google_photos)
    if gallery:return gallery
    stage('checking_public_facebook_photos')
    # Only public supplied/discovered pages; PublicFetcher enforces robots,
    # redirect, timeout and size limits. Login walls are never bypassed.
    return linked_venue_gallery(document,draft,fetcher,stage,limit,pages=social)


def source_gallery(document, draft, fetcher, stage=lambda _: None, limit=4, photo_search=None, google_photos=None, licensed_photos=None):
    """Ordered source figures only. Never generate filler images for a gallery."""
    sources = [document]
    from .licensed_venue_photos import venue_photo_mode,LicensedVenuePhotos
    venue=draft.get('analysis',{}).get('action',{}).get('kind')=='places'
    mode=venue_photo_mode()
    licensed=mode=='licensed' and venue
    # Venue links are visited by the ordered venue chain, after email photos.
    for url in ([] if venue and mode=='source_first' else primary_visual_urls(document)):
        try:
            from .documents import parse_document
            sources.insert(0, parse_document(fetcher.fetch(url)))
        except SourceError:
            continue
    for source in sources:
        selected, seen = [], set()
        for figure in rank_figures(source, draft)[:8]:
            host=(urlsplit(figure.get('url','')).hostname or '').lower()
            if venue and mode!='google' and (host.endswith(('.googleusercontent.com','.ggpht.com')) or host=='places.googleapis.com'):continue
            stage("selecting_source_visual")
            value = source_visual(replace(source, figures=[figure], metadata={k: v for k, v in source.metadata.items() if k != "pdf_url"}), draft, fetcher, allow_pdf=False)
            if value:
                visual, image = value
                identity = hashlib.sha256(image).hexdigest()
                if identity not in seen:
                    selected.append(({**visual, "source_role": "primary" if source.url != document.url else "article"}, image))
                    seen.add(identity)
            if len(selected) >= limit:
                break
        if selected:
            return selected
        # HTML papers with vector figures may expose the usable rendition only
        # in their linked PDF. Check that before any generated fallback.
        fallback = source_visual(source, draft, fetcher) if source.metadata.get('pdf_url') else None
        if fallback:
            return [fallback]
    if venue and mode=='source_first':
        return source_first_venue_gallery(document,draft,fetcher,stage,limit,photo_search,google_photos,licensed_photos)
    if licensed:
        stage('checking_permitted_venue_photos')
        venues=linked_venue_gallery(document,draft,fetcher,stage,limit,require_permission=True)
        if not venues and document.metadata.get('venue_reference_pages'):
            venues=linked_venue_gallery(document,draft,fetcher,stage,limit,pages=document.metadata['venue_reference_pages'],require_permission=True)
        if venues:return venues
        stage('checking_openly_licensed_venue_photos')
        lookup=licensed_photos or LicensedVenuePhotos(fetcher=fetcher).gallery
        places=[dict(place) for place in draft['analysis']['action'].get('data',[])]
        for place in places:
            page=document.metadata.get('licensed_venue_identity_pages',{}).get(place.get('address'))
            if page:place['identity_page_url']=page
        if len(places)==1 and not places[0].get('website_url'):
            from .venue_enrichment import newsletter_fields
            website=newsletter_fields(document).get('website')
            if website:places[0]['website_url']=website
        gallery=lookup(places)
        if gallery:return gallery
        # One cached discovery request can recover a usable official/media
        # website when the newsletter supplied only a social profile or dead URL.
        return searched_venue_gallery(document,draft,fetcher,stage,photo_search,limit,require_permission=True,retry=False)
    venues=google_venue_gallery(draft,stage,google_photos)
    if not venues:venues=linked_venue_gallery(document,draft,fetcher,stage,limit)
    if not venues and document.metadata.get('venue_reference_pages'):
        venues=linked_venue_gallery(document,draft,fetcher,stage,limit,pages=document.metadata['venue_reference_pages'])
    return venues or searched_venue_gallery(document,draft,fetcher,stage,photo_search,limit)
