import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QLabel, QHeaderView
)
from PySide6.QtGui import QColor, QBrush
from PySide6.QtCore import Qt, Signal

from model import Frame, NO_REF
from . import theme


def add_frame_header_nodes(parent, frame: Frame) -> QTreeWidgetItem:
    """'Frame Header' node (picture-level fields) under a tree or item."""
    frm = QTreeWidgetItem(parent, ["Frame Header", ""])
    QTreeWidgetItem(frm, ["Output Idx", str(frame.output_idx)])
    QTreeWidgetItem(frm, ["Decoded Idx", str(frame.decoded_idx)])
    QTreeWidgetItem(frm, ["POC", str(frame.poc)])
    QTreeWidgetItem(frm, ["frame_num", str(frame.frame_num)])
    QTreeWidgetItem(frm, ["IDR", str(frame.idr)])
    frm.setExpanded(True)
    return frm


def add_slice_nodes(parent, frame: Frame) -> QTreeWidgetItem:
    slc_item = QTreeWidgetItem(parent, ["Slices", f"Count: {len(frame.slices)}"])
    for i, sh in enumerate(frame.slices):
        sh_node = QTreeWidgetItem(slc_item, [f"Slice {i}", ""])
        QTreeWidgetItem(sh_node, ["Type", sh.slice_type])
        QTreeWidgetItem(sh_node, ["First MB", str(sh.first_mb)])
        QTreeWidgetItem(sh_node, ["Slice QP", str(sh.slice_qp)])
        if sh.slice_type == "B":
            QTreeWidgetItem(sh_node, ["Direct MV pred mode", str(sh.direct_mv_pred_mode)])
        if sh.slice_type in ("P", "B"):
            QTreeWidgetItem(sh_node, ["Override of default number of refs", str(sh.num_ref_idx_active_override)])
            if sh.num_ref_idx_active_override:
                QTreeWidgetItem(sh_node, ["Number of active refs L0", str(sh.num_ref_idx_l0_active)])
                if sh.slice_type == "B":
                    QTreeWidgetItem(sh_node, ["Number of active refs L1", str(sh.num_ref_idx_l1_active)])
        QTreeWidgetItem(sh_node, ["CABAC init_idc", str(sh.cabac_init_idc)])
        QTreeWidgetItem(sh_node, ["Disable deblocking filter idc", str(sh.disable_deblocking_filter_idc)])
        QTreeWidgetItem(sh_node, ["Slice alpha c0 offset", str(sh.slice_alpha_c0_offset)])
        QTreeWidgetItem(sh_node, ["Slice beta offset", str(sh.slice_beta_offset)])
        sh_node.setExpanded(True)
    slc_item.setExpanded(True)
    return slc_item


