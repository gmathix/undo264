import os
import time
from collections import deque

import numpy as np
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QDockWidget, QSlider, QStackedWidget, QToolBar, QWidget,
)
from PySide6.QtCore import Qt, QTimer, QRectF, QSettings
from PySide6.QtGui import QActionGroup, QColor, QImage, QPixmap

from model import MbType, PredDir
from parsing.loader_qt import QtFrameStore, QtIndexer
from parsing.references import ReferenceResolver

from . import theme
from .frame_dialog import FrameInfoDialog
from .help_dialogs import APP_NAME, AboutDialog, ControlsDialog
from .launch_page import LaunchPage
from .left_dock import LeftDockPanel
from .partitions import partitions, Partition
from .right_dock import RightDockPanel
from .viewport import VideoView
from .yuv import yuv420_to_rgb


class ViewerWindow(QMainWindow):
    FPS = 30.0          # playback rate; the dump carries no timing information
    PREFETCH_AHEAD = 24  # frames parsed ahead of the playhead
    PREFETCH_BEHIND = 4
    CACHE_FRAMES = 64    # parsed Frames kept in RAM (~2 MB each at 1080p)

    LEFT_DOCK_WIDTH = 320

    def __init__(self, json_path: str = "", yuv_path: str = "", workers: int | None = None):
        super().__init__()
        self.setWindowTitle("undo264 Info Viewer")
        self.resize(1280, 720)

        self.json_path = json_path
        self.yuv_path = yuv_path
        self.workers = workers  # None -> min(4, cpu_count - 1)
        self.indexer: QtIndexer | None = None
        self._settings = QSettings("undo264", "viewer")
        self._controls_dialog: ControlsDialog | None = None

        # Set once the index is built (see _on_index_ready)
        self.index = None
        self.store: QtFrameStore | None = None
        self.resolver: ReferenceResolver | None = None
        self.yuv = None  # np.memmap of the raw video
        self.total_frames = 0
        self.pic_w = 0
        self.pic_h = 0
        self.frame_bytes = 0

        self.current_out_idx = 0
        self.fps = self.FPS
        self._return_idx: int | None = None  # frame to go back to after a 'jump to reference'
        self._ref_dialog: FrameInfoDialog | None = None
        self._info_idx = -1          # output_idx currently shown in the left panel
        self._deps: dict[int, list[int]] = {}  # output_idx -> reference output_idxs (memo)
        self._current_rgb_data = None  # keeps the QImage buffer alive
        self._peek_rgb_data = None
        self._play_t0 = 0.0
        self._play_i0 = 0
        self._shown_times: deque[float] = deque(maxlen=64)
        self._render_ms: deque[float] = deque(maxlen=30)

        self._setup_ui()
        if json_path and yuv_path:  # both known up front: skip the picker once the window is up
            QTimer.singleShot(0, lambda: self.open_files(json_path, yuv_path))
        else:
            self.launch.set_paths(json_path, yuv_path)

    # ------------------------------------------------------------------ setup
    def _setup_ui(self):
        # 1. Viewport: the file picker is shown first, then replaced by the video view
        self.view = VideoView(self)
        self.view.mbHovered.connect(self._on_mb_hovered)
        self.view.mbSelected.connect(self._on_mb_selected)

        self.launch = LaunchPage(self)
        self.launch.launchRequested.connect(self.open_files)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.launch)
        self.stack.addWidget(self.view)

        # All three panels are docks. The (empty) central widget stays hidden so the docks use the whole window.
        self.setDockNestingEnabled(True)
        central = QWidget()
        central.hide()
        self.setCentralWidget(central)

        self.dock_view = QDockWidget("Viewport", self)
        self.dock_view.setObjectName("dock_viewport")
        self.dock_view.setWidget(self.stack)

        # 2. Docks
        self.dock_left = QDockWidget("Stream Info", self)
        self.dock_left.setObjectName("dock_left")
        self.left_panel = LeftDockPanel(self.dock_left)
        self.dock_left.setWidget(self.left_panel)

        self.dock_right = QDockWidget("Macroblock Inspector", self)
        self.dock_right.setObjectName("dock_right")
        self.right_panel = RightDockPanel(self.dock_right)
        self.dock_right.setWidget(self.right_panel)

        self._reset_layout()

        # 3. Bottom Toolbar & Transport Controls
        toolbar = QToolBar("Transport", self)
        toolbar.setObjectName("Transport")
        self.addToolBar(Qt.BottomToolBarArea, toolbar)

        self.btn_play = toolbar.addAction("Play")
        self.btn_pause = toolbar.addAction("Pause")
        self.btn_prev = toolbar.addAction("Step <")
        self.btn_next = toolbar.addAction("Step >")
        self.act_return = toolbar.addAction("Return")
        self.act_return.setEnabled(False)
        self.act_return.triggered.connect(self._return)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(0)
        self.slider.setEnabled(False)
        toolbar.addWidget(self.slider)

        self.lbl_frame = QLabel(" No video loaded ")
        toolbar.addWidget(self.lbl_frame)

        self.lbl_stats = QLabel("")
        self.statusBar().addPermanentWidget(self.lbl_stats)
        self.lbl_hover = QLabel("")
        self.statusBar().addWidget(self.lbl_hover)

        # Connections
        self.btn_play.triggered.connect(self._play)
        self.btn_pause.triggered.connect(self._pause)
        self.btn_prev.triggered.connect(lambda: self._seek(self.current_out_idx - 1))
        self.btn_next.triggered.connect(lambda: self._seek(self.current_out_idx + 1))
        self.slider.valueChanged.connect(self._seek)
        self.left_panel.referenceSelected.connect(self.view.set_highlight_reference)
        self.left_panel.referenceOpened.connect(self._open_reference)
        self.right_panel.filtersChanged.connect(self.view.set_mb_filters)

        # Connect Bi-Directional Selection logic
        self.view.partitionSelected.connect(self._on_partition_selected)
        self.right_panel.map_widget.partitionClicked.connect(self.view.set_selected_partition)

        # Playback clock: ticks often, the target frame comes from elapsed time,
        # so the video keeps real-time pace (frames are skipped, never slowed down).
        self.play_timer = QTimer(self)
        self.play_timer.setTimerType(Qt.PreciseTimer)
        self.play_timer.setInterval(max(1, int(500 / self.fps)))
        self.play_timer.timeout.connect(self._tick)

        self.stats_timer = QTimer(self)
        self.stats_timer.setInterval(500)
        self.stats_timer.timeout.connect(self._update_stats)
        self.stats_timer.start()

        self._setup_overlay_toolbar()
        self._setup_menus()
        self._apply_theme(self._settings.value("theme", theme.DEFAULT_THEME))

    # ------------------------------------------------------- menus / layout / theme
    def _setup_menus(self):
        bar = self.menuBar()

        m_file = bar.addMenu("&File")
        act_quit = m_file.addAction("&Quit")
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)

        m_win = bar.addMenu("&Window")
        for dock, key in ((self.dock_left, "Ctrl+1"), (self.dock_view, "Ctrl+2"), (self.dock_right, "Ctrl+3")):
            act = dock.toggleViewAction()  # checkable, follows the dock's visibility (close button included)
            act.setShortcut(key)
            m_win.addAction(act)
        m_win.addSeparator()
        m_win.addAction("&Reset Layout").triggered.connect(self._reset_layout)
        m_win.addSeparator()

        m_theme = m_win.addMenu("&Theme")
        group = QActionGroup(self)
        group.setExclusive(True)
        for name in theme.THEMES:
            act = m_theme.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == theme.current().name)
            act.triggered.connect(lambda _=False, n=name: self._apply_theme(n))
            group.addAction(act)
        self._theme_actions = group

        m_help = bar.addMenu("&Help")
        act_controls = m_help.addAction("&Controls…")
        act_controls.setShortcut("F1")
        act_controls.triggered.connect(self._show_controls)
        m_help.addSeparator()
        m_help.addAction(f"&About {APP_NAME}…").triggered.connect(self._show_about)

    def _show_controls(self):
        if self._controls_dialog is None:  # one non-modal window, kept around so it can stay open next to the viewer
            self._controls_dialog = ControlsDialog(self)
        self._controls_dialog.show()
        self._controls_dialog.raise_()
        self._controls_dialog.activateWindow()

    def _show_about(self):
        AboutDialog(self).exec()

    def _reset_layout(self):
        """(Re)build the default dock arrangement: [Stream Info | Viewport | Inspector]; reopens closed docks."""
        docks = (self.dock_left, self.dock_view, self.dock_right)
        for d in docks:
            d.setFloating(False)
            self.removeDockWidget(d)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_left)
        self.splitDockWidget(self.dock_left, self.dock_view, Qt.Horizontal)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock_right)
        for d in docks:
            d.show()
        QTimer.singleShot(0, self._apply_dock_sizes)  # needs the layout to exist

    def _apply_dock_sizes(self):
        right = RightDockPanel.WIDTH
        left = self.LEFT_DOCK_WIDTH
        self.resizeDocks([self.dock_left, self.dock_view, self.dock_right],
                         [left, max(200, self.width() - left - right), right], Qt.Horizontal)

    def _apply_theme(self, name: str):
        t = theme.apply(QApplication.instance(), name)
        self.view.setBackgroundBrush(QColor(t.viewport))
        self.left_panel.refresh_theme()
        self.right_panel.refresh_theme()
        self._settings.setValue("theme", t.name)
        for act in self._theme_actions.actions():
            act.setChecked(act.text() == t.name)

    def _setup_overlay_toolbar(self):
        tb = QToolBar("Overlays", self)
        tb.setObjectName("Overlays")
        self.tb_overlays = tb
        tb.setEnabled(False)  # nothing to overlay until a stream is loaded
        self.addToolBar(Qt.TopToolBarArea, tb)

        act = tb.addAction("Fit")
        act.setShortcut("F")
        act.triggered.connect(self.view.fit_to_window)
        act = tb.addAction("1:1")
        act.setShortcut("1")
        act.triggered.connect(self.view.actual_size)
        tb.addSeparator()

        def toggle(text, key, slot, checked=False):
            a = tb.addAction(text)
            a.setCheckable(True)
            a.setChecked(checked)
            if key:
                a.setShortcut(key)
            a.toggled.connect(slot)
            return a

        self.act_grid = toggle("MB grid", "G", self.view.set_grid_visible)
        self.act_slices = toggle("Slices", "S", self.view.set_slices_visible)
        tb.addSeparator()
        self.act_mv = toggle("Motion vectors", "M", self.view.set_mv_visible)
        self.act_l0 = toggle("L0", None, lambda _: self._mv_lists_changed(), True)
        self.act_l1 = toggle("L1", None, lambda _: self._mv_lists_changed(), True)

        tb.addWidget(QLabel("  MV length: linear "))
        self.slider_mv = QSlider(Qt.Horizontal)
        self.slider_mv.setRange(0, 100)
        self.slider_mv.setFixedWidth(140)
        self.slider_mv.setToolTip("Blend from true vector length (left) to logarithmic length (right)")
        self.slider_mv.valueChanged.connect(lambda v: self.view.set_mv_log(v / 100.0))
        tb.addWidget(self.slider_mv)
        tb.addWidget(QLabel(" log"))

    def _mv_lists_changed(self):
        self.view.set_mv_lists(self.act_l0.isChecked(), self.act_l1.isChecked())

    # ------------------------------------------------------- viewport callbacks
    def _on_mb_hovered(self, mb: int):
        if mb < 0 or not self.view.mb_w:
            self.lbl_hover.setText("")
        else:
            w = self.view.mb_w
            self.lbl_hover.setText(f" MB ({mb % w}, {mb // w})  #{mb} ")

    def _on_mb_selected(self, mb: int):
        self._update_inspector(mb, self.view.selected_partition)

    def _on_partition_selected(self, part):
        self._update_inspector(self.view.selected_mb, part)

    def _update_inspector(self, mb: int, part):
        frame = self.store.get(self.current_out_idx) if self.store else None
        self.right_panel.update_data(frame, mb, part)
        self._update_peek(frame, mb, part)

        # Update Left Dock Highlight Colors
        l0_ref, l1_ref = -1, -1
        if frame is not None and mb >= 0 and frame.mb_type[mb] >= MbType.SKIP:
            if part is None:
                parts = partitions(frame, mb)
                if parts: part = parts[0]
            if part:
                blk8 = part.blk8
                d = frame.pred_dir[mb, blk8]
                if d & PredDir.L0: l0_ref = frame.ref_dec_idx[0, mb, blk8]
                if d & PredDir.L1: l1_ref = frame.ref_dec_idx[1, mb, blk8]

        self.left_panel.highlight_partition_refs(l0_ref, l1_ref)

    # --------------------------------------------------------------- indexing
    def open_files(self, json_path: str, yuv_path: str):
        """Called by the picker (or the command line): index the dump, then swap the picker for the video."""
        if self.indexer is not None and self.indexer.isRunning():
            return
        self.json_path, self.yuv_path = json_path, yuv_path
        self.launch.set_paths(json_path, yuv_path)
        self.launch.set_busy(True, f"Indexing {os.path.basename(json_path)}…")
        self.lbl_frame.setText(" Indexing... ")
        self.stack.setCurrentWidget(self.launch)
        self._start_indexing()

    def _start_indexing(self):
        self.indexer = QtIndexer(self.json_path, self)
        self.indexer.ready.connect(self._on_index_ready)
        self.indexer.failed.connect(self._on_index_failed)
        self.indexer.start()

    def _on_index_failed(self, msg: str):
        self.lbl_frame.setText(" No video loaded ")
        self.statusBar().showMessage(msg)
        self.yuv = None
        self.launch.show_error(msg)  # back to the picker, with the reason, so the user can pick other files
        self.stack.setCurrentWidget(self.launch)

    def _on_index_ready(self, index):
        self.index = index
        parser = index.parser_for_epoch(0)
        if not parser.sps:
            self._on_index_failed("no SPS record in the dump")
            return
        sps = parser.sps[min(parser.sps)]

        # Output picture size = coded size minus SPS cropping. The YUV file holds
        # cropped pictures, so use the same size for the frame count and the seek offsets.
        w = sps.pic_width_in_mbs * 16
        h = sps.pic_height_in_mbs * 16
        left = top = 0
        if sps.frame_cropping_flag and sps.crop:
            left, right, top, bottom = sps.crop
            w -= left + right
            h -= top + bottom
        self.pic_w, self.pic_h = w, h
        self.frame_bytes = w * h * 3 // 2
        self.view.set_geometry(w, h, sps.pic_width_in_mbs, sps.pic_height_in_mbs, left, top)

        try:
            self.yuv = np.memmap(self.yuv_path, dtype=np.uint8, mode="r")
        except (OSError, ValueError) as e:  # missing, unreadable or empty file
            self._on_index_failed(f"cannot open the video: {e}")
            return
        yuv_frames = len(self.yuv) // self.frame_bytes
        self.total_frames = min(index.num_frames, yuv_frames)
        if self.total_frames == 0:
            self._on_index_failed("no displayable frame (YUV smaller than one picture?)")
            return
        if yuv_frames != index.num_frames:
            self.statusBar().showMessage(
                f"dump has {index.num_frames} frames but the YUV holds {yuv_frames}; using {self.total_frames}")

        self.slider.setMaximum(self.total_frames - 1)
        self.slider.setEnabled(True)

        self.resolver = ReferenceResolver(index)
        self.store = QtFrameStore(index, workers=self.workers, cache_size=self.CACHE_FRAMES, parent=self)
        self.store.frame_ready.connect(self._on_frame_ready)
        self.store.frame_failed.connect(self._on_frame_failed)
        self.store.warm()  # start the worker processes now, not on the first seek

        self.tb_overlays.setEnabled(True)
        self.launch.set_busy(False)
        self.stack.setCurrentWidget(self.view)  # the picker gives way to the frames
        self._seek(0)

    def _get_peek_pixmap(self, out_idx: int) -> QPixmap | None:
        """Reads a completely isolated frame buffer to prep the peek flipbook."""
        if out_idx < 0 or out_idx >= self.total_frames or self.frame_bytes == 0:
            return None
        off = out_idx * self.frame_bytes
        buf = np.asarray(self.yuv[off:off + self.frame_bytes])
        rgb = yuv420_to_rgb(buf, self.pic_w, self.pic_h)
        self._peek_rgb_data = rgb
        img = QImage(rgb.data, self.pic_w, self.pic_h, self.pic_w * 3, QImage.Format_RGB888)
        return QPixmap.fromImage(img)

    # ------------------------------------------------------- frames from store
    def _on_frame_ready(self, out_idx: int):
        self._refresh_ref_dialog(out_idx)
        if out_idx == self.current_out_idx:
            self._update_window()  # the frame is now known: also prefetch its references
            self._update_info()

    def _on_frame_failed(self, out_idx: int, msg: str):
        print(f"Frame {out_idx}: {msg}")
        if out_idx == self.current_out_idx:
            self.statusBar().showMessage(f"Frame {out_idx} could not be parsed: {msg}")

    def _dependencies(self, out_idx: int) -> list[int]:
        deps = self._deps.get(out_idx)
        if deps is None:
            frame = self.store.get(out_idx)
            if frame is None:
                return []
            if len(self._deps) > 4096:
                self._deps.clear()
            deps = self._deps[out_idx] = self.resolver.dependency_outputs(frame)
        return deps

    def _update_window(self):
        self.store.set_window(
            self.current_out_idx,
            ahead=self.PREFETCH_AHEAD,
            behind=self.PREFETCH_BEHIND,
            extra=self._dependencies(self.current_out_idx),
        )

    def _update_info(self):
        idx = self.current_out_idx
        if self._info_idx == idx:
            return
        frame = self.store.get(idx)
        if frame is None:
            self.view.set_frame(None)
            self._info_idx = -1
            self.store.request(idx)
            return
        self.left_panel.update_data(frame)
        self._info_idx = idx
        self.view.set_frame(frame)
        self._update_inspector(self.view.selected_mb, self.view.selected_partition)

    def _update_peek(self, frame, mb: int, part: Partition | None):
        is_single_mb = self.view.selection_rect_mb and self.view.selection_rect_mb.width() == 1 and self.view.selection_rect_mb.height() == 1

        # Clear peek if nothing valid is selected or if it's an intra MB
        if frame is None or mb < 0 or frame.mb_type[mb] < MbType.SKIP or not is_single_mb:
            self.view.set_peek_data(None, None, None, None)
            return

        if part is None:
            parts = partitions(frame, mb)
            if not parts:
                self.view.set_peek_data(None, None, None, None)
                return
            part = parts[0]

        bx, by = part.blk
        blk8 = part.blk8
        d = PredDir(frame.pred_dir[mb, blk8])

        def get_list_data(list_idx):
            ref_dec = frame.ref_dec_idx[list_idx, mb, blk8]
            if ref_dec == -1:
                return None, None

            ref_out = self.resolver.resolve(ref_dec).output_idx
            if ref_out is None:
                return None, None

            mvx, mvy = frame.mv[list_idx, mb, by, bx]

            # Calculate absolute pixel coordinates in the reference frame
            mb_x = (mb % frame.mb_width) * 16
            mb_y = (mb // frame.mb_width) * 16
            rect = QRectF(mb_x + part.x + mvx / 4.0, mb_y + part.y + mvy / 4.0, part.w, part.h)

            pixmap = self._get_peek_pixmap(ref_out)
            return pixmap, rect

        # Fetch L0 and L1 data independently
        pix_l0, rect_l0 = get_list_data(0) if (d & PredDir.L0) else (None, None)
        pix_l1, rect_l1 = get_list_data(1) if (d & PredDir.L1) else (None, None)

        self.view.set_peek_data(pix_l0, rect_l0, pix_l1, rect_l1)


    # ------------------------------------------------- reference popup / jump
    def _open_reference(self, decoded_idx: int):
        if self.store is None:
            return
        if self._ref_dialog is None:
            self._ref_dialog = FrameInfoDialog(self)
            self._ref_dialog.jumpRequested.connect(self._jump_to)
            self._ref_dialog.returnRequested.connect(self._return)
        out = self.resolver.resolve(decoded_idx).output_idx
        if out is not None and out >= self.total_frames:
            out = None  # beyond the displayable range
        frame = self.store.get(out) if out is not None else None
        if out is not None and frame is None:
            self.store.request(out)
        self._ref_dialog.show_reference(decoded_idx, out, frame)
        self._sync_nav()
        self._ref_dialog.show()
        self._ref_dialog.raise_()

    def _refresh_ref_dialog(self, out_idx: int):
        d = self._ref_dialog
        if d is not None and d.is_loading and d.output_idx == out_idx:
            d.show_reference(d.decoded_idx, out_idx, self.store.get(out_idx))
            self._sync_nav()

    def _jump_to(self, out_idx: int):
        self._pause()
        if self._return_idx is None:  # chained jumps keep the original picture
            self._return_idx = self.current_out_idx
        self._seek(out_idx)
        self._sync_nav()

    def _return(self):
        if self._return_idx is None:
            return
        target, self._return_idx = self._return_idx, None
        self._pause()
        self._seek(target)
        self._sync_nav()

    def _sync_nav(self):
        r = self._return_idx
        self.act_return.setEnabled(r is not None)
        self.act_return.setText("Return" if r is None else f"Return to #{r}")
        if self._ref_dialog is not None:
            self._ref_dialog.set_nav_state(self.current_out_idx, r)

    # ------------------------------------------------------------- navigation
    def _seek(self, out_idx: int, from_timer: bool = False):
        if self.store is None or out_idx < 0 or out_idx >= self.total_frames:
            self._pause()
            return

        if not from_timer and self.play_timer.isActive():  # manual jump while playing: re-anchor the clock
            self._play_t0 = time.perf_counter()
            self._play_i0 = out_idx

        self.current_out_idx = out_idx

        self.slider.blockSignals(True)
        self.slider.setValue(out_idx)
        self.slider.blockSignals(False)

        self.lbl_frame.setText(f" Frame: {out_idx} / {self.total_frames - 1} ")
        self._show_picture(out_idx)
        self._update_window()
        self._update_info()
        self._sync_nav()

    def _show_picture(self, out_idx: int):
        """Display the decoded picture. Independent of the parsed Frame: it never waits for the parser."""
        t0 = time.perf_counter()
        off = out_idx * self.frame_bytes
        buf = np.asarray(self.yuv[off:off + self.frame_bytes])
        rgb = yuv420_to_rgb(buf, self.pic_w, self.pic_h)
        self._current_rgb_data = rgb  # QImage does not own its buffer
        img = QImage(rgb.data, self.pic_w, self.pic_h, self.pic_w * 3, QImage.Format_RGB888)
        self.view.set_picture(QPixmap.fromImage(img))

        now = time.perf_counter()
        self._render_ms.append((now - t0) * 1000.0)
        self._shown_times.append(now)

    # --------------------------------------------------------------- playback
    def _play(self):
        if self.store is None:
            return
        if self.current_out_idx >= self.total_frames - 1:
            self._seek(0)
        self._play_t0 = time.perf_counter()
        self._play_i0 = self.current_out_idx
        self.play_timer.start()

    def _pause(self):
        self.play_timer.stop()

    def _tick(self):
        elapsed = time.perf_counter() - self._play_t0
        target = min(self._play_i0 + int(elapsed * self.fps), self.total_frames - 1)
        if target != self.current_out_idx:
            self._seek(target, from_timer=True)
        if target >= self.total_frames - 1:
            self._pause()


    # ------------------------------------------------------------------ stats
    def _update_stats(self):
        if self.store is None:
            return
        shown = [t for t in self._shown_times if t > time.perf_counter() - 1.0]
        fps = len(shown)  # frames displayed during the last second
        ms = sum(self._render_ms) / len(self._render_ms) if self._render_ms else 0.0
        s = self.store.stats()
        self.lbl_stats.setText(
            f" display {fps} fps | picture {ms:.1f} ms | parsed cache {s['cached']}/{s['cache_size']} "
            f"| {s['workers']} workers ")

    def closeEvent(self, event):
        self._pause()
        self.stats_timer.stop()
        if self.indexer is not None and self.indexer.isRunning():
            self.indexer.wait()
        if self.store is not None:
            self.store.close()
        self.yuv = None
        super().closeEvent(event)