"""Conservative link grouping: shared identity or an explicit source cross-link.

Adjacency, a shared host, and generic labels such as 'Paper' never prove that
two links describe the same story. Uncertain links remain independent stories.
"""
import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .documents import digest_links, parse_document
from .retrieval import FETCH_DEADLINE, SourceError


def link_identity(url):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower().removeprefix('www.')
    paper = re.fullmatch(r'/(?:abs|pdf|html)/((?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7}))(?:v\d+)?(?:\.pdf)?/?', parts.path, re.I)
    if host == 'arxiv.org' and paper:
        return 'paper:arxiv:' + paper[1].lower()
    if host in ('doi.org', 'dx.doi.org'):
        return 'paper:doi:' + parts.path.strip('/').lower()
    if host == 'github.com':
        path = parts.path.strip('/').split('/')
        if len(path) >= 2:
            return 'repo:github:' + '/'.join(path[:2]).removesuffix('.git').lower()
    if parts.path.lower().endswith('.pdf'):
        return 'paper:pdf:' + urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ''))
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip('/'), parts.query, ''))


def group_story_links(root, fetcher, max_checks=6):
    """Return primary candidates and fetched documents, bounded per edition.

    Supporting pages are inspected only when the edition includes a paper.
    A repository/project must explicitly link to exactly one candidate paper;
    a multi-paper index cannot merge an entire digest into one story.
    """
    unique = {}
    for link in digest_links(root, limit=None, allow_companions=True):
        identity = link_identity(link['url'])
        if identity not in unique:
            unique[identity] = {**link, 'supporting_links': []}
        elif link['url'] != unique[identity]['url']:
            unique[identity]['supporting_links'].append(link)
    papers = {key for key in unique if key.startswith('paper:')}
    fetched, assignments, checks, companions = {}, {}, 0, {}
    deadline = FETCH_DEADLINE.set(min(FETCH_DEADLINE.get() or float('inf'), time.monotonic() + 30))
    try:
        for identity, link in unique.items():
            if not papers or identity in papers or checks >= max_checks or time.monotonic() >= FETCH_DEADLINE.get():
                continue
            checks += 1
            try:
                resource = fetcher.fetch(link['url'])
                document = parse_document(resource)
                fetched[link['url']] = document
            except SourceError:
                continue
            targets = {link_identity(item['url']) for item in document.links}
            if 'html' in resource.content_type:
                # A repository's README can sit in a second <article>; general
                # article extraction may select its directory panel instead.
                soup = BeautifulSoup(resource.body, 'html.parser')
                for anchor in soup.select('main a[href], article a[href]')[:1000]:
                    targets.add(link_identity(urljoin(resource.url, anchor['href'])))
                # BibTeX/source sections often print the paper URL as plain
                # text instead of an anchor. They are still explicit evidence.
                for region in soup.select('main, article')[:20]:
                    for value in re.findall(r'https?://[^\s<>"{}]+', region.get_text(' ', strip=True))[:100]:
                        targets.add(link_identity(value.rstrip('.,);')))
            matches = targets & papers
            # Restrict arbitrary article cross-links: a paper's companion page
            # or repository is supporting material; commentary stays a story.
            role = link.get('title', '')
            if role.startswith(('http://','https://')):
                role = ' '.join(line.replace(link['url'], '') for line in root.metadata.get('email_text', '').splitlines()
                                if link['url'] in line).strip(' :.-')
            companion = identity.startswith('repo:') or bool(re.fullmatch(
                r'(?:(?:official|open|view|visit|read|see)\s+)?(?:the\s+)?(?:project(?: page| website)?|source code|code|repository|implementation|demo|supplement(?:ary)?(?: material)?|materials)',
                role.strip(), re.I) or re.search(r'\b(?:project page|project website|source code|supplementary material)\b', role, re.I))
            if len(matches) == 1 and companion:
                assignments[identity] = next(iter(matches))
            if companion:companions[identity] = targets
        # A companion project may link to the confirmed repository instead of
        # repeating its paper URL. Follow only the already proven relationship.
        for _ in range(2):
            for identity, targets in companions.items():
                matches = (targets & papers) | {assignments[target] for target in targets if target in assignments}
                if len(matches) == 1:assignments[identity] = next(iter(matches))
                elif len(matches) > 1:assignments.pop(identity, None)
    finally:
        FETCH_DEADLINE.reset(deadline)
    for identity, primary in assignments.items():
        unique[primary]['supporting_links'].extend([
            {key: value for key, value in unique[identity].items() if key != 'supporting_links'},
            *unique[identity]['supporting_links']])
    return [link for key, link in unique.items() if key not in assignments], fetched
