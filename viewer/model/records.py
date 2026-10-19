"""Typed objects for each record kind in an undo264 info dump.

All per-macroblock data in `Frame` is stored as numpy arrays indexed by
macroblock address (raster order, `mbAddr = mb_y * mb_width + mb_x`).
Use `Frame.grid()` / `Frame.mv_grid()` to get (mb_height, mb_width, ...) views.
"""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

import numpy as np

from .constants import CBP_LUMA_MASK, NO_REF, MbType


# ---------------------------------------------------------------- header records

@dataclass(frozen=True, eq=False)
class InfoRecord:
    schema_version: str
    cli_options: dict[str, bool]
    mb_types_code: tuple[str, ...]
    sub_mb_types_code: tuple[str, ...]
    intra_pred_4x4_8x8_mode_code: tuple[str, ...]
    intra_pred_16x16_mode_code: tuple[str, ...]
    intra_pred_chroma_mode_code: tuple[str, ...]


@dataclass(frozen=True)
class SPS:
    profile: str
    level_idc: int
    sps_id: int
    max_frame_num: int
    poc_type: int
    max_pic_order_cnt_lsb: int | None  # only dumped for poc_type 0
    max_num_ref_frames: int
    gaps_in_frame_num_allowed: bool
    pic_width_in_mbs: int
    pic_height_in_mbs: int
    pic_width_luma: int
    pic_height_luma: int
    direct_8x8_inference: bool
    frame_cropping_flag: bool
    crop: tuple[int, int, int, int] | None  # (left, right, top, bottom) in samples

    @property
    def num_mbs(self) -> int:
        return self.pic_width_in_mbs * self.pic_height_in_mbs


@dataclass(frozen=True)
class PPS:
    pps_id: int
    sps_id: int
    entropy_coding_mode: str  # "CABAC" | "CAVLC"
    num_ref_idx_l0_default_active: int
    num_ref_idx_l1_default_active: int
    weighted_pred: bool
    weighted_bipred_idc: int
    pic_init_qp: int
    pic_init_qs: int
    chroma_qp_index_offset: int
    deblocking_filter_control_present: bool
    constrained_intra_pred: bool
    redundant_pic_cnt_present: bool
    transform_8x8_mode: bool
    second_chroma_qp_index_offset: int

    @property
    def cabac(self) -> bool:
        return self.entropy_coding_mode == "CABAC"


@dataclass(frozen=True)
class UnknownRecord:
    type: str
    content: dict


# ---------------------------------------------------------------- frame records

@dataclass(frozen=True)
class SliceHeader:
    first_mb: int
    slice_type: str  # "I" | "P" | "B" (| "SP" | "SI"), as printed by the decoder
    pps_id: int
    frame_num: int
    idr_pic: bool
    slice_qp: int
    idr_pic_id: int | None = None
    poc_lsb: int | None = None
    direct_mv_pred_mode: str | None = None  # "spatial" | "temporal" (B only)
    num_ref_idx_active_override: bool | None = None  # P/B only
    num_ref_idx_l0_active: int | None = None  # only when override
    num_ref_idx_l1_active: int | None = None  # only when override (B)
    cabac_init_idc: int | None = None
    disable_deblocking_filter_idc: int | None = None
    slice_alpha_c0_offset: int | None = None
    slice_beta_offset: int | None = None

    @property
    def is_i(self) -> bool:
        return self.slice_type in ("I", "SI")

    @property
    def is_p(self) -> bool:
        return self.slice_type in ("P", "SP")

    @property
    def is_b(self) -> bool:
        return self.slice_type == "B"


@dataclass(eq=False)
class DpbStatus:
    """DPB state as seen by the last slice of the picture.

    `slots`: decoded_idx of the picture held in each DPB slot (-1 = empty).
    `l0`/`l1`: decoded_idx of each reference list entry (-1 = empty). The
    decoder dumps every populated entry, not only the first
    num_ref_idx_active ones; see `Frame.num_ref_idx_active()`.
    """
    slots_size: int
    lists_size: int
    slots: np.ndarray  # (slots_size,) int32
    l0: np.ndarray  # (lists_size,) int32
    l1: np.ndarray  # (lists_size,) int32

    def ref_list(self, list_idx: int) -> list[int]:
        arr = self.l1 if list_idx else self.l0
        return [int(v) for v in arr if v != NO_REF]

    def occupied_slots(self) -> list[int]:
        return [int(v) for v in self.slots if v != NO_REF]


@dataclass(eq=False)
class Frame:
    # picture header
    decoded_idx: int
    output_idx: int
    poc: int
    frame_num: int
    idr: bool

    # context resolved by the parser (see StreamParser notes on parameter-set lag)
    sps: SPS
    pps: PPS  # PPS of the first slice

    slices: tuple[SliceHeader, ...]
    dpb: DpbStatus

    mb_width: int
    mb_height: int

    # --- per-macroblock arrays, N = mb_width * mb_height --------------------
    mb_type: np.ndarray  # (N,) int8      MbType code
    sub_mb_type: np.ndarray  # (N,4) int8     SubMbType code per 8x8, -1 if not 8x8
    qpy: np.ndarray  # (N,) int16
    cbp: np.ndarray  # (N,) int16     (chroma << 4) | luma
    t8x8: np.ndarray  # (N,) bool      transform_size_8x8_flag
    mb_slice_idx: np.ndarray  # (N,) int16     index into `slices`

    intra_modes: np.ndarray  # (N,16) int8    1 (16x16) / 4 (8x8) / 16 (4x4) valid entries, -1 padded
    intra_mode_count: np.ndarray  # (N,) int8      0, 1, 4 or 16
    chroma_intra_modes: np.ndarray  # (N,) int8      -1 for non-intra / PCM

    pred_dir: np.ndarray  # (N,4) uint8    PredDir per 8x8 partition, 0 for intra
    ref_dec_idx: np.ndarray  # (2,N,4) int32  [list][mb][8x8] decoded_idx of the ref picture, -1 unused
    mv: np.ndarray  # (2,N,4,4,2) int16  [list][mb][y4][x4] = (mvx, mvy) quarter-pel, raster 4x4 order
    mv_valid: np.ndarray  # (2,N,4,4) bool  True where that list is used for the 4x4 block

    # ------------------------------------------------------------------ helpers
    @property
    def num_mbs(self) -> int:
        return self.mb_width * self.mb_height

    @property
    def width(self) -> int:
        return self.mb_width * 16

    @property
    def height(self) -> int:
        return self.mb_height * 16

    @property
    def cbp_luma(self) -> np.ndarray:
        return (self.cbp & CBP_LUMA_MASK).astype(np.int8)

    @property
    def cbp_chroma(self) -> np.ndarray:
        return (self.cbp >> 4).astype(np.int8)

    @property
    def intra_mask(self) -> np.ndarray:
        return self.mb_type <= MbType.PCM

    @property
    def inter_mask(self) -> np.ndarray:
        return self.mb_type >= MbType.SKIP

    def grid(self, arr: np.ndarray) -> np.ndarray:
        """Reshape an (N, ...) per-MB array to (mb_height, mb_width, ...)."""
        return arr.reshape(self.mb_height, self.mb_width, *arr.shape[1:])

    def mv_grid(self, list_idx: int) -> np.ndarray:
        """(mb_height, mb_width, 4, 4, 2) motion vectors for list 0 or 1."""
        return self.grid(self.mv[list_idx])

    def referenced_decoded_indices(self) -> frozenset[int]:
        """decoded_idx of every picture referenced by at least one partition."""
        refs = np.unique(self.ref_dec_idx)
        return frozenset(int(r) for r in refs if r != NO_REF)

    def num_ref_idx_active(self, slice_idx: int = 0) -> tuple[int, int]:
        """(n_l0, n_l1) active reference counts for a slice (0 for unused lists)."""
        sh = self.slices[slice_idx]
        if sh.is_i:
            return 0, 0
        if sh.num_ref_idx_active_override:
            n0 = sh.num_ref_idx_l0_active or 0
            n1 = sh.num_ref_idx_l1_active or 0
        else:
            n0 = self.pps.num_ref_idx_l0_default_active
            n1 = self.pps.num_ref_idx_l1_default_active
        return n0, (n1 if sh.is_b else 0)


Record = Union[InfoRecord, SPS, PPS, Frame, UnknownRecord]