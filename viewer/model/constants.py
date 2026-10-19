"""Enumerations and layout constants mirroring the undo264 dumper (dump_info.c).

The integer codes below must match the `*_code` string tables that the dumper
writes in the "info" record; `parsing.parser` cross-checks them at load time.
"""


from __future__ import annotations

from enum import IntEnum, IntFlag

import numpy as np

SCHEMA_VERSION = "1.0"


class MbType(IntEnum):
    INTRA_4x4 = 0
    INTRA_8x8 = 1
    INTRA_16x16 = 2
    PCM = 3
    SKIP = 4
    DIRECT = 5
    INTER_16x16 = 6
    INTER_16x8 = 7
    INTER_8x16 = 8
    INTER_8x8 = 9


class SubMbType(IntEnum):
    NONE = -1  # MB is not Inter 8x8
    DIRECT = 0
    SUB_8x8 = 1
    SUB_8x4 = 2
    SUB_4x8 = 3
    SUB_4x4 = 4


class PredDir(IntFlag):
    """Per 8x8 partition prediction direction (bit 0 = L0, bit 1 = L1)."""
    NONE = 0  # intra MB
    L0 = 1
    L1 = 2
    BI = 3


class Intra4x4Mode(IntEnum):  # also used for Intra 8x8
    VERTICAL = 0
    HORIZONTAL = 1
    DC = 2
    DIAGONAL_DOWN_LEFT = 3
    DIAGONAL_DOWN_RIGHT = 4
    VERTICAL_RIGHT = 5
    HORIZONTAL_DOWN = 6
    VERTICAL_LEFT = 7
    HORIZONTAL_UP = 8


class Intra16x16Mode(IntEnum):
    VERTICAL = 0
    HORIZONTAL = 1
    DC = 2
    PLANE = 3


class IntraChromaMode(IntEnum):
    DC = 0
    HORIZONTAL = 1
    VERTICAL = 2
    PLANE = 3


NO_REF = -1          # "no picture" in ref_dec_idx / DPB arrays
NUM_DPB_SLOTS = 17   # length of dpb_status.slots as dumped
NUM_REF_ENTRIES = 16  # length of dpb_status.l0 / l1 as dumped

CBP_LUMA_MASK = 0x0F  # cbp = (cbp_chroma << 4) | cbp_luma


def _blk_idx_to_raster(i: int) -> int:
    """luma4x4BlkIdx (8x8-quadrant zig-zag order, §6.4.3) -> raster index y*4+x."""
    x = (i & 1) | (((i >> 2) & 1) << 1)
    y = ((i >> 1) & 1) | (((i >> 3) & 1) << 1)
    return y * 4 + x


# The dumper writes motion vectors per 4x4 block in luma4x4BlkIdx order.
# The parser re-orders them to raster order using this table.
BLK_IDX_TO_RASTER = np.array([_blk_idx_to_raster(i) for i in range(16)], dtype=np.intp)