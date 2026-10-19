import numpy as np
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QCheckBox
)
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from PySide6.QtCore import Qt, Signal, QRectF, QPointF

from model import MbType, PredDir, Intra4x4Mode, Intra16x16Mode, IntraChromaMode
from .partitions import partitions, Partition


class PartitionMapWidget(QWidget):
    partitionClicked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(160, 160)
        self.frame = None
        self.mb = -1
        self.parts: list[Partition] = []
        self.selected: Partition | None = None

    def set_data(self, frame, mb: int, selected: Partition | None):
        self.frame = frame
        self.mb = mb
        self.parts = partitions(frame, mb) if frame and mb >= 0 else []
        self.selected = selected
        self.update()

    def mousePressEvent(self, event):
        if not self.parts:
            return
        px = event.position().x() / (self.width() / 16.0)
        py = event.position().y() / (self.height() / 16.0)
        for p in self.parts:
            if p.x <= px < p.x + p.w and p.y <= py < p.y + p.h:
                self.partitionClicked.emit(p)
                return
        self.partitionClicked.emit(None)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(30, 30, 30))

        if not self.parts:
            return

        scale_x = self.width() / 16.0
        scale_y = self.height() / 16.0
        painter.scale(scale_x, scale_y)

        # Draw partitions
        for p in self.parts:
            rect = QRectF(p.x, p.y, p.w, p.h)
            if p == self.selected:
                painter.setBrush(QBrush(QColor(255, 225, 0, 150)))
            else:
                painter.setBrush(QBrush(QColor(100, 100, 100, 100)))

            painter.setPen(QPen(QColor(200, 200, 200), 0.5, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
            painter.drawRect(rect)

            # Draw intra arrows if applicable
            if self.frame.mb_type[self.mb] <= MbType.PCM:
                self._draw_intra_arrow(painter, p)

        # Draw 8x8 grid bounds slightly thicker
        painter.setPen(QPen(QColor(255, 255, 255), 0.8))
        painter.drawLine(8, 0, 8, 16)
        painter.drawLine(0, 8, 16, 8)

    def _draw_intra_arrow(self, painter, p):
        mode = self.frame.intra_modes[self.mb, p.idx]
        is_16 = self.frame.intra_mode_count[self.mb] == 1

        if is_16:
            vecs = {0: (0, 1), 1: (1, 0), 2: (0, 0), 3: (0, 0)}  # Plane = no directional arrow
        else:
            # Derived from Pred directions where Y is down
            vecs = {0: (0, 1), 1: (1, 0), 2: (0, 0), 3: (-1, 1), 4: (1, 1),
                    5: (0.5, 1), 6: (1, 0.5), 7: (-0.5, 1), 8: (1, -0.5)}

        vx, vy = vecs.get(int(mode), (0, 0))
        cx, cy = p.x + p.w / 2.0, p.y + p.h / 2.0

        if vx == 0 and vy == 0:
            painter.setBrush(QColor(255, 100, 100))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(cx, cy), 1.0, 1.0)
        else:
            length = np.hypot(vx, vy)
            # Fix: Calculate the scalar first, then multiply individually
            scale = min(p.w, p.h) * 0.35 / length
            vx, vy = vx * scale, vy * scale

            painter.setPen(QPen(QColor(255, 100, 100), 0.6))
            painter.drawLine(QPointF(cx, cy), QPointF(cx + vx, cy + vy))


class RightDockPanel(QWidget):
    filtersChanged = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(300)

        layout = QVBoxLayout(self)

        # Top Metadata
        self.lbl_meta = QLabel("No Selection")
        self.lbl_meta.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_meta.setMinimumHeight(110)
        layout.addWidget(self.lbl_meta)

        # Visual Map
        self.map_widget = PartitionMapWidget()
        layout.addWidget(self.map_widget, alignment=Qt.AlignHCenter)

        # Bottom Partition Details
        self.lbl_details = QLabel()
        self.lbl_details.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.lbl_details.setMinimumHeight(170)
        layout.addWidget(self.lbl_details)

        # New Feature: MB Filters
        self.group_filters = QGroupBox("Filter Macroblocks")
        flay = QVBoxLayout(self.group_filters)

        def make_chk(name, parent_layout, indent_level=0):
            chk = QCheckBox(name)
            if indent_level > 0:
                h = QHBoxLayout()
                h.addSpacing(20 * indent_level)
                h.addWidget(chk)
                h.setContentsMargins(0, 0, 0, 0)
                parent_layout.addLayout(h)
            else:
                parent_layout.addWidget(chk)
            return chk

        self.chk_intra = make_chk("Intra", flay)
        self.chk_i16 = make_chk("16x16", flay, indent_level=1)
        self.chk_i8 = make_chk("8x8", flay, indent_level=1)
        self.chk_i4 = make_chk("4x4", flay, indent_level=1)

        self.chk_inter = make_chk("Inter", flay)
        self.chk_p16 = make_chk("16x16", flay, indent_level=1)
        self.chk_p16x8 = make_chk("16x8 / 8x16", flay, indent_level=1)

        # Nested 8x8 branch
        self.chk_p8 = make_chk("8x8", flay, indent_level=1)
        self.chk_p8_8x8 = make_chk("8x8", flay, indent_level=2)
        self.chk_p8_8x4 = make_chk("8x4", flay, indent_level=2)
        self.chk_p8_4x8 = make_chk("4x8", flay, indent_level=2)
        self.chk_p8_4x4 = make_chk("4x4", flay, indent_level=2)

        self.chk_skip = make_chk("Skip", flay, indent_level=1)
        self.chk_direct = make_chk("Direct", flay, indent_level=1)

        layout.addWidget(self.group_filters)
        layout.addStretch()

        # Connect Filter Events
        self.chk_intra.clicked.connect(self._toggle_intra)
        self.chk_inter.clicked.connect(self._toggle_inter)
        self.chk_p8.clicked.connect(self._toggle_p8)

        for chk in [self.chk_i16, self.chk_i8, self.chk_i4,
                    self.chk_p16, self.chk_p16x8,
                    self.chk_p8_8x8, self.chk_p8_8x4, self.chk_p8_4x8, self.chk_p8_4x4,
                    self.chk_skip, self.chk_direct]:
            chk.clicked.connect(self._emit_filters)

    def _toggle_intra(self, checked):
        for chk in [self.chk_i16, self.chk_i8, self.chk_i4]:
            chk.setChecked(checked)
        self._emit_filters()

    def _toggle_p8(self, checked):
        for chk in [self.chk_p8_8x8, self.chk_p8_8x4, self.chk_p8_4x8, self.chk_p8_4x4]:
            chk.setChecked(checked)
        self._emit_filters()

    def _toggle_inter(self, checked):
        for chk in [self.chk_p16, self.chk_p16x8, self.chk_p8, self.chk_skip, self.chk_direct,
                    self.chk_p8_8x8, self.chk_p8_8x4, self.chk_p8_4x8, self.chk_p8_4x4]:
            chk.setChecked(checked)
        self._emit_filters()

    def _emit_filters(self):
        # Sync parent checkbox states visually
        i_all = self.chk_i16.isChecked() and self.chk_i8.isChecked() and self.chk_i4.isChecked()
        self.chk_intra.blockSignals(True)
        self.chk_intra.setChecked(i_all)
        self.chk_intra.blockSignals(False)

        p8_all = all(c.isChecked() for c in [self.chk_p8_8x8, self.chk_p8_8x4, self.chk_p8_4x8, self.chk_p8_4x4])
        self.chk_p8.blockSignals(True)
        self.chk_p8.setChecked(p8_all)
        self.chk_p8.blockSignals(False)

        p_all = all(c.isChecked() for c in [self.chk_p16, self.chk_p16x8, self.chk_p8, self.chk_skip, self.chk_direct])
        self.chk_inter.blockSignals(True)
        self.chk_inter.setChecked(p_all)
        self.chk_inter.blockSignals(False)

        filters = {
            "intra_16x16": self.chk_i16.isChecked(),
            "intra_8x8": self.chk_i8.isChecked(),
            "intra_4x4": self.chk_i4.isChecked(),
            "inter_16x16": self.chk_p16.isChecked(),
            "inter_16x8": self.chk_p16x8.isChecked(),
            "inter_8x8_8x8": self.chk_p8_8x8.isChecked(),
            "inter_8x8_8x4": self.chk_p8_8x4.isChecked(),
            "inter_8x8_4x8": self.chk_p8_4x8.isChecked(),
            "inter_8x8_4x4": self.chk_p8_4x4.isChecked(),
            "skip": self.chk_skip.isChecked(),
            "direct": self.chk_direct.isChecked(),
        }
        self.filtersChanged.emit(filters)

    def update_data(self, frame, mb: int, selected_part: Partition | None):
        if frame is None or mb < 0:
            self.lbl_meta.setText("No Selection")
            self.map_widget.set_data(None, -1, None)
            self.lbl_details.setText("")
            return

        w = frame.mb_width
        cbp = int(frame.cbp[mb])
        t8x8 = bool(frame.t8x8[mb])
        mb_type = MbType(int(frame.mb_type[mb])).name

        meta_text = (
            f"MB ({mb % w}, {mb // w})  #{mb}\n"
            f"Type: {mb_type}\n"
            f"QPY: {int(frame.qpy[mb])}\n"
            f"CBP Luma: {cbp & 15:04b}\n"
            f"CBP Chroma: {cbp >> 4}\n"
            f"Transform 8x8: {'Yes' if t8x8 else 'No'}"
        )
        self.lbl_meta.setText(meta_text)
        self.map_widget.set_data(frame, mb, selected_part)

        if not selected_part:
            parts = partitions(frame, mb)
            selected_part = parts[0] if parts else None

        self._update_details(frame, mb, selected_part)

    def _update_details(self, frame, mb: int, p: Partition | None):
        if p is None:
            self.lbl_details.setText("Select a partition...")
            return

        lines = [f"Partition: {p.w}x{p.h} at ({p.x}, {p.y})"]
        t = int(frame.mb_type[mb])

        if t >= MbType.SKIP:
            blk8 = p.blk8
            bx, by = p.blk
            d = PredDir(frame.pred_dir[mb, blk8])
            lines.append(f"Pred Dir: {d.name}")

            if d & PredDir.L0:
                ref = frame.ref_dec_idx[0, mb, blk8]
                mv = frame.mv[0, mb, by, bx]
                lines.append(f"L0 Ref: {ref if ref != -1 else 'N/A'}")
                lines.append(f"L0 MV:  {mv[0]:>5d}, {mv[1]:>5d} (qp)")
                lines.append(f"        {mv[0] / 4:>7.2f}, {mv[1] / 4:>7.2f} (px)")

            if d & PredDir.L1:
                ref = frame.ref_dec_idx[1, mb, blk8]
                mv = frame.mv[1, mb, by, bx]
                lines.append(f"L1 Ref: {ref if ref != -1 else 'N/A'}")
                lines.append(f"L1 MV:  {mv[0]:>5d}, {mv[1]:>5d} (qp)")
                lines.append(f"        {mv[0] / 4:>7.2f}, {mv[1] / 4:>7.2f} (px)")

        else:
            if t == MbType.PCM:
                lines.append("Mode: PCM")
            else:
                mode_val = frame.intra_modes[mb, p.idx]
                if frame.intra_mode_count[mb] == 1:
                    lines.append(f"Luma Mode: {Intra16x16Mode(mode_val).name}")
                else:
                    lines.append(f"Luma Mode: {Intra4x4Mode(mode_val).name}")

                c_mode = frame.chroma_intra_modes[mb]
                lines.append(f"Chroma Mode: {IntraChromaMode(c_mode).name}")

        self.lbl_details.setText("\n".join(lines))