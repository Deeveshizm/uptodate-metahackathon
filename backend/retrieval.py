"""Public-document retrieval: DNS-pinned connections, bounded bodies, no cookies.

This transport is deliberately separate from the trusted loopback model client.
Every redirect is re-resolved and checked before a connection is attempted.
"""
import http.client
import ipaddress
import logging
import random
import socket
import ssl
import time
import zlib
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .pipeline import safe_url

USER_AGENT = "KnowledgeFeedPrototype/0.2"
MAX_BYTES = 6_000_000
MAX_READ_ATTEMPTS = 3
FETCH_RETRY_WINDOW = 30
TRANSIENT_HTTP = frozenset((408, 429, 500, 502, 503, 504))
FETCH_DEADLINE = ContextVar('source_fetch_deadline', default=None)
logger = logging.getLogger(__name__)


class SourceError(Exception):
    """Safe, user-facing failure, without credentials or response bodies."""


class TemporarySourceError(SourceError):
    """A read may succeed on a fresh, independently validated connection."""


@dataclass
class Resource:
    url: str
    content_type: str
    body: bytes
    status: int = 200
    retry_after: str | None = None


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
    except socket.gaierror as exc:
        if exc.errno == socket.EAI_AGAIN:
            raise TemporarySourceError('Source hostname lookup is temporarily unavailable') from None
        raise SourceError('The source hostname could not be resolved') from None
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
    @staticmethod
    def _timeout():
        deadline = FETCH_DEADLINE.get()
        remaining = deadline - time.monotonic() if deadline is not None else 15
        if remaining <= 0:
            raise TemporarySourceError('Source download retry window expired; try again later')
        return min(15, remaining)

    @staticmethod
    def _retry_after(value):
        if not value:
            return 0
        value = value.strip()
        try:
            if value.isdecimal():
                return float(value)
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0, target.timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0

    def _read_request(self, url):
        for attempt in range(MAX_READ_ATTEMPTS):
            self._timeout()
            retry_after = 0
            try:
                resource, location = self._request(public_url(url))
                if resource.status not in TRANSIENT_HTTP:
                    return resource, location
                error = TemporarySourceError(f'Source returned temporary HTTP {resource.status}; try again later')
                retry_after = self._retry_after(resource.retry_after)
            except TemporarySourceError as exc:
                error = exc
            if attempt + 1 == MAX_READ_ATTEMPTS:
                raise error
            delay = max(2 ** attempt + random.uniform(0, 0.25), retry_after)
            deadline = FETCH_DEADLINE.get()
            if deadline is not None and time.monotonic() + delay >= deadline:
                raise TemporarySourceError('Source requires a later retry; automatic retry window reached')
            logger.info('Retrying source GET host=%s attempt=%d/%d wait=%.2fs reason=%s',
                        urlsplit(url).hostname, attempt + 2, MAX_READ_ATTEMPTS, delay, error)
            time.sleep(delay)

    def _request(self, url, method="GET", body=None):
        self._timeout()
        parts = urlsplit(public_url(url))
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = resolve_public(parts.hostname, port)
        connection = (PinnedHTTPS if parts.scheme == "https" else PinnedHTTP)(parts.hostname, port, addresses)
        connection.timeout = self._timeout()
        path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity", "Accept": "text/html,application/pdf,image/png,image/jpeg,image/webp,application/rss+xml,application/atom+xml,text/plain;q=0.8"}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            connection.request(method, path, body=body, headers=headers)
            if connection.sock:
                connection.sock.settimeout(self._timeout())
            response = connection.getresponse()
            data = bytearray()
            while len(data) <= MAX_BYTES:
                timeout = self._timeout()
                if connection.sock:
                    connection.sock.settimeout(timeout)
                chunk = response.read1(min(64_000, MAX_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            data = bytes(data)
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
            return Resource(url, response.getheader("Content-Type", ""), data, response.status,
                            response.getheader('Retry-After')), response.getheader("Location")
        except TimeoutError:
            raise TemporarySourceError('Source connection or download timed out; try again later') from None
        except ssl.SSLEOFError:
            raise TemporarySourceError('Source TLS connection closed unexpectedly; try again later') from None
        except ssl.SSLError:
            raise SourceError('Source TLS connection failed; try again later') from None
        except zlib.error:
            raise SourceError('Source returned invalid compressed data') from None
        except (OSError, http.client.HTTPException):
            raise TemporarySourceError('Source connection failed while downloading the article; try again later') from None
        finally:
            connection.close()

    def _follow(self, url, method="GET", body=None):
        for _ in range(5):
            resource, location = self._read_request(public_url(url)) if method == 'GET' else self._request(public_url(url), method, body)
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
        previous = FETCH_DEADLINE.get()
        deadline = time.monotonic() + FETCH_RETRY_WINDOW
        token = FETCH_DEADLINE.set(min(previous, deadline) if previous is not None else deadline)
        try:
            return self._redirect_fetch(public_url(url), remaining=5)
        finally:
            FETCH_DEADLINE.reset(token)

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
        resource, location = self._read_request(url)
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
