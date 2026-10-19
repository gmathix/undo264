"""Stream / decoding viewer for the undo264 decoder.

Usage (from this directory):
    python .                          # opens the file picker
    python . dump.json video.yuv      # loads both files right away (either one pre-fills the picker)
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.app import ViewerWindow


def main(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setStyle("Fusion")  # consistent cross-platform base; the colours come from app/theme.py

    json_path = yuv_path = ""
    for arg in argv[1:]:
        low = arg.lower()
        if low.endswith(".json"):
            json_path = arg
        elif low.endswith(".yuv"):
            yuv_path = arg

    window = ViewerWindow(json_path, yuv_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
