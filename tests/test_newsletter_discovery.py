import pytest
from unittest.mock import Mock

from backend.discovery import article_page, discover_issue, feed_issue, issue_mode
from backend.documents import Document, publication_name
from bs4 import BeautifulSoup
from backend.retrieval import Resource, SourceError

HOME = "https://publication.example/"
ISSUE = "https://publication.example/issues/coffee"


def test_feed_selects_most_recent_dated_issue_not_channel_link():
    data = b'<rss version="2.0"><channel><link>https://publication.example/</link><item><link>https://publication.example/older</link><pubDate>Sun, 11 Oct 2026 10:00:00 GMT</pubDate></item><item><link>https://publication.example/newer</link><pubDate>Sun, 18 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>'
    assert feed_issue(Resource(HOME + "feed", "application/rss+xml", data)) == HOME + "newer"


def test_atom_selects_html_alternate_not_enclosure():
    data = b'<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://publication.example/"><entry><link rel="enclosure" href="audio.mp3"/><link rel="alternate" type="text/html" href="issues/coffee"/><updated>2026-10-18T10:00:00Z</updated></entry></feed>'
    assert feed_issue(Resource(HOME + "feed", "application/atom+xml", data)) == ISSUE


@pytest.mark.parametrize("body", [b'<!DOCTYPE rss [<!ENTITY a "expansion">]><rss/>', b'<rss', b'<html/>', b'<rss>\x00</rss>', b'x' * 1_000_001])
def test_feed_rejects_unsupported_or_unsafe_xml(body):
    with pytest.raises(SourceError):
        feed_issue(Resource(HOME, "application/rss+xml", body))


def test_undated_feed_uses_editorial_order_and_ignores_non_http_links():
    data = b'<rss><channel><item><link>javascript:alert(1)</link></item><item><link>https://publication.example/first</link></item><item><link>https://publication.example/second</link></item></channel></rss>'
    assert feed_issue(Resource(HOME + "feed", "application/rss+xml", data)) == HOME + "first"


def test_long_signup_page_is_not_article_content():
    body = ('<html><head><meta property="og:type" content="article"></head><body><h1>Subscribe</h1><p>' + 'Newsletter marketing. ' * 40 + '</p><form>Subscribe here</form></body></html>').encode()
    assert not article_page(Resource(HOME + "subscribe", "text/html", body))
    assert not article_page(Resource(HOME, "text/html", body))


def test_explicit_latest_issue_link_is_discovered_not_arbitrary_navigation():
    page = Resource(HOME, "text/html", b'<a href="/about">About us</a><a href="/issues/coffee">Read the latest issue</a>')
    result = discover_issue(page, Mock())
    assert result["url"] == ISSUE and result["via"] == "latest_issue_link"


def test_article_markup_and_roundup_routing():
    body = ('<html><head><meta property="og:type" content="article"></head><body><main><h1>Coffee news</h1><p>' + 'New neighborhood coffee shops opened this week. ' * 10 + '</p></main></body></html>').encode()
    assert article_page(Resource(ISSUE, "text/html", body))
    doc = Document(ISSUE, "The coffee weekly roundup", [], links=[{}, {}])
    assert issue_mode(doc) == "digest"
    doc.title = "A new neighborhood cafe"
    assert issue_mode(doc) == "article"


def test_malformed_jsonld_does_not_crash_discovery():
    body = ('<html><body><script type="application/ld+json">{"@graph":false}</script><p>' + 'Newsletter marketing. ' * 40 + '</p></body></html>').encode()
    assert not article_page(Resource(HOME + "newsletter", "text/html", body))


def test_publication_name_resolves_jsonld_publisher_without_using_article_author():
    body = '<script type="application/ld+json">{"@graph":[{"@type":"Organization","@id":"#publisher","name":"AlphaSignal"},{"@type":"Article","publisher":{"@id":"#publisher"},"author":{"name":"Research authors"}}]}</script>'
    assert publication_name(BeautifulSoup(body, "html.parser")) == "AlphaSignal"


@pytest.mark.parametrize("data", ['{"@graph":false}', '{"publisher":{"@id":[]}}', '{"@type":[]}'])
def test_invalid_publication_metadata_is_ignored(data):
    assert publication_name(BeautifulSoup('<script type="application/ld+json">' + data + '</script>', "html.parser")) == ""
