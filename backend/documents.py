"""Convert public originals into bounded, addressable evidence spans."""
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader

from .pipeline import safe_url
from .retrieval import Resource, SourceError


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def recipe_instructions(value, depth=0):
    if depth>4:return []
    if isinstance(value,str):
        text=normalize(BeautifulSoup(value,'html.parser').get_text(' ',strip=True))
        return [text] if text else []
    if isinstance(value,list):
        return [step for item in value[:40] for step in recipe_instructions(item,depth+1)]
    if isinstance(value,dict):
        if value.get('itemListElement'):
            steps=recipe_instructions(value['itemListElement'],depth+1)
            name=value.get('name')
            if isinstance(name,str) and name.strip():
                heading=normalize(BeautifulSoup(name,'html.parser').get_text(' ',strip=True))
                return [heading+': '+step for step in steps]
            return steps
        return recipe_instructions(value.get('text') or (value.get('name') if value.get('@type')=='HowToStep' else []) or [],depth+1)
    return []


def publication_name(soup):
    site = soup.find("meta", attrs={"property": "og:site_name"})
    if site and site.get("content", "").strip():
        return normalize(site["content"])[:150]
    import json
    objects = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}, limit=5):
        if len(script.get_text()) > 50_000:
            continue
        try:
            data = json.loads(script.get_text())
        except (ValueError, RecursionError):
            continue
        for obj in (data if isinstance(data, list) else [data])[:30]:
            if isinstance(obj, dict):
                objects.append(obj)
                graph = obj.get("@graph", [])
                if isinstance(graph, list):
                    objects.extend(item for item in graph[:30] if isinstance(item, dict))
    by_id = {obj["@id"]: obj for obj in objects if isinstance(obj.get("@id"), str)}
    for obj in objects:
        publisher = obj.get("publisher")
        if isinstance(publisher, dict):
            publisher = by_id.get(publisher.get("@id"), publisher) if isinstance(publisher.get("@id"), str) else publisher
            if isinstance(publisher.get("name"), str) and publisher["name"].strip():
                return normalize(publisher["name"])[:150]
    for obj in objects:
        if obj.get("@type") in ("Organization", "NewsMediaOrganization") and isinstance(obj.get("name"), str):
            return normalize(obj["name"])[:150]
    return ""


def primary_source_urls(document):
    """Bounded, explicitly linked originals; never crawl a paper's references."""
    origin = urlsplit(document.url)
    arxiv_path = re.compile(r"^/(abs|html|pdf)/((?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?)(?:\.pdf)?/?$", re.I)
    own_paper = arxiv_path.fullmatch(origin.path) if origin.hostname in ("arxiv.org", "www.arxiv.org") else None
    if own_paper and own_paper[1] != "abs":
        return []
    links = [{"url": document.url, "title": "Original paper"}] if own_paper else document.links
    urls, originals = [], set()
    for link in links:
        parts = urlsplit(link["url"])
        if parts.scheme not in ("http", "https") or parts.username:
            continue
        match = arxiv_path.fullmatch(parts.path) if parts.hostname in ("arxiv.org", "www.arxiv.org") else None
        if match:
            identity = "arxiv:" + match[2]
            candidates = ["https://arxiv.org/html/" + match[2], "https://arxiv.org/pdf/" + match[2]]
        elif parts.hostname != origin.hostname and re.search(r"\b(?:original (?:paper|research|source|article)|research paper|full paper|read (?:the )?paper|primary source)\b", link["title"], re.I):
            identity, candidates = link["url"], [link["url"]]
        else:
            continue
        if identity in originals:
            continue
        originals.add(identity)
        urls.extend(url for url in candidates if url != document.url)
        if len(originals) >= 2:
            break
    return urls


def content_root(soup):
    main = soup.find("main") or soup.find(attrs={"role": "main"})
    if main is not None:
        articles = main.find_all("article", limit=20)
        if len(articles) == 1:
            return articles[0]
        if len(articles) > 1:
            primary = next((article for article in articles if article.find("h1")), None)
            return primary if primary is not None else main
        return main
    return soup.find("article") or soup.body or soup


@dataclass
class Document:
    url: str
    title: str
    spans: list[dict]
    links: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    figures: list[dict] = field(default_factory=list)

    @property
    def content_hash(self):
        return self.metadata.get("source_content_hash") or hashlib.sha256("\n".join(s["text"] for s in self.spans).encode()).hexdigest()


