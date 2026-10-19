"""Macroblock partition geometry. Qt-free (shared by the viewport and the inspector).

Rectangles are in pixels *inside* the 16x16 macroblock. Inter partitions follow
`mb_type` / `sub_mb_type`; where the bitstream does not spell the partitioning
out (B_Skip, B_Direct_16x16, direct sub-macroblocks) it is recovered from the
data: the largest square of 4x4 blocks that share motion vectors and references.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from model import BLK_IDX_TO_RASTER, MbType, SubMbType


@dataclass(frozen=True)
class Partition:
    x: int
    y: int
    w: int
    h: int
    idx: int = 0  # intra: luma4x4BlkIdx (4x4) / 8x8 index; inter: order in the MB

    @property
    def blk(self) -> tuple[int, int]:
        """(bx, by): top-left 4x4 block of the partition (index into mv[..., by, bx, :])."""
        return self.x // 4, self.y // 4

    @property
    def blk8(self) -> int:
        """Index (0..3, raster) of the 8x8 quadrant holding the partition's top-left corner:
        indexes `pred_dir[mb]` and `ref_dec_idx[list, mb]`."""
        return (self.y // 8) * 2 + (self.x // 8)

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


def _uniform(frame, mb: int, bx: int, by: int, n: int) -> bool:
    """True if the n x n 4x4 blocks at (bx, by) share validity, motion vectors and references."""
    mv = frame.mv[:, mb, by:by + n, bx:bx + n]
    va = frame.mv_valid[:, mb, by:by + n, bx:bx + n]
    key = np.where(va[..., None], mv, 0)
    if not ((key == key[:, :1, :1]).all() and (va == va[:, :1, :1]).all()):
        return False
    q = [(by // 2 + dy) * 2 + (bx // 2 + dx) for dy in range(max(1, n // 2)) for dx in range(max(1, n // 2))]
    refs = frame.ref_dec_idx[:, mb][:, q]
    return bool((refs == refs[:, :1]).all())


def _quadrant_rects(frame, mb: int, qx: int, qy: int) -> list[tuple[int, int, int, int]]:
    """An 8x8 quadrant: dynamically infer its sub-partitioning from motion vector uniformity."""
    bx, by = qx // 4, qy // 4

    def check_mv(dx, dy, w, h):
        """Checks if a WxH grid of 4x4 blocks share the exact same MV and validity."""
        mv = frame.mv[:, mb, by + dy:by + dy + h, bx + dx:bx + dx + w]
        va = frame.mv_valid[:, mb, by + dy:by + dy + h, bx + dx:bx + dx + w]
        key = np.where(va[..., None], mv, 0)
        return bool((key == key[:, :1, :1]).all() and (va == va[:, :1, :1]).all())

    # Sub-partitions inside an 8x8 always share the same reference picture index,
    # so we only need to verify if the motion vectors are unified.
    if check_mv(0, 0, 2, 2):
        return [(qx, qy, 8, 8)]
    if check_mv(0, 0, 2, 1) and check_mv(0, 1, 2, 1):
        return [(qx, qy, 8, 4), (qx, qy + 4, 8, 4)]
    if check_mv(0, 0, 1, 2) and check_mv(1, 0, 1, 2):
        return [(qx, qy, 4, 8), (qx + 4, qy, 4, 8)]

    return [(qx + dx, qy + dy, 4, 4) for dy in (0, 4) for dx in (0, 4)]


def inter_partitions(frame, mb: int) -> list[Partition]:
    t = int(frame.mb_type[mb])
    rects: list[tuple[int, int, int, int]]

    if t == MbType.INTER_16x16:
        rects = [(0, 0, 16, 16)]
    elif t == MbType.INTER_16x8:
        rects = [(0, 0, 16, 8), (0, 8, 16, 8)]
    elif t == MbType.INTER_8x16:
        rects = [(0, 0, 8, 16), (8, 0, 8, 16)]
    elif t in (MbType.SKIP, MbType.DIRECT):
        is_b = frame.slices[int(frame.mb_slice_idx[mb])].is_b
        if t == MbType.SKIP and not is_b:  # P_Skip
            rects = [(0, 0, 16, 16)]
        elif _uniform(frame, mb, 0, 0, 4):
            rects = [(0, 0, 16, 16)]
        else:
            rects = [r for q in range(4) for r in _quadrant_rects(frame, mb, (q & 1) * 8, (q >> 1) * 8)]
    elif t == MbType.INTER_8x8:
        rects = []
        for q in range(4):
            qx, qy = (q & 1) * 8, (q >> 1) * 8
            # Replaced sub_mb_type enum lookup with direct structural inference
            rects += _quadrant_rects(frame, mb, qx, qy)
    else:
        return []
    return [Partition(*r, idx=i) for i, r in enumerate(rects)]


def intra_partitions(frame, mb: int) -> list[Partition]:
    """Blocks carrying an intra prediction mode; `idx` indexes `frame.intra_modes[mb]`."""
    t = int(frame.mb_type[mb])
    if t == MbType.INTRA_4x4:
        out = []
        for i in range(16):
            r = int(BLK_IDX_TO_RASTER[i])
            out.append(Partition((r & 3) * 4, (r >> 2) * 4, 4, 4, idx=i))
        return out
    if t == MbType.INTRA_8x8:
        return [Partition((q & 1) * 8, (q >> 1) * 8, 8, 8, idx=q) for q in range(4)]
    if t in (MbType.INTRA_16x16, MbType.PCM):
        return [Partition(0, 0, 16, 16, idx=0)]
    return []


def partitions(frame, mb: int) -> list[Partition]:
    return intra_partitions(frame, mb) if frame.mb_type[mb] <= MbType.PCM else inter_partitions(frame, mb)