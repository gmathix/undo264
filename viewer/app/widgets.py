"""Small widgets whose size never depends on their text.

Qt labels report a size hint that follows their content. Inside a dock that is what makes
the whole dock grow and shrink while the content changes (e.g. motion vectors of varying
digit count). These widgets ignore their content for layout purposes and elide instead.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontDatabase, QPainter
from PySide6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QWidget


def mono_font(delta: int = 0):
    """The platform's fixed-pitch font: every digit takes the same width."""
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    if delta:
        f.setPointSize(max(6, f.pointSize() + delta))
    return f


class ElidedLabel(QLabel):
    """Single-line label that elides its text and never asks the layout for more room."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setTextFormat(Qt.PlainText)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        if text != self._full:
            self._full = text
            super().setText("")  # QLabel's own text is unused; we paint it
            self.update()

    def text(self) -> str:
        return self._full

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, self.fontMetrics().height() + 2 * self.margin())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _event):  # noqa: N802
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        r = self.contentsRect()
        elided = self.fontMetrics().elidedText(self._full, Qt.ElideRight, r.width())
        p.drawText(r, int(self.alignment()), elided)  # QLabel's default alignment already centres vertically


class KeyValueGrid(QWidget):
    """Fixed number of key / value rows. Height and width are constant whatever the text."""

    def __init__(self, rows: int, key_width: int = 104, mono_values: bool = True, parent=None):
        super().__init__(parent)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(3)
        lay.setColumnMinimumWidth(0, key_width)
        lay.setColumnStretch(1, 1)
        self._keys: list[ElidedLabel] = []
        self._vals: list[ElidedLabel] = []
        for r in range(rows):
            k = ElidedLabel()
            k.setProperty("role", "dim")
            k.setFixedWidth(key_width)
            v = ElidedLabel()
            if mono_values:
                v.setFont(mono_font())
            self._keys.append(k)
            self._vals.append(v)
            lay.addWidget(k, r, 0)
            lay.addWidget(v, r, 1)
        # one text line tall, even when empty
        for w in self._keys + self._vals:
            w.setMinimumHeight(w.fontMetrics().height() + 4)

    def set_rows(self, rows: list[tuple[str, str]]) -> None:
        """Fill the rows in order; the remaining rows are blanked (they keep their height)."""
        for i, (k, v) in enumerate(zip(self._keys, self._vals)):
            key, val = rows[i] if i < len(rows) else ("", "")
            k.setText(key)
            v.setText(val)
            v.setToolTip(val)

    def clear(self) -> None:
        self.set_rows([])