class LeftDockPanel(QWidget):
    referenceSelected = Signal(int)   # decoded_idx, NO_REF when cleared
    referenceOpened = Signal(int)     # decoded_idx double-clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        def section(text: str) -> QLabel:
            lbl = QLabel(text.upper())
            lbl.setProperty("role", "section")
            return lbl

        # 1. Stream Context Header Tree
        layout.addWidget(section("Stream context"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Property", "Value"])
        self.tree.setColumnWidth(0, 150)
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree, stretch=2)

        # 2. DPB Status Table
        layout.addSpacing(4)
        layout.addWidget(section("DPB status"))
        self.lbl_legend = QLabel()
        self.lbl_legend.setProperty("role", "dim")
        layout.addWidget(self.lbl_legend)
        self.dpb_table = QTableWidget(3, 17)
        self.dpb_table.setVerticalHeaderLabels(["Slots", "L0", "L1"])
        self.dpb_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.dpb_table.horizontalHeader().setMinimumSectionSize(8)
        self.dpb_table.horizontalHeader().hide()
        self.dpb_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.dpb_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.dpb_table.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.dpb_table.setEditTriggers(QTableWidget.NoEditTriggers)
        # Our own colouring owns the look: Qt's selection/focus painting would fight it
        self.dpb_table.setSelectionMode(QTableWidget.NoSelection)
        self.dpb_table.setFocusPolicy(Qt.NoFocus)
        self.dpb_table.setToolTip("Click: highlight blocks using this reference (persists across frames)\nDouble-click: frame info / jump")
        layout.addWidget(self.dpb_table, stretch=1)

        # Highlight State
        self.hl_l0 = NO_REF
        self.hl_l1 = NO_REF
        self._clicked_ref = NO_REF
        self.current_active_refs = frozenset()

        self._last_dbl = 0.0
        self._update_legend()
        self.dpb_table.cellClicked.connect(self._on_dpb_clicked)
        self.dpb_table.cellDoubleClicked.connect(self._on_dpb_double_clicked)

    def _cell_ref(self, row, col) -> int:
        item = self.dpb_table.item(row, col)
        return int(item.text()) if item and item.text() else NO_REF

    def _on_dpb_double_clicked(self, row, col):
        val = self._cell_ref(row, col)
        if val == NO_REF:
            return
        self._last_dbl = time.monotonic()
        if val != self._clicked_ref:  # a double-click also selects the reference
            self._clicked_ref = val
            self.referenceSelected.emit(val)
            self._apply_dpb_colors()
        self.referenceOpened.emit(val)

    def _on_dpb_clicked(self, row, col):
        # the release that ends a double-click also reports 'clicked': it must not toggle the selection back
        if time.monotonic() - self._last_dbl < 0.3:
            return
        item = self.dpb_table.item(row, col)
        if not item or not item.text():
            return

        val = int(item.text())
        # Toggle selection off if clicking the same item again
        if val == self._clicked_ref:
            self._clicked_ref = NO_REF
        else:
            self._clicked_ref = val

        self.referenceSelected.emit(self._clicked_ref)
        self._apply_dpb_colors()

    def refresh_theme(self):
        self._update_legend()
        self._apply_dpb_colors()

    def _update_legend(self):
        t = theme.current()
        chip = lambda color, label: f'<span style="color:{color}">&#9632;</span>&nbsp;{label}'  # noqa: E731
        self.lbl_legend.setText("&nbsp;&nbsp;".join([
            chip(t.dpb_l0, "L0 block"), chip(t.dpb_l1, "L1 block"), chip(t.dpb_slot, "slot"),
            chip(t.dpb_clicked_bg, "selected"), f'<span style="color:{t.dpb_active_fg}">12</span>&nbsp;active ref',
        ]))

    def highlight_partition_refs(self, l0_ref: int, l1_ref: int):
        """Called by the main app when the viewport selection changes."""
        self.hl_l0 = l0_ref
        self.hl_l1 = l1_ref
        self._apply_dpb_colors()

    def update_data(self, frame: Frame):
        self.tree.clear()

        # SPS nodes
        sps_item = QTreeWidgetItem(self.tree, ["SPS", ""])
        QTreeWidgetItem(sps_item, ["Profile / Level", f"{frame.sps.profile} / {frame.sps.level_idc}"])
        QTreeWidgetItem(sps_item, ["SPS id", str(frame.sps.sps_id)])
        QTreeWidgetItem(sps_item, ["Max frame_num", str(frame.sps.max_frame_num)])
        QTreeWidgetItem(sps_item, ["POC type", str(frame.sps.poc_type)])
        if (frame.sps.poc_type == 0):
            QTreeWidgetItem(sps_item, ["Max POC LSB", str(frame.sps.max_pic_order_cnt_lsb)])
        QTreeWidgetItem(sps_item, ["Max number of ref frames", str(frame.sps.max_num_ref_frames)])
        QTreeWidgetItem(sps_item, ["Gaps in frame_num allowed", str(frame.sps.gaps_in_frame_num_allowed)])
        QTreeWidgetItem(sps_item, ["Pic width (MBs)", str(frame.sps.pic_width_in_mbs)])
        QTreeWidgetItem(sps_item, ["Pic height (MBs)", str(frame.sps.pic_height_in_mbs)])
        QTreeWidgetItem(sps_item, ["Direct 8x8 inference", str(frame.sps.direct_8x8_inference)])
        if frame.sps.frame_cropping_flag and frame.sps.crop:
            QTreeWidgetItem(sps_item, ["Crop (L,R,T,B)", str(frame.sps.crop)])


        # PPS nodes
        pps_item = QTreeWidgetItem(self.tree, ["PPS", ""])
        QTreeWidgetItem(pps_item, ["PPS id", str(frame.pps.pps_id)])
        QTreeWidgetItem(pps_item, ["Entropy coding", frame.pps.entropy_coding_mode])
        QTreeWidgetItem(pps_item, ["Default number of active refs L0", str(frame.pps.num_ref_idx_l0_default_active)])
        QTreeWidgetItem(pps_item, ["Default number of active refs L1", str(frame.pps.num_ref_idx_l1_default_active)])
        QTreeWidgetItem(pps_item, ["Weighted prediction", str(frame.pps.weighted_pred)])
        if (frame.pps.weighted_pred):
            QTreeWidgetItem(pps_item, ["weighted_bipred_idc", str(frame.pps.weighted_bipred_idc)])
        QTreeWidgetItem(pps_item, ["Initial QP", str(frame.pps.pic_init_qp)])
        QTreeWidgetItem(pps_item, ["Initial QS ", str(frame.pps.pic_init_qs)])
        QTreeWidgetItem(pps_item, ["Chroma qp_index_offset", str(frame.pps.chroma_qp_index_offset)])
        QTreeWidgetItem(pps_item, ["Deblocking filter control present", str(frame.pps.deblocking_filter_control_present)])
        QTreeWidgetItem(pps_item, ["Constrained intra pred", str(frame.pps.constrained_intra_pred)])
        QTreeWidgetItem(pps_item, ["Redundant pic_cnt present", str(frame.pps.redundant_pic_cnt_present)])
        QTreeWidgetItem(pps_item, ["Transform 8x8 mode", str(frame.pps.transform_8x8_mode)])
        QTreeWidgetItem(pps_item, ["Second chroma qp_index_offset", str(frame.pps.second_chroma_qp_index_offset)])


        add_frame_header_nodes(self.tree, frame)
        add_slice_nodes(self.tree, frame)

        # The clicked reference is deliberately kept across frames (it is a decoded_idx, valid stream-wide).
        self.dpb_table.clearContents()
        self.current_active_refs = frame.referenced_decoded_indices()

        # Populate baseline text items cleanly
        self._fill_dpb_row(0, frame.dpb.slots, frame.dpb.slots_size)
        self._fill_dpb_row(1, frame.dpb.l0, frame.dpb.lists_size)
        self._fill_dpb_row(2, frame.dpb.l1, frame.dpb.lists_size)

        # Apply the master coloring pass
        self._apply_dpb_colors()


    def _fill_dpb_row(self, row: int, data, max_len: int):
        for col in range(max_len):
            val = int(data[col])
            item = QTableWidgetItem("" if val == NO_REF else str(val))
            item.setTextAlignment(Qt.AlignCenter)
            self.dpb_table.setItem(row, col, item)

    def _apply_dpb_colors(self):
        t = theme.current()
        for row in range(3):
            for col in range(17):
                item = self.dpb_table.item(row, col)
                if not item:
                    continue

                txt = item.text()
                val = int(txt) if txt else NO_REF

                bg_color = QColor(t.dpb_cell)
                fg_color = QColor(t.text)
                font = item.font()
                font.setBold(False)

                if val == NO_REF:
                    bg_color = QColor(t.dpb_empty)
                else:
                    if val in self.current_active_refs:
                        fg_color = QColor(t.dpb_active_fg)
                        font.setBold(True)

                    # Prioritize highlighting the clicked reference
                    if val == self._clicked_ref:
                        bg_color = QColor(t.dpb_clicked_bg)
                        fg_color = QColor(t.dpb_clicked_fg)
                    else:
                        is_l0 = (row == 1 and val == self.hl_l0)
                        is_l1 = (row == 2 and val == self.hl_l1)
                        is_slot = (row == 0 and (val == self.hl_l0 or val == self.hl_l1))

                        bg = t.dpb_l0 if is_l0 else t.dpb_l1 if is_l1 else t.dpb_slot if is_slot else None
                        if bg:
                            bg_color = QColor(bg)
                            if val not in self.current_active_refs:  # keep the green of active references
                                fg_color = QColor("#ffffff")

                item.setBackground(QBrush(bg_color))
                item.setForeground(QBrush(fg_color))
                item.setFont(font)
