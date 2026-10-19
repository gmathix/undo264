"""JSON record -> typed object conversion.

`StreamParser` is stateful: it remembers the info header and the SPS/PPS seen
so far, and resolves them when a frame record is parsed.

Parameter-set caveat: frames are dumped in *output* order while SPS/PPS are
dumped when they are *parsed* (decode order). A frame is therefore bound to the
most recent SPS/PPS with the right id seen before it in the file. That is
exact as long as a re-sent SPS/PPS has identical content (as in the sample
dump); if parameter sets change mid-stream, a frame still waiting for output
could pick up the newer one. Adding an sps/pps index to the frame record in
the dumper would remove the ambiguity.
"""


from __future__ import annotations

import json
import warnings
from itertools import chain
from typing import Any, Sequence

import numpy as np

from model import BLK_IDX_TO_RASTER, SCHEMA_VERSION, MbType
from model import (
    PPS,
    SPS,
    DpbStatus,
    Frame,
    InfoRecord,
    Record,
    SliceHeader,
    UnknownRecord,
)

try:  # optional accelerator
    import orjson

    _loads = orjson.loads
except ImportError:  # pragma: no cover
    _loads = json.loads


class ParseError(ValueError):
    pass


# ------------------------------------------------------------ small converters

def _parse_info(c: dict) -> InfoRecord:
    if c["schema_version"] != SCHEMA_VERSION:
        warnings.warn(
            f"info schema_version {c['schema_version']!r} != supported {SCHEMA_VERSION!r}",
            stacklevel=3,
        )
    expected_mb_types = [t.name for t in MbType]  # sanity check on code tables
    if len(c["mb_types_code"]) != len(expected_mb_types):
        warnings.warn("mb_types_code table size differs from MbType enum", stacklevel=3)
    return InfoRecord(
        schema_version=c["schema_version"],
        cli_options=dict(c["decoding_cli_options"]),
        mb_types_code=tuple(c["mb_types_code"]),
        sub_mb_types_code=tuple(c["sub_mb_types_code"]),
        intra_pred_4x4_8x8_mode_code=tuple(c["intra_pred_4x4_8x8_mode_code"]),
        intra_pred_16x16_mode_code=tuple(c["intra_pred_16x16_mode_code"]),
        intra_pred_chroma_mode_code=tuple(c["intra_pred_chroma_mode_code"]),
    )


def _parse_sps(c: dict) -> SPS:
    crop = None
    if c["frame_cropping_flag"]:
        crop = (
            c["frame_crop_left_offset_samples"],
            c["frame_crop_right_offset_samples"],
            c["frame_crop_top_offset_samples"],
            c["frame_crop_bottom_offset_samples"],
        )
    return SPS(
        profile=c["profile"],
        level_idc=c["level_idc"],
        sps_id=c["sps_id"],
        max_frame_num=c["max_frame_num"],
        poc_type=c["poc_type"],
        max_pic_order_cnt_lsb=c.get("max_pic_order_cnt_lsb"),
        max_num_ref_frames=c["max_num_ref_frames"],
        gaps_in_frame_num_allowed=c["gaps_in_frame_num_allowed"],
        pic_width_in_mbs=c["pic_width_in_mbs"],
        pic_height_in_mbs=c["pic_height_in_mbs"],
        pic_width_luma=c["pic_width_luma"],
        pic_height_luma=c["pic_height_luma"],
        direct_8x8_inference=c["direct_8x8_inference"],
        frame_cropping_flag=c["frame_cropping_flag"],
        crop=crop,
    )


def _parse_pps(c: dict) -> PPS:
    return PPS(**{f: c[f] for f in PPS.__dataclass_fields__})


def _parse_slice(d: dict) -> SliceHeader:
    return SliceHeader(
        first_mb=d["first_mb"],
        slice_type=d["slice_type"],
        pps_id=d["pps_id"],
        frame_num=d["frame_num"],
        idr_pic=d["idr_pic"],
        slice_qp=d["slice_qp"],
        idr_pic_id=d.get("idr_pic_id"),
        poc_lsb=d.get("poc_lsb"),
        direct_mv_pred_mode=d.get("direct_mv_pred_mode"),
        num_ref_idx_active_override=d.get("num_ref_idx_active_override"),
        num_ref_idx_l0_active=d.get("num_ref_idx_l0_active"),
        num_ref_idx_l1_active=d.get("num_ref_idx_l1_active"),
        cabac_init_idc=d.get("cabac_init_idc"),
        disable_deblocking_filter_idc=d.get("disable_deblocking_filter_idc"),
        slice_alpha_c0_offset=d.get("slice_alpha_c0_offset"),
        slice_beta_offset=d.get("slice_beta_offset"),
    )


