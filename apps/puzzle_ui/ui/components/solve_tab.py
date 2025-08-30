# apps/puzzle_ui/ui/components/solve_tab.py

# import shim: support both direct-run and module-run
import sys
from pathlib import Path

_pkg = __package__
if _pkg in (None, "", "components"):
    # Direct run or imported as top-level "components"
    _UI_DIR = Path(__file__).resolve().parents[1]  # .../apps/puzzle_ui/ui
    if str(_UI_DIR) not in sys.path:
        sys.path.insert(0, str(_UI_DIR))
    from utils import app_root, repo_root  # noqa: E402
    from components.options_panel import OptionsPanel  # noqa: E402
else:
    # Module run: python -m apps.puzzle_ui.ui.main
    from ..utils import app_root, repo_root  # type: ignore
    from .options_panel import OptionsPanel  # type: ignore

import json
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QUrl, QTimer, QFileSystemWatcher, QObject, Signal
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QSizePolicy,
    QGroupBox, QGridLayout, QLabel, QPushButton, QMessageBox,
    QComboBox, QCheckBox, QSlider
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel


class ViewerBridge(QObject):
    """Qt↔JS bridge. JS listens for `bridge.sendPayload`."""
    sendPayload = Signal(dict)  # emits a JSON-serializable dict to JS


