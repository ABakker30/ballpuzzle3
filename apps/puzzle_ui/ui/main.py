# apps/puzzle_ui/ui/main.py
# v0.1 UI with schema-driven options, presets, command preview, process launch,
# and robust live progress (tail jsonl + parse stdout fallback)

import json, shlex, sys, re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QUrl, QProcess, QTimer, QFileSystemWatcher
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QFileDialog, QTabWidget, QSplitter, QSizePolicy,
    QMessageBox, QComboBox, QCheckBox, QSpinBox, QLineEdit, QTextEdit,
    QGroupBox, QScrollArea
)
from PySide6.QtWebEngineWidgets import QWebEngineView


APP_TITLE = "Puzzle Solver UI — v0.1"


# ---------- Paths ----------
def app_root() -> Path:
    return Path(__file__).resolve().parents[1]  # .../apps/puzzle_ui


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]  # repo root


# ---------- Small helpers ----------
def win_quote(arg: str) -> str:
    if not arg:
        return '""'
    if any(c.isspace() for c in arg) or '"' in arg or "'" in arg or "\\" in arg:
        return f'"{arg}"'
    return arg


def as_str(v: Any) -> str:
    return str(v)


# ---------- Schema-driven options panel ----------
class OptionsPanel(QWidget):
    """
    Builds a form from apps/puzzle_ui/config/solver_options.schema.json
    Supports field types: file, dir, enum, bool, int, string (multiline)
    Arg mappings: position, flag, pattern, raw
    Visible-if: {"other_key": true/false/enum_value}
    """
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.schema_path = app_root() / "config" / "solver_options.schema.json"
        self.presets_path = app_root() / "config" / "solver_presets.json"
        self.schema: Dict[str, Any] = {}
        self.presets: List[Dict[str, Any]] = []
        self.widgets: Dict[str, QWidget] = {}
        self.fields: List[Dict[str, Any]] = []
        self._building = False

        self._build_ui()
        self.load_schema()
        self.load_presets()
        self.populate_from_schema()
        self.update_visibility()

    # ---------- Public API ----------
    def values(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for f in self.fields:
            key = f.get("key")
            if not key:
                continue
            w = self.widgets.get(key)
            if not w:
                continue
            out[key] = self._get_widget_value(key, w)
        return out

    def apply_preset(self, name: str):
        for p in self.presets:
            if p.get("name") == name:
                vals = p.get("values", {})
                self._set_values(vals)
                break
        self.update_visibility()
        self.values_changed()

    def build_command(self) -> Tuple[str, List[str], str]:
        """
        Returns (program, argv, pretty_string).
        - program: python executable (defaults to current interpreter)
        - argv: [solver_script, <positional>, <flags/values> ...]
        - pretty: single string with resolved paths for display/copy
        """
        v = self.values()
        program = v.get("python_path") or sys.executable

        solver_script = v.get("solver_script") or (repo_root() / "external" / "solver" / "solver.py")
        sp = Path(solver_script)
        if not sp.is_absolute():
            sp = (repo_root() / sp).resolve()

        positional: List[Tuple[int, str]] = []
        flagged: List[str] = []
        raw: List[str] = []

        for f in self.fields:
            key = f.get("key")
            arg = f.get("arg")
            if not key or arg is None:
                continue

            visible = True
            cond = f.get("visible_if")
            if cond:
                for dep_key, dep_val in cond.items():
                    visible = (v.get(dep_key) == dep_val)
            if not visible:
                continue

            val = v.get(key, None)
            if f.get("type") in ("string", "file", "dir") and (val is None or val == ""):
                continue

            if "position" in (arg or {}):
                try:
                    pos = int(arg["position"])
                    positional.append((pos, as_str(val)))
                except Exception:
                    pass
            elif "flag" in (arg or {}):
                flag = arg["flag"]
                if f.get("type") == "bool":
                    if bool(val):
                        flagged.append(flag)
                else:
                    flagged.extend([flag, as_str(val)])
            elif "pattern" in (arg or {}):
                formatted = arg["pattern"].replace("{value}", as_str(val))
                if formatted.strip():
                    flagged.append(formatted)
            elif (arg or {}).get("raw"):
                s = as_str(val)
                if s:
                    try:
                        raw.extend(shlex.split(s))
                    except Exception:
                        raw.extend(s.split())

        positional_sorted = [p for _, p in sorted(positional, key=lambda t: t[0])]
        argv: List[str] = [str(sp)] + positional_sorted + flagged + raw

        # Pretty display: resolve container path if present
        if len(argv) >= 2 and not argv[1].startswith("-"):
            cpath = Path(argv[1])
            if not cpath.is_absolute():
                argv[1] = str((repo_root() / cpath).resolve())

        pretty = " ".join([win_quote(str(program))] + [win_quote(a) for a in argv])
        return str(program), argv, pretty

    # ---------- Internals ----------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # Header: Presets + Command preview
        top = QHBoxLayout()
        self.cmbPreset = QComboBox(self)
        self.cmbPreset.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmbPreset.addItem("— Select preset —")
        self.btnApplyPreset = QPushButton("Apply", self)
        self.btnApplyPreset.setEnabled(False)
        top.addWidget(QLabel("Preset:", self))
        top.addWidget(self.cmbPreset, 1)
        top.addWidget(self.btnApplyPreset)
        outer.addLayout(top)

        # Scrollable form
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.formHost = QWidget(self.scroll)
        self.formLayout = QVBoxLayout(self.formHost)
        self.formLayout.setContentsMargins(0, 0, 0, 0)
        self.formLayout.setSpacing(6)
        self.scroll.setWidget(self.formHost)
        outer.addWidget(self.scroll, 1)

        # Command preview
        outer.addWidget(QLabel("Command preview:", self))
        self.txtCmd = QTextEdit(self)
        self.txtCmd.setReadOnly(True)
        self.txtCmd.setFixedHeight(72)
        self.txtCmd.setLineWrapMode(QTextEdit.NoWrap)
        outer.addWidget(self.txtCmd)

        # Wire
        self.cmbPreset.currentIndexChanged.connect(self._preset_changed)
        self.btnApplyPreset.clicked.connect(self._apply_preset_clicked)

    def load_schema(self):
        try:
            txt = self.schema_path.read_text(encoding="utf-8-sig")
            self.schema = json.loads(txt)
        except Exception as e:
            QMessageBox.critical(self, "Schema error", f"Failed to read schema:\n{self.schema_path}\n\n{e}")
            self.schema = {"version": "1", "groups": []}

    def load_presets(self):
        try:
            txt = self.presets_path.read_text(encoding="utf-8-sig")
            data = json.loads(txt)
            self.presets = data.get("presets", [])
            for p in self.presets:
                self.cmbPreset.addItem(p.get("name", "preset"), userData=p.get("name"))
            self.btnApplyPreset.setEnabled(len(self.presets) > 0)
        except Exception:
            self.presets = []
            self.btnApplyPreset.setEnabled(False)

    def populate_from_schema(self):
        self._building = True
        self.fields.clear()
        while self.formLayout.count():
            item = self.formLayout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.widgets.clear()

        for grp in self.schema.get("groups", []):
            box = QGroupBox(grp.get("title", ""), self.formHost)
            g = QGridLayout(box)
            g.setContentsMargins(8, 8, 8, 8)
            g.setHorizontalSpacing(8)
            g.setVerticalSpacing(6)

            hint = grp.get("hint")
            row = 0
            if hint:
                lblHint = QLabel(hint, box)
                lblHint.setWordWrap(True)
                lblHint.setStyleSheet("color:#888;")
                g.addWidget(lblHint, row, 0, 1, 2)
                row += 1

            for f in grp.get("fields", []):
                key = f.get("key")
                if not key:
                    continue
                self.fields.append(f)
                label = QLabel(f.get("label", key), box)
                w = self._create_widget_for_field(f, box)
                self.widgets[key] = w
                g.addWidget(label, row, 0)
                g.addWidget(w, row, 1)
                row += 1

            self.formLayout.addWidget(box)

        self.formLayout.addStretch(1)
        self._building = False
        self.values_changed()

    def _create_widget_for_field(self, f: Dict[str, Any], parent: QWidget) -> QWidget:
        t = f.get("type")
        default = f.get("default")

        if t == "bool":
            w = QCheckBox(parent)
            w.setChecked(bool(default))
            w.toggled.connect(self._field_changed)
            return w

        if t == "int":
            w = QSpinBox(parent)
            w.setRange(int(f.get("min", -10**9)), int(f.get("max", 10**9)))
            w.setValue(int(default if default is not None else 0))
            w.valueChanged.connect(self._field_changed)
            return w

        if t == "enum":
            w = QComboBox(parent)
            choices = f.get("choices", [])
            for ch in choices:
                w.addItem(str(ch), userData=ch)
            if default in choices:
                w.setCurrentIndex(choices.index(default))
            w.currentIndexChanged.connect(self._field_changed)
            return w

        if t in ("file", "dir"):
            wrap = QWidget(parent)
            h = QHBoxLayout(wrap)
            h.setContentsMargins(0, 0, 0, 0)
            line = QLineEdit(wrap)
            if isinstance(default, str):
                line.setText(default)
            btn = QPushButton("Browse…", wrap)

            def browse():
                if t == "file":
                    filt = (f.get("dialog", {}) or {}).get("filter", "All Files (*)")
                    start_rel = (f.get("dialog", {}) or {}).get("start_dir")
                    start_dir = (repo_root() / start_rel) if start_rel else repo_root()
                    p, _ = QFileDialog.getOpenFileName(wrap, "Select file", str(start_dir), filt)
                else:
                    start_dir = repo_root()
                    p = QFileDialog.getExistingDirectory(wrap, "Select folder", str(start_dir))
                if p:
                    line.setText(p)
                    self._field_changed()

            btn.clicked.connect(browse)
            line.textChanged.connect(self._field_changed)
            h.addWidget(line, 1)
            h.addWidget(btn)
            wrap._value_line = line  # type: ignore[attr-defined]
            return wrap

        if f.get("multiline"):
            w = QTextEdit(parent)
            if isinstance(default, str):
                w.setPlainText(default)
            w.textChanged.connect(self._field_changed)
            return w
        else:
            w = QLineEdit(parent)
            if isinstance(default, str):
                w.setText(default)
            if f.get("placeholder"):
                w.setPlaceholderText(f["placeholder"])
            w.textChanged.connect(self._field_changed)
            return w

    def _get_widget_value(self, key: str, w: QWidget) -> Any:
        f = next((x for x in self.fields if x.get("key") == key), None)
        if not f:
            return None
        t = f.get("type")
        if t == "bool" and isinstance(w, QCheckBox):
            return bool(w.isChecked())
        if t == "int" and isinstance(w, QSpinBox):
            return int(w.value())
        if t == "enum" and isinstance(w, QComboBox):
            return w.currentData() if w.currentData() is not None else w.currentText()
        if isinstance(w, QWidget) and hasattr(w, "_value_line"):
            return getattr(w, "_value_line").text().strip()  # type: ignore[attr-defined]
        if isinstance(w, QLineEdit):
            return w.text().strip()
        if isinstance(w, QTextEdit):
            return w.toPlainText().strip()
        return None

    def _set_widget_value(self, key: str, val: Any):
        w = self.widgets.get(key)
        if not w:
            return
        if isinstance(w, QCheckBox):
            w.setChecked(bool(val))
        elif isinstance(w, QSpinBox):
            try:
                w.setValue(int(val))
            except Exception:
                pass
        elif isinstance(w, QComboBox):
            idx = -1
            for i in range(w.count()):
                if w.itemData(i) == val or w.itemText(i) == str(val):
                    idx = i
                    break
            if idx >= 0:
                w.setCurrentIndex(idx)
        elif isinstance(w, QWidget) and hasattr(w, "_value_line"):
            getattr(w, "_value_line").setText(str(val))  # type: ignore[attr-defined]
        elif isinstance(w, QLineEdit):
            w.setText(str(val))
        elif isinstance(w, QTextEdit):
            w.setPlainText(str(val))

    def _set_values(self, values: Dict[str, Any]):
        for k, v in values.items():
            self._set_widget_value(k, v)

    def _preset_changed(self, idx: int):
        self.btnApplyPreset.setEnabled(idx > 0)

    def _apply_preset_clicked(self):
        name = self.cmbPreset.currentData()
        if name:
            self.apply_preset(name)

    def _field_changed(self, *args):
        if self._building:
            return
        self.update_visibility()
        self.values_changed()

    def update_visibility(self):
        v = self.values()
        for f in self.fields:
            key = f.get("key")
            w = self.widgets.get(key)
            if not w:
                continue
            visible = True
            cond = f.get("visible_if")
            if cond:
                for dep_key, dep_val in cond.items():
                    visible = (v.get(dep_key) == dep_val)
            w.setVisible(visible)

    def values_changed(self):
        _prog, _argv, pretty = self.build_command()
        self.txtCmd.setPlainText(pretty)


# ---------- Solve Tab (stats + viewer + robust progress follower) ----------
class SolveTab(QWidget):
    """
    Left: OptionsPanel + Stats
    Right: Embedded three.js viewer
    Follows logs/progress.json(.jsonl) with tailing; parses stdout as fallback.
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

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal, self)
        root.addWidget(splitter)

        # --- Left panel ---
        left = QWidget(self)
        left.setMinimumWidth(420)
        left.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(10, 10, 10, 10)
        lbox.setSpacing(8)

        self.opts = OptionsPanel(left)
        lbox.addWidget(self.opts, 1)

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

        self.btnRefresh = QPushButton("Refresh viewer  stats", left)
        self.btnRefresh.clicked.connect(self.refresh_all)
        lbox.addWidget(self.btnRefresh)

        # --- Right: viewer ---
        self.web = QWebEngineView(self)
        self._load_viewer_index()

        splitter.addWidget(left)
        splitter.addWidget(self.web)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    def _init_followers(self):
        self._fsw = QFileSystemWatcher(self)
        self._fsw.fileChanged.connect(lambda *_: self._poll_tick(force=True))
        self._fsw.directoryChanged.connect(lambda *_: self._poll_tick(force=True))

        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._poll_tick)
        self._poll.start()

        self.set_logs_dir(self.logs_dir)

    # ---------- follower control ----------
    def set_follow_enabled(self, enabled: bool):
        self._poll.setActive(enabled)

    def set_logs_dir(self, path: Path):
        self.logs_dir = Path(path)
        self.progress_json = self.logs_dir / "progress.json"
        self.progress_jsonl = self.logs_dir / "progress.jsonl"
        self._jsonl_last_size = 0
        self._jsonl_buf = ""
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

    # ---------- world file handling ----------
    def open_world_file(self, path: Path):
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

    def _clear_stats_world_only(self):
        self.lblRun.setText("—")
        self.lblContainer.setText("—")
        self.lblPlaced.setText("— / —")

    def _load_viewer_index(self):
        viewer_index = app_root() / "viewer" / "index.html"
        if not viewer_index.exists():
            QMessageBox.critical(self, "Missing viewer", f"Viewer file not found:\n{viewer_index}")
        self.web.setUrl(QUrl.fromLocalFile(str(viewer_index)))

    def refresh_viewer(self):
        self.web.reload()

    def refresh_all(self):
        self.refresh_viewer()
        self._poll_tick(force=True)

    # ---------- progress polling (snapshot + jsonl tail) ----------
    def _poll_tick(self, force: bool = False):
        # progress.json snapshot (cheap)
        try:
            st = self.progress_json.stat()
            sig = (int(st.st_mtime_ns), int(st.st_size))
            if force or sig != self._last_sig_json:
                self._last_sig_json = sig
                self._read_progress_json()
        except Exception:
            pass

        # progress.jsonl tail (read only new bytes; cap initial read)
        try:
            st = self.progress_jsonl.stat()
            size = int(st.st_size)
            # rotation/truncation
            if self._jsonl_last_size > size:
                self._jsonl_last_size = 0
                self._jsonl_buf = ""
            start = self._jsonl_last_size
            # first time on large file → only last 64KB
            if start == 0 and size > 65536:
                start = size - 65536
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
        # parse from the end to find a valid JSON line
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


# ---------- Main Window ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1400, 880)

        self.proc: Optional[QProcess] = None
        self.process_running = False
        self.viewer_paused = False

        self._build_ui()

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        top = QHBoxLayout()
        self.btnStart = QPushButton("Start")
        self.btnPause = QPushButton("Pause viewer")
        self.btnStop = QPushButton("Stop")
        self.btnRefreshTop = QPushButton("Refresh viewer")
        self.btnOpen = QPushButton("Open .current.world.json…")
        self.lblStatus = QLabel("Status: Idle")
        self.lblStatus.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        for w in (self.btnStart, self.btnPause, self.btnStop, self.btnRefreshTop, self.btnOpen):
            top.addWidget(w)
        top.addStretch(1)
        top.addWidget(self.lblStatus)

        tabs = QTabWidget(self)
        self.solve_tab = SolveTab(self)
        tabs.addTab(self.solve_tab, "Solve")

        mint_tab = QWidget(self)
        mv = QVBoxLayout(mint_tab)
        mv.addWidget(QLabel("Mint Solution (placeholder)\n\n"
                            "Will collect metadata, connect wallet, submit tx, and show receipt.", mint_tab))
        mv.addStretch(1)
        tabs.addTab(mint_tab, "Mint Solution")

        builder_tab = QWidget(self)
        bv = QVBoxLayout(builder_tab)
        bv.addWidget(QLabel("Container Builder (placeholder)\n\n"
                            "Will author/validate container JSON.", builder_tab))
        bv.addStretch(1)
        tabs.addTab(builder_tab, "Container Builder")

        v.addLayout(top)
        v.addWidget(tabs, 1)

        # wiring
        self.btnRefreshTop.clicked.connect(self.solve_tab.refresh_all)
        self.btnOpen.clicked.connect(self._pick_world_file)
        self.btnStart.clicked.connect(self._start_solver)
        self.btnStop.clicked.connect(self._stop_solver)
        self.btnPause.clicked.connect(self._toggle_viewer_pause)

        self._update_buttons()

    # ---------- Actions ----------
    def _pick_world_file(self):
        start_dir = str((repo_root() / "samples").resolve())
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open world JSON", start_dir,
            "World JSON (*.current.world.json *.json);;All Files (*)",
        )
        if not file_path:
            return
        self.solve_tab.open_world_file(Path(file_path))
        self.lblStatus.setText("Status: File loaded")

    def _start_solver(self):
        if self.process_running:
            QMessageBox.information(self, "Already running", "Solver process is already running.")
            return

        program, argv, _ = self.solve_tab.opts.build_command()

        vals = self.solve_tab.opts.values()
        container = vals.get("container")
        if not container:
            QMessageBox.warning(self, "Missing container", "Please select a Container JSON.")
            return

        prog_abs = Path(program)
        if not prog_abs.is_absolute():
            prog_abs = (repo_root() / prog_abs).resolve()
        if len(argv) >= 1:
            argv[0] = str(Path(argv[0]).expanduser().resolve())  # solver (or wrapper)
        if len(argv) >= 2 and not argv[1].startswith("-"):
            argv[1] = str(Path(argv[1]).expanduser().resolve())  # container

        pretty = " ".join([win_quote(str(prog_abs))] + [win_quote(a) for a in argv])
        self._append_status(f"Launching: {pretty}")

        # logs dir for follower
        logs_val = vals.get("logs_dir")
        if logs_val:
            p = Path(logs_val)
            logs_path = p if p.is_absolute() else (repo_root() / p).resolve()
        else:
            logs_path = Path(argv[0]).parent / "logs"
        self.solve_tab.set_logs_dir(logs_path)

        # working dir: parent of the script (wrapper or real solver)
        workdir = str(Path(argv[0]).parent)

        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(workdir)
        self.proc.readyReadStandardOutput.connect(lambda: self._drain_output("stdout"))
        self.proc.readyReadStandardError.connect(lambda: self._drain_output("stderr"))
        self.proc.finished.connect(self._proc_finished)

        try:
            self.proc.start(str(prog_abs), argv)
        except Exception as e:
            QMessageBox.critical(self, "Launch error", f"Failed to start solver:\n{e}")
            self.proc = None
            return

        if not self.proc.waitForStarted(3000):
            QMessageBox.critical(self, "Launch error", "Solver failed to start.")
            self.proc = None
            return

        self.process_running = True
        self.lblStatus.setText("Status: Running")
        self._update_buttons()

    def _stop_solver(self):
        if not self.proc or not self.process_running:
            return
        self._append_status("Stopping solver...")
        self.proc.terminate()
        if not self.proc.waitForFinished(5000):
            self._append_status("Force-killing solver...")
            self.proc.kill()
            self.proc.waitForFinished(2000)
        self.process_running = False
        self.lblStatus.setText("Status: Stopped")
        self._update_buttons()

    def _toggle_viewer_pause(self):
        self.viewer_paused = not self.viewer_paused
        self.btnPause.setText("Resume viewer" if self.viewer_paused else "Pause viewer")
        self._append_status("Viewer paused" if self.viewer_paused else "Viewer resumed")
        self.solve_tab.set_follow_enabled(not self.viewer_paused)

    def _proc_finished(self, code: int, status: QProcess.ExitStatus):
        self.process_running = False
        self.lblStatus.setText(f"Status: Exited ({code})")
        self._update_buttons()
        self._append_status(f"Solver exited with code {code}")

    def _drain_output(self, which: str):
        if not self.proc:
            return
        stream = self.proc.readAllStandardOutput() if which == "stdout" else self.proc.readAllStandardError()
        text = bytes(stream).decode(errors="ignore")
        if text.strip():
            # show last line in status
            last = text.strip().splitlines()[-1]
            self._append_status(last)
            # parse and update stats from stdout as fallback
            self._parse_and_apply_stdout(text)

    def _parse_and_apply_stdout(self, text: str):
        """
        Parse lines like:
        [run 0 seed=42000] placed 22/25 | best 24 | rate 7301/s
        """
        pat = re.compile(
            r"\[run\s+(?P<run>\d+)[^\]]*\]\s*placed\s+(?P<placed>\d+)\/(?P<total>\d+)\s*\|\s*best\s+(?P<best>\d+)\s*\|\s*rate\s+(?P<rate>[0-9.]+)\/s",
            re.IGNORECASE,
        )
        for ln in text.splitlines()[::-1]:  # search from last line back
            m = pat.search(ln)
            if not m:
                continue
            self.solve_tab.lblRun.setText(m.group("run"))
            self.solve_tab.lblPlaced.setText(f"{m.group('placed')} / {m.group('total')}")
            self.solve_tab.lblBest.setText(m.group("best"))
            self.solve_tab.lblRate.setText(m.group("rate"))
            break

    def _append_status(self, msg: str):
        self.lblStatus.setText(f"Status: {msg}")

    def _update_buttons(self):
        self.btnStart.setEnabled(not self.process_running)
        self.btnStop.setEnabled(self.process_running)
        self.btnPause.setEnabled(True)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
