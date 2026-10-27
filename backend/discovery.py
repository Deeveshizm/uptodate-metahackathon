"""Bounded public-issue discovery; never draft from a signup landing page."""
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .documents import content_root, normalize
from .pipeline import safe_url
from .retrieval import SourceError


def feed_issue(resource):
    # The transport bounds all bodies; feeds get a smaller parsing budget too.
    if len(resource.body) > 1_000_000 or b"\x00" in resource.body or re.search(br"<!\s*(?:DOCTYPE|ENTITY)", resource.body, re.I):
        raise SourceError("Public feed exceeds limits or contains unsupported XML declarations")
    try:
        root = ET.fromstring(resource.body)
    except ET.ParseError as exc:
        raise SourceError("Public feed is not valid XML") from exc
    if len(list(root.iter())) > 5000:
        raise SourceError("Public feed exceeds the element limit")
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag == atom + "feed":
        entries = root.findall(atom + "entry")
        is_atom = True
    elif root.tag == "rss":
        entries = root.findall("channel/item")
        is_atom = False
    else:
        raise SourceError("Only RSS 2.0 and Atom public feeds are supported")
    candidates = []
    root_base = urljoin(resource.url, root.get("{http://www.w3.org/XML/1998/namespace}base", ""))
    for index, entry in enumerate(entries[:20]):
        if is_atom:
            links = [link for link in entry.findall(atom + "link")
                     if link.get("rel", "alternate") == "alternate" and link.get("type", "text/html") in ("text/html", "application/xhtml+xml")]
            raw = links[0].get("href", "") if links else ""
            date = entry.findtext(atom + "published") or entry.findtext(atom + "updated") or ""
        else:
            raw, date = entry.findtext("link", ""), entry.findtext("pubDate", "")
        entry_base = urljoin(root_base, entry.get("{http://www.w3.org/XML/1998/namespace}base", ""))
        url = safe_url(urljoin(entry_base, raw.strip())) if raw.strip() else ""
        if not url or url == resource.url:
            continue
        published = None
        try:
            published = datetime.fromisoformat(date.replace("Z", "+00:00")) if is_atom else parsedate_to_datetime(date)
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, OverflowError):
            pass
        if published and published > datetime.now(timezone.utc):
            continue
        candidates.append((published.timestamp() if published else float("-inf"), -index, url))
    if not candidates:
        return None
    return max(candidates)[2]


def article_page(resource):
    """Require an issue/article signal, not just a long signup-page body."""
    soup = BeautifulSoup(resource.body, "html.parser")
    path = urlsplit(resource.url).path.rstrip("/")
    if not path or re.search(r"/(?:subscribe|signup|sign-up|login|signin|privacy|terms)(?:/|$)", path, re.I):
        return False
    content = content_root(soup)
    for node in content(["nav", "footer", "header", "form", "script", "style", "aside"]):
        node.decompose()
    if len(content.get_text(" ", strip=True)) < 300:
        return False
    og_type = soup.find("meta", attrs={"property": "og:type", "content": "article"})
    published = soup.find("meta", attrs={"property": "article:published_time"})
    if og_type or published:
        return True
    # JSON-LD was removed from the content clone above, so inspect the original.
    original = BeautifulSoup(resource.body, "html.parser")
    for node in original.find_all("script", attrs={"type": "application/ld+json"}, limit=5):
        if len(node.get_text()) > 50_000:
            continue
        try:
            data = json.loads(node.get_text())
        except (ValueError, RecursionError):
            continue
        objects = data if isinstance(data, list) else [data]
        for obj in objects[:30]:
            if not isinstance(obj, dict):
                continue
            graph = obj.get("@graph", [])
            graph = graph if isinstance(graph, list) else []
            for item in [obj, *graph][:30]:
                if not isinstance(item, dict):
                    continue
                types = item.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if isinstance(types, list) and any(t in ("Article", "NewsArticle", "BlogPosting", "ScholarlyArticle") for t in types):
                    return True
    articles = original.find_all("article", limit=2)
    return len(articles) == 1 and articles[0].find("h1") is not None


def discover_issue(resource, fetcher):
    """Return one public issue/article, or a truthful waiting state."""
    soup = BeautifulSoup(resource.body, "html.parser")
    feed = soup.find("link", attrs={"type": re.compile(r"^application/(rss|atom)\+xml$", re.I)})
    feed_url = safe_url(urljoin(resource.url, feed.get("href", ""))) if feed else ""
    if article_page(resource):
        return {"status": "found", "url": resource.url, "via": "submitted_article", "feed_url": feed_url}
    if feed_url:
        url = feed_issue(fetcher.fetch(feed_url))
        if url:
            return {"status": "found", "url": url, "via": "public_feed", "feed_url": feed_url}
    for link in soup.find_all("a", href=True, limit=100):
        label = normalize(link.get_text(" ")).lower()
        if re.fullmatch(r"(?:read |view |read the |view the )?latest (?:issue|edition|newsletter)", label):
            url = safe_url(urljoin(resource.url, link["href"]))
            if url and url != resource.url:
                return {"status": "found", "url": url, "via": "latest_issue_link", "feed_url": feed_url}
    return {"status": "waiting_for_content", "feed_url": feed_url,
            "detail": "No public issue found. Waiting for an accessible newsletter edition or incoming email; the signup page is not post content."}


def issue_mode(document):
    # Public roundups follow original editorial links; ordinary articles stay whole.
    return "digest" if re.search(r"\b(roundup|digest|newsletter|weekly edition)\b", document.title, re.I) and len(document.links) >= 2 else "article"
