"""Presentation cleanup without adding facts or changing source evidence."""
import html
import re


def clean_text(text, decode_entities=True):
    text = (html.unescape(text) if decode_entities else text).replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = '\n'.join(line.rstrip() for line in text.splitlines())
    return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', text).strip()


def post_prose(text):
    text = clean_text(text)
    text = re.sub(r'!?\[([^\]\n]+)\]\(https?://[^\s)]+\)', r'\1', text)
    text = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', text)
    for pattern in (r'\*\*([^\n]+?)\*\*', r'__([^\n]+?)__', r'`([^`\n]+)`'):
        text = re.sub(pattern, r'\1', text)
    text = re.sub(r'(?m)^\s*[-*]\s+', '• ', text)
    return clean_text(text)


def sentence_key(text):
    return ' '.join(re.findall(r'\w+', text.casefold()))


def same_sentence(text, body):
    key = sentence_key(text)
    return bool(key) and any(sentence_key(sentence) == key for sentence in re.split(r'(?<=[.!?])\s+|\n\n', body))


def readable_paragraphs(text):
    """Keep post prose compact; only explicit lists need separate lines."""
    text = post_prose(text)
    if any(re.match(r'^\s*(?:[•*-]|\d+[.)])\s+',line) for line in text.splitlines()):return text
    return ' '.join(text.split())


def normalize_draft(draft):
    data = draft.model_dump()
    data['title'] = ' '.join(post_prose(data['title']).split())
    # The fields remain readable for older clients, but cannot create a fixed
    # research template or a second, unchecked copy of the same story.
    for field in ('problem','approach','finding','impact'):data[field] = None
    for field in ('hook','summary','limitations'):
        if data.get(field):data[field]['text'] = readable_paragraphs(data[field]['text'])
    summary = data['summary']['text']
    if data.get('hook') and (same_sentence(data['hook']['text'], summary) or sentence_key(data['hook']['text']) == sentence_key(data['title'])):
        data['hook'] = None
    if data.get('limitations') and same_sentence(data['limitations']['text'], summary):data['limitations'] = None
    sections, seen = [], {sentence_key(summary)}
    for section in data.get('sections', []):
        section['heading'] = ' '.join(post_prose(section['heading']).split())
        section['body']['text'] = readable_paragraphs(section['body']['text'])
        key = sentence_key(section['body']['text'])
        if key not in seen:
            sections.append(section);seen.add(key)
    data['sections'] = sections
    for step in data.get('method_steps', []):step['text'] = ' '.join(post_prose(step['text']).split())
    for step in data.get('cooking_steps', []):step['text'] = ' '.join(post_prose(step['text']).split())
    return type(draft).model_validate(data)


def chat_prose(text):
    # Validate provider citation spans BEFORE this cleanup. Citations remain
    # separate response metadata; invented marker strings never become sources.
    lines, fence = [], None
    for line in clean_text(text,decode_entities=False).splitlines():
        trimmed = line.strip()
        if fence is not None:
            if trimmed.startswith(fence):fence = None
            lines.append(line);continue
        if trimmed.startswith(('```','~~~')):
            fence = trimmed[:3]
            lines.append(line);continue
        line = re.sub(r'[^]*|【[^】]*†[^】]*】', '', line)
        lines.append(line)
    return clean_text('\n'.join(lines),decode_entities=False)
