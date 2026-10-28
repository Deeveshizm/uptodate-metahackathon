"""Offline extraction adapter; never invents analysis or fetches untrusted URLs.

The worker queues evidence excerpts for human review. A future LLM adapter can
replace draft generation while preserving the evidence and publication gates.
"""
import hashlib
import ipaddress
import re
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit


def safe_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
            return ""
        if host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".localhost", ".internal")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            if "." not in host:
                return ""
        _ = parts.port  # Reject malformed ports.
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    except ValueError:
        return ""


class ArticleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.links = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.hidden += 1
        if not self.hidden and tag == "a":
            url = safe_url(dict(attrs).get("href", ""))
            if url:
                self.links.append(url)
        if tag in ("p", "br", "div", "h1", "h2", "h3", "li"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.hidden:
            self.hidden -= 1
        if tag in ("p", "div", "h1", "h2", "h3", "li"):
            self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def parse_newsletter(raw: bytes, require_links=True) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    name, sender = parseaddr(str(msg.get("From", "")))
    if not sender or "@" not in sender or "\n" in sender:
        raise ValueError("A valid From address is required")
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is None:
        raise ValueError("No supported text or HTML body")
    content = body.get_content()
    if not isinstance(content, str):
        raise ValueError("Unsupported body encoding")
    parser = ArticleHTML()
    labeled_links = []
    if body.get_content_type() == "text/html":
        parser.feed(content)
        text = "".join(parser.text)
        urls = parser.links
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, "html.parser")
        for anchor in soup.find_all("a", href=True)[:300]:
            url = safe_url(anchor["href"])
            if url:
                parent = anchor.find_parent(["p", "li", "article"])
                labeled_links.append({"url": url, "title": re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))[:200],
                                      "context": re.sub(r"\s+", " ", parent.get_text(" ", strip=True) if parent else "")[:500]})
    else:
        text = content
        urls = [safe_url(x.rstrip(".,);>")) for x in re.findall(r"https?://[^\s<>\"]+", text)]
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    all_urls = list(dict.fromkeys(url for url in urls if url and not re.search(
        r"unsubscribe|manage.preferences|email.preferences|view.in.browser", url, re.I
    )))
    urls = all_urls[:50]
    if not text or (require_links and not urls):
        raise ValueError("No readable content with an eligible source link")
    return {
        "sender": sender.lower(), "source_name": name or sender.split("@")[1],
        "subject": str(msg.get("Subject", "Untitled edition"))[:300],
        "text": text[:60000], "urls": urls, "links": [link for link in labeled_links if link["url"] in urls],
        "link_limit_reached": len(all_urls) > len(urls),
        # Content hash avoids forged Message-ID collisions; same import is idempotent.
        "fingerprint": hashlib.sha256(raw).hexdigest(),
    }


def make_drafts(parsed: dict) -> list[dict]:
    lines = parsed["text"].splitlines()
    drafts = []
    for index, url in enumerate(parsed["urls"]):
        # Plaintext links permit a local excerpt; HTML links cannot be reliably
        # aligned in this prototype, so retain a labeled whole-edition excerpt.
        matching = next((i for i, line in enumerate(lines) if url in line), None)
        selected = lines[max(0, matching - 2):matching + 2] if matching is not None else lines[:6]
        excerpt = "\n".join(selected)[:1800]
        drafts.append({
            "title": f"{parsed['subject']} · item {index + 1}",
            "hook": "Newsletter excerpt — original-source analysis pending",
            "finding": excerpt,
            "limitations": "Extracted from the newsletter, not independently verified against the original. Human review required.",
            "original_url": url,
            "evidence": [{"source": "newsletter", "quote": excerpt, "url": url}],
        })
    return drafts
