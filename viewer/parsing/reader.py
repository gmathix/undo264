"""Streaming access to an undo264 info dump (one JSON record per line)."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from model import Record
from .parser import ParseError, StreamParser


# reader.py
def iter_records(path: str | Path, parser: StreamParser | None = None) -> Iterator[Record]:
    parser = parser or StreamParser()
    with open(path, "rb") as f:
        # Pass raw lines directly to the parser, skipping strip() and empty checks
        # for maximum throughput if you can guarantee no empty lines in the dump.
        for lineno, line in enumerate(f, 1):
            if len(line) > 1:  # cheap check for non-empty lines (ignoring \n)
                try:
                    yield parser.parse_line(line)
                except ParseError as e:
                    raise ParseError(f"{path}:{lineno}: {e}") from None