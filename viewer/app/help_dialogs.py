"""Help menu dialogs: the Controls reference and the About box.

The controls list is plain data (`CONTROLS`): to document a new shortcut, add a row there.
Each row is (keys, description); `keys` is a list of alternatives, each alternative being the
keys to press together, e.g. [["Ctrl", "Wheel"]] or [["Left"], ["Right"]].
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .widgets import mono_font

APP_NAME = "undo264 Viewer"
APP_VERSION = "0.1.0"  # keep in sync with pyproject.toml

Keys = list[list[str]]

CONTROLS: list[tuple[str, list[tuple[Keys, str]]]] = [
    ("View", [
        ([["Ctrl", "Wheel"]], "Zoom, centred on the mouse cursor"),
        ([["Middle drag"]], "Pan the picture"),
        ([["Arrows"], ["Wheel"]], "Scroll the picture when it is zoomed in"),
        ([["F"]], "Fit the picture to the viewport"),
        ([["1"]], "Show the picture at 1:1 (one video pixel per screen pixel)"),
    ]),
    ("Overlays", [
        ([["G"]], "Show or hide the macroblock grid"),
        ([["S"]], "Show or hide slice boundaries"),
        ([["M"]], "Show or hide the motion vectors of the whole frame"),
    ]),
    ("Selecting", [
        ([["Click"]], "Select a macroblock: the inspector shows its details and its vectors are drawn"),
        ([["Click"]], "On an already selected macroblock: select the partition under the cursor"),
        ([["Drag"]], "Select a rectangle of macroblocks; the motion vector overlay is then limited to it"),
        ([["Esc"]], "Clear the selection"),
        ([["Click"]], "In the inspector's partition map: select that partition"),
    ]),
    ("Peeking at references", [
        ([["Shift", "Left"]], "Hold to see the L0 reference the selected partition predicts from, "
                              "with a box on the block its vector points to"),
        ([["Shift", "Right"]], "Hold to see the L1 reference the same way"),
    ]),
    ("Reference list (DPB table)", [
        ([["Click"]], "Highlight every block that uses this reference; click again to clear. "
                      "It stays active while you change frames"),
        ([["Double click"]], "Open the reference picture's header, with a button to jump to it"),
        ([["Return"]], "Toolbar button: come back to the frame you jumped from"),
    ]),
    ("Playback", [
        ([["Play"], ["Pause"]], "Toolbar buttons: play or pause (playing from the last frame restarts at the first)"),
        ([["Step <"], ["Step >"]], "Toolbar buttons: previous / next frame, in display order"),
        ([["Slider"]], "Drag to seek to any frame"),
    ]),
    ("Windows", [
        ([["Ctrl", "1 / 2 / 3"]],
         "Show or hide Stream Info, Viewport and Macroblock Inspector (Window menu)"),
        ([["F1"]], "Open this list"),
        ([["Ctrl", "Q"]], "Quit"),
    ]),
]


def _keycap(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "key")
    lbl.setFont(mono_font())
    lbl.setAlignment(Qt.AlignCenter)
    return lbl


def _keys_widget(alternatives: Keys) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    for i, combo in enumerate(alternatives):
        if i:
            sep = QLabel("/")
            sep.setProperty("role", "dim")
            lay.addWidget(sep)
        for j, key in enumerate(combo):
            if j:
                plus = QLabel("+")
                plus.setProperty("role", "dim")
                lay.addWidget(plus)
            lay.addWidget(_keycap(key))
    lay.addStretch(1)
    return w


class ControlsDialog(QDialog):
    KEYS_COLUMN_WIDTH = 250

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Controls")
        self.setModal(False)
        self.resize(760, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 12)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 18, 22, 10)
        lay.setSpacing(4)
        scroll.setWidget(body)

        intro = QLabel("Everything you can do with the mouse and keyboard. "
                       "Most actions need a loaded stream.")
        intro.setProperty("role", "dim")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        for title, rows in CONTROLS:
            lay.addSpacing(14)
            head = QLabel(title.upper())
            head.setProperty("role", "section")
            lay.addWidget(head)

            card = QFrame()
            card.setObjectName("card")
            grid = QGridLayout(card)
            grid.setContentsMargins(14, 10, 14, 10)
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(8)
            grid.setColumnMinimumWidth(0, self.KEYS_COLUMN_WIDTH)
            grid.setColumnStretch(1, 1)
            for r, (keys, text) in enumerate(rows):
                grid.addWidget(_keys_widget(keys), r, 0, Qt.AlignVCenter)
                desc = QLabel(text)
                desc.setWordWrap(True)
                grid.addWidget(desc, r, 1, Qt.AlignVCenter)
            lay.addWidget(card)
        lay.addStretch(1)

        row = QHBoxLayout()
        row.setContentsMargins(22, 10, 22, 0)
        row.addStretch(1)
        btn = QPushButton("Close")
        btn.clicked.connect(self.close)
        row.addWidget(btn)
        root.addLayout(row)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setModal(True)
        self.setFixedWidth(460)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 26, 28, 20)
        lay.setSpacing(10)

        title = QLabel(APP_NAME)
        f = title.font()
        f.setPointSize(f.pointSize() + 8)
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)

        version = QLabel(f"Version {APP_VERSION}")
        version.setProperty("role", "dim")
        lay.addWidget(version)
        lay.addSpacing(6)

        blurb = QLabel(
            "A viewer for the H.264 streams decoded by undo264. It shows the decoded video next to "
            "the information the decoder recorded for each macroblock: types, partitions, motion "
            "vectors, references, quantizers and the decoded picture buffer."
        )
        blurb.setWordWrap(True)
        lay.addWidget(blurb)

        ai = QLabel("This application was written entirely by AI (Claude, by Anthropic).")
        ai.setWordWrap(True)
        lay.addWidget(ai)

        lay.addSpacing(6)
        built = QLabel("Built with Python, Qt for Python (PySide6), NumPy and OpenCV.")
        built.setProperty("role", "dim")
        built.setWordWrap(True)
        lay.addWidget(built)

        license_ = QLabel("Part of undo264 · MIT license")
        license_.setProperty("role", "dim")
        lay.addWidget(license_)

        lay.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("Close")
        btn.setProperty("primary", True)
        btn.setDefault(True)
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        lay.addLayout(row)
