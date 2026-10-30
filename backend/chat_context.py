"""Question-aware selection from already saved evidence; no extra model call."""
import json
import re

STOP = set('what does this that how why tell about explain could would should when where which the are was were have has had can will for with and not its they them me it in of to on an is be do did'.split())


def terms(text):
    return set(re.findall(r'\w[\w-]+', text.casefold())) - STOP


def question_evidence(post, question, source_spans=None, history=()):
    keywords = terms(question)
    if len(keywords) <= 2:
        for turn in list(history)[-2:]:
            if turn.role == 'user':keywords |= terms(turn.text)
    candidates, seen = [], set()
    for item in json.loads(post['evidence']):
        text = item.get('quote', '').strip()
        key = ' '.join(text.split()).casefold()
        if not text or key in seen:continue
        seen.add(key);candidates.append({'text':text, **{k:item[k] for k in ('section',) if item.get(k)}})
    for item in source_spans or []:
        text = item.get('text', '').strip()
        key = ' '.join(text.split()).casefold()
        if not text or key in seen:continue
        seen.add(key);candidates.append({'text':text, **{k:item[k] for k in ('section',) if item.get(k)}})
    def rank(item):
        matches = keywords & terms(item['text'])
        section = item.get('section', '').casefold()
        intent = (('limit','limitation','limitations','caveat','risk','weakness'), 'limitation|discussion')
        bonus = 4 if keywords.intersection(intent[0]) and re.search(intent[1],section) else 0
        return 3*len(matches) + bonus
    ranked = sorted(candidates,key=rank,reverse=True)
    selected, size = [], 0
    for item in ranked:
        text = item['text']
        if len(text)>1400:
            # Keep a bounded window around the question's matching term rather
            # than always cutting away a relevant result near the end.
            matches=[m for m in re.finditer(r'\w[\w-]+',text.casefold()) if m[0] in keywords]
            start=max(0,matches[0].start()-450) if matches else 0
            text=text[start:start+1400]
        if len(selected)>=6 or size+len(text)>6500:continue
        selected.append({'id':len(selected),**item,'text':text});size+=len(text)
    return selected
