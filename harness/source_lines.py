"""Attribute returned excerpts to matching lines of a frozen UTF-8 checkout."""

from itertools import groupby
from pathlib import Path


def verified_lines(path: str, snippet: str, span: tuple[int, int | None]) -> list[tuple[int, int]]:
    """Return matching source windows at the claimed, inclusive coordinates.

    Ignore leading/trailing ASCII spaces and tabs (display indentation), and normalize
    CRLF to LF. Preserve every other character, including interior whitespace and
    form-feed. A matching window needs nonblank code; its returned blank lines count
    too, just as in file reads. Never search elsewhere or infer omitted coverage.
    """
    start, end = span
    if start < 1 or (end is not None and end < start):
        return []
    try:
        source = Path(path).read_bytes().decode("utf-8").replace("\r\n", "\n").removesuffix("\n").split("\n")
    except (OSError, UnicodeDecodeError):
        return []  # Unavailable source cannot establish evidence.
    shown = snippet.replace("\r\n", "\n").removesuffix("\n").split("\n")
    pairs = zip(shown, source[start - 1:end])
    result: list[tuple[int, int]] = []
    for matches, block in groupby(enumerate(pairs, start),
                                  key=lambda row: row[1][0].strip(" \t") == row[1][1].strip(" \t")):
        if matches:
            window = list(block)
            if any(got.strip() for _, (got, _) in window):
                result.extend((line, line) for line, _ in window)
    return result
