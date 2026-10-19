"""On-demand, multi-process frame parsing with a bounded cache. Qt-free.

Parsing a 1080p frame is CPU-bound (~25 ms JSON decode + ~35 ms numpy
conversion) and holds the GIL, so a thread cannot add throughput. Frames are
parsed in worker *processes*: each worker mmaps the dump itself, so a job is
just ``(epoch, offset, length)`` and only the parsed ``Frame`` travels back
(~2 MB pickled, ~1 ms).

`FrameStore` keeps a small cache around the playhead, dispatches the frames it
is asked to prefetch nearest-first, and never has more jobs submitted than
there are workers, so a seek waits for at most one already-running parse
instead of queueing behind a long FIFO.
"""
from __future__ import annotations

import contextlib
import gc
import mmap
import multiprocessing
import os
import sys
import threading
import time
from collections import deque
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Callable, Iterable, Optional

from model import Frame

from .index import StreamIndex, build_parser

# --------------------------------------------------------------- worker side

_W: dict = {}


def _worker_init(path: str, epochs) -> None:
    gc.disable()  # parsed JSON has no cycles; refcounting frees it, GC only costs time
    f = open(path, "rb")
    _W["file"] = f
    _W["mm"] = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    _W["epochs"] = epochs
    _W["parsers"] = {}


def _worker_parse(epoch: int, offset: int, length: int) -> Frame:
    mm = _W["mm"]
    parser = _W["parsers"].get(epoch)
    if parser is None:
        parser = _W["parsers"][epoch] = build_parser(mm[o:o + n] for o, n in _W["epochs"][epoch])
    return parser.parse_line(mm[offset:offset + length])


def _worker_ping() -> int:
    return os.getpid()


def default_workers() -> int:
    return max(1, min(4, (os.cpu_count() or 2) - 1))


@contextlib.contextmanager
def _main_not_reimported():
    """Stop `spawn` from re-importing the application's entry script in every worker.

    Workers only need `parsing` and `model`. A GUI entry script usually imports
    PySide6 at module level; re-importing it in each worker costs seconds of CPU
    and ~100 MB each, and that startup competes with the first parses. With
    spawn, all workers are launched during the first `submit`, so hiding
    `__main__.__spec__` / `__file__` around that call is enough.
    """
    main = sys.modules.get("__main__")
    if main is None:
        yield
        return
    saved = {k: main.__dict__[k] for k in ("__spec__", "__file__") if k in main.__dict__}
    main.__dict__["__spec__"] = None
    main.__dict__.pop("__file__", None)
    try:
        yield
    finally:
        main.__dict__.update(saved)


# ---------------------------------------------------------------- main side

FrameCallback = Callable[[int], None]
ErrorCallback = Callable[[int, BaseException], None]


