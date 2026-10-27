"""Public-document retrieval: DNS-pinned connections, bounded bodies, no cookies.

This transport is deliberately separate from the trusted loopback model client.
Every redirect is re-resolved and checked before a connection is attempted.
"""
import http.client
import ipaddress
import socket
import ssl
import zlib
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .pipeline import safe_url

USER_AGENT = "KnowledgeFeedPrototype/0.2"
MAX_BYTES = 6_000_000


class SourceError(Exception):
    """Safe, user-facing failure, without credentials or response bodies."""


@dataclass
class Resource:
    url: str
    content_type: str
    body: bytes
    status: int = 200


def public_url(value: str) -> str:
    value = safe_url(value)
    if not value or len(value) > 3000:
        raise SourceError("Use a public HTTP(S) source URL")
    parts = urlsplit(value)
    if parts.port not in (None, 80, 443):
        raise SourceError("Source URLs must use standard HTTP(S) ports")
    return value


def resolve_public(host: str, port: int):
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SourceError("The source hostname could not be resolved") from exc
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise SourceError("Source resolves to a non-public network address")
    return addresses


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, addresses):
        super().__init__(host, port, timeout=15)
        self.addresses = addresses

    def connect(self):
        # Connect to a validated IP, never resolve the hostname a second time.
        self.sock = socket.create_connection((self.addresses[0], self.port), timeout=self.timeout)


class PinnedHTTPS(PinnedHTTP):
    def connect(self):
        super().connect()
        self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


class PublicFetcher:
    def _request(self, url, method="GET", body=None):
        parts = urlsplit(public_url(url))
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = resolve_public(parts.hostname, port)
        connection = (PinnedHTTPS if parts.scheme == "https" else PinnedHTTP)(parts.hostname, port, addresses)
        path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity", "Accept": "text/html,application/pdf,image/png,image/jpeg,image/webp,application/rss+xml,application/atom+xml,text/plain;q=0.8"}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise SourceError("Source exceeds the 6 MB download limit")
            encoding = response.getheader("Content-Encoding", "identity").lower()
            if encoding in ("gzip", "deflate"):
                decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
                data = decompressor.decompress(data, MAX_BYTES + 1)
                if len(data) > MAX_BYTES or decompressor.unconsumed_tail:
                    raise SourceError("Source exceeds the decompressed size limit")
            elif encoding not in ("", "identity"):
                raise SourceError("Source uses an unsupported response encoding")
            return Resource(url, response.getheader("Content-Type", ""), data, response.status), response.getheader("Location")
        except (OSError, http.client.HTTPException, zlib.error) as exc:
            raise SourceError("Could not securely download the source; try its public article URL") from exc
        finally:
            connection.close()

    def _follow(self, url, method="GET", body=None):
        for _ in range(5):
            resource, location = self._request(public_url(url), method, body)
            if resource.status not in (301, 302, 303, 307, 308):
                return resource
            if not location:
                raise SourceError("Source returned a redirect without a destination")
            target = public_url(urljoin(url, location))
            if urlsplit(url).scheme == "https" and urlsplit(target).scheme != "https":
                raise SourceError("Source redirected to an insecure connection")
            # Never resend an email address or form tokens to a redirect target.
            if method != "GET":
                return resource
            url = target
        raise SourceError("Source redirected too many times")

    def fetch(self, url):
        return self._redirect_fetch(public_url(url), remaining=5)

    def _redirect_fetch(self, url, remaining):
        if remaining <= 0:
            raise SourceError("Too many redirects")
        parts = urlsplit(public_url(url))
        robots = self._follow(urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", "")))
        if robots.status in (401, 403) or robots.status >= 500:
            raise SourceError("Source does not currently permit automated retrieval")
        if robots.status == 200:
            policy = RobotFileParser()
            policy.parse(robots.body.decode("utf-8", errors="replace").splitlines())
            if not policy.can_fetch(USER_AGENT, url):
                raise SourceError("Source robots.txt disallows this article")
        resource, location = self._request(url)
        if resource.status in (301, 302, 303, 307, 308):
            if not location:
                raise SourceError("Invalid redirect")
            target = public_url(urljoin(url, location))
            if parts.scheme == "https" and urlsplit(target).scheme != "https":
                raise SourceError("Source redirected to an insecure connection")
            return self._redirect_fetch(target, remaining - 1)
        if resource.status != 200:
            raise SourceError(f"Source returned HTTP {resource.status}")
        return resource

    def submit_form(self, url, encoded_body):
        if urlsplit(public_url(url)).scheme != "https":
            raise SourceError("Newsletter signup requires HTTPS")
        resource = self._follow(url, "POST", encoded_body)
        if not (200 <= resource.status < 400):
            raise SourceError(f"Signup returned HTTP {resource.status}")
        return resource
