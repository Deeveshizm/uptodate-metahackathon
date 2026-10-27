"""Convert public originals into bounded, addressable evidence spans."""
import hashlib
import io
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader

from .pipeline import safe_url
from .retrieval import Resource, SourceError


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


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
        return hashlib.sha256("\n".join(s["text"] for s in self.spans).encode()).hexdigest()


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
    title = normalize(soup.title.get_text(" ")) if soup.title else urlsplit(resource.url).hostname
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
    }
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
    spans = make_spans(chunks)
    if sum(len(s["text"]) for s in spans) < 100:
        raise SourceError("Not enough accessible source text; try a public issue or article rather than the signup page")
    from .visuals import html_figures
    figures = html_figures(resource, metadata.get("license_url", ""))
    return Document(resource.url, title[:250], spans, links, metadata, figures)


def make_spans(chunks):
    spans, budget = [], 0
    for text, section, page in chunks:
        text = normalize(text)
        if len(text) < 15:
            continue
        # Stable contiguous slices: exact quotes can be checked deterministically.
        for start in range(0, len(text), 1400):
            part = text[start:start + 1400]
            if budget + len(part) > 18000 or len(spans) >= 50:
                return spans
            spans.append({"id": f"s{len(spans) + 1:03d}", "text": part, "section": section, "page": page})
            budget += len(part)
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
            if sum(len(item[0]) for item in chunks) >= 18000:
                break
        spans = make_spans(chunks)
        if sum(len(s["text"]) for s in spans) < 100:
            raise SourceError("PDF has insufficient extractable text; try the publisher's HTML version")
        title = str((reader.metadata or {}).get("/Title") or urlsplit(resource.url).path.split("/")[-1])
        from .visuals import pdf_figures
        figures, license_url = pdf_figures(resource)
        return Document(resource.url, title[:250], spans,
                        metadata={"format": "pdf", "pages_available": len(reader.pages), "pages_examined": min(25, len(reader.pages)), "license_url": license_url},
                        figures=figures)
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError("PDF text extraction failed; try a public HTML version") from exc


def digest_links(document, limit=3):
    seen, candidates = {document.url}, []
    for link in document.links:
        url, title = link["url"], link["title"]
        if url in seen or len(title) < 15:
            continue
        if urlsplit(url).hostname in ("twitter.com", "x.com", "linkedin.com", "facebook.com", "instagram.com"):
            continue
        seen.add(url)
        candidates.append(link)
    # Keep editorial order. Do not infer that every navigation link is an article.
    return candidates[:limit]
