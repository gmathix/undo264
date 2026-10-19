"""QGraphicsItem overlays drawn in coded-picture coordinates (see viewport.VideoView).

All pens are cosmetic (constant on-screen width at any zoom). The big ones
(motion vectors) cull to the exposed rectangle at paint time, so zooming in is cheap.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, QRect
from PySide6.QtGui import QBrush, QColor, QPen, QPainterPath
from PySide6.QtWidgets import QGraphicsItem

from .overlay_data import ArrowSet, arrow_lines, compress_lengths

L0_COLOR = QColor(235, 50, 50)
L1_COLOR = QColor(60, 120, 255)
GRID_COLOR = QColor(255, 255, 255, 90)
SLICE_COLOR = QColor(255, 0, 255)
HOVER_COLOR = QColor(0, 230, 255)
SELECT_COLOR = QColor(255, 225, 0)

MIN_HEAD_SCREEN_PX = 7.0  # arrows shorter than this on screen get no arrowhead


def _pen(color: QColor, width: float, round_: bool = True) -> QPen:
    p = QPen(color, width)
    p.setCosmetic(True)
    if round_:
        p.setCapStyle(Qt.RoundCap)
        p.setJoinStyle(Qt.RoundJoin)
    return p


def _qlines(a: np.ndarray) -> list[QLineF]:
    return [QLineF(x0, y0, x1, y1) for x0, y0, x1, y1 in a.tolist()]


class _Overlay(QGraphicsItem):
    def __init__(self, rect: QRectF, parent: QGraphicsItem):
        super().__init__(parent)
        self._rect = rect
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self) -> QRectF:
        return self._rect


class GridItem(_Overlay):
    """16x16 macroblock wireframe."""

    def __init__(self, mb_w: int, mb_h: int, parent: QGraphicsItem):
        w, h = mb_w * 16, mb_h * 16
        super().__init__(QRectF(0, 0, w, h), parent)
        self._lines = [QLineF(x, 0, x, h) for x in range(0, w + 1, 16)] + [QLineF(0, y, w, y) for y in range(0, h + 1, 16)]

    def paint(self, painter, option, widget=None):
        painter.setPen(_pen(GRID_COLOR, 1.0, round_=False))
        painter.drawLines(self._lines)


class SliceItem(_Overlay):
    """Thick lines where adjacent macroblocks belong to different slices."""

    def __init__(self, rect: QRectF, parent: QGraphicsItem):
        super().__init__(rect, parent)
        self._lines: list[QLineF] = []

    def set_segments(self, segs: np.ndarray | None) -> None:
        self._lines = [] if segs is None else _qlines(segs)
        self.update()

    def paint(self, painter, option, widget=None):
        if self._lines:
            painter.setPen(_pen(SLICE_COLOR, 3.0, round_=False))
            painter.drawLines(self._lines)


class MVItem(_Overlay):
    """All motion vectors of the frame (L0 red, L1 blue)."""

    def __init__(self, rect: QRectF, parent: QGraphicsItem):
        super().__init__(rect, parent)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)
        self.arrows: ArrowSet | None = None
        self.show_list = (True, True)
        self.log_t = 0.0
        self.cull_rect_mb: QRect | None = None  # Region filter

    def set_arrows(self, arrows: ArrowSet | None) -> None:
        self.arrows = arrows
        self.update()

    def paint(self, painter, option, widget=None):
        a = self.arrows
        if a is None or len(a) == 0:
            return
        vx, vy = compress_lengths(a.dx, a.dy, self.log_t)
        r = option.exposedRect.adjusted(-16, -16, 16, 16)
        x1, y1 = a.x0 + vx, a.y0 + vy
        vis = (
                (np.maximum(a.x0, x1) >= r.left()) & (np.minimum(a.x0, x1) <= r.right())
                & (np.maximum(a.y0, y1) >= r.top()) & (np.minimum(a.y0, y1) <= r.bottom())
                & ((vx != 0) | (vy != 0))
        )

        # Restrict rendering to selected region if one is defined
        if self.cull_rect_mb is not None:
            # x1, y1 represent the destination pixel inside the current frame
            block_mb_x = (x1 / 16.0).astype(np.int32)
            block_mb_y = (y1 / 16.0).astype(np.int32)
            cr = self.cull_rect_mb
            vis &= (block_mb_x >= cr.left()) & (block_mb_x <= cr.right()) & \
                   (block_mb_y >= cr.top()) & (block_mb_y <= cr.bottom())

        scale = painter.worldTransform().m11()
        screen_len = np.hypot(vx, vy) * scale
        for l, color in ((0, L0_COLOR), (1, L1_COLOR)):
            if not self.show_list[l]:
                continue
            m = vis & (a.lst == l)
            if not m.any():
                continue
            big = m & (screen_len >= MIN_HEAD_SCREEN_PX)
            small = m & ~big
            painter.setPen(_pen(color, 1.5))
            if small.any():
                painter.drawLines(_qlines(np.stack([a.x0[small], a.y0[small], x1[small], y1[small]], axis=1)))
            if big.any():
                painter.drawLines(_qlines(arrow_lines(a.x0[big], a.y0[big], vx[big], vy[big])))


class LocalMVItem(_Overlay):
    """Thick arrows for the selected macroblock / partition, drawn from each partition's centre."""

    def __init__(self, rect: QRectF, parent: QGraphicsItem):
        super().__init__(rect, parent)
        self.arrows: ArrowSet | None = None
        self.log_t = 0.0

    def set_arrows(self, arrows: ArrowSet | None) -> None:
        self.arrows = arrows
        self.update()

    def paint(self, painter, option, widget=None):
        a = self.arrows
        if a is None or len(a) == 0:
            return
        vx, vy = compress_lengths(a.dx, a.dy, self.log_t)
        moving = (vx != 0) | (vy != 0)
        black = QColor(0, 0, 0, 200)
        for l, color in ((0, L0_COLOR), (1, L1_COLOR)):
            m = moving & (a.lst == l)
            if m.any():
                lines = _qlines(arrow_lines(a.x0[m], a.y0[m], vx[m], vy[m], min_head=3.0, max_head=14.0))
                painter.setPen(_pen(black, 7.0))
                painter.drawLines(lines)
                painter.setPen(_pen(color.lighter(125), 4.0))
                painter.drawLines(lines)
        # zero vectors: a dot, so "mv = (0,0)" is visible rather than missing
        still = ~moving
        if still.any():
            wt = painter.worldTransform()
            painter.save()
            painter.resetTransform()
            for x, y, l in zip(a.x0[still].tolist(), a.y0[still].tolist(), a.lst[still].tolist()):
                c = wt.map(QPointF(x, y))
                painter.setPen(_pen(black, 2.0))
                painter.setBrush(QBrush(L1_COLOR if l else L0_COLOR))
                painter.drawEllipse(c, 5.0, 5.0)
            painter.restore()


