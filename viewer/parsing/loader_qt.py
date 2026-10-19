"""Qt glue for index building and on-demand frame parsing."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from .frame_store import FrameStore
from .index import StreamIndex


class QtIndexer(QThread):
    """Builds the `StreamIndex` off the GUI thread (one mmap pass over the dump)."""

    ready = Signal(object)  # StreamIndex
    failed = Signal(str)

    def __init__(self, json_path: str | Path, parent=None):
        super().__init__(parent)
        self.json_path = json_path

    def run(self):
        try:
            self.ready.emit(StreamIndex.build(self.json_path))
        except Exception as e:  # noqa: BLE001 - reported to the UI
            self.failed.emit(f"{type(e).__name__}: {e}")


class QtFrameStore(QObject):
    """`FrameStore` whose completion callbacks (pool manager thread) become queued Qt signals."""

    frame_ready = Signal(int)  # output_idx
    frame_failed = Signal(int, str)

    def __init__(self, index: StreamIndex, workers: Optional[int] = None, cache_size: int = 64, parent=None):
        super().__init__(parent)
        self.store = FrameStore(
            index,
            workers=workers,
            cache_size=cache_size,
            on_frame=self.frame_ready.emit,
            on_error=lambda i, e: self.frame_failed.emit(i, f"{type(e).__name__}: {e}"),
        )

    # thin delegation so the UI does not need to know about the inner store
    def get(self, out_idx):
        return self.store.get(out_idx)

    def request(self, out_idx):
        self.store.request(out_idx)

    def set_window(self, *a, **kw):
        self.store.set_window(*a, **kw)

    def warm(self):
        self.store.warm()

    def stats(self) -> dict:
        s = self.store
        return {"cached": len(s.cached_indices()), "cache_size": s.cache_size,
                "parsed": s.parsed_total, "workers": s.workers}

    def close(self):
        self.store.close()


# ----------------------------------------------------------------------------
# Legacy linear loader, kept so existing imports keep working. The viewer no
# longer uses it: it parses the whole file front to back in one thread.
from .parser import StreamParser  # noqa: E402
from .reader import iter_records  # noqa: E402


class QtInfoLoader(QThread):
    record_loaded = Signal(object)
    finished = Signal()

    def __init__(self, json_path: str | Path, parent=None):
        super().__init__(parent)
        self.json_path = json_path
        self._stop_requested = False

    def run(self):
        parser = StreamParser()
        try:
            for record in iter_records(self.json_path, parser):
                if self._stop_requested:
                    break
                self.record_loaded.emit(record)
        except Exception as e:
            print(f"Loader error: {e}")
        finally:
            self.finished.emit()

    def stop(self):
        self._stop_requested = True
        self.wait()