def source_metadata(soup, url):
    """Original metadata and structured actions are extracted, never invented."""
    result = {}
    robots = soup.find("meta", attrs={"name": re.compile(r"^(?:robots|googlebot)$", re.I)})
    if robots and re.search(r"\bnoindex\b", robots.get("content", ""), re.I):
        result["private"] = True
    abstract = soup.select_one('.ltx_abstract, section.abstract, blockquote.abstract, div.abstract, #abstract, #Abs1, #enc-abstract')
    if abstract:
        text = normalize(abstract.get_text(" "))
        if abstract.name in ("h1", "h2", "h3", "h4"):
            parts = []
            for sibling in abstract.find_next_siblings(limit=12):
                if sibling.name in ("h1", "h2", "h3", "h4"):
                    break
                parts.append(sibling.get_text(" "))
            text = normalize(" ".join(parts))
        result["abstract"] = re.sub(r"^Abstract\s*[:.]?\s*", "", text, flags=re.I)[:8000]
    abstract_meta = soup.find("meta", attrs={"name": "citation_abstract"})
    if not result.get("abstract") and abstract_meta:
        result["abstract"] = normalize(abstract_meta.get("content", ""))[:8000]
    for name, field_name in (("citation_doi", "doi"), ("citation_publication_date", "published_date")):
        node = soup.find("meta", attrs={"name": name})
        if node:
            result[field_name] = normalize(node.get("content", ""))[:250]
    import json
    objects = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}, limit=8):
        if len(script.get_text()) > 100_000:
            continue
        try:
            value = json.loads(script.get_text())
        except (ValueError, RecursionError):
            continue
        for obj in (value if isinstance(value, list) else [value])[:40]:
            if isinstance(obj, dict):
                objects.append(obj)
                graph = obj.get("@graph")
                if isinstance(graph, list):
                    objects.extend(item for item in graph[:40] if isinstance(item, dict))
    recipe_objects=[obj for obj in objects if obj.get('@type')=='Recipe' or isinstance(obj.get('@type'),list) and 'Recipe' in obj['@type']]
    for obj in objects:
        kinds = obj.get("@type", [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not isinstance(kinds, list):
            continue
        if "ScholarlyArticle" in kinds and not result.get("abstract") and isinstance(obj.get("abstract"), str):
            result["abstract"] = normalize(BeautifulSoup(obj["abstract"], "html.parser").get_text(" "))[:8000]
        if "Recipe" in kinds and isinstance(obj.get("recipeIngredient"), list):
            if len(recipe_objects)>1:
                own=obj.get('url') or obj.get('@id') or ''
                if not isinstance(own,str) or urlsplit(urljoin(url,own))._replace(fragment='') != urlsplit(url)._replace(fragment=''):continue
            ingredients = [normalize(v)[:300] for v in obj["recipeIngredient"][:60] if isinstance(v, str) and normalize(v)]
            if ingredients:
                result["recipe"] = {"name": normalize(str(obj.get("name", "Recipe")))[:200], "ingredients": ingredients, "source_url": url}
                instructions=recipe_instructions(obj.get('recipeInstructions'))
                if len(instructions)<=30 and sum(len(step) for step in instructions)<=12000:
                    result['recipe']['instructions']=instructions
        if any(kind in kinds for kind in ("Place", "LocalBusiness", "Restaurant", "CafeOrCoffeeShop")):
            address = obj.get("address")
            if isinstance(address, dict):
                address = ", ".join(str(address[k]) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode") if address.get(k))
            if isinstance(address, str) and address.strip() and isinstance(obj.get("name"), str):
                result.setdefault("places", []).append({"name": normalize(obj["name"])[:200], "address": normalize(address)[:500], "source_url": url})
            if isinstance(obj.get('telephone'), str):
                result.setdefault('contact_links', []).append({'url': 'tel:' + obj['telephone'], 'context': ''})
    return result


def parse_document(resource: Resource) -> Document:
    kind = resource.content_type.split(";")[0].lower().strip()
    if kind == "application/pdf" or resource.body.startswith(b"%PDF-"):
        return parse_pdf(resource)
    if kind not in ("text/html", "application/xhtml+xml", "text/plain"):
        raise SourceError("This source is not a supported HTML, text, or PDF document")
    if kind == "text/plain":
        text = resource.body.decode("utf-8", errors="replace")
        spans = make_spans([(line, "Text", None) for line in text.splitlines() if normalize(line)])
        return Document(resource.url, urlsplit(resource.url).hostname, spans)
    soup = BeautifulSoup(resource.body, "html.parser")
    title_node=soup.title or soup.find('h1')
    title = normalize(title_node.get_text(" ")) if title_node else (urlsplit(resource.url).hostname or 'Email edition')
    author_nodes = soup.find_all("meta", attrs={"name": re.compile(r"^(?:author|citation_author|dc\.creator)$", re.I)}, limit=12)
    authors = [node.get("content", "").strip() for node in author_nodes if node.get("content", "").strip()]
    if not authors:
        authors = [normalize(node.get_text(" ")) for node in soup.select(".ltx_authors .ltx_personname")[:12]]
    license_link = soup.find(["link", "a"], rel=lambda x: x and "license" in x)
    if license_link is None:
        license_link = soup.find("a", href=re.compile(r"^https?://(?:www\.)?creativecommons\.org/", re.I))
    if license_link is None and urlsplit(resource.url).hostname in ("arxiv.org", "www.arxiv.org"):
        license_link = next((node for node in soup.find_all("a", href=True) if normalize(node.get_text(" ")).startswith("License:")), None)
    feed = soup.find("link", attrs={"type": re.compile(r"application/(rss|atom)\+xml")})
    metadata = {
        "author": ", ".join(dict.fromkeys(authors))[:400],
        "license_url": safe_url(urljoin(resource.url, license_link.get("href", ""))) if license_link else "",
        "license_name": normalize(license_link.get_text(" "))[:150] if license_link else "",
        "feed_url": safe_url(urljoin(resource.url, feed.get("href", ""))) if feed else "",
        "publisher": publication_name(soup),
        "format": "html",
        **source_metadata(soup, resource.url),
    }
    metadata.setdefault('contact_links', []).extend([
        {'url': anchor['href'], 'context': normalize((anchor.find_parent(['p', 'li']) or anchor).get_text(' ', strip=True))[:500]}
        for anchor in soup.find_all('a', href=re.compile(r'^tel:', re.I), limit=30)
        if not anchor.find_parent(['footer', 'nav', 'aside'])
    ])
    if urlsplit(resource.url).hostname in ("arxiv.org", "www.arxiv.org"):
        paper = re.fullmatch(r"/(?:abs|html)/(\d{4}\.\d{4,5})(?:v\d+)?/?", urlsplit(resource.url).path)
        if paper:
            for anchor in soup.find_all("a", href=True):
                candidate = safe_url(urljoin(resource.url, anchor["href"]))
                parsed = urlsplit(candidate)
                if parsed.hostname in ("arxiv.org", "www.arxiv.org") and re.fullmatch(r"/pdf/" + re.escape(paper[1]) + r"(?:v\d+)?(?:\.pdf)?/?", parsed.path):
                    metadata["pdf_url"] = candidate
                    break
    for element in soup(["script", "style", "noscript", "nav", "footer", "header", "form", "aside", "svg", "iframe"]):
        element.decompose()
    main = content_root(soup)
    links = []
    for anchor in main.find_all("a", href=True):
        url = safe_url(urljoin(resource.url, anchor["href"]))
        label = normalize(anchor.get_text(" "))
        if url and label and not re.search(r"unsubscribe|subscribe|signin|login|privacy|terms|preferences|mailto:", url, re.I):
            links.append({"url": url, "title": label[:200]})
    section, chunks, grouped_lists = "Body", [], set()
    for node in main.find_all(["h1", "h2", "h3", "p", "li", "blockquote", "pre"]):
        # Avoid copying nested list/blockquote paragraphs twice.
        if node.find_parent(["p", "li", "blockquote", "pre"]) is not None:
            continue
        parent_list = node.find_parent(["ul", "ol"])
        if parent_list is not None and id(parent_list) in grouped_lists:
            continue
        text = normalize(node.get_text(" "))
        following = node.find_next_sibling()
        if node.name == "p" and following is not None and following.name in ("ul", "ol"):
            # A list introduction and its bullets are one evidence unit.
            text += " " + normalize(following.get_text(" "))
            grouped_lists.add(id(following))
        if node.name in ("h1", "h2", "h3"):
            section = text[:200]
        if text:
            chunks.append((text, section, None))
    if not chunks:
        chunks = [(line, "Body", None) for line in main.stripped_strings]
    steps=(metadata.get('recipe') or {}).get('instructions',[])
    if steps:
        chunks.append(('Recipe instructions: '+' '.join(f'{index}. {step}' for index,step in enumerate(steps,1)),'Instructions',None))
    spans = make_spans(chunks)
    content="\n".join(normalize(chunk[0]) for chunk in chunks)
    if metadata.get('recipe'):
        content+='\n'+json.dumps({key:value for key,value in metadata['recipe'].items() if key!='source_url'},sort_keys=True,ensure_ascii=False)
    metadata["source_content_hash"] = hashlib.sha256(content.encode()).hexdigest()
    metadata["extraction"] = {"strategy": "section_balanced", "available_characters": sum(len(c[0]) for c in chunks),
                              "selected_characters": sum(len(s["text"]) for s in spans)}
    if sum(len(s["text"]) for s in spans) < 100:
        raise SourceError("Not enough accessible source text; try a public issue or article rather than the signup page")
    from .visuals import html_figures
    figures = html_figures(resource, metadata.get("license_url", ""))
    return Document(resource.url, title[:250], spans, links, metadata, figures)


def make_spans(chunks):
    candidates, total = [], 0
    for text, section, page in chunks:
        text = normalize(text)
        if len(text) < 15:
            continue
        for start in range(0, len(text), 1400):
            part = text[start:start + 1400]
            if total + len(part) > 160_000 or len(candidates) >= 400:
                break
            candidates.append({"id": f"s{len(candidates) + 1:03d}", "text": part, "section": section, "page": page})
            total += len(part)
        if total >= 158_600 or len(candidates) >= 400:
            break
    if total <= 18000 and len(candidates) <= 50:
        return candidates
    # Preserve later findings/limitations before spending the entire budget on
    # the introduction. Spread remaining space across sections, not just pages 1–N.
    def priority(span):
        section = span["section"].lower()
        if re.search(r"abstract|conclusion|limitation|result|discussion", section):
            return 0
        if re.search(r"method|approach|architecture|experiment|evaluation", section):
            return 1
        if re.search(r"reference|bibliograph|acknowledg", section):
            return 4
        return 2
    sections = {}
    for span in candidates:
        sections.setdefault(span["section"], []).append(span)
    ordered = []
    groups = sorted(sections.values(), key=lambda values: priority(values[0]))
    for depth in range(max((len(values) for values in groups), default=0)):
        ordered.extend(values[depth] for values in groups if len(values) > depth)
    spans, budget = [], 0
    for span in ordered:
        if budget + len(span["text"]) <= 18000 and len(spans) < 50:
            spans.append(span)
            budget += len(span["text"])
    spans.sort(key=lambda span: int(span["id"][1:]))
    return spans


def parse_pdf(resource):
    try:
        reader = PdfReader(io.BytesIO(resource.body))
        if reader.is_encrypted:
            raise SourceError("Encrypted PDFs are not supported")
        chunks = []
        for index, page in enumerate(reader.pages):
            if index >= 25:
                break
            text = page.extract_text() or ""
            chunks.extend((part, f"Page {index + 1}", index + 1) for part in re.split(r"\n\s*\n", text))
            if sum(len(item[0]) for item in chunks) >= 160_000:
                break
        spans = make_spans(chunks)
        if sum(len(s["text"]) for s in spans) < 100:
            raise SourceError("PDF has insufficient extractable text; try the publisher's HTML version")
        title = str((reader.metadata or {}).get("/Title") or urlsplit(resource.url).path.split("/")[-1])
        from .visuals import pdf_figures
        figures, license_url = pdf_figures(resource)
        all_text = "\n".join(chunk[0] for chunk in chunks)
        abstract = re.search(r"\bAbstract\s*[:.—-]?\s*(.{80,8000}?)(?=\n\s*(?:(?:1|I)[.\s]+)?Introduction\b|\n\s*(?:Keywords|Index terms)\s*[:.—-])", all_text, re.I | re.S)
        return Document(resource.url, title[:250], spans,
                        metadata={"format": "pdf", "pages_available": len(reader.pages), "pages_examined": min(25, len(reader.pages)), "license_url": license_url,
                                  "abstract": normalize(abstract[1]) if abstract else "",
                                  "source_content_hash": hashlib.sha256(all_text.encode()).hexdigest()},
                        figures=figures)
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError("PDF text extraction failed; try a public HTML version") from exc


def digest_links(document, limit=3, allow_companions=False):
    seen, candidates = {document.url}, []
    for link in document.links:
        url, title = link["url"], link["title"]
        description = title + " " + link.get("context", "")
        companion = allow_companions and (urlsplit(url).hostname in ('arxiv.org','doi.org','github.com') or re.fullmatch(
            r'(?:paper|full paper|repository|repo|code|github|project(?: page| website)?|demo|supplement(?:ary)?(?: material)?)', title.strip(), re.I))
        if url in seen or (len(description.strip()) < 15 and not companion):
            continue
        if re.search(r"^(?:home|about|contact|advertis|sponsor|unsubscribe|preferences|sign.?in|subscribe|forward|share)\b", title, re.I):
            continue
        if not urlsplit(url).path.strip("/"):
            continue
        if urlsplit(url).hostname in ("twitter.com", "x.com", "linkedin.com", "facebook.com", "instagram.com"):
            continue
        seen.add(url)
        candidates.append(link)
    # Keep editorial order. Do not infer that every navigation link is an article.
    return candidates if limit is None else candidates[:limit]
