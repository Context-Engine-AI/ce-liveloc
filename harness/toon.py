"""Minimal TOON decoder for CE's text replies (the agent sees this text; structuredContent carries the JSON).

Covers the forms CE emits: `key: value` scalars, nested `key:` objects, `key[N]:` list blocks with `- ` items,
inline primitive arrays `key[N]: a,"b"`, and tabular arrays `key[N]{f1,f2}:` followed by N comma rows.
Strings are JSON-quoted when needed; bare tokens are numbers, true/false/null or plain strings.
"""
import json
import re

_HEAD = re.compile(r'^("(?:[^"\\]|\\.)*"|[^:\[\]{}"]+?)(?:\[(\d+)\])?(?:\{([^}]*)\})?:(?:\s(.*))?$')
# toon-format 0.5 emits controls other than newline/carriage return/tab literally.
_DEC = json.JSONDecoder(strict=False)


def _scalar(tok):
    tok = tok.strip()
    if tok.startswith('"'):
        return _DEC.decode(tok)
    if '"' in tok:
        raise ValueError(f"invalid TOON scalar: {tok[:60]!r}")
    if tok in ("true", "false", "null"):
        return {"true": True, "false": False, "null": None}[tok]
    if re.fullmatch(r"-?\d+", tok):
        return int(tok)
    if re.fullmatch(r"-?\d+\.\d*(?:[eE][-+]?\d+)?", tok):
        return float(tok)
    return tok


def _split_row(s):
    """Split comma-separated tokens without losing quoted delimiters or framing."""
    out, i = [], 0
    while i <= len(s):
        while i < len(s) and s[i] == " ":
            i += 1
        if i < len(s) and s[i] == '"':
            _, end = _DEC.raw_decode(s, i)
            out.append(s[i:end])
            i = end
            while i < len(s) and s[i] == " ":
                i += 1
            if i < len(s) and s[i] != ",":
                raise ValueError(f"expected comma after quoted token: {s[i:i + 60]!r}")
        else:
            j = s.find(",", i)
            j = len(s) if j < 0 else j
            token = s[i:j].strip()
            if not token or '"' in token:
                raise ValueError(f"invalid TOON row token: {token[:60]!r}")
            out.append(token)
            i = j
        i += 1
    return out


def _key(k):
    return _DEC.decode(k) if k.startswith('"') else k.strip()


def _lines(text):
    return [(len(line) - len(line.lstrip(" ")), line.strip()) for line in text.split("\n") if line.strip()]


def _block(lines, i, indent):
    """Parse `key...` lines at exactly `indent` into a dict; returns (dict, next index)."""
    obj = {}
    while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
        i = _field(lines, i, indent, lines[i][1], obj)
    return obj, i


def _field(lines, i, indent, content, obj):
    m = _HEAD.match(content)
    if not m:
        raise ValueError(f"not TOON: {content[:60]!r}")
    key, n, fields, rest = _key(m.group(1)), m.group(2), m.group(3), m.group(4)
    if fields is not None and (n is None or rest):
        raise ValueError(f"invalid TOON table header: {content[:60]!r}")
    i += 1
    child = lines[i][0] if i < len(lines) else -1
    if n is not None and fields is not None:  # tabular rows
        names = [_key(f) for f in _split_row(fields)]
        rows = []
        while child > indent and i < len(lines) and lines[i][0] == child:
            values = [_scalar(v) for v in _split_row(lines[i][1])]
            if len(values) != len(names):
                raise ValueError(f"TOON row width: expected {len(names)}, got {len(values)}")
            rows.append(dict(zip(names, values)))
            i += 1
        obj[key] = rows
    elif n is not None and rest:  # inline primitive array
        obj[key] = [_scalar(v) for v in _split_row(rest)]
    elif n is not None:  # list block of "- " items
        items = []
        while child > indent and i < len(lines) and lines[i][0] == child and lines[i][1].startswith("- "):
            item_indent, body = lines[i][0], lines[i][1][2:]
            if _HEAD.match(body):  # object item: first field on the dash line, the rest indented by 2
                item = {}
                i = _field(lines, i, item_indent + 2, body, item)
                more, i = _block(lines, i, item_indent + 2)
                item.update(more)
                items.append(item)
            else:
                items.append(_scalar(body))
                i += 1
        obj[key] = items
    elif rest is None or rest == "":
        if child > indent:  # nested object
            obj[key], i = _block(lines, i, child)
        else:
            obj[key] = {}  # `key:` with nothing nested is an empty object
    else:
        obj[key] = _scalar(rest)
    if n is not None and len(obj[key]) != int(n):
        raise ValueError(f"TOON array length: expected {n}, got {len(obj[key])}")
    return i


def loads(text):
    """Decode a TOON document into Python objects; raises ValueError when the text is not TOON."""
    lines = _lines(text)
    if not lines:
        raise ValueError("empty")
    obj, i = _block(lines, 0, lines[0][0])
    if i != len(lines):
        raise ValueError(f"unparsed TOON at line {i}: {lines[i][1][:60]!r}")
    return obj
