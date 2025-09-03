# apps/puzzle_ui/ui/components/solve_tab.py

# import shim: support both direct-run and module-run
import sys
from pathlib import Path
import os, time
from PySide6.QtCore import QObject, Signal, Slot, QUrl, QTimer, Qt, QFileSystemWatcher
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QSizePolicy,
    QGroupBox, QGridLayout, QLabel, QPushButton, QMessageBox, QFormLayout,
    QSlider, QComboBox, QFileDialog
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWidgets import QVBoxLayout

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
from collections import defaultdict

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
        self._reveal_docked = False
        self._reveal_docked_once = False

        # progress paths + tail state
        self.logs_dir = Path("C:/.../ballpuzzle3/external/solver/logs")
        self.results_dir = Path("C:/.../ballpuzzle3/external/solver/results")
        self.progress_json: Path = self.logs_dir / "progress.json"
        self.progress_jsonl: Path = self.logs_dir / "progress.jsonl"
        self._last_sig_json: Optional[Tuple[int, int]] = None
        self._jsonl_last_size: int = 0
        self._jsonl_buf: str = ""

        # ---- Totals (all runs) ----
        self._agg_by_run = defaultdict(lambda: {"best": -1, "attempts": 0})

        self._world_path: Path | None = None
        self._world_mtime: float = 0.0

        self._world_watch = QFileSystemWatcher(self)
        self._world_debounce = QTimer(self); self._world_debounce.setSingleShot(True); self._world_debounce.setInterval(150)
        self._world_poll = QTimer(self);     self._world_poll.setInterval(1000)
        
        # Dynamic snapshot interval (will be updated from solver options)
        self._snapshot_interval_sec = 10  # default fallback

        self._world_watch.fileChanged.connect(lambda _: self._world_debounce.start())
        self._world_watch.directoryChanged.connect(lambda _: self._world_debounce.start())
        self._world_debounce.timeout.connect(self._on_world_file_changed_debounced)
        self._world_poll.timeout.connect(self._poll_world_mtime)

        self._camfit_done_for_path = False  # Track “first load” for current world

        self.lblRun = QLabel("—")            # ← store on self and set default here

        self._build_ui()
        self._init_followers()

        self.set_logs_dir(self.logs_dir)
        self.refresh_all()
        self.lblFile.setText(str(self.results_dir / "hollowpyramid.current.world.json"))

        # Optional: Add a poll timer if not already present
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(250)  # ms; adjust if your v1.3 uses a different cadence
        self._poll_timer.timeout.connect(self._poll_tick)
        self._poll_timer.start()

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
        
        # Connect to options changes to update snapshot interval
        # Store original method if it exists, then override
        if hasattr(self.opts, 'values_changed'):
            self.opts._original_values_changed = self.opts.values_changed
        self.opts.values_changed = self._on_solver_options_changed

        # Progress stats
        stats = QGroupBox("Progress (auto-follow)", left)
        sgrid = QFormLayout(stats)
        self.lblFile = QLabel("—", stats)
        run_label = QLabel("Run:", stats)
        self.lblRun = QLabel("—", stats)            # ← store on self and set default here
        self.lblContainer = QLabel("—", stats)
        self.lblPlaced = QLabel("— / —", stats)
        self.lblBest = QLabel("—", stats)
        self.lblAttempts = QLabel("—", stats)
        self.lblRate = QLabel("—", stats)
        rows = [
            (QLabel("World file:"), self.lblFile),
            (QLabel("Run:"), self.lblRun),
            (QLabel("Container:"), self.lblContainer),
            (QLabel("Placed / Total:"), self.lblPlaced, QLabel("—", stats)),
            (QLabel("Best depth:"), self.lblBest),
            (QLabel("Attempts:"), self.lblAttempts),
            (QLabel("Attempts/sec:"), self.lblRate),
        ]
        for row in rows:
            if len(row) == 2:
                lab, w = row
                sgrid.addRow(lab, w)
            elif len(row) == 3:
                lab, w1, w2 = row
                holder = QWidget(stats)
                hb = QHBoxLayout(holder)
                hb.setContentsMargins(0, 0, 0, 0)
                hb.setSpacing(6)
                hb.addWidget(w1)
                hb.addWidget(w2)
                sgrid.addRow(lab, holder)
            else:
                continue  # skip bad rows
        lbox.addWidget(stats, 0)

        # Totals (all runs)
        totals_box  = QGroupBox("Totals (all runs)")
        totals_form = QFormLayout(totals_box)

        self.lblBestAll     = QLabel("0")
        self.lblAttemptsAll = QLabel("0")
        self.btnResetTotals = QPushButton("Reset totals")

        totals_form.addRow("Best depth:", self.lblBestAll)
        totals_form.addRow("Attempts:",   self.lblAttemptsAll)
        totals_form.addRow(self.btnResetTotals)

        lbox.addWidget(totals_box)

        # Manual refresh
        self.btnRefresh = QPushButton("Refresh viewer  stats", left)
        self.btnRefresh.clicked.connect(self.refresh_all)
        lbox.addWidget(self.btnRefresh)

        # Colors (strategy) — below the other sliders
        colors_box = QGroupBox("Colors")
        colors_layout = QGridLayout(colors_box)

        self.cmbColors = QComboBox(colors_box)
        # Show nice names, store strategy keys as item data (stable)
        options = [
            ("Distinct HSL – Golden (3-band)", "golden-3band"),
            ("Distinct HSL – Equal (3-band)",  "equal-3band"),
            ("Distinct HSL – Equal (4-band)",  "equal-4band"),
            ("Warm / Cool Alternating",        "warm-cool"),
            ("High Contrast",                  "high-contrast"),
            ("Pastel Distinct",                "pastel"),
            ("Muted Distinct",                 "muted"),
            ("Okabe–Ito (25)",                 "okabe-ito-25"),
            ("Tableau-like (25)",              "tableau-25"),
            ("Distinct (Seeded by Piece)",     "distinct-seeded"),
        ]
        for label, key in options:
            self.cmbColors.addItem(label, key)
        self.cmbColors.setCurrentIndex(0)  # default

        colors_layout.addWidget(QLabel("Strategy:", colors_box), 0, 0)
        colors_layout.addWidget(self.cmbColors,                     0, 1)

        lbox.addWidget(colors_box)

        # Right: viewer
        self.web = QWebEngineView(self)

        # WebChannel hookup (Python→JS)
        self.channel = QWebChannel(self.web.page())
        self.bridge = ViewerBridge()
        self.channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(self.channel)

        # --- BEGIN: safety shim for _load_viewer_index ---
        if not hasattr(self, "_load_viewer_index"):
            def __load_viewer_index_impl(_self):
                layout = _self.layout()
                if layout is None:
                    layout = QVBoxLayout(_self)
                    layout.setContentsMargins(6, 6, 6, 6)
                    layout.setSpacing(6)
                    _self.setLayout(layout)

                if not hasattr(_self, "web") or _self.web is None:
                    _self.web = _self.findChild(QWebEngineView, "viewer")
                    if _self.web is None:
                        _self.web = QWebEngineView(_self)
                        _self.web.setObjectName("viewer")
                        layout.addWidget(_self.web, 1)

                html = Path(__file__).resolve().parents[2] / "viewer" / "index.html"
                _self.web.load(QUrl.fromLocalFile(str(html)))

            self._load_viewer_index = __load_viewer_index_impl.__get__(self, type(self))
        # --- END: safety shim ---

        self._load_viewer_index()

        # Initialize slider after the viewer page is ready and on refresh
        wv = self._get_webview()
        if wv:
            try:
                wv.loadFinished.connect(lambda ok: self.refresh_reveal_total())
            except Exception:
                pass

        splitter.addWidget(left)
        splitter.addWidget(self.web)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # Try to disable/hide any old Timeline/Snapshot controls if they exist
        for maybe in ("timeline_box","modeCombo","mode_selector","completionSlider","playButton","speedCombo","extendCheckbox"):
            w = getattr(self, maybe, None)
            try:
                if w: w.hide()
            except Exception:
                pass

        # Reveal (current geometry)
        reveal_box = QGroupBox("Reveal (current geometry)")
        _reveal_layout = QHBoxLayout(reveal_box)
        self.sldReveal = QSlider(Qt.Horizontal)
        self.sldReveal.setObjectName("sldReveal")
        self.sldBright = QSlider(Qt.Horizontal)
        self.sldBright.setObjectName("sldBright")
        self.sldBright.setRange(10, 500)
        self.sldBright.setValue(150)
        self.sldBright.valueChanged.connect(self.on_brightness_changed)

        # Single layout to avoid UI overlap
        _reveal_layout.addWidget(self.sldReveal, 1)
        _reveal_layout.addWidget(QLabel("Brightness:", reveal_box))
        _reveal_layout.addWidget(self.sldBright, 1)

        reveal_box.setMaximumHeight(90)
        if not self._dock_reveal_under_viewer(reveal_box):
            (self.layout() or QVBoxLayout(self)).addWidget(reveal_box)

        # Bond
        # bond_box = QGroupBox("Bond")
        # bond_layout = QHBoxLayout(bond_box)
        # self.sldBond = QSlider(Qt.Horizontal)
        # self.sldBond.setObjectName("sldBond")
        # self.sldBond.setRange(10, 500)
        # self.sldBond.setValue(150)
        # self.sldBond.valueChanged.connect(lambda v: self.web.page().runJavaScript(f"setBondRadius({v/100.0});"))
        # bond_layout.addWidget(self.sldBond, 1)
        # bond_layout.addWidget(QLabel("Bond:", bond_box))
        # bond_box.setMaximumHeight(90)
        # if not self._dock_reveal_under_viewer(bond_box):
        #     (self.layout() or QVBoxLayout(self)).addWidget(bond_box)

        # Wire slider -> viewer.setRevealCount(n); no camera refit
        self.sldReveal.valueChanged.connect(self._on_reveal_changed)

        # self.web.loadFinished.connect(lambda ok: self.web.page().runJavaScript(f"setBondRadius({self.sldBond.value()/100.0});"))

        # Apply on change
        self.cmbColors.currentIndexChanged.connect(
            lambda _:
                self.web.page().runJavaScript(
                    f'setColorStrategy("{self.cmbColors.currentData()}")'
                )
        )

        # Also apply once after the web page loads (so dropdown state takes effect)
        self.web.loadFinished.connect(
            lambda ok:
                self.web.page().runJavaScript(
                    f'setColorStrategy("{self.cmbColors.currentData()}")'
                )
        )

    @Slot()
    def _poll_tick(self, force: bool = False):
        """Stub to satisfy file-watcher/timer callbacks."""
        return

    @Slot()
    def _poll_tick(self):
        """
        Timer/auto-follow poll tick.
        Some builds expect this slot to exist (connected by name).
        Keep it lightweight; forward to an existing poll method if present.
        """
        if hasattr(self, "poll_once") and callable(getattr(self, "poll_once")):
            try:
                self.poll_once()
            except Exception:
                pass

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

        self._update_run_label(data)
        self.lblContainer.setText(str(data.get("container_name", "—")))
        total = 25
        placed = sum(len(p.get("centers", [])) > 0 for p in data.get("pieces", []))
        self.lblPlaced.setText(f"{placed} / {total}")

        # Push to viewer
        self._send_world_to_viewer(data)

        try:
            self.set_world_watch_path(path)
            # keep Reveal label correct after a reload
            self.refresh_reveal_total()  # no-op if your viewer hooks aren’t present
        except Exception:
            pass

        # If this is the first load, fit once
        if not self._camfit_done_for_path:
            self._viewer_eval_js("typeof viewer!=='undefined' && viewer.fitOnce && viewer.fitOnce({margin:1.15})")
            self._camfit_done_for_path = True
            print(f"[Camera] Initial fit applied for: {path}")

    def _clear_stats_world_only(self):
        self._update_run_label({})
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
        self.refresh_reveal_total()

    def refresh_all(self):
        self.refresh_viewer()
        self._poll_tick(force=True)
        try:
            SAFE_GETCOUNT = (
                "typeof viewer!=='undefined' && viewer.getPieceCount ? "
                "viewer.getPieceCount() : 0"
            )
            self._viewer_eval_js(SAFE_GETCOUNT, self._on_piece_count)
        except Exception:
            pass

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
        self._update_run_label(data)

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

        self._ingest_progress_obj(data)  # Update cross-run totals

    def _reset_run_aggregates(self):
        """Manual clear for cross-run totals."""
        self._agg_by_run.clear()
        self.lblBestAll.setText("0")
        self.lblAttemptsAll.setText("0")

    def _ingest_progress_obj(self, obj: dict):
        """
        Update cross-run totals from one JSON 'progress' payload:
          {"event":"progress","run":int,"best_depth":int,"attempts":int,...}
        Safe to call even if some keys are missing.
        """
        try:
            if not isinstance(obj, dict) or obj.get("event") != "progress":
                return
            run = obj.get("run")
            if run is None:
                return

            # last-seen values for that run
            best = obj.get("best_depth")
            attempts = obj.get("attempts")
            if best is not None:
                try: best = int(best)
                except Exception: best = None
            try: attempts = int(attempts) if attempts is not None else None
            except Exception: attempts = None

            rec = self._agg_by_run[run]
            if best is not None and best > rec["best"]:
                rec["best"] = best
            if attempts is not None and attempts > rec["attempts"]:
                rec["attempts"] = attempts

            # recompute totals
            if self._agg_by_run:
                best_all = max((v["best"] for v in self._agg_by_run.values()), default=-1)
                attempts_all = sum(v["attempts"] for v in self._agg_by_run.values())
                self.lblBestAll.setText(str(max(0, best_all)))
                self.lblAttemptsAll.setText(str(attempts_all))
        except Exception:
            # Never let totals break the UI
            pass

    def _get_webview(self):
        """Return the QWebEngineView used for the viewer, if any."""
        for name in ("viewer", "webView", "web", "view"):
            w = getattr(self, name, None)
            if w is not None and hasattr(w, "page"):
                return w
        try:
            vs = self.findChildren(QWebEngineView)
            if vs:
                return vs[0]
        except Exception:
            pass
        if hasattr(self.main, "viewer") and hasattr(self.main.viewer, "page"):
            return self.main.viewer
        return None

    def _dock_reveal_under_viewer(self, reveal_box) -> bool:
        if self._reveal_docked_once:
            # already placed once; just avoid rewrapping
            lay = (self._get_webview().parent().layout()
                   if self._get_webview() and self._get_webview().parent() else None)
            if lay:
                lay.addWidget(reveal_box)
                return True
            return False

        wv = self._get_webview()
        if not wv:
            return False

        p = wv.parentWidget()
        while p:
            lay = getattr(p, "layout", lambda: None)()
            from PySide6.QtWidgets import QSplitter, QVBoxLayout, QGridLayout, QHBoxLayout, QWidget
            if isinstance(lay, QVBoxLayout):
                lay.addWidget(reveal_box, 0); self._reveal_docked_once = True; return True
            if isinstance(lay, QGridLayout):
                idx = lay.indexOf(wv)
                if idx >= 0:
                    r,c,rs,cs = lay.getItemPosition(idx)
                    lay.addWidget(reveal_box, r+1, c, 1, cs)
                    self._reveal_docked_once = True
                    return True
            if isinstance(p, QSplitter):
                idx = p.indexOf(wv)
                if idx >= 0:
                    if wv.parent() is not p:
                        # already wrapped; just add under existing container
                        cont = wv.parent(); cont.layout().addWidget(reveal_box)
                        self._reveal_docked_once = True
                        return True
                    container = QWidget(p); container.setObjectName("viewerRevealContainer")
                    v = QVBoxLayout(container); v.setContentsMargins(0,0,0,0); v.setSpacing(6)
                    p.replaceWidget(idx, container)
                    wv.setParent(container)
                    v.addWidget(wv, 1)
                    v.addWidget(reveal_box, 0)
                    self._reveal_docked_once = True
                    return True
            if isinstance(lay, QHBoxLayout):
                idx = lay.indexOf(wv)
                if idx >= 0:
                    container = QWidget(p)
                    v = QVBoxLayout(container); v.setContentsMargins(0,0,0,0); v.setSpacing(6)
                    lay.replaceWidget(wv, container)
                    wv.setParent(container)
                    v.addWidget(wv, 1)
                    v.addWidget(reveal_box, 0)
                    self._reveal_docked_once = True
                    return True
            p = p.parentWidget()
        return False

    def _viewer_eval_js(self, script: str, callback=None):
        """Run JS in the viewer page; silently no-op if not found."""
        try:
            wv = self._get_webview()
            if not wv: return
            page = wv.page()
            if callback: page.runJavaScript(script, callback)
            else:        page.runJavaScript(script)
        except Exception:
            pass

    def refresh_reveal_total(self):
        SAFE_GETCOUNT = (
            "typeof viewer!=='undefined' && viewer.getPieceCount ? "
            "viewer.getPieceCount() : 0"
        )
        self._viewer_eval_js("typeof viewer!=='undefined' && viewer.resetRevealOrder && viewer.resetRevealOrder();"+SAFE_GETCOUNT, self._on_piece_count)

    def _on_piece_count(self, total):
        try:
            total = int(total) if total is not None else 0
        except Exception:
            total = 0
        self.sldReveal.setRange(0, total)
        self.sldReveal.setValue(total)
        if hasattr(self, "revealLabel") and self.revealLabel is not None:
            self.revealLabel.setText(f"{total} / {total}")

    def _on_reveal_changed(self, n):
        try:
            n = int(n)
        except Exception:
            n = 0
        # Reveal slider changes should NOT affect camera position
        self._viewer_eval_js(
            f"typeof viewer!=='undefined' && viewer.setRevealCount && viewer.setRevealCount({int(n)})"
        )
        print(f"[Camera] Reveal changed to {n} - camera position preserved")
    
    def _on_solver_options_changed(self):
        """Called when solver options change - update snapshot interval for viewer sync."""
        try:
            values = self.opts.values()
            snapshot_interval = values.get('snapshot_interval', 10)
            self._snapshot_interval_sec = int(snapshot_interval)
            
            # Update polling intervals to match solver snapshot interval
            # Convert seconds to milliseconds and add small buffer for responsiveness
            poll_interval_ms = max(250, self._snapshot_interval_sec * 1000 // 2)
            
            self._poll.setInterval(poll_interval_ms)
            self._poll_timer.setInterval(poll_interval_ms)
            
            print(f"[Viewer] Updated polling intervals to {poll_interval_ms}ms (snapshot_interval: {self._snapshot_interval_sec}s)")
        except Exception as e:
            print(f"[Viewer] Failed to update snapshot interval: {e}")
        
        # Call original values_changed if it exists
        if hasattr(self.opts, '_original_values_changed'):
            self.opts._original_values_changed()

    def set_world_watch_path(self, path: Path):
        """Start watching the given *.current.world.json for atomic replace updates."""
        try:
            path = Path(path).resolve()
        except Exception:
            return
        # Clear previous watches
        try:
            for p in list(self._world_watch.files()):
                self._world_watch.removePath(p)
            for d in list(self._world_watch.directories()):
                self._world_watch.removePath(d)
        except Exception:
            pass

        # Watch directory (reliable for atomic replace) + file (extra signal)
        try:
            self._world_watch.addPath(str(path.parent))
        except Exception:
            pass
        try:
            self._world_watch.addPath(str(path))
        except Exception:
            pass

        self._world_path = path
        self._camfit_done_for_path = False  # Reset "first load" for current world
        # tell viewer it may fit again for this new container
        self._viewer_eval_js("typeof viewer!=='undefined' && viewer.resetFit && viewer.resetFit()")
        print(f"[Camera] New world file set, camera fit reset for: {path}")

    def _on_world_dir_changed(self, changed_dir: str):
        # Directory change (atomic replace often fires this); debounce a reload
        self._world_debounce.start()

    def _on_world_file_changed_debounced(self):
        if not self._world_path:
            return
        p = self._world_path
        # If `os.replace` dropped and recreated the file, re-add the file watch
        try:
            if str(p) not in self._world_watch.files():
                self._world_watch.addPath(str(p))
        except Exception:
            pass
        # Reload using the same path you use when you click "Open"
        try:
            self.open_world_file(p)
            # Skip camera fitting on file watcher updates - preserve user camera settings
            print(f"[Camera] File watcher update - preserving user camera position")
            # keep Reveal count/label in sync
            self.refresh_reveal_total()
        except Exception:
            # Don't let a failed parse kill the loop; try again on next change
            pass

    def _poll_world_mtime(self):
        """Fallback for missed fileChanged events (Windows, atomic replace)."""
        if not self._world_path:
            return
        try:
            mt = os.path.getmtime(str(self._world_path))
        except Exception:
            return
        if mt > (self._world_mtime + 1e-6):  # changed
            self._world_mtime = mt
            self._on_world_changed_debounced()

    def _on_world_changed_debounced(self):
        if not self._world_path:
            return
        p = self._world_path
        # If `os.replace` dropped and recreated the file, re-add the file watch
        try:
            if str(p) not in self._world_watch.files():
                self._world_watch.addPath(str(p))
        except Exception:
            pass
        # Reload using the same path you use when you click "Open"
        try:
            self.open_world_file(p)
            # Skip camera fitting on mtime polling updates - preserve user camera settings
            print(f"[Camera] Mtime polling update - preserving user camera position")
            # keep Reveal count/label in sync
            self.refresh_reveal_total()
        except Exception:
            # Don't let a failed parse kill the loop; try again on next change
            pass

    def _update_run_label(self, payload: dict):
        run = payload.get("run") or payload.get("run_id")
        if isinstance(run, (int, float)) or (isinstance(run, str) and run.isdigit()):
            self._last_run_id = int(run)
        if getattr(self, "_last_run_id", None) is not None and hasattr(self, "lblRun"):
            self.lblRun.setText(str(self._last_run_id))

    def reset_progress_ui(self):
        self._last_run_id = None
        if hasattr(self, "lblRun"):
            self.lblRun.setText("—")
        # ... existing clears for other fields ...

    def on_brightness_changed(self, val: int):
        brightness = val / 100.0
        self.web.page().runJavaScript(f"setBrightness({brightness});")

    def _send_js(self, code: str):
        view = getattr(self.parent(), "webview", None) or getattr(self.parent(), "web", None) \
               or getattr(self.parent(), "viewer", None)
        if view is None:
            return
        page = view.page() if hasattr(view, "page") else None
        if page:
            page.runJavaScript(code)

    def _load_viewer_index(self):
        """
        Load apps/puzzle_ui/viewer/index.html into a single QWebEngineView.
        """
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.setSpacing(6)
            self.setLayout(layout)

        if not hasattr(self, "web") or self.web is None:
            self.web = self.findChild(QWebEngineView, "viewer")
            if self.web is None:
                self.web = QWebEngineView(self)
                self.web.setObjectName("viewer")
                layout.addWidget(self.web, 1)

        html = Path(__file__).resolve().parents[2] / "viewer" / "index.html"
        self.web.load(QUrl.fromLocalFile(str(html)))
