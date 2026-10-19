"""Overlay geometry computed with numpy from a parsed `Frame`. Qt-free.

All coordinates are in *coded* picture pixels (the 16x16 MB grid origin is (0, 0)).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

INVALID_KEY = np.int64(-(1 << 40))
LOG_KNEE = 8.0  # px: vectors shorter than this stay ~linear under the log scale


@dataclass
class ArrowSet:
    """One arrow per distinct motion region: x0/y0 = origin, dx/dy = vector in pixels (mv / 4)."""
    x0: np.ndarray  # (M,) float32
    y0: np.ndarray
    dx: np.ndarray
    dy: np.ndarray
    lst: np.ndarray  # (M,) uint8, 0 = L0, 1 = L1

    def __len__(self) -> int:
        return len(self.x0)


def build_arrows(frame) -> ArrowSet:
    """Motion vectors merged hierarchically: a whole MB with one vector gives one arrow, otherwise
    each uniform 8x8 gives one, otherwise each 4x4. Fully vectorized (no per-MB Python loop)."""
    n, mbw = frame.num_mbs, frame.mb_width
    mbx = (np.arange(n) % mbw) * 16
    mby = (np.arange(n) // mbw) * 16
    xs, ys, dxs, dys, ls = [], [], [], [], []

    def add(l, x, y, mv):
        dx = mv[:, 0].astype(np.float32) / 4.0
        dy = mv[:, 1].astype(np.float32) / 4.0
        # The vector starts at the referenced pixel and points TO the block
        xs.append((x + dx).astype(np.float32))
        ys.append((y + dy).astype(np.float32))
        dxs.append(-dx)
        dys.append(-dy)
        ls.append(np.full(len(x), l, np.uint8))

    for l in (0, 1):
        mv = frame.mv[l]  # (n,4,4,2)
        va = frame.mv_valid[l]  # (n,4,4)
        if not va.any():
            continue
        key = np.where(va, (mv[..., 0].astype(np.int64) << 16) | (mv[..., 1].astype(np.int64) & 0xFFFF), INVALID_KEY)
        kq = key.reshape(n, 2, 2, 2, 2).transpose(0, 1, 3, 2, 4).reshape(n, 2, 2, 4)  # (n, qy, qx, blocks)
        u8 = (kq == kq[..., :1]).all(-1)  # (n,2,2) quadrant has a single vector
        qkey = kq[..., 0]
        u16 = u8.all((1, 2)) & (qkey == qkey[:, :1, :1]).all((1, 2))  # (n,)

        m = np.flatnonzero(u16 & (qkey[:, 0, 0] != INVALID_KEY))  # 16x16
        add(l, mbx[m] + 8, mby[m] + 8, mv[m, 0, 0])

        m, qy, qx = np.nonzero(u8 & ~u16[:, None, None] & (qkey != INVALID_KEY))  # 8x8
        add(l, mbx[m] + qx * 8 + 4, mby[m] + qy * 8 + 4, mv[m, qy * 2, qx * 2])

        u8e = np.repeat(np.repeat(u8, 2, axis=1), 2, axis=2)  # (n,4,4)
        m, by, bx = np.nonzero(va & ~u8e)  # 4x4
        add(l, mbx[m] + bx * 4 + 2, mby[m] + by * 4 + 2, mv[m, by, bx])

    if not xs:
        z = np.zeros(0, np.float32)
        return ArrowSet(z, z, z, z, np.zeros(0, np.uint8))
    return ArrowSet(np.concatenate(xs), np.concatenate(ys), np.concatenate(dxs), np.concatenate(dys), np.concatenate(ls))


def slice_boundaries(frame) -> np.ndarray:
    """(M,4) float32 segments (x0,y0,x1,y1) along MB edges whose two sides belong to different slices."""
    g = frame.grid(frame.mb_slice_idx)
    segs = []
    r, c = np.nonzero(g[:, 1:] != g[:, :-1])  # vertical edge right of column c
    segs.append(np.stack([(c + 1) * 16, r * 16, (c + 1) * 16, (r + 1) * 16], axis=1))
    r, c = np.nonzero(g[1:, :] != g[:-1, :])  # horizontal edge below row r
    segs.append(np.stack([c * 16, (r + 1) * 16, (c + 1) * 16, (r + 1) * 16], axis=1))
    return np.concatenate(segs).astype(np.float32)


def compress_lengths(vx: np.ndarray, vy: np.ndarray, t: float) -> tuple[np.ndarray, np.ndarray]:
    """Blend between true length (t=0) and a logarithmic length (t=1) with the same direction:
    L' = (1-t)*L + t*K*ln(1 + L/K). Short vectors stay about the same, long ones are tamed."""
    if t <= 0.0:
        return vx, vy
    L = np.hypot(vx, vy)
    Ld = (1.0 - t) * L + t * LOG_KNEE * np.log1p(L / LOG_KNEE)
    s = np.divide(Ld, L, out=np.zeros_like(L), where=L > 1e-6)
    return vx * s, vy * s


def arrow_lines(x0, y0, vx, vy, min_head: float = 2.0, max_head: float = 10.0, half_angle_deg: float = 25.0) -> np.ndarray:
    """(3M,4) float32 segments: shaft, then the two barbs of every arrow. Head length grows with the
    vector length (35 %, clamped), and never exceeds 60 % of it."""
    L = np.hypot(vx, vy)
    ok = L > 1e-6
    x0, y0, vx, vy, L = x0[ok], y0[ok], vx[ok], vy[ok], L[ok]
    if len(L) == 0:
        return np.zeros((0, 4), np.float32)
    ux, uy = vx / L, vy / L
    head = np.minimum(np.clip(0.35 * L, min_head, max_head), 0.6 * L)
    c, s = np.cos(np.radians(half_angle_deg)), np.sin(np.radians(half_angle_deg))
    x1, y1 = x0 + vx, y0 + vy
    r1x, r1y = -(ux * c - uy * s), -(ux * s + uy * c)
    r2x, r2y = -(ux * c + uy * s), -(-ux * s + uy * c)
    out = np.empty((len(L), 3, 4), np.float32)
    out[:, 0] = np.stack([x0, y0, x1, y1], axis=1)
    out[:, 1] = np.stack([x1, y1, x1 + r1x * head, y1 + r1y * head], axis=1)
    out[:, 2] = np.stack([x1, y1, x1 + r2x * head, y1 + r2y * head], axis=1)
    return out.reshape(-1, 4)