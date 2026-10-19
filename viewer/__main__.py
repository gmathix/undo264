"""`python -m undo264_viewer dump.json` : print a one-line summary per record."""
from __future__ import annotations

import sys

from model import PPS, SPS, Frame, InfoRecord
from parsing import iter_records, QtInfoLoader
from app import *


def main(argv: list[str]) -> int:

    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Gives a clean, cross-platform dark/light dock interface

    dialog = LaunchDialog()
    if dialog.exec():
        window = ViewerWindow(dialog.json_path, dialog.yuv_path)
        window.show()
        sys.exit(app.exec())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))