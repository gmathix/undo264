"""Planar YUV 4:2:0 (I420) -> RGB, full-range BT.601. No Qt dependency."""
import cv2
import numpy as np


def yuv420_to_rgb(buf: np.ndarray, w: int, h: int) -> np.ndarray:
    """`buf`: flat uint8 array of w*h*3/2 bytes (Y plane, then U, then V). Returns (h, w, 3) uint8 RGB.

    Same maths as the previous numpy float32 version (max difference: 1 level),
    ~10x faster. Chroma is replicated (nearest) exactly like np.repeat did.
    """
    ys = w * h
    uvs = ys // 4
    Y = buf[:ys].reshape(h, w)
    U = buf[ys:ys + uvs].reshape(h // 2, w // 2)
    V = buf[ys + uvs:ys + 2 * uvs].reshape(h // 2, w // 2)
    Uu = cv2.resize(U, (w, h), interpolation=cv2.INTER_NEAREST)
    Vu = cv2.resize(V, (w, h), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(cv2.merge((Y, Vu, Uu)), cv2.COLOR_YCrCb2RGB)