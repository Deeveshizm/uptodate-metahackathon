"""Read ignored local settings without shell evaluation or printing secrets."""
import os
from pathlib import Path
import re
import shlex


def load_settings(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.removeprefix('export ').partition('=')
        key = key.strip()
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise ValueError('Invalid settings entry; use NAME=value')
        words = shlex.split(value, comments=False)
        if len(words) > 1:
            raise ValueError('Settings values containing spaces must be quoted')
        values[key] = words[0] if words else ''
    return values


def configure(path):
    values = load_settings(path)
    os.environ.update(values)
    return values
