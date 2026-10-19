"""Right dock: macroblock inspector (selection details, partition map, macroblock filters).

Sizing rule: nothing in this panel may depend on its content. The panel has a fixed width,
every text field is an elided fixed-pitch label, numbers are formatted to a fixed width and
the detail tables always have the same number of rows. Selecting another macroblock (or
playing the video) therefore never moves a single pixel of the layout.
"""
import numpy as np
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QPainter, QColor, QPen, QBrush
from PySide6.QtCore import Qt, Signal, QRectF, QPointF

from model import MbType, PredDir, Intra4x4Mode, Intra16x16Mode, IntraChromaMode
from . import theme
from .partitions import partitions, Partition
from .widgets import ElidedLabel, KeyValueGrid, mono_font

DASH = "\u2014"


class PartitionMapWidget(QWidget):
    partitionClicked = Signal(object)
    SIZE = 176

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
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
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(t.map_bg))
        painter.setPen(QPen(QColor(t.border), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        if not self.parts:
            painter.setPen(QColor(t.text_dim))
            painter.drawText(self.rect(), Qt.AlignCenter, "No macroblock selected")
            return

        painter.scale(self.width() / 16.0, self.height() / 16.0)
        border = QColor(t.map_part_border)
        select = QColor(t.map_select)
        select.setAlpha(150)

        for p in self.parts:
            painter.setBrush(QBrush(select if p == self.selected else QColor(t.map_part)))
            painter.setPen(QPen(border, 0.5, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
            painter.drawRect(QRectF(p.x, p.y, p.w, p.h))
            if self.frame.mb_type[self.mb] <= MbType.PCM:
                self._draw_intra_arrow(painter, p, QColor(t.map_intra))

        # 8x8 quadrant guides, slightly stronger
        guide = QColor(t.text)
        guide.setAlpha(110)
        painter.setPen(QPen(guide, 0.35))
        painter.drawLine(8, 0, 8, 16)
        painter.drawLine(0, 8, 16, 8)

    def _draw_intra_arrow(self, painter, p, color: QColor):
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
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(cx, cy), 1.0, 1.0)
        else:
            length = np.hypot(vx, vy)
            scale = min(p.w, p.h) * 0.35 / length
            vx, vy = vx * scale, vy * scale
            painter.setPen(QPen(color, 0.6))
            painter.drawLine(QPointF(cx, cy), QPointF(cx + vx, cy + vy))


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(12, 10, 12, 12)
    lay.setSpacing(8)
    lbl = QLabel(title.upper())
    lbl.setProperty("role", "section")
    lay.addWidget(lbl)
    return card, lay


class RightDockPanel(QWidget):
    filtersChanged = Signal(dict)

    WIDTH = 330  # fixed: the dock never resizes itself, whatever it displays
    DETAIL_ROWS = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self.WIDTH)
        self._badge_key = None
        self._last_kind: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        scroll.setWidget(body)

        # ---- Macroblock card
        card, lay = _card("Macroblock")
        head = QHBoxLayout()
        self.lbl_title = ElidedLabel("No selection")
        f = self.lbl_title.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        self.lbl_title.setFont(f)
        self.lbl_title.setMinimumHeight(self.lbl_title.fontMetrics().height() + 4)
        self.lbl_addr = ElidedLabel("")
        self.lbl_addr.setFont(mono_font())
        self.lbl_addr.setProperty("role", "dim")
        self.lbl_addr.setFixedWidth(72)
        self.lbl_addr.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.lbl_title, 1)
        head.addWidget(self.lbl_addr)
        lay.addLayout(head)

        self.lbl_kind = QLabel("")
        self.lbl_kind.setFixedSize(136, 22)
        self.lbl_kind.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.lbl_kind, 0, Qt.AlignLeft)

        self.grid_meta = KeyValueGrid(4)
        lay.addWidget(self.grid_meta)
        layout.addWidget(card)

        # ---- Partition card
        card, lay = _card("Partition")
        self.map_widget = PartitionMapWidget()
        lay.addWidget(self.map_widget, alignment=Qt.AlignHCenter)
        self.grid_details = KeyValueGrid(self.DETAIL_ROWS)
        lay.addWidget(self.grid_details)
        layout.addWidget(card)

        # ---- Filters card
        card, flay = _card("Filter macroblocks")

        def chk(text, tip=None):
            c = QCheckBox(text)
            if tip:
                c.setToolTip(tip)
            return c

        def row(indent, *boxes):
            h = QHBoxLayout()
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(14)
            h.addSpacing(18 * indent)
            for b in boxes:
                h.addWidget(b)
            h.addStretch(1)
            flay.addLayout(h)

        self.chk_intra = chk("Intra")
        self.chk_i16, self.chk_i8, self.chk_i4 = chk("16x16"), chk("8x8"), chk("4x4")
        self.chk_inter = chk("Inter")
        self.chk_p16, self.chk_p16x8 = chk("16x16"), chk("16x8 / 8x16")
        self.chk_skip, self.chk_direct = chk("Skip"), chk("Direct")
        self.chk_p8 = chk("8x8 sub-blocks", "Inter 8x8 macroblocks, by sub-partition")
        self.chk_p8_8x8, self.chk_p8_8x4 = chk("8x8"), chk("8x4")
        self.chk_p8_4x8, self.chk_p8_4x4 = chk("4x8"), chk("4x4")

        row(0, self.chk_intra)
        row(1, self.chk_i16, self.chk_i8, self.chk_i4)
        row(0, self.chk_inter)
        row(1, self.chk_p16, self.chk_p16x8)
        row(1, self.chk_skip, self.chk_direct)
        row(1, self.chk_p8)
        row(2, self.chk_p8_8x8, self.chk_p8_8x4, self.chk_p8_4x8, self.chk_p8_4x4)
        layout.addWidget(card)
        layout.addStretch(1)

        # Connect Filter Events
        self.chk_intra.clicked.connect(self._toggle_intra)
        self.chk_inter.clicked.connect(self._toggle_inter)
        self.chk_p8.clicked.connect(self._toggle_p8)

        for c in [self.chk_i16, self.chk_i8, self.chk_i4,
                  self.chk_p16, self.chk_p16x8,
                  self.chk_p8_8x8, self.chk_p8_8x4, self.chk_p8_4x8, self.chk_p8_4x4,
                  self.chk_skip, self.chk_direct]:
            c.clicked.connect(self._emit_filters)

        self.update_data(None, -1, None)

    def refresh_theme(self):
        self._badge_key = None  # force the badge to restyle
        self.map_widget.update()
        self._set_badge(self.lbl_kind.text(), self._last_kind)

    def _set_badge(self, text: str, kind: str | None):
        """kind: 'intra' | 'inter' | 'skip' | None (empty, invisible badge that still holds its space)."""
        self._last_kind = kind
        self.lbl_kind.setText(text)
        key = (kind, theme.current().name)
        if key == self._badge_key:
            return
        self._badge_key = key
        t = theme.current()
        bg = {"intra": t.badge_intra, "inter": t.badge_inter, "skip": t.badge_skip}.get(kind)
        if bg is None:
            self.lbl_kind.setStyleSheet("background: transparent;")
        else:
            self.lbl_kind.setStyleSheet(
                f"background: {bg}; color: #ffffff; border-radius: 11px; font-weight: 700; font-size: 11px;")

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
            self.lbl_title.setText("No selection")
            self.lbl_addr.setText("")
            self._set_badge("", None)
            self.grid_meta.set_rows([("QPY", DASH), ("CBP luma", DASH), ("CBP chroma", DASH), ("Transform 8x8", DASH)])
            self.map_widget.set_data(None, -1, None)
            self.grid_details.set_rows([("Partition", DASH)])
            return

        w = frame.mb_width
        cbp = int(frame.cbp[mb])
        t = int(frame.mb_type[mb])
        self.lbl_title.setText(f"MB ({mb % w}, {mb // w})")
        self.lbl_addr.setText(f"#{mb}")
        kind = "intra" if t <= MbType.PCM else ("skip" if t == MbType.SKIP else "inter")
        self._set_badge(MbType(t).name.replace("_", " "), kind)
        self.grid_meta.set_rows([
            ("QPY", f"{int(frame.qpy[mb])}"),
            ("CBP luma", f"{cbp & 15:04b}"),
            ("CBP chroma", f"{cbp >> 4}"),
            ("Transform 8x8", "Yes" if bool(frame.t8x8[mb]) else "No"),
        ])
        self.map_widget.set_data(frame, mb, selected_part)

        if not selected_part:
            parts = partitions(frame, mb)
            selected_part = parts[0] if parts else None

        self._update_details(frame, mb, selected_part)

    def _update_details(self, frame, mb: int, p: Partition | None):
        if p is None:
            self.grid_details.set_rows([("Partition", DASH)])
            return

        rows = [("Partition", f"{p.w}x{p.h} @ ({p.x}, {p.y})")]
        t = int(frame.mb_type[mb])

        if t >= MbType.SKIP:
            blk8 = p.blk8
            bx, by = p.blk
            d = PredDir(frame.pred_dir[mb, blk8])
            used = [name for flag, name in ((PredDir.L0, "L0"), (PredDir.L1, "L1")) if d & flag]
            rows.append(("Prediction", " + ".join(used) if used else DASH))

            # Fixed-width numbers (monospace font): the text always has the same length, so the
            # layout cannot change with the vector. Range: qpel +-8192 -> 6 chars, px +-2048.00 -> 8 chars.
            for lst, flag in ((0, PredDir.L0), (1, PredDir.L1)):
                if d & flag:
                    ref = int(frame.ref_dec_idx[lst, mb, blk8])
                    mvx, mvy = (int(v) for v in frame.mv[lst, mb, by, bx])
                    rows.append((f"L{lst} ref", f"{ref}" if ref != -1 else "N/A"))
                    rows.append((f"L{lst} MV (qpel)", f"{mvx:+6d} {mvy:+6d}"))
                    rows.append((f"L{lst} MV (px)", f"{mvx / 4:+8.2f} {mvy / 4:+8.2f}"))
                else:
                    rows += [(f"L{lst} ref", DASH), (f"L{lst} MV (qpel)", DASH), (f"L{lst} MV (px)", DASH)]
        else:
            if t == MbType.PCM:
                rows.append(("Mode", "PCM"))
            else:
                mode_val = int(frame.intra_modes[mb, p.idx])
                enum = Intra16x16Mode if frame.intra_mode_count[mb] == 1 else Intra4x4Mode
                rows.append(("Luma mode", enum(mode_val).name))
                rows.append(("Chroma mode", IntraChromaMode(int(frame.chroma_intra_modes[mb])).name))

        self.grid_details.set_rows(rows)
