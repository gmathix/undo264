"""Central viewport: zoomable/pannable picture with macroblock overlays.

Interaction
    Ctrl + wheel        zoom around the cursor
    middle-button drag  pan
    hover               cyan [ ] brackets on the macroblock under the cursor
    left click          select that macroblock (yellow box); Esc or a click outside clears it

Overlays (all toggleable): MB grid, slice boundaries, global motion vectors
(L0 red / L1 blue, with a linear->logarithmic length slider), and local motion
vectors of the selected macroblock / partition (always on when a MB is selected).

Coordinates: overlays live in *coded* coordinates (MB grid origin at 0,0). The
displayed picture is the coded picture minus the SPS crop (left/top), so the
overlay root is shifted by (-crop_left, -crop_top) and clipped to the picture.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QPen, QPainterPath
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QGraphicsView

from model import MbType
from .overlay_data import ArrowSet, build_arrows, slice_boundaries
from .overlays import GridItem, HoverItem, LocalMVItem, MVItem, SelectionItem, SliceItem, RefHighlightItem
from .partitions import Partition, inter_partitions

MIN_SCALE, MAX_SCALE = 0.05, 80.0
CLICK_SLOP_PX = 4


class VideoView(QGraphicsView):
    mbHovered = Signal(int)   # macroblock address under the cursor, -1 when outside the picture
    mbSelected = Signal(int)  # selected macroblock address, -1 when cleared
    partitionSelected = Signal(object)  # New signal emitted for bi-directional linking

    def __init__(self, parent=None):
        scene = QGraphicsScene()
        super().__init__(scene, parent)
        scene.setParent(self)  # the view does not take ownership
        self._scene = scene
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setBackgroundBrush(QColor(28, 28, 30))
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.pixmap_item = scene.addPixmap(QPixmap())
        self.pixmap_item.setTransformationMode(Qt.FastTransformation)  # crisp pixels when zoomed in

        # picture / grid geometry (set_geometry)
        self.pic_w = self.pic_h = 0
        self.mb_w = self.mb_h = 0
        self.crop_left = self.crop_top = 0
        self._fitted = False

        # overlay state
        self.frame = None
        self.selected_mb = -1
        self.selection_rect_mb: QRect | None = None
        self.selected_partition: Partition | None = None
        self._hover_mb = -1
        self._drag_start_mb: tuple[int, int] | None = None
        self._clicking_existing_mb = False  # Track 2-click partition selection
        self._mv_enabled = False
        self._mv_arrows: ArrowSet | None = None  # built lazily, cached per frame
        self._root: QGraphicsRectItem | None = None
        self._grid = self._slices = self._mv = self._local = self._hover = self._select = None
        self._highlight_ref_idx = -1
        self._ref_highlight: RefHighlightItem | None = None
        self._mb_filters = {}

        # --- New Peek State ---
        self._normal_pixmap = QPixmap()
        self._peek_pixmap_l0: QPixmap | None = None
        self._peek_rect_l0 = QRectF()
        self._peek_pixmap_l1: QPixmap | None = None
        self._peek_rect_l1 = QRectF()
        self._peek_active_list = -1  # -1 = None, 0 = L0, 1 = L1
        self._peek_box: QGraphicsRectItem | None = None


        self._pan_last = None
        self._press_pos = None

    # ------------------------------------------------------------------ setup
    def set_geometry(self, pic_w: int, pic_h: int, mb_w: int, mb_h: int, crop_left: int = 0, crop_top: int = 0) -> None:
        """Picture size (after cropping), macroblock grid size and the crop offset of the picture's top-left."""
        self.pic_w, self.pic_h, self.mb_w, self.mb_h = pic_w, pic_h, mb_w, mb_h
        self.crop_left, self.crop_top = crop_left, crop_top
        sc = self.scene()
        if self._root is not None:
            sc.removeItem(self._root)  # takes its overlay children with it
        self._fitted = False

        # margin around the picture so it can be panned off-centre
        sc.setSceneRect(QRectF(-pic_w / 2, -pic_h / 2, pic_w * 2, pic_h * 2))

        self._root = QGraphicsRectItem(QRectF(0, 0, pic_w, pic_h))
        self._root.setPen(Qt.NoPen)
        self._root.setFlag(QGraphicsRectItem.ItemClipsChildrenToShape, True)
        self._root.setZValue(1)
        sc.addItem(self._root)

        # Create the dashed peek box
        self._peek_box = QGraphicsRectItem(self._root)
        pen = QPen(QColor(0, 255, 255), 2.0, Qt.DashLine)
        pen.setCosmetic(True)
        self._peek_box.setPen(pen)
        self._peek_box.setBrush(Qt.NoBrush)
        self._peek_box.setZValue(10)
        self._peek_box.setVisible(False)

        coded = QRectF(0, 0, mb_w * 16, mb_h * 16)
        self._grid = GridItem(mb_w, mb_h, self._root)
        self._slices = SliceItem(coded, self._root)
        self._mv = MVItem(coded, self._root)
        self._local = LocalMVItem(coded, self._root)
        self._hover = HoverItem(self._root)
        self._select = SelectionItem(self._root)
        self._ref_highlight = RefHighlightItem(QRectF(0, 0, pic_w, pic_h), self._root)
        self._ref_highlight.setZValue(9)  # Below the peek box but above MVs
        self._ref_highlight.setVisible(False)
        for z, it in enumerate((self._grid, self._slices, self._mv, self._local, self._hover, self._select), start=1):
            it.setZValue(z)
        for it in (self._grid, self._slices, self._mv, self._local, self._peek_box, self._ref_highlight):
            it.setPos(-crop_left, -crop_top)
        self._grid.setVisible(False)
        self._slices.setVisible(False)
        self._mv.setVisible(False)
        self._place_markers()

    def _coded_to_scene(self, mb: int) -> tuple[int, int]:
        return (mb % self.mb_w) * 16 - self.crop_left, (mb // self.mb_w) * 16 - self.crop_top

    def _place_markers(self) -> None:
        if self._hover_mb >= 0:
            self._hover.setVisible(True)
            self._hover.setPos(*self._coded_to_scene(self._hover_mb))
        else:
            self._hover.setVisible(False)

        if self.selection_rect_mb is not None:
            self._select.setVisible(True)
            r = self.selection_rect_mb
            self._select.setPos(r.x() * 16 - self.crop_left, r.y() * 16 - self.crop_top)

            parts = []
            # Only calculate/draw partitions if exactly 1 MB is selected
            if r.width() == 1 and r.height() == 1 and self.frame is not None:
                from .partitions import partitions
                parts = partitions(self.frame, self.selected_mb)

            self._select.set_geometry(QRectF(0, 0, r.width() * 16, r.height() * 16), parts, self.selected_partition)
        else:
            self._select.setVisible(False)

    # ---------------------------------------------------------------- picture
    def set_picture(self, pixmap: QPixmap) -> None:
        self._normal_pixmap = pixmap
        if self._peek_active_list == -1:
            self.pixmap_item.setPixmap(pixmap)
        if not self._fitted and self.pic_w:
            self.fit_to_window()
            self._fitted = True

    def set_peek_data(self, pix_l0, r_l0, pix_l1, r_l1) -> None:
        """Caches reference images and MV bounds for both L0 and L1."""
        self._peek_pixmap_l0 = pix_l0
        self._peek_rect_l0 = r_l0 or QRectF()
        self._peek_pixmap_l1 = pix_l1
        self._peek_rect_l1 = r_l1 or QRectF()

        # If currently peeking and data changes, abort the peek safely
        if self._peek_active_list != -1:
            self._stop_peek()

    def fit_to_window(self) -> None:
        if self.pic_w:
            self.fitInView(QRectF(0, 0, self.pic_w, self.pic_h), Qt.KeepAspectRatio)

    def actual_size(self) -> None:
        self.resetTransform()

    @property
    def view_scale(self) -> float:
        return self.transform().m11()

    # ----------------------------------------------------------- overlay state
    def set_frame(self, frame) -> None:
        self.frame = frame
        self._mv_arrows = None
        if self._slices is not None:
            self._slices.set_segments(
                slice_boundaries(frame) if frame is not None and self._slices.isVisible() else None)

        # Try to persist the currently selected partition if the frame changes underneath
        if self.selected_partition and frame is not None and self.selected_mb >= 0:
            from .partitions import partitions
            parts = partitions(frame, self.selected_mb)
            cx, cy = self.selected_partition.center
            new_p = None
            for p in parts:
                if p.x <= cx < p.x + p.w and p.y <= cy < p.y + p.h:
                    new_p = p
                    break
            self.selected_partition = new_p

        self._refresh_mv()
        self._place_markers()
        self._refresh_local()
        self._refresh_highlights()

    def set_grid_visible(self, on: bool) -> None:
        if self._grid is not None:
            self._grid.setVisible(on)

    def set_slices_visible(self, on: bool) -> None:
        if self._slices is None:
            return
        self._slices.setVisible(on)
        self._slices.set_segments(slice_boundaries(self.frame) if on and self.frame is not None else None)

    def set_mv_visible(self, on: bool) -> None:
        self._mv_enabled = on
        self._refresh_mv()

    def set_mv_lists(self, l0: bool, l1: bool) -> None:
        if self._mv is not None:
            self._mv.show_list = (l0, l1)
            self._mv.update()

    def set_mv_log(self, t: float) -> None:
        """0 = true vector lengths, 1 = fully logarithmic."""
        if self._mv is not None:
            self._mv.log_t = t
            self._local.log_t = t
            self._mv.update()
            self._local.update()

    def _refresh_mv(self) -> None:
        if self._mv is None:
            return
        show = self._mv_enabled and self.frame is not None
        self._mv.setVisible(show)
        if show:
            if self._mv_arrows is None:
                self._mv_arrows = build_arrows(self.frame)
            self._mv.set_arrows(self._mv_arrows)
        else:
            self._mv.set_arrows(None)

    # -------------------------------------------------------------- selection
    def select_mb(self, mb: int) -> None:
        if mb == self.selected_mb:
            return
        self.selected_mb = mb
        self.selected_partition = None
        self._place_markers()
        self._refresh_local()
        self.mbSelected.emit(mb)

    def set_selected_partition(self, part: Partition | None) -> None:
        """Restrict the local vectors to one partition (None: all partitions of the selected MB)."""
        if self.selected_partition == part:
            return # Prevent recursion from bi-directional selection
        self.selected_partition = part
        self._place_markers()
        self._refresh_local()
        self.partitionSelected.emit(part)

    def _refresh_local(self) -> None:
        if self._local is None:
            return
        f, mb = self.frame, self.selected_mb

        is_single_mb = self.selection_rect_mb and self.selection_rect_mb.width() == 1 and self.selection_rect_mb.height() == 1
        if f is None or mb < 0 or not is_single_mb:
            self._local.set_arrows(None)
            return

        from .partitions import partitions
        parts = [self.selected_partition] if self.selected_partition is not None else partitions(f, mb)
        ox, oy = (mb % self.mb_w) * 16, (mb // self.mb_w) * 16
        xs, ys, dx, dy, ls = [], [], [], [], []

        if f.mb_type[mb] >= 4:  # Inter partitions
            for p in parts:
                bx, by = p.blk
                cx, cy = p.center
                for l in (0, 1):
                    if f.mv_valid[l, mb, by, bx]:
                        mvx, mvy = f.mv[l, mb, by, bx]
                        dx_val, dy_val = mvx / 4.0, mvy / 4.0
                        xs.append(ox + cx + dx_val)
                        ys.append(oy + cy + dy_val)
                        dx.append(-dx_val)
                        dy.append(-dy_val)
                        ls.append(l)
        else:  # Intra partitions
            for p in parts:
                cx, cy = p.center
                mode = f.intra_modes[mb, p.idx]
                is_16 = f.intra_mode_count[mb] == 1

                if is_16:
                    vecs = {0: (0, 1), 1: (1, 0), 2: (0, 0), 3: (0, 0)}
                else:
                    vecs = {0: (0, 1), 1: (1, 0), 2: (0, 0), 3: (-1, 1), 4: (1, 1),
                            5: (0.5, 1), 6: (1, 0.5), 7: (-0.5, 1), 8: (1, -0.5)}

                vx, vy = vecs.get(int(mode), (0, 0))
                length = np.hypot(vx, vy)
                if length > 0:
                    vx, vy = (vx / length) * 4.0, (vy / length) * 4.0

                xs.append(ox + cx)
                ys.append(oy + cy)
                dx.append(vx)
                dy.append(vy)
                ls.append(0)  # Borrow L0's red color for Intra arrows

        f32 = lambda v: np.asarray(v, np.float32)
        self._local.set_arrows(ArrowSet(f32(xs), f32(ys), f32(dx), f32(dy), np.asarray(ls, np.uint8)))

    def set_highlight_reference(self, ref_idx: int):
        """Called by the main app when the DPB table is clicked."""
        self._highlight_ref_idx = ref_idx
        self._refresh_highlights()

    def set_mb_filters(self, filters: dict):
        """Called by the main app when the Right Dock filters are toggled."""
        self._mb_filters = dict(filters)
        self._refresh_highlights()

    def _refresh_highlights(self):
        if self._ref_highlight is None:
            return

        ref_idx = self._highlight_ref_idx
        filters = self._mb_filters

        has_ref = ref_idx != -1
        has_filter = any(filters.values()) if filters else False

        if not has_ref and not has_filter:
            self._ref_highlight.setVisible(False)
            return

        f = self.frame
        if f is None:
            self._ref_highlight.setVisible(False)
            return

        # 1. Macroblock Type Filter Mask
        mb_mask = np.ones(f.num_mbs, dtype=bool)
        if has_filter:
            mb_mask = np.zeros(f.num_mbs, dtype=bool)
            t = f.mb_type
            if filters.get("intra_16x16"): mb_mask |= (t == MbType.INTRA_16x16) | (t == MbType.PCM)
            if filters.get("intra_8x8"): mb_mask |= (t == MbType.INTRA_8x8)
            if filters.get("intra_4x4"): mb_mask |= (t == MbType.INTRA_4x4)
            if filters.get("inter_16x16"): mb_mask |= (t == MbType.INTER_16x16)
            if filters.get("inter_16x8"): mb_mask |= (t == MbType.INTER_16x8) | (t == MbType.INTER_8x16)
            if filters.get("skip"): mb_mask |= (t == MbType.SKIP)
            if filters.get("direct"): mb_mask |= (t == MbType.DIRECT)

            # --- Vectorized Inter 8x8 Sub-Partition Check ---
            p8_keys = ["inter_8x8_8x8", "inter_8x8_8x4", "inter_8x8_4x8", "inter_8x8_4x4"]
            if any(filters.get(k) for k in p8_keys):
                # Reshape (List, MB, Y_quad, Y_sub, X_quad, X_sub) -> (List, MB, Quadrant, SubBlock)
                va = f.mv_valid.reshape(2, f.num_mbs, 2, 2, 2, 2).transpose(0, 1, 2, 4, 3, 5).reshape(2, f.num_mbs, 4,
                                                                                                      4)
                mv = f.mv.reshape(2, f.num_mbs, 2, 2, 2, 2, 2).transpose(0, 1, 2, 4, 3, 5, 6).reshape(2, f.num_mbs, 4,
                                                                                                      4, 2)

                def eq(i, j):
                    """Checks if subblock 'i' and 'j' in a quadrant share validity and MVs across L0/L1."""
                    v_eq = va[:, :, :, i] == va[:, :, :, j]
                    m_eq = (mv[:, :, :, i, :] == mv[:, :, :, j, :]).all(axis=-1)
                    return np.all(v_eq & (~va[:, :, :, i] | m_eq), axis=0)  # shape: (num_mbs, 4_quadrants)

                # Subblocks (0:TL, 1:TR, 2:BL, 3:BR)
                eq01, eq23 = eq(0, 1), eq(2, 3)
                eq02, eq13 = eq(0, 2), eq(1, 3)

                is_8x8 = eq01 & eq23 & eq02
                is_8x4 = eq01 & eq23 & ~is_8x8
                is_4x8 = eq02 & eq13 & ~is_8x8
                is_4x4 = ~is_8x8 & ~is_8x4 & ~is_4x8

                is_inter_8x8 = (t == MbType.INTER_8x8)
                if filters.get("inter_8x8_8x8"): mb_mask |= is_inter_8x8 & is_8x8.any(axis=1)
                if filters.get("inter_8x8_8x4"): mb_mask |= is_inter_8x8 & is_8x4.any(axis=1)
                if filters.get("inter_8x8_4x8"): mb_mask |= is_inter_8x8 & is_4x8.any(axis=1)
                if filters.get("inter_8x8_4x4"): mb_mask |= is_inter_8x8 & is_4x4.any(axis=1)

        # 2. DPB Reference Mask
        if has_ref:
            # Mask out strictly the 8x8 blocks belonging to the specific reference
            ref_mask_8x8 = (f.ref_dec_idx[0] == ref_idx) | (f.ref_dec_idx[1] == ref_idx)

            # Intersection: The block must ALSO match the MB filter if one is active
            ref_mask_8x8 &= mb_mask[:, None]

            mbs, blk8s = np.nonzero(ref_mask_8x8)
        else:
            # Filters ONLY: Highlight the entirety of the matching Macroblocks
            mbs = np.repeat(np.flatnonzero(mb_mask), 4)
            blk8s = np.tile(np.arange(4), np.count_nonzero(mb_mask))

        # 3. Path generation: boolean 8x8 grid -> horizontal runs (far fewer rects than one per block)
        cw, ch = f.mb_width * 16, f.mb_height * 16
        full = QPainterPath()
        full.addRect(0, 0, cw, ch)
        if len(mbs) == 0:
            self._ref_highlight.set_paths(full, QPainterPath())
            self._ref_highlight.setVisible(True)
            return

        gw, gh = f.mb_width * 2, f.mb_height * 2
        grid = np.zeros((gh, gw), dtype=bool)
        grid[(mbs // f.mb_width) * 2 + (blk8s >> 1), (mbs % f.mb_width) * 2 + (blk8s & 1)] = True
        padded = np.zeros((gh, gw + 2), dtype=np.int8)
        padded[:, 1:-1] = grid
        d = np.diff(padded, axis=1)
        sy, sx = np.nonzero(d == 1)
        _, ex = np.nonzero(d == -1)  # same row-major order as the starts
        hl_path = QPainterPath()
        for y, x0, x1 in zip(sy.tolist(), sx.tolist(), ex.tolist()):
            hl_path.addRect(x0 * 8, y * 8, (x1 - x0) * 8, 8)
        hl_path = hl_path.simplified()  # merge runs into solid shapes (clean outline)
        self._ref_highlight.set_paths(full.subtracted(hl_path), hl_path)
        self._ref_highlight.setVisible(True)

    # ------------------------------------------------------------ hit testing
    def mb_at(self, scene_pos: QPointF) -> int:
        """Macroblock address under a scene position, -1 outside the displayed picture."""
        x, y = scene_pos.x(), scene_pos.y()
        if not (0 <= x < self.pic_w and 0 <= y < self.pic_h) or self.mb_w == 0:
            return -1
        mb = int((y + self.crop_top) // 16) * self.mb_w + int((x + self.crop_left) // 16)
        return mb if mb < self.mb_w * self.mb_h else -1

    def select_region(self, rect: QRect | None) -> None:
        """Replaces select_mb to handle single MBs and multi-MB rectangles."""
        if self.selection_rect_mb == rect:
            return  # Prevent wiping the selected_partition on repeated clicks to the same MB

        self.selection_rect_mb = rect
        if rect is not None:
            self.selected_mb = rect.y() * self.mb_w + rect.x()
        else:
            self.selected_mb = -1

        self.selected_partition = None

        if self._mv is not None:
            self._mv.cull_rect_mb = rect

        self._place_markers()
        self._refresh_local()
        self._refresh_mv()
        self.mbSelected.emit(self.selected_mb)

    def _update_selection_from_drag(self, current: tuple[int, int]):
        sx, sy = self._drag_start_mb
        cx, cy = current
        x0, x1 = min(sx, cx), max(sx, cx)
        y0, y1 = min(sy, cy), max(sy, cy)
        new_rect = QRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1)
        self.select_region(new_rect)

    def _handle_partition_click(self, mb: int, scene_pos: QPointF):
        if self.frame is None: return
        px = scene_pos.x() + self.crop_left - (mb % self.mb_w) * 16
        py = scene_pos.y() + self.crop_top - (mb // self.mb_w) * 16

        from .partitions import partitions
        parts = partitions(self.frame, mb)
        for p in parts:
            if p.x <= px < p.x + p.w and p.y <= py < p.y + p.h:
                self.set_selected_partition(p)
                self._place_markers()
                return

    # ----------------------------------------------------------------- events
    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            f = 1.0015 ** e.angleDelta().y()
            s = self.view_scale
            f = min(max(f, MIN_SCALE / s), MAX_SCALE / s)
            self.scale(f, f)  # AnchorUnderMouse keeps the point under the cursor fixed
            e.accept()
        else:
            super().wheelEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._pan_last = e.position().toPoint()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        if e.button() == Qt.LeftButton:
            mb = self.mb_at(self.mapToScene(e.position().toPoint()))
            if mb >= 0:
                self._drag_start_mb = (mb % self.mb_w, mb // self.mb_w)

                # Check if this MB is already the sole selected item
                self._clicking_existing_mb = (
                        self.selection_rect_mb is not None and
                        self.selection_rect_mb.width() == 1 and
                        self.selection_rect_mb.height() == 1 and
                        self.selected_mb == mb
                )

                self._update_selection_from_drag(self._drag_start_mb)
            else:
                self._drag_start_mb = None
                self._clicking_existing_mb = False
                self.select_region(None)
        super().mousePressEvent(e)
        e.accept()

    def mouseMoveEvent(self, e):
        if self._pan_last is not None:
            p = e.position().toPoint()
            d = p - self._pan_last
            self._pan_last = p
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
            e.accept()
            return

        # Rubber band logic
        if self._drag_start_mb is not None:
            mb = self.mb_at(self.mapToScene(e.position().toPoint()))
            if mb >= 0:
                self._update_selection_from_drag((mb % self.mb_w, mb // self.mb_w))
            e.accept()
            return

        mb = self.mb_at(self.mapToScene(e.position().toPoint()))
        if mb != self._hover_mb:
            self._hover_mb = mb
            self._place_markers()
            self.mbHovered.emit(mb)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton and self._pan_last is not None:
            self._pan_last = None
            self.viewport().unsetCursor()
            e.accept()
            return
        if e.button() == Qt.LeftButton and self._drag_start_mb is not None:
            mb = self.mb_at(self.mapToScene(e.position().toPoint()))
            if mb >= 0:
                curr = (mb % self.mb_w, mb // self.mb_w)
                # Only drill down into the partition if the MB was already selected before the click
                if curr == self._drag_start_mb and self._clicking_existing_mb:
                    self._handle_partition_click(mb, self.mapToScene(e.position().toPoint()))

            self._drag_start_mb = None
            self._clicking_existing_mb = False
            e.accept()
            return
        super().mouseReleaseEvent(e)
        e.accept()

    def leaveEvent(self, e):
        if self._hover_mb != -1:
            self._hover_mb = -1
            self._place_markers()
            self.mbHovered.emit(-1)
        super().leaveEvent(e)

    def keyPressEvent(self, e):
        is_shift = e.key() == Qt.Key_Shift
        is_alt = e.key() == Qt.Key_Alt

        if (is_shift or is_alt) and not e.isAutoRepeat():
            list_to_peek = -1

            if is_shift:
                # Shift maps to L0, OR to L1 if it's the only one available (uni-prediction)
                if self._peek_pixmap_l0:
                    list_to_peek = 0
                elif self._peek_pixmap_l1:
                    list_to_peek = 1
            elif is_alt:
                # Ctrl strictly maps to L1
                if self._peek_pixmap_l1:
                    list_to_peek = 1

            if list_to_peek != -1 and self._peek_active_list == -1:
                self._peek_active_list = list_to_peek
                pixmap = self._peek_pixmap_l0 if list_to_peek == 0 else self._peek_pixmap_l1
                rect = self._peek_rect_l0 if list_to_peek == 0 else self._peek_rect_l1

                self.pixmap_item.setPixmap(pixmap)
                self._peek_box.setRect(rect)
                self._peek_box.setVisible(True)

                if self._select: self._select.setVisible(False)
                if self._local: self._local.setVisible(False)
            e.accept()
            return

        if e.key() == Qt.Key_Escape:
            self.select_region(None)
            e.accept()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        is_shift = e.key() == Qt.Key_Shift
        is_alt = e.key() == Qt.Key_Alt

        if (is_shift or is_alt) and not e.isAutoRepeat():
            # Determine which list this key was driving
            list_released = 0 if is_shift else 1
            if is_shift and not self._peek_pixmap_l0 and self._peek_pixmap_l1:
                list_released = 1

            if self._peek_active_list == list_released:
                self._stop_peek()
            e.accept()
            return
        super().keyReleaseEvent(e)

    def _stop_peek(self):
        self._peek_active_list = -1
        self.pixmap_item.setPixmap(self._normal_pixmap)
        if self._peek_box:
            self._peek_box.setVisible(False)
        self._place_markers()
        self._refresh_local()