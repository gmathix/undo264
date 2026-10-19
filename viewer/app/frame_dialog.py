"""Popup showing the Frame Header of a reference picture, with Jump / Return navigation."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QVBoxLayout

from .left_dock import add_frame_header_nodes, add_slice_nodes


class FrameInfoDialog(QDialog):
    jumpRequested = Signal(int)   # output_idx
    returnRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reference picture")
        self.setModal(False)
        self.resize(380, 460)
        self._decoded = -1
        self._output: int | None = None
        self._loaded = False

        lay = QVBoxLayout(self)
        self.lbl_title = QLabel()
        self.lbl_title.setWordWrap(True)
        lay.addWidget(self.lbl_title)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Property", "Value"])
        self.tree.setColumnWidth(0, 190)
        lay.addWidget(self.tree, 1)

        row = QHBoxLayout()
        self.btn_jump = QPushButton("Jump to frame")
        self.btn_return = QPushButton("Return")
        self.btn_close = QPushButton("Close")
        row.addWidget(self.btn_jump)
        row.addWidget(self.btn_return)
        row.addStretch()
        row.addWidget(self.btn_close)
        lay.addLayout(row)

        self.btn_jump.clicked.connect(lambda: self._output is not None and self.jumpRequested.emit(self._output))
        self.btn_return.clicked.connect(self.returnRequested)
        self.btn_close.clicked.connect(self.close)
        self.set_nav_state(-1, None)

    @property
    def decoded_idx(self) -> int:
        return self._decoded

    @property
    def output_idx(self) -> int | None:
        return self._output

    def show_reference(self, decoded_idx: int, output_idx: int | None, frame) -> None:
        """frame: parsed Frame of that picture, or None while it is still loading / not in the dump."""
        self._decoded, self._output = decoded_idx, output_idx
        self._loaded = frame is not None
        self.tree.clear()
        if output_idx is None:
            self.lbl_title.setText(f"<b>Reference #{decoded_idx}</b><br>This picture is not part of the dump.")
        elif frame is None:
            self.lbl_title.setText(f"<b>Reference #{decoded_idx}</b> → output frame {output_idx}<br>Loading…")
        else:
            self.lbl_title.setText(f"<b>Reference #{decoded_idx}</b> → output frame {output_idx}")
            add_frame_header_nodes(self.tree, frame)
            add_slice_nodes(self.tree, frame)
        self.btn_jump.setEnabled(output_idx is not None)

    def set_nav_state(self, current_out_idx: int, return_idx: int | None) -> None:
        """Jump is pointless when already on the target; Return needs a place to go back to."""
        self.btn_jump.setEnabled(self._output is not None and self._output != current_out_idx)
        self.btn_return.setEnabled(return_idx is not None)
        self.btn_return.setText("Return" if return_idx is None else f"Return to frame {return_idx}")

    @property
    def is_loading(self) -> bool:
        return self._output is not None and not self._loaded