class _MbMarker(_Overlay):
    """Base for the hover/selection markers: a 16x16 item the viewport moves onto a macroblock."""

    def __init__(self, parent: QGraphicsItem):
        super().__init__(QRectF(-4, -4, 24, 24), parent)
        self.setVisible(False)


class HoverItem(_MbMarker):
    """Cyan corner brackets [ ]."""

    ARM = 5.0

    def paint(self, painter, option, widget=None):
        a = self.ARM
        painter.setPen(_pen(HOVER_COLOR, 2.0, round_=False))
        painter.drawLines([
            QLineF(0, 0, a, 0), QLineF(0, 0, 0, a), QLineF(16, 0, 16 - a, 0), QLineF(16, 0, 16, a),
            QLineF(0, 16, a, 16), QLineF(0, 16, 0, 16 - a), QLineF(16, 16, 16 - a, 16), QLineF(16, 16, 16, 16 - a),
        ])


class SelectionItem(_Overlay):
    """Solid yellow box around the selected region, rendering sub-partitions for single MBs."""

    def __init__(self, parent: QGraphicsItem):
        super().__init__(QRectF(), parent)
        self.partitions = []
        self.selected_partition = None
        self.setVisible(False)

    def set_geometry(self, rect_local: QRectF, parts: list, sel_part):
        self.prepareGeometryChange()
        self._rect = rect_local
        self.partitions = parts
        self.selected_partition = sel_part
        self.update()

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-3, -3, 3, 3)  # accommodate the thick border pen

    def paint(self, painter, option, widget=None):
        painter.setPen(_pen(SELECT_COLOR, 2.5, round_=False))

        # Big region without partitions visible
        if not self.partitions:
            painter.setBrush(QBrush(QColor(255, 225, 0, 35)))
            painter.drawRect(self._rect)
            return

        # Single MB with visible partition wireframes
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self._rect)

        painter.setPen(_pen(SELECT_COLOR, 1.0, round_=False))
        for p in self.partitions:
            pr = QRectF(p.x, p.y, p.w, p.h)
            if p == self.selected_partition:
                painter.setBrush(QBrush(QColor(255, 225, 0, 120))) # bright highlight
            else:
                painter.setBrush(QBrush(QColor(255, 225, 0, 30)))
            painter.drawRect(pr)


class RefHighlightItem(_Overlay):
    """Dims the picture and brightly outlines partitions using a specific reference."""
    def __init__(self, rect: QRectF, parent: QGraphicsItem):
        super().__init__(rect, parent)
        self.dim_path = QPainterPath()
        self.hl_path = QPainterPath()

    def set_paths(self, dim_path: QPainterPath, hl_path: QPainterPath):
        self.dim_path = dim_path
        self.hl_path = hl_path
        self.update()

    def paint(self, painter, option, widget=None):
        if not self.dim_path.isEmpty():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 180))
            painter.drawPath(self.dim_path)

        if not self.hl_path.isEmpty():
            painter.setPen(_pen(QColor(0, 255, 255), 2.0, round_=False))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self.hl_path)