"""Picture-reference resolution (decoded_idx -> output position). Qt-free.

Frames are stored in output order but reference each other by `decoded_idx`.
Because the `StreamIndex` already knows every frame's decoded_idx -> output_idx,
resolving references needs no parsing: a parsed `Frame` is enough to say which
*other* output positions it depends on, and `FrameStore.set_window(extra=...)`
keeps those resident.
"""
from __future__ import annotations

from dataclasses import dataclass

from model import NO_REF, Frame

from .index import StreamIndex


@dataclass(frozen=True)
class ResolvedReference:
    decoded_idx: int
    output_idx: int | None  # None: picture is not part of the dump (e.g. precedes the first frame)


class ReferenceResolver:
    def __init__(self, index: StreamIndex) -> None:
        self.index = index

    def resolve(self, decoded_idx: int) -> ResolvedReference:
        return ResolvedReference(decoded_idx, self.index.output_idx_of(decoded_idx))

    def used(self, frame: Frame) -> list[ResolvedReference]:
        """Pictures actually referenced by at least one partition, in decoded_idx order."""
        return [self.resolve(d) for d in sorted(frame.referenced_decoded_indices())]

    def list_entries(self, frame: Frame, list_idx: int, active_only: bool = True) -> list[ResolvedReference]:
        """Reference list 0/1 as resolved pictures (optionally cut to num_ref_idx_active)."""
        entries = frame.dpb.ref_list(list_idx)
        if active_only:
            entries = entries[: frame.num_ref_idx_active()[list_idx]]
        return [self.resolve(d) for d in entries]

    def dependency_outputs(self, frame: Frame) -> list[int]:
        """Output indices of every resolvable picture `frame` depends on (what to keep loaded)."""
        outs = [r.output_idx for r in self.used(frame) if r.output_idx is not None]
        for li in (0, 1):
            outs += [r.output_idx for r in self.list_entries(frame, li) if r.output_idx is not None]
        return sorted(dict.fromkeys(outs))