def _parse_dpb(d: dict) -> DpbStatus:
    return DpbStatus(
        slots_size=d["slots_size"],
        lists_size=d["lists_size"],
        slots=np.asarray(d["slots"], dtype=np.int32),
        l0=np.asarray(d["l0"], dtype=np.int32),
        l1=np.asarray(d["l1"], dtype=np.int32),
    )


# ------------------------------------------------------- per-MB column helpers

def _column(c: dict, key: str, n: int) -> list:
    col = c[key]
    if len(col) != n:
        raise ParseError(f"column {key!r} has {len(col)} entries, expected {n}")
    return col


def _row_counts(raw: Sequence[Sequence], n: int, what: str) -> np.ndarray:
    if len(raw) != n:
        raise ParseError(f"column {what!r} has {len(raw)} entries, expected {n}")
    return np.fromiter(map(len, raw), dtype=np.intp, count=n)


def _ragged_rows(
    raw: Sequence[Sequence[int]],
    n: int,
    width: int,
    fill: int,
    dtype: Any,
    allowed: tuple[int, ...] = (),
    what: str = "column",
) -> np.ndarray:
    """(n, width) array from rows that may be empty or shorter than `width`.

    No per-row Python work: row lengths and the flattened values are pulled out
    with C-level iterators, then scattered with one fancy-indexing assignment.
    """
    counts = _row_counts(raw, n, what)
    total = int(counts.sum())
    if total == 0:
        return np.full((n, width), fill, dtype=dtype)
    if allowed:
        bad = ~np.isin(counts, (0, *allowed))
        if bad.any():
            i = int(np.flatnonzero(bad)[0])
            raise ParseError(f"{what}[{i}] has {counts[i]} entries, expected one of {allowed}")
    flat = np.fromiter(chain.from_iterable(raw), dtype=dtype, count=total)
    if (counts == width).all():  # dense rectangle (e.g. pred_dir, ref_dec_idx)
        return flat.reshape(n, width)
    out = np.full((n, width), fill, dtype=dtype)
    rows = np.repeat(np.arange(n), counts)
    cols = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
    out[rows, cols] = flat
    return out


def _parse_mv_list(raw: Sequence[Sequence[Sequence[int]]], n: int, what: str = "mv") -> tuple[np.ndarray, np.ndarray]:
    """One list's motion vectors -> ((n,4,4,2) int16, (n,4,4) bool), raster 4x4 order.

    `raw[mb]` is [] (intra / list unused) or 16 entries in luma4x4BlkIdx order,
    each [] or [mvx, mvy].
    """
    mv = np.zeros((n, 16, 2), dtype=np.int16)
    valid = np.zeros((n, 16), dtype=bool)
    counts = _row_counts(raw, n, what)
    bad = (counts != 0) & (counts != 16)
    if bad.any():
        mb = int(np.flatnonzero(bad)[0])
        raise ParseError(f"{what}[{mb}] has {counts[mb]} blocks, expected 16")
    rows = np.flatnonzero(counts)
    if rows.size:
        blocks = list(chain.from_iterable(raw))  # 16 per non-empty MB
        lens = np.fromiter(map(len, blocks), dtype=np.int8, count=len(blocks))
        if ((lens != 0) & (lens != 2)).any():
            raise ParseError(f"{what}: motion vector with a length other than 2")
        ok = (lens == 2).reshape(-1, 16)
        vals = np.fromiter(chain.from_iterable(blocks), dtype=np.int16, count=2 * int(ok.sum()))
        mv_blk = np.zeros((rows.size, 16, 2), dtype=np.int16)
        mv_blk[ok] = vals.reshape(-1, 2)
        # luma4x4BlkIdx -> raster: output slot BLK_IDX_TO_RASTER[blk] receives blk
        mv[rows[:, None], BLK_IDX_TO_RASTER[None, :]] = mv_blk
        valid[rows[:, None], BLK_IDX_TO_RASTER[None, :]] = ok
    return mv.reshape(n, 4, 4, 2), valid.reshape(n, 4, 4)


