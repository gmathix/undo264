"""File selection screen shown in the viewport until a stream is loaded.

Replaces the former modal LaunchDialog: it lives inside the main window, centred in the
viewport, and is swapped for the video view once the dump has been indexed.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from .widgets import ElidedLabel


def _repolish(w: QWidget) -> None:
    """Re-evaluate the stylesheet after a dynamic property changed."""
    w.style().unpolish(w)
    w.style().polish(w)


class FileRow(QFrame):
    """One 'kind of file' line: caption, chosen file name (or hint) and a Browse button."""
    browseClicked = Signal()

    def __init__(self, caption: str, placeholder: str, parent=None):
        super().__init__(parent)
        self.setObjectName("fileRow")
        self.setProperty("filled", False)
        self._placeholder = placeholder

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(12)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.lbl_caption = QLabel(caption)
        self.lbl_caption.setProperty("role", "section")
        self.lbl_name = ElidedLabel(placeholder)
        self.lbl_name.setProperty("role", "dim")
        text.addWidget(self.lbl_caption)
        text.addWidget(self.lbl_name)
        lay.addLayout(text, 1)

        self.btn = QPushButton("Browse…")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self.browseClicked)
        lay.addWidget(self.btn)

    def set_path(self, path: str) -> None:
        filled = bool(path)
        self.lbl_name.setText(os.path.basename(path) if filled else self._placeholder)
        self.lbl_name.setToolTip(path)
        self.lbl_name.setProperty("role", "" if filled else "dim")
        self.setProperty("filled", filled)
        self.btn.setText("Change…" if filled else "Browse…")
        for w in (self, self.lbl_name):
            _repolish(w)


class LaunchPage(QWidget):
    launchRequested = Signal(str, str)  # json_path, yuv_path

    CARD_WIDTH = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("launchPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAcceptDrops(True)
        self.json_path = ""
        self.yuv_path = ""
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)

        card = self.card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(self.CARD_WIDTH)
        outer.addWidget(card, 0, Qt.AlignHCenter)
        outer.addStretch(1)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(28, 26, 28, 24)
        lay.setSpacing(12)

        title = QLabel("undo264 Viewer")
        f = title.font()
        f.setPointSize(f.pointSize() + 9)
        f.setBold(True)
        title.setFont(f)
        subtitle = QLabel("Open a decoder info dump and the matching raw video to inspect the stream.")
        subtitle.setWordWrap(True)
        subtitle.setProperty("role", "dim")
        lay.addWidget(title)
        lay.addWidget(subtitle)
        lay.addSpacing(8)

        self.row_json = FileRow("INFO DUMP  ·  .json", "Choose the dump written by the decoder")
        self.row_yuv = FileRow("RAW VIDEO  ·  .yuv", "Choose the decoded 4:2:0 video")
        self.row_json.browseClicked.connect(self._pick_json)
        self.row_yuv.browseClicked.connect(self._pick_yuv)
        lay.addWidget(self.row_json)
        lay.addWidget(self.row_yuv)
        lay.addSpacing(6)

        self.btn_launch = QPushButton("Open")
        self.btn_launch.setProperty("primary", True)
        self.btn_launch.setCursor(Qt.PointingHandCursor)
        self.btn_launch.setMinimumHeight(34)
        self.btn_launch.setEnabled(False)
        self.btn_launch.clicked.connect(self._emit_launch)
        lay.addWidget(self.btn_launch)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        # One status line, fixed height so the card never jumps between states.
        self.lbl_status = QLabel("Tip: you can also drop the two files onto this window.")
        self.lbl_status.setProperty("role", "dim")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setMinimumHeight(self.lbl_status.fontMetrics().lineSpacing() * 2 + 2)
        self.lbl_status.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(self.lbl_status)

    # ---------------------------------------------------------------- public
    def set_paths(self, json_path: str = "", yuv_path: str = "") -> None:
        if json_path:
            self.json_path = json_path
            self.row_json.set_path(json_path)
        if yuv_path:
            self.yuv_path = yuv_path
            self.row_yuv.set_path(yuv_path)
        self._update_enabled()

    def set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self._set_status(text if busy else "Tip: you can also drop the two files onto this window.", error=False)
        self.row_json.btn.setEnabled(not busy)
        self.row_yuv.btn.setEnabled(not busy)
        self._update_enabled()

    def show_error(self, msg: str) -> None:
        self._busy = False
        self.progress.setVisible(False)
        self.row_json.btn.setEnabled(True)
        self.row_yuv.btn.setEnabled(True)
        self._set_status(msg, error=True)
        self._update_enabled()

    # --------------------------------------------------------------- internals
    def _set_status(self, text: str, error: bool) -> None:
        self.lbl_status.setText(text)
        self.lbl_status.setObjectName("launchError" if error else "")
        self.lbl_status.setProperty("role", "" if error else "dim")
        _repolish(self.lbl_status)

    def _update_enabled(self) -> None:
        self.btn_launch.setEnabled(bool(self.json_path and self.yuv_path) and not self._busy)

    def _start_dir(self, other: str) -> str:
        return os.path.dirname(other) if other else ""

    def _pick_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Info Dump", self._start_dir(self.json_path or self.yuv_path), "JSON Files (*.json)")
        if path:
            self.set_paths(json_path=path)

    def _pick_yuv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Raw Video", self._start_dir(self.yuv_path or self.json_path), "YUV Files (*.yuv)")
        if path:
            self.set_paths(yuv_path=path)

    def _emit_launch(self) -> None:
        if self.json_path and self.yuv_path and not self._busy:
            self.launchRequested.emit(self.json_path, self.yuv_path)

    # ----------------------------------------------------------------- layout
    def _relayout(self) -> None:
        # Size hints change when the stylesheet (theme) is applied after the widgets were laid out.
        # The card holds word-wrapped labels, whose height depends on the width: pin the card to the
        # height its layout really needs at its fixed width.
        inner = self.card.layout()
        inner.invalidate()
        self.card.setFixedHeight(inner.totalHeightForWidth(self.CARD_WIDTH))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.StyleChange:
            QTimer.singleShot(0, self._relayout)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._relayout)

    # ------------------------------------------------------------ drag & drop
    @staticmethod
    def _dropped_paths(event) -> list[str]:
        return [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]

    def dragEnterEvent(self, event):
        if not self._busy and any(p.lower().endswith((".json", ".yuv")) for p in self._dropped_paths(event)):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for p in self._dropped_paths(event):
            low = p.lower()
            if low.endswith(".json"):
                self.set_paths(json_path=p)
            elif low.endswith(".yuv"):
                self.set_paths(yuv_path=p)
        event.acceptProposedAction()
