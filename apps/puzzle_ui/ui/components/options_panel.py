# apps/puzzle_ui/ui/components/options_panel.py

# import shim: support both direct-run and module-run
import sys
from pathlib import Path

_pkg = __package__
if _pkg in (None, "", "components"):
    # Direct run or imported as top-level "components"
    _UI_DIR = Path(__file__).resolve().parents[1]  # .../apps/puzzle_ui/ui
    if str(_UI_DIR) not in sys.path:
        sys.path.insert(0, str(_UI_DIR))
    from utils import app_root, repo_root, win_quote, as_str  # noqa: E402
else:
    # Module run: python -m apps.puzzle_ui.ui.main
    from ..utils import app_root, repo_root, win_quote, as_str  # type: ignore

import json, shlex
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QSpinBox,
    QLineEdit, QTextEdit, QGroupBox, QScrollArea, QFileDialog
)


class OptionsPanel(QWidget):
    """
    Builds a form from apps/puzzle_ui/config/solver_options.schema.json
    Supports field types: file, dir, enum, bool, int, string (multiline)
    Arg mappings in schema:
      - {"position": 0}          → positional arg slot
      - {"flag": "--rng-seed"}   → flag + value (bool emits flag if True)
      - {"pattern": "--hole{value}"}
      - {"raw": true}            → split string into args with shlex
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
        self.update_visibility()  # ensure conditional fields are correct

    # ---------- Public API ----------
    def values(self) -> Dict[str, Any]:
        """Collect current values from widgets (supports file/dir wrappers)."""
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
        """Apply values from presets by name."""
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
        - program: python executable path/name (defaults to current interpreter)
        - argv: [solver_script, <positional>, <flags/values> ...]
        - pretty: a single string for UI preview (resolved where sensible)
        """
        v = self.values()

        # default to the Python running this app (your venv)
        program = v.get("python_path") or sys.executable

        # default solver path → external/solver/solver.py
        solver_script = v.get("solver_script") or (repo_root() / "external" / "solver" / "solver.py")
        sp = Path(solver_script)
        if not sp.is_absolute():
            sp = (repo_root() / sp).resolve()

        # compile args
        positional: List[Tuple[int, str]] = []
        flagged: List[str] = []
        raw: List[str] = []

        for f in self.fields:
            key = f.get("key")
            arg = f.get("arg")
            if not key or arg is None:
                continue

            # visibility (respect visible_if)
            cond = f.get("visible_if")
            visible = True
            if cond:
                for dep_key, dep_val in cond.items():
                    visible = (v.get(dep_key) == dep_val)
            if not visible:
                continue

            val = v.get(key, None)
            # Skip empty strings for text/file/dir fields, but allow 0 for int fields
            if f.get("type") in ("string", "file", "dir") and (val is None or val == ""):
                continue
            # Skip None values for int fields, but allow 0
            if f.get("type") == "int" and val is None:
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
                    # Debug logging for snapshot_interval
                    if key == "snapshot_interval":
                        print(f"[DEBUG] Processing snapshot_interval: key={key}, val={val}, flag={flag}")
                    flagged.extend([flag, as_str(val)])
            elif "pattern" in (arg or {}):
                pattern = arg["pattern"]
                formatted = pattern.replace("{value}", as_str(val))
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

        # Pretty preview with resolved container path (if present)
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

        # Scrollable form area
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

        # Wiring
        self.cmbPreset.currentIndexChanged.connect(self._preset_changed)
        self.btnApplyPreset.clicked.connect(self._apply_preset_clicked)

    def load_schema(self):
        try:
            txt = self.schema_path.read_text(encoding="utf-8-sig")
            self.schema = json.loads(txt)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
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
        # clear previous form
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

            row = 0
            hint = grp.get("hint")
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
        self.values_changed()  # populate preview

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
                    
                    # Auto-load companion viewer file when container is selected
                    field_key = f.get("key")
                    if field_key == "container" and p:
                        # Kill any running solver when new container is selected
                        main_window = self._get_main_window()
                        if main_window and hasattr(main_window, 'process_running') and main_window.process_running:
                            print("[UI DEBUG] New container selected - stopping current solver")
                            main_window._stop_solver()
                        
                        self._auto_load_companion_viewer_file(p)

            btn.clicked.connect(browse)
            line.textChanged.connect(self._field_changed)
            h.addWidget(line, 1)
            h.addWidget(btn)
            wrap._value_line = line  # type: ignore[attr-defined]
            return wrap

        # string (single or multiline)
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
            # file/dir wrapper returns the line edit content
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

    def _auto_load_companion_viewer_file(self, container_path: str):
        """Auto-select companion viewer file when container is selected."""
        try:
            from pathlib import Path
            container_file = Path(container_path)
            
            # Generate the companion viewer filename
            # e.g., "32spheres.json" -> "32spheres.current.world.json"
            container_stem = container_file.stem  # "32spheres"
            companion_name = f"{container_stem}.current.world.json"
            
            # Look for the companion file in multiple locations
            search_paths = [
                repo_root() / "samples" / companion_name,
                repo_root() / "samples" / "results" / companion_name,
                repo_root() / "external" / "solver" / "results" / companion_name
            ]
            
            print(f"[UI DEBUG] Auto-loading companion viewer file for container: {container_file.name}")
            print(f"[UI DEBUG] Looking for companion file: {companion_name}")
            
            companion_path = None
            for path in search_paths:
                print(f"[UI DEBUG] Checking: {path}")
                if path.exists():
                    companion_path = path
                    print(f"[UI DEBUG] Found companion file: {companion_path}")
                    break
            
            if companion_path:
                # Get the main window and load the viewer file
                main_window = self._get_main_window()
                if main_window and hasattr(main_window, 'solve_tab'):
                    main_window.solve_tab.open_world_file(companion_path)
                    print(f"[UI DEBUG] Successfully loaded companion viewer file: {companion_name}")
                else:
                    print("[UI DEBUG] Could not access main window or solve_tab")
            else:
                print(f"[UI DEBUG] Companion file not found in any search location: {companion_name}")
                
        except Exception as e:
            print(f"[UI DEBUG] Error auto-loading companion viewer file: {e}")

    def _get_main_window(self):
        """Find the main window by traversing up the widget hierarchy."""
        widget = self
        while widget:
            if hasattr(widget, 'solve_tab'):  # MainWindow has solve_tab attribute
                return widget
            widget = widget.parent()
        return None
