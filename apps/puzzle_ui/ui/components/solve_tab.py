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
    QGroupBox, QGridLayout, QLabel, QPushButton, QMessageBox
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

    def _load_viewer_index(self):
        viewer_index = app_root() / "viewer" / "index.html"
        if not viewer_index.exists():
            QMessageBox.critical(self, "Missing viewer", f"Viewer file not found:\n{viewer_index}")
        self.web.setUrl(QUrl.fromLocalFile(str(viewer_index)))

    # ---------- world payload to viewer ----------
    def _send_world_to_viewer(self, data: dict):
        """Emit the viewer payload via WebChannel."""
        payload = {
            "version": data.get("version"),
            "run_id": data.get("run_id"),
            "r": data.get("r"),
            "bbox": data.get("bbox"),
            "pieces": data.get("pieces", []),  # [{id, name, centers:[[x,y,z],...]}]
            "container_name": data.get("container_name"),
            "lattice": data.get("lattice"),
            "space": data.get("space"),
        }
        self.bridge.sendPayload.emit(payload)

    # ---------- world file handling ----------
    def open_world_file(self, path: Path):
        """Parse a *.current.world.json (or sample) and update labels + viewer."""
        self._current_world_file = path
        self.lblFile.setText(str(path))
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            self._clear_stats_world_only()
            return

        self.lblRun.setText(str(data.get("run_id", "—")))
        self.lblContainer.setText(str(data.get("container_name", "—")))
        total = 25
        placed = sum(len(p.get("centers", [])) > 0 for p in data.get("pieces", []))
        self.lblPlaced.setText(f"{placed} / {total}")

        # Push to viewer
        self._send_world_to_viewer(data)

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
