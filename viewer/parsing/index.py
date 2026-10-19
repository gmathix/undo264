"""One-pass index of an undo264 info dump (one JSON record per line).

The dump is scanned once with ``mmap`` without decoding the big per-macroblock
payloads: only the first bytes of each frame line are inspected to read
``decoded_idx`` / ``output_idx``. The result lets any frame be parsed later,
independently, from ``(offset, length)``.

Parameter sets: SPS/PPS are dumped at parse time while frames are dumped in
output order, so a frame must be parsed with "the most recent SPS/PPS seen
before it in the file". The index records that state as *epochs* (a new epoch
starts only when an SPS/PPS line actually changes content), and every frame
points at its epoch, so random-access parsing gives the same result as a
linear read.
"""
from __future__ import annotations

import mmap
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .parser import ParseError, StreamParser, _loads

_KIND_RE = re.compile(rb'^\s*\{\s*"type"\s*:\s*"([A-Za-z_]+)"')
_FRAME_IDX_RE = re.compile(rb'"decoded_idx"\s*:\s*(-?\d+)\s*,\s*"output_idx"\s*:\s*(-?\d+)')
_HEAD = 400  # bytes of a frame line inspected for the picture header

Span = tuple[int, int]  # (offset, length) of one line, without line terminator


@dataclass(frozen=True)
class FrameRef:
    decoded_idx: int
    output_idx: int
    offset: int
    length: int
    epoch: int  # index into StreamIndex.epochs


def build_parser(lines: Iterable[bytes]) -> StreamParser:
    """A StreamParser primed with header (info/SPS/PPS) lines."""
    parser = StreamParser()
    for line in lines:
        parser.parse_line(line)
    return parser


class StreamIndex:
    def __init__(self, path: Path, frames: list[FrameRef], epochs: list[tuple[Span, ...]]):
        self.path = path
        self.frames = frames  # sorted by output_idx
        self.epochs = epochs  # header spans (info + current SPS/PPS), file order
        self._by_output = {f.output_idx: f for f in frames}
        self._by_decoded = {f.decoded_idx: f for f in frames}
        self._parsers: dict[int, StreamParser] = {}

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, path: str | Path) -> "StreamIndex":
        path = Path(path)
        frames: list[FrameRef] = []
        epochs: list[tuple[Span, ...]] = []
        current: dict[tuple[str, int], Span] = {}  # header key -> span
        raw: dict[tuple[str, int], bytes] = {}
        dirty = True

        with open(path, "rb") as f:
            if path.stat().st_size == 0:
                raise ParseError(f"{path}: empty file")
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                size = len(mm)
                pos = 0
                while pos < size:
                    nl = mm.find(b"\n", pos)
                    end = size if nl < 0 else nl
                    nxt = end + 1
                    while end > pos and mm[end - 1] in (10, 13):  # \n \r
                        end -= 1
                    if end == pos:  # blank line
                        pos = nxt
                        continue
                    m = _KIND_RE.match(mm[pos:min(end, pos + _HEAD)])
                    kind = m.group(1) if m else None
                    if kind == b"frame":
                        head = mm[pos:min(end, pos + _HEAD)]
                        mi = _FRAME_IDX_RE.search(head)
                        if mi:
                            dec, out = int(mi.group(1)), int(mi.group(2))
                        else:  # unexpected key order: slow but correct
                            c = _loads(mm[pos:end])["content"]
                            dec, out = c["decoded_idx"], c["output_idx"]
                        if dirty:
                            epochs.append(tuple(sorted(current.values())))
                            dirty = False
                        frames.append(FrameRef(dec, out, pos, end - pos, len(epochs) - 1))
                    elif kind in (b"info", b"sps", b"pps"):
                        line = mm[pos:end]
                        c = _loads(line)["content"]
                        key = (kind.decode(), c.get(f"{kind.decode()}_id", 0))
                        if raw.get(key) != line:
                            raw[key] = line
                            current[key] = (pos, end - pos)
                            dirty = True
                    pos = nxt
            finally:
                mm.close()

        if not frames:
            raise ParseError(f"{path}: no frame records")
        frames.sort(key=lambda r: r.output_idx)
        idx = cls(path, frames, epochs)
        idx._validate()
        return idx

    def _validate(self) -> None:
        if len(self._by_output) != len(self.frames):
            warnings.warn("duplicate output_idx values in dump; last occurrence wins", stacklevel=3)
        if len(self._by_decoded) != len(self.frames):
            warnings.warn("duplicate decoded_idx values in dump; last occurrence wins", stacklevel=3)
        if [f.output_idx for f in self.frames] != list(range(len(self.frames))):
            warnings.warn("output_idx values are not contiguous 0..N-1", stacklevel=3)

    # ----------------------------------------------------------------- lookup
    @property
    def num_frames(self) -> int:
        return len(self.frames)

    def by_output(self, output_idx: int) -> FrameRef | None:
        return self._by_output.get(output_idx)

    def output_idx_of(self, decoded_idx: int) -> int | None:
        """Output index of the picture with this decoded_idx (None if not in the dump)."""
        ref = self._by_decoded.get(decoded_idx)
        return None if ref is None else ref.output_idx

    def read(self, offset: int, length: int) -> bytes:
        with open(self.path, "rb") as f:
            f.seek(offset)
            return f.read(length)

    def parser_for_epoch(self, epoch: int = 0) -> StreamParser:
        """Parser primed with the SPS/PPS/info valid for `epoch` (cached)."""
        p = self._parsers.get(epoch)
        if p is None:
            p = self._parsers[epoch] = build_parser(self.read(o, n) for o, n in self.epochs[epoch])
        return p