class FrameStore:
    """Cache + prefetcher over a `StreamIndex`. All public methods are thread-safe."""

    def __init__(
        self,
        index: StreamIndex,
        workers: Optional[int] = None,
        cache_size: int = 64,
        max_inflight: Optional[int] = None,
        on_frame: Optional[FrameCallback] = None,
        on_error: Optional[ErrorCallback] = None,
    ) -> None:
        self.index = index
        self.workers = workers or default_workers()
        self.cache_size = max(4, cache_size)
        self.max_inflight = max_inflight or self.workers  # nothing queued ahead of a seek target
        self.on_frame = on_frame
        self.on_error = on_error

        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._cache: dict[int, Frame] = {}
        self._inflight: set[int] = set()
        self._errors: dict[int, BaseException] = {}
        self._wanted: deque[int] = deque()
        self._center = 0
        self._protected: frozenset[int] = frozenset()
        self._closed = False
        self.parsed_total = 0
        self.parse_seconds = 0.0  # summed worker wall time per job, as seen from the main process
        self._t_submit: dict[int, float] = {}

        self._launched = False
        self._pool = ProcessPoolExecutor(
            max_workers=self.workers,
            mp_context=multiprocessing.get_context("spawn"),  # fork + Qt threads is unsafe
            initializer=_worker_init,
            initargs=(str(index.path), index.epochs),
        )

    # ----------------------------------------------------------------- public
    def _submit(self, fn, *args) -> Future:
        with self._lock:
            if not self._launched:  # first submit launches every worker process
                self._launched = True
                with _main_not_reimported():
                    return self._pool.submit(fn, *args)
        return self._pool.submit(fn, *args)

    def warm(self) -> None:
        """Start all worker processes now (imports numpy etc.) instead of on first use."""
        for _ in range(self.workers):
            self._submit(_worker_ping)

    def get(self, out_idx: int) -> Optional[Frame]:
        with self._lock:
            return self._cache.get(out_idx)

    def error(self, out_idx: int) -> Optional[BaseException]:
        with self._lock:
            return self._errors.get(out_idx)

    def cached_indices(self) -> list[int]:
        with self._lock:
            return sorted(self._cache)

    def set_window(
        self,
        center: int,
        ahead: int = 32,
        behind: int = 4,
        extra: Iterable[int] = (),
    ) -> None:
        """Declare what should be resident: `center` first, then `ahead` frames
        forward and `behind` backward (nearest first, forward favoured), plus
        `extra` indices (e.g. reference pictures of the current frame) which
        are prefetched right after `center` and protected from eviction."""
        n = self.index.num_frames
        extra = [i for i in dict.fromkeys(extra) if 0 <= i < n and i != center]
        room = self.cache_size - 1 - len(extra)
        behind = max(0, min(behind, room // 4))
        ahead = max(0, min(ahead, room - behind))

        prio: dict[int, float] = {center: 0.0}
        for i in extra:
            prio[i] = 0.5
        for d in range(1, ahead + 1):
            prio.setdefault(center + d, float(d))
        for d in range(1, behind + 1):
            prio.setdefault(center - d, 2.0 * d + 0.5)
        order = [i for i, _ in sorted(prio.items(), key=lambda kv: kv[1]) if 0 <= i < n]

        with self._lock:
            if self._closed:
                return
            self._center = center
            self._protected = frozenset(order)
            self._wanted = deque(i for i in order if i not in self._cache and i not in self._inflight)
            self._evict_locked()
            self._pump_locked()

    def request(self, out_idx: int) -> None:
        """Make `out_idx` the next job (without changing the window)."""
        with self._lock:
            if self._closed or out_idx in self._cache or out_idx in self._inflight:
                return
            if not 0 <= out_idx < self.index.num_frames:
                return
            try:
                self._wanted.remove(out_idx)
            except ValueError:
                pass
            self._wanted.appendleft(out_idx)
            self._pump_locked()

    def wait(self, out_idx: int, timeout: Optional[float] = None) -> Optional[Frame]:
        """Blocking helper for non-GUI callers: request and wait for a frame."""
        self.request(out_idx)
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while out_idx not in self._cache and out_idx not in self._errors:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._cond.wait(remaining)
            if out_idx in self._errors:
                raise self._errors[out_idx]
            return self._cache[out_idx]

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._wanted.clear()
        self._pool.shutdown(wait=True, cancel_futures=True)

    def __enter__(self) -> "FrameStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --------------------------------------------------------------- internals
    def _dist(self, i: int) -> float:
        d = i - self._center
        return d if d >= 0 else -2.0 * d  # frames behind the playhead are cheaper to drop

    def _evict_locked(self) -> None:
        while len(self._cache) > self.cache_size:
            victims = [i for i in self._cache if i not in self._protected] or list(self._cache)
            del self._cache[max(victims, key=self._dist)]

    def _pump_locked(self) -> None:
        while self._wanted and len(self._inflight) < self.max_inflight and not self._closed:
            i = self._wanted.popleft()
            if i in self._cache or i in self._inflight:
                continue
            ref = self.index.by_output(i)
            if ref is None:
                continue
            try:
                fut = self._submit(_worker_parse, ref.epoch, ref.offset, ref.length)
            except Exception as e:  # pool broken / shut down
                self._errors[i] = e
                self._cond.notify_all()
                if self.on_error:
                    self.on_error(i, e)
                continue
            self._inflight.add(i)
            self._t_submit[i] = time.perf_counter()
            fut.add_done_callback(lambda f, i=i: self._on_done(i, f))

    def _on_done(self, i: int, fut: Future) -> None:
        err: Optional[BaseException] = None
        with self._lock:
            self._inflight.discard(i)
            t0 = self._t_submit.pop(i, None)
            if fut.cancelled():
                err = None
            elif fut.exception() is not None:
                err = fut.exception()
                self._errors[i] = err
            else:
                self._errors.pop(i, None)
                self._cache[i] = fut.result()
                self.parsed_total += 1
                if t0 is not None:
                    self.parse_seconds += time.perf_counter() - t0
                self._evict_locked()
            self._cond.notify_all()
            self._pump_locked()
        if fut.cancelled():
            return
        if err is not None:
            if self.on_error:
                self.on_error(i, err)
        elif self.on_frame:
            self.on_frame(i)