def _mb_slice_map(slices: tuple[SliceHeader, ...], n: int) -> np.ndarray:
    """Slice index of every MB, assuming slices cover MBs in increasing first_mb order (no ASO/FMO)."""
    first = np.array([s.first_mb for s in slices])
    order = np.argsort(first, kind="stable")
    pos = np.searchsorted(first[order], np.arange(n), side="right") - 1
    return order[np.maximum(pos, 0)].astype(np.int16)


# ----------------------------------------------------------------- the parser

class StreamParser:
    def __init__(self) -> None:
        self.info: InfoRecord | None = None
        self.sps: dict[int, SPS] = {}
        self.pps: dict[int, PPS] = {}

    def parse_line(self, line: str | bytes) -> Record:
        try:
            obj = _loads(line)
        except ValueError as e:
            raise ParseError(f"invalid JSON: {e}") from None
        return self.parse_record(obj)

    def parse_record(self, obj: dict) -> Record:
        try:
            kind, content = obj["type"], obj["content"]
            if kind == "info":
                self.info = _parse_info(content)
                return self.info
            if kind == "sps":
                sps = _parse_sps(content)
                self.sps[sps.sps_id] = sps
                return sps
            if kind == "pps":
                pps = _parse_pps(content)
                self.pps[pps.pps_id] = pps
                return pps
            if kind == "frame":
                return self._parse_frame(content)
            return UnknownRecord(kind, content)
        except KeyError as e:
            raise ParseError(f"missing field {e} in {obj.get('type', '?')!r} record") from None

    def _parse_frame(self, c: dict) -> Frame:
        where = f"frame decoded_idx={c.get('decoded_idx')}"
        try:
            slices = tuple(_parse_slice(s) for s in c["slices"])
            if not slices:
                raise ParseError("no slices")

            pps = self.pps.get(slices[0].pps_id)
            sps = self.sps.get(pps.sps_id) if pps else None
            if pps is None or sps is None:
                raise ParseError(f"frame precedes its PPS {slices[0].pps_id} / SPS")

            n = len(c["mb_type"])
            if n != sps.num_mbs:
                raise ParseError(f"{n} macroblocks, SPS says {sps.num_mbs}")

            mb_type = np.array(c["mb_type"], dtype=np.int8)
            sub_mb_type = np.array(c["sub_mb_type"], dtype=np.int8).reshape(n, 4)

            intra_modes = _ragged_rows(c["intra_modes"], n, 16, -1, np.int8, (1, 4, 16), "intra_modes")
            mv_l0, valid_l0 = _parse_mv_list(c["mv_l0"], n, "mv_l0")
            mv_l1, valid_l1 = _parse_mv_list(c["mv_l1"], n, "mv_l1")
            ref = np.stack([
                _ragged_rows(c[k], n, 4, -1, np.int32, (4,), k)
                for k in ("ref_dec_idx_l0", "ref_dec_idx_l1")
            ])

            return Frame(
                decoded_idx=c["decoded_idx"],
                output_idx=c["output_idx"],
                poc=c["POC"],
                frame_num=c["frame_num"],
                idr=c["IDR"],
                sps=sps,
                pps=pps,
                slices=slices,
                dpb=_parse_dpb(c["dpb_status"]),
                mb_width=sps.pic_width_in_mbs,
                mb_height=sps.pic_height_in_mbs,
                mb_type=mb_type,
                sub_mb_type=sub_mb_type,
                qpy=np.asarray(_column(c, "qpy", n), dtype=np.int16),
                cbp=np.asarray(_column(c, "cbp", n), dtype=np.int16),
                t8x8=np.asarray(_column(c, "t8x8", n), dtype=bool),
                mb_slice_idx=_mb_slice_map(slices, n),
                intra_modes=intra_modes,
                intra_mode_count=(intra_modes >= 0).sum(axis=1).astype(np.int8),
                chroma_intra_modes=np.asarray(_column(c, "chroma_intra_modes", n), dtype=np.int8),
                pred_dir=_ragged_rows(c["pred_dir"], n, 4, 0, np.uint8, (4,), "pred_dir"),
                ref_dec_idx=ref,
                mv=np.stack([mv_l0, mv_l1]),
                mv_valid=np.stack([valid_l0, valid_l1]),
            )
        except ParseError as e:
            raise ParseError(f"{where}: {e}") from None
        except KeyError as e:
            raise ParseError(f"{where}: missing field {e}") from None