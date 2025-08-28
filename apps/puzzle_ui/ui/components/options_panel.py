import json, shlex, sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QSpinBox,
    QLineEdit, QTextEdit, QGroupBox, QScrollArea, QFileDialog
)

from ..utils import app_root, repo_root, win_quote, as_str


class OptionsPanel(QWidget):
    """
    Builds a form from apps/puzzle_ui/config/solver_options.schema.json
    Supports: file, dir, enum, bool, int, string (multiline)
    Arg mappings: position, flag, pattern, raw
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
                self._set_values(p.get("values", {}))
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
            key = f.get("key"); arg = f.get("arg")
            if not key or arg is None:
                continue

            # visibility
            vis = True
            cond = f.get("visible_if")
            if cond:
                for depk, depv in cond.items():
                    vis = (v.get(depk) == depv)
            if not vis:
                continue

            val = v.get(key, None)
            if f.get("type") in ("string", "file", "dir") and (val is None or val == ""):
                continue

            if "position" in (arg or {}):
                try:
                    positional.append((int(arg["position"]), as_str(val)))
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

        # Pretty: resolve container path if present
        if len(argv) >= 2 and not argv[1].startswith("-"):
            cpath = Path(argv[1])
            if not cpath.is_absolute():
                argv[1] = str((repo_root() / cpath).resolve())

        pretty = " ".join([win_quote(str(program))] + [win_quote(a) for a in argv])
        return str(program), argv, pretty

    # ---------- Internals ----------
    def _build_ui(self):
        outer = QVBoxLayout(self); outer.setContentsMargins(0,0,0,0); outer.setSpacing(8)

        # header (presets)
        top = QHBoxLayout()
        self.cmbPreset = QComboBox(self); self.cmbPreset.addItem("— Select preset —")
        self.btnApplyPreset = QPushButton("Apply", self); self.btnApplyPreset.setEnabled(False)
        top.addWidget(QLabel("Preset:", self)); top.addWidget(self.cmbPreset, 1); top.addWidget(self.btnApplyPreset)
        outer.addLayout(top)

        # scrollable form
        self.scroll = QScrollArea(self); self.scroll.setWidgetResizable(True)
        self.formHost = QWidget(self.scroll)
        self.formLayout = QVBoxLayout(self.formHost); self.formLayout.setContentsMargins(0,0,0,0)
        self.scroll.setWidget(self.formHost)
        outer.addWidget(self.scroll, 1)

        # command preview
        outer.addWidget(QLabel("Command preview:", self))
        self.txtCmd = QTextEdit(self); self.txtCmd.setReadOnly(True); self.txtCmd.setFixedHeight(72); self.txtCmd.setLineWrapMode(QTextEdit.NoWrap)
        outer.addWidget(self.txtCmd)

        # wire
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
            self.presets = []; self.btnApplyPreset.setEnabled(False)

    def populate_from_schema(self):
        self._building = True
        self.fields.clear()
        while self.formLayout.count():
            it = self.formLayout.takeAt(0); w = it.widget()
            if w: w.deleteLater()
        self.widgets.clear()

        for grp in self.schema.get("groups", []):
            box = QGroupBox(grp.get("title", ""), self.formHost)
            g = QGridLayout(box); g.setContentsMargins(8,8,8,8); g.setHorizontalSpacing(8); g.setVerticalSpacing(6)

            row = 0
            hint = grp.get("hint")
            if hint:
                lbl = QLabel(hint, box); lbl.setWordWrap(True); lbl.setStyleSheet("color:#888;")
                g.addWidget(lbl, row, 0, 1, 2); row += 1

            for f in grp.get("fields", []):
                key = f.get("key")
                if not key: continue
                self.fields.append(f)
                label = QLabel(f.get("label", key), box)
                w = self._create_widget_for_field(f, box); self.widgets[key] = w
                g.addWidget(label, row, 0); g.addWidget(w, row, 1); row += 1

            self.formLayout.addWidget(box)

        self.formLayout.addStretch(1)
        self._building = False
        self.values_changed()

    def _create_widget_for_field(self, f: Dict[str, Any], parent: QWidget) -> QWidget:
        t = f.get("type"); default = f.get("default")

        if t == "bool":
            w = QCheckBox(parent); w.setChecked(bool(default)); w.toggled.connect(self._field_changed); return w

        if t == "int":
            w = QSpinBox(parent); w.setRange(int(f.get("min", -10**9)), int(f.get("max", 10**9)))
            w.setValue(int(default if default is not None else 0)); w.valueChanged.connect(self._field_changed); return w

        if t == "enum":
            w = QComboBox(parent); choices = f.get("choices", [])
            for ch in choices: w.addItem(str(ch), userData=ch)
            if default in choices: w.setCurrentIndex(choices.index(default))
            w.currentIndexChanged.connect(self._field_changed); return w

        if t in ("file", "dir"):
            wrap = QWidget(parent); h = QHBoxLayout(wrap); h.setContentsMargins(0,0,0,0)
            line = QLineEdit(wrap)
            if isinstance(default, str): line.setText(default)
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
                    line.setText(p); self._field_changed()

            btn.clicked.connect(browse); line.textChanged.connect(self._field_changed)
            h.addWidget(line, 1); h.addWidget(btn)
            wrap._value_line = line  # type: ignore[attr-defined]
            return wrap

        # string
        if f.get("multiline"):
            w = QTextEdit(parent)
            if isinstance(default, str): w.setPlainText(default)
            w.textChanged.connect(self._field_changed); return w
        else:
            w = QLineEdit(parent)
            if isinstance(default, str): w.setText(default)
            if f.get("placeholder"): w.setPlaceholderText(f["placeholder"])
            w.textChanged.connect(self._field_changed); return w

    def _get_widget_value(self, key: str, w: QWidget) -> Any:
        f = next((x for x in self.fields if x.get("key") == key), None)
        if not f: return None
        t = f.get("type")
        if t == "bool" and isinstance(w, QCheckBox): return bool(w.isChecked())
        if t == "int" and isinstance(w, QSpinBox): return int(w.value())
        if t == "enum" and isinstance(w, QComboBox): return w.currentData() if w.currentData() is not None else w.currentText()
        if hasattr(w, "_value_line"): return getattr(w, "_value_line").text().strip()  # file/dir wrapper
        if isinstance(w, QLineEdit): return w.text().strip()
        if isinstance(w, QTextEdit): return w.toPlainText().strip()
        return None

    def _set_widget_value(self, key: str, val: Any):
        w = self.widgets.get(key)
        if not w: return
        if isinstance(w, QCheckBox): w.setChecked(bool(val)); return
        if isinstance(w, QSpinBox):
            try: w.setValue(int(val))
            except Exception: pass
            return
        if isinstance(w, QComboBox):
            idx = -1
            for i in range(w.count()):
                if w.itemData(i) == val or w.itemText(i) == str(val): idx = i; break
            if idx >= 0: w.setCurrentIndex(idx); return
        if hasattr(w, "_value_line"): getattr(w, "_value_line").setText(str(val)); return
        if isinstance(w, QLineEdit): w.setText(str(val)); return
        if isinstance(w, QTextEdit): w.setPlainText(str(val)); return

    def _set_values(self, values: Dict[str, Any]):
        for k, v in values.items():
            self._set_widget_value(k, v)

    def _preset_changed(self, idx: int):
        self.btnApplyPreset.setEnabled(idx > 0)

    def _apply_preset_clicked(self):
        name = self.cmbPreset.currentData()
        if name: self.apply_preset(name)

    def _field_changed(self, *args):
        if self._building: return
        self.update_visibility(); self.values_changed()

    def update_visibility(self):
        v = self.values()
        for f in self.fields:
            w = self.widgets.get(f.get("key"))
            if not w: continue
            visible = True
            cond = f.get("visible_if")
            if cond:
                for depk, depv in cond.items():
                    visible = (v.get(depk) == depv)
            w.setVisible(visible)

    def values_changed(self):
        _prog, _argv, pretty = self.build_command()
        self.txtCmd.setPlainText(pretty)