class SolveTab(QWidget):
    """
    Left: OptionsPanel + Progress stats
    Right: QWebEngineView (three.js viewer)
    Follows logs/progress.json (snapshot) and logs/progress.jsonl (tail).
    """
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._current_world_file: Optional[Path] = None

        # progress paths + tail state
        self.logs_dir: Path = (repo_root() / "external" / "solver" / "logs")
        self.progress_json: Path = self.logs_dir / "progress.json"
        self.progress_jsonl: Path = self.logs_dir / "progress.jsonl"
        self._last_sig_json: Optional[Tuple[int, int]] = None
        self._jsonl_last_size: int = 0
        self._jsonl_buf: str = ""

        # world-file follow state
        self.world_path: Optional[Path] = None
        self._last_world_sig: Optional[Tuple[int, int]] = None  # (mtime_ns, size)

        # --- timeline playback state (no solver changes required) ---
        self.timeline_frames: List[Path] = []     # ordered list of snapshot frames
        self.timeline_index: int = -1             # current frame index
        self.play_timer = QTimer(self)            # playback timer
        self.play_speed_ms = 600                  # default ~1x
        self.live_extend = True                   # extend timeline when new frames arrive

        # --- Reveal (current geometry) state ---
        self.reveal_count: int = -1                  # -1 = show all
        self._last_world_data: Optional[dict] = None # cache last loaded world data

        self._build_ui()
        self._init_followers()

    # ---------- UI ----------
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal, self)
        root.addWidget(splitter)

        # Left column
        left = QWidget(self)
        left.setMinimumWidth(420)
        left.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(10, 10, 10, 10)
        lbox.setSpacing(8)

        # Options panel (schema-driven)
        self.opts = OptionsPanel(left)
        lbox.addWidget(self.opts, 1)

        # Progress stats
        stats = QGroupBox("Progress (auto-follow)", left)
        sgrid = QGridLayout(stats)
        self.lblFile = QLabel("—", stats)
        self.lblRun = QLabel("—", stats)
        self.lblContainer = QLabel("—", stats)
        self.lblPlaced = QLabel("— / —", stats)
        self.lblBest = QLabel("—", stats)
        self.lblAttempts = QLabel("—", stats)
        self.lblRate = QLabel("—", stats)
        rows = [
            ("World file:", self.lblFile),
            ("Run:", self.lblRun),
            ("Container:", self.lblContainer),
            ("Placed / Total:", self.lblPlaced),
            ("Best depth:", self.lblBest),
            ("Attempts:", self.lblAttempts),
            ("Attempts/sec:", self.lblRate),
        ]
        for i, (lab, w) in enumerate(rows):
            sgrid.addWidget(QLabel(lab, stats), i, 0)
            sgrid.addWidget(w, i, 1)
        lbox.addWidget(stats, 0)

        # Manual refresh
        self.btnRefresh = QPushButton("Refresh viewer  stats", left)
        self.btnRefresh.clicked.connect(self.refresh_all)
        lbox.addWidget(self.btnRefresh)

        # --- Timeline controls (Build mode) ---
        tl = QGroupBox("Timeline (snapshots)", left)
        tlbox = QGridLayout(tl)

        # Mode: Full | Build (timeline)
        self.cmbMode = QComboBox(tl)
        self.cmbMode.addItem("Full", userData="full")
        self.cmbMode.addItem("Build (timeline)", userData="build")
        tlbox.addWidget(QLabel("Mode:", tl), 0, 0)
        tlbox.addWidget(self.cmbMode, 0, 1)

        # Completion slider
        self.slider = QSlider(Qt.Horizontal, tl)
        self.slider.setRange(0, 0)
        self.slider.setEnabled(False)
        tlbox.addWidget(QLabel("Completion:", tl), 1, 0)
        tlbox.addWidget(self.slider, 1, 1)

        # Frame label (index / count)
        self.lblFrame = QLabel("— / —", tl)
        tlbox.addWidget(self.lblFrame, 1, 2)

        # Play / Speed
        self.btnPlay = QPushButton("Play", tl)
        self.btnPlay.setEnabled(False)
        self.cmbSpeed = QComboBox(tl)
        self.cmbSpeed.addItems(["0.5×", "1×", "2×"])
        self.cmbSpeed.setCurrentIndex(1)  # 1×
        tlbox.addWidget(self.btnPlay, 2, 1)
        tlbox.addWidget(self.cmbSpeed, 2, 2)

        # Live extend (auto-append new frames)
        self.chkLive = QCheckBox("Extend as new frames arrive", tl)
        self.chkLive.setChecked(True)
        tlbox.addWidget(self.chkLive, 3, 1, 1, 2)

        lbox.addWidget(tl)

        # --- Reveal controls (current geometry only) ---
        rev = QGroupBox("Reveal (current geometry)", left)
        rgrid = QGridLayout(rev)

        self.revealSlider = QSlider(Qt.Horizontal, rev)
        self.revealSlider.setEnabled(False)
        self.revealSlider.setRange(0, 0)

        self.lblReveal = QLabel("0 / 0", rev)

        rgrid.addWidget(QLabel("Pieces to show:", rev), 0, 0)
        rgrid.addWidget(self.revealSlider,               0, 1)
        rgrid.addWidget(self.lblReveal,                  0, 2)

        lbox.addWidget(rev)

        # Right: viewer
        self.web = QWebEngineView(self)

        # WebChannel hookup (Python→JS)
        self.channel = QWebChannel(self.web.page())
        self.bridge = ViewerBridge()
        self.channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(self.channel)

        self._load_viewer_index()

        splitter.addWidget(left)
        splitter.addWidget(self.web)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # Timeline wiring
        self.cmbMode.currentIndexChanged.connect(self._on_mode_changed)
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.btnPlay.clicked.connect(self._on_play_clicked)
        self.cmbSpeed.currentIndexChanged.connect(self._on_speed_changed)
        self.chkLive.toggled.connect(lambda v: setattr(self, "live_extend", bool(v)))

        # playback timer
        self.play_timer.setInterval(self.play_speed_ms)
        self.play_timer.timeout.connect(self._on_play_tick)

        # Reveal wiring
        self.revealSlider.valueChanged.connect(self._on_reveal_slider)

    def _load_viewer_index(self):
        viewer_index = app_root() / "viewer" / "index.html"
        if not viewer_index.exists():
            QMessageBox.critical(self, "Missing viewer", f"Viewer file not found:\n{viewer_index}")
        self.web.setUrl(QUrl.fromLocalFile(str(viewer_index)))

    # ---------- world payload to viewer ----------
    def _send_world_to_viewer(self, data: dict):
        """Entry point used by world-follow/open; updates UI and applies reveal filter."""
        # cache latest
        self._last_world_data = data
        # keep reveal UI in sync with this geometry
        self._update_reveal_ui_from_data(data)
        # compute how many to show
        pieces_total = len(data.get("pieces") or [])
        n = pieces_total if self.reveal_count < 0 else max(0, min(self.reveal_count, pieces_total))
        filtered = self._filter_data_by_reveal(data, n)
        # delegate to the actual emitter
        self._really_send_to_viewer(filtered)

    def _really_send_to_viewer(self, data: dict):
        """Build the payload and emit to the web viewer (no filtering here)."""
        payload = {
            "version": data.get("version"),
            "run_id": data.get("run_id"),
            "r": data.get("r"),
            "bbox": data.get("bbox"),
            "pieces": data.get("pieces", []),  # [{id,name,centers/world_centers}]
            "container_name": data.get("container_name"),
            "lattice": data.get("lattice"),
            "space": data.get("space"),
        }
        self.bridge.sendPayload.emit(payload)

    # ---------- world file handling ----------
    def open_world_file(self, path: Path):
        """User chose a world file; start following and/or build timeline."""
        self._current_world_file = path
        self.lblFile.setText(str(path))

        # Always try an immediate read for labels/viewer (full mode)
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            self.lblRun.setText(str(data.get("run_id", "—")))
            self.lblContainer.setText(str(data.get("container_name", "—")))
            total = 25
            placed = sum(len(p.get("centers", [])) > 0 for p in data.get("pieces", []))
            self.lblPlaced.setText(f"{placed} / {total}")
            self._send_world_to_viewer(data)
        except Exception:
            pass

        # World follow target (full mode uses this; build mode ignores it)
        self.world_path = Path(path)
        self._last_world_sig = None

        # Build timeline index now if Build mode is selected
        if self._mode_is_build():
            self._timeline_scan(select="last")
            if self.timeline_frames:
                self._load_frame_and_send(self.timeline_index)
        else:
            self._timeline_update_ui()

    def _clear_stats_world_only(self):
        self.lblRun.setText("—")
        self.lblContainer.setText("—")
        self.lblPlaced.setText("— / —")

    # ---------- progress followers ----------
    def _init_followers(self):
        self._fsw = QFileSystemWatcher(self)
        self._fsw.fileChanged.connect(lambda *_: self._poll_tick(force=True))
        self._fsw.directoryChanged.connect(lambda *_: self._poll_tick(force=True))

        self._poll = QTimer(self)
        self._poll.setInterval(500)  # ms
        self._poll.timeout.connect(self._poll_tick)
        self._poll.start()

        self.set_logs_dir(self.logs_dir)

    def set_follow_enabled(self, enabled: bool):
        if enabled:
            self._poll.start()
        else:
            self._poll.stop()

    def set_logs_dir(self, path: Path):
        self.logs_dir = Path(path)
        self.progress_json = self.logs_dir / "progress.json"
        self.progress_jsonl = self.logs_dir / "progress.jsonl"
        self._jsonl_last_size = 0
        self._jsonl_buf = ""

        # reset watcher
        try:
            if self._fsw.files():
                self._fsw.removePaths(self._fsw.files())
            if self._fsw.directories():
                self._fsw.removePaths(self._fsw.directories())
        except Exception:
            pass
        if self.logs_dir.exists():
            self._fsw.addPath(str(self.logs_dir))
        if self.progress_json.exists():
            self._fsw.addPath(str(self.progress_json))
        if self.progress_jsonl.exists():
            self._fsw.addPath(str(self.progress_jsonl))

        self._poll_tick(force=True)

    def refresh_viewer(self):
        self.web.reload()

    def refresh_all(self):
        self.refresh_viewer()
        self._poll_tick(force=True)

    # ---------- progress polling (snapshot + jsonl tail) ----------
    def _poll_tick(self, force: bool = False):
        # World follow OR timeline extend
        try:
            if self._mode_is_build():
                # extend timeline list if new frames appear
                if self._current_world_file and self.live_extend:
                    prev_count = len(self.timeline_frames)
                    self._timeline_scan(select="last")
                    # do not auto-show here; playback tick/slider change drives display
            else:
                # Full mode: follow the selected world file live
                if self.world_path and self.world_path.exists():
                    stw = self.world_path.stat()
                    sigw = (int(stw.st_mtime_ns), int(stw.st_size))
                    if force or sigw != self._last_world_sig:
                        self._last_world_sig = sigw
                        self._read_world_and_send()  # safe read + push to viewer
        except Exception:
            pass

        # progress.json snapshot
        try:
            st = self.progress_json.stat()
            sig = (int(st.st_mtime_ns), int(st.st_size))
            if force or sig != self._last_sig_json:
                self._last_sig_json = sig
                self._read_progress_json()
        except Exception:
            pass

        # progress.jsonl tail (efficient)
        try:
            st = self.progress_jsonl.stat()
            size = int(st.st_size)
            if self._jsonl_last_size > size:  # rotated/truncated
                self._jsonl_last_size = 0
                self._jsonl_buf = ""
            start = self._jsonl_last_size
            if start == 0 and size > 65536:
                start = size - 65536  # first time on large files: tail only
            if size > start:
                with open(self.progress_jsonl, "rb") as f:
                    f.seek(start)
                    chunk = f.read(size - start)
                self._jsonl_last_size = size
                self._consume_jsonl_bytes(chunk)
        except Exception:
            pass

    def _read_progress_json(self):
        try:
            txt = self.progress_json.read_text(encoding="utf-8-sig", errors="ignore")
            data = json.loads(txt)
        except Exception:
            return
        self._apply_progress_obj(data)

    def _consume_jsonl_bytes(self, b: bytes):
        s = b.decode("utf-8", errors="ignore").replace("\r", "")
        self._jsonl_buf += s
        # keep last partial line
        if self._jsonl_buf.endswith("\n"):
            lines = self._jsonl_buf.split("\n")
            self._jsonl_buf = ""
        else:
            parts = self._jsonl_buf.split("\n")
            self._jsonl_buf = parts[-1]
            lines = parts[:-1]

        # parse from end until we find a valid JSON object
        last_obj = None
        for ln in reversed(lines):
            ln = ln.strip()
            if not ln:
                continue
            try:
                last_obj = json.loads(ln)
                break
            except Exception:
                continue
        if last_obj:
            self._apply_progress_obj(last_obj)

    def _apply_progress_obj(self, data: Dict[str, Any]):
        rid = data.get("run_id") or (str(data.get("run")) if data.get("run") is not None else None)
        if rid is not None:
            self.lblRun.setText(str(rid))

        placed = data.get("placed")
        total = data.get("total", 25)
        if placed is not None:
            self.lblPlaced.setText(f"{placed} / {total}")

        best = data.get("best_depth")
        if best is not None:
            self.lblBest.setText(str(best))

        attempts = data.get("attempts")
        if attempts is not None:
            self.lblAttempts.setText(str(attempts))

        rate = data.get("attempts_per_sec")
        if rate is not None:
            try:
                self.lblRate.setText(f"{float(rate):.1f}")
            except Exception:
                self.lblRate.setText(str(rate))

    def _read_world_and_send(self) -> None:
        """Read current world file and push to viewer; ignore partial writes."""
        if not self.world_path:
            return
        try:
            txt = self.world_path.read_text(encoding="utf-8-sig", errors="ignore")
            data = json.loads(txt)
        except Exception:
            return  # partial write or transient parse error; try again next tick

        # Update left-side labels from world file
        self.lblRun.setText(str(data.get("run_id", "—")))
        self.lblContainer.setText(str(data.get("container_name", "—")))
        total = int(data.get("total", 25)) if isinstance(data.get("total"), (int, float)) else 25
        placed = sum(len(p.get("centers", [])) > 0 for p in data.get("pieces", []))
        self.lblPlaced.setText(f"{placed} / {total}")
        # Push to viewer
        self._send_world_to_viewer(data)

    def _mode_is_build(self) -> bool:
        try:
            return self.cmbMode.currentData() == "build"
        except Exception:
            return False

    def _timeline_scan(self, select: str = "last") -> None:
        """Scan directory of the selected world file for snapshot frames; build ordered list."""
        self.timeline_frames.clear()
        self.timeline_index = -1
        if not self._current_world_file:
            self._timeline_update_ui()
            return
        folder = self._current_world_file.parent
        chosen = self._current_world_file.name

        # Determine prefix to match related frames
        if ".current." in chosen:
            prefix = chosen.split(".current.")[0]
        else:
            # strip ".world.json"
            prefix = chosen[:-len(".world.json")] if chosen.endswith(".world.json") else chosen

        # Collect *.world.json that share prefix, excluding ".current."
        for f in folder.glob("*.world.json"):
            n = f.name
            if ".current." in n:
                continue
            if not n.startswith(prefix):
                continue
            self.timeline_frames.append(f)

        # Sort by mtime, then name
        self.timeline_frames.sort(key=lambda p: (p.stat().st_mtime_ns, p.name))

        # Initialize index
        if self.timeline_frames:
            self.timeline_index = 0 if select == "first" else len(self.timeline_frames) - 1
        self._timeline_update_ui()

    def _timeline_update_ui(self) -> None:
        count = len(self.timeline_frames)
        self.slider.setEnabled(self._mode_is_build() and count > 0)
        self.btnPlay.setEnabled(self._mode_is_build() and count > 1)
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, count - 1))
        self.slider.setValue(max(0, self.timeline_index) if count else 0)
        self.slider.blockSignals(False)
        self.lblFrame.setText(f"{self.timeline_index+1 if count else 0} / {count}")

    def _load_frame_and_send(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.timeline_frames):
            return
        p = self.timeline_frames[idx]
        try:
            txt = p.read_text(encoding="utf-8-sig", errors="ignore")
            data = json.loads(txt)
        except Exception:
            return
        # Update stats from frame
        self.lblFile.setText(str(p))
        self.lblRun.setText(str(data.get("run_id", "—")))
        self.lblContainer.setText(str(data.get("container_name", "—")))
        total = int(data.get("total", 25)) if isinstance(data.get("total"), (int, float)) else 25
        placed = sum(len(piece.get("centers", [])) > 0 for piece in data.get("pieces", []))
        self.lblPlaced.setText(f"{placed} / {total}")
        # Push to viewer
        self._send_world_to_viewer(data)

    def _on_mode_changed(self) -> None:
        is_build = self._mode_is_build()
        # Stop playback
        self.play_timer.stop()
        self.btnPlay.setText("Play")
        # In Build mode, scan timeline and show the last frame
        if is_build:
            self._timeline_scan(select="last")
            if self.timeline_frames:
                self._load_frame_and_send(self.timeline_index)
        else:
            # Full mode: revert file label to selected world file and allow live follow
            if self._current_world_file:
                self.lblFile.setText(str(self._current_world_file))
            self._timeline_update_ui()

    def _on_slider_changed(self, value: int) -> None:
        if not self._mode_is_build():
            return
        self.timeline_index = int(value)
        self._timeline_update_ui()
        self._load_frame_and_send(self.timeline_index)

    def _on_play_clicked(self) -> None:
        if not self._mode_is_build() or len(self.timeline_frames) <= 1:
            return
        if self.play_timer.isActive():
            self.play_timer.stop()
            self.btnPlay.setText("Play")
        else:
            self.play_timer.start()
            self.btnPlay.setText("Pause")

    def _on_speed_changed(self, idx: int) -> None:
        self.play_speed_ms = {0: 1200, 1: 600, 2: 300}.get(int(idx), 600)
        self.play_timer.setInterval(self.play_speed_ms)

    def _on_play_tick(self) -> None:
        if not self._mode_is_build() or not self.timeline_frames:
            self.play_timer.stop()
        else:
            # advance; if at end, either extend or loop
            if self.timeline_index + 1 < len(self.timeline_frames):
                self.timeline_index += 1
            else:
                # try extend
                if self.live_extend:
                    prev_count = len(self.timeline_frames)
                    self._timeline_scan(select="last")
                    if len(self.timeline_frames) == prev_count:
                        # no new frames; loop from start
                        self.timeline_index = 0
                else:
                    self.timeline_index = 0
            self._timeline_update_ui()
            self._load_frame_and_send(self.timeline_index)

    def _piece_sort_key(self, p: dict, idx: int) -> tuple:
        """Stable ordering: numeric id → id, string id → base-26 (A..Z, AA..), else by name then index."""
        pid = p.get("id", None)
        if isinstance(pid, int):
            return (0, pid, idx)
        if isinstance(pid, str):
            s = pid.upper()
            n = 0
            for ch in s:
                if "A" <= ch <= "Z":
                    n = n * 26 + (ord(ch) - 64)
                else:
                    # non-letter: fall back to name
                    return (2, p.get("name", ""), idx)
            return (1, n, idx)
        return (3, p.get("name", ""), idx)

    def _update_reveal_ui_from_data(self, data: dict) -> None:
        pieces = data.get("pieces") or []
        total = len(pieces)
        # default to full if unset
        current = total if self.reveal_count < 0 else max(0, min(self.reveal_count, total))
        self.revealSlider.blockSignals(True)
        self.revealSlider.setEnabled(total > 0)
        self.revealSlider.setRange(0, total)
        self.revealSlider.setValue(current)
        self.revealSlider.blockSignals(False)
        self.lblReveal.setText(f"{current} / {total}")

    def _filter_data_by_reveal(self, data: dict, n: int) -> dict:
        """Return a shallow-copied data dict with only first n pieces by ordered key."""
        if not isinstance(data, dict):
            return data
        pieces = list(data.get("pieces") or [])
        total = len(pieces)
        if n >= total or n < 0:
            return data  # no filtering needed

        # order by stable key
        ordered_indices = sorted(range(total), key=lambda i: self._piece_sort_key(pieces[i], i))
        keep_set = set(ordered_indices[:n])

        # shallow copy with filtered pieces
        out = dict(data)
        out["pieces"] = [pieces[i] for i in range(total) if i in keep_set]
        return out

    def _on_reveal_slider(self, value: int) -> None:
        """User moved the slider: re-send filtered current geometry."""
        self.reveal_count = int(value)
        if not self._last_world_data:
            return
        self.lblReveal.setText(f"{self.reveal_count} / {len(self._last_world_data.get('pieces') or [])}")
        # Filter and push again
        filtered = self._filter_data_by_reveal(self._last_world_data, self.reveal_count)
        self._really_send_to_viewer(filtered)
