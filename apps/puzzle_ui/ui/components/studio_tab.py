from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QLabel, QFileDialog, QSlider, QMessageBox
)
from PySide6.QtWebEngineWidgets import QWebEngineView
import json
import os
import base64

class StudioTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StudioTab")

        # --- Top controls row (Open + Color Strategy + Brightness)
        row = QHBoxLayout()
        self.btnOpen = QPushButton("Open file…")
        self.cmbColors = QComboBox()
        self.cmbColors.setMinimumWidth(220)

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
        self.cmbColors.setCurrentIndex(0)

        # Brightness slider (10%..300%, default 100%)
        self.sldBright = QSlider(Qt.Horizontal)
        self.sldBright.setRange(10, 300)
        self.sldBright.setValue(100)
        self.sldBright.setSingleStep(1)
        self.sldBright.setFixedWidth(160)

        self.btnSave = QPushButton("Save PNG…")
        row.addSpacing(12)
        row.addWidget(self.btnSave, 0, Qt.AlignLeft)

        row.addWidget(self.btnOpen, 0, Qt.AlignLeft)
        row.addWidget(QLabel("Colors:"))
        row.addWidget(self.cmbColors, 0, Qt.AlignLeft)
        row.addSpacing(12)
        row.addWidget(QLabel("Brightness:"))
        row.addWidget(self.sldBright, 0, Qt.AlignLeft)
        row.addStretch(1)

        # --- Web view (isolated Studio viewer)
        self.web = QWebEngineView(self)
        studio_index = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "studio", "index.html")
        )
        self.web.setUrl(f"file:///{studio_index.replace(os.sep, '/')}" )

        # --- Layout
        col = QVBoxLayout(self)
        col.addLayout(row)
        col.addWidget(self.web, 1)

        # --- Wiring
        self.btnOpen.clicked.connect(self._on_open)
        self.cmbColors.currentIndexChanged.connect(
            lambda _:
                self.web.page().runJavaScript(
                    f'(window.setColorStrategy ? setColorStrategy("{self.cmbColors.currentData()}") : undefined)'
                )
        )
        self.sldBright.valueChanged.connect(
            lambda v: self.web.page().runJavaScript(f'(window.setStudioBrightness ? setStudioBrightness({v}/100.0) : undefined)')
        )
        self.web.loadFinished.connect(
            lambda ok: self.web.page().runJavaScript(
                f'(window.setColorStrategy ? setColorStrategy("{self.cmbColors.currentData()}") : undefined);'
                f'(window.setStudioBrightness ? setStudioBrightness({self.sldBright.value()/100.0}) : undefined);'
            )
        )
        self.btnSave.clicked.connect(self._on_save_png)

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open geometry JSON", "", "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            print(f"[Studio] Failed to read file: {e}")
            return

        # Pass raw text (Studio normalizes internally)
        payload = json.dumps(text)
        self.web.page().runJavaScript(f'studioLoadJson({payload})')

    def _on_save_png(self):
        # Choose path
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PNG", "studio.png", "PNG Image (*.png)"
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"

        # Export scale (2x is a good default). You can make this user-configurable later.
        scale = 2
        js = f'(window.studioCapturePng ? studioCapturePng({scale}) : null)'

        def _write_png(data_url):
            if not data_url or not isinstance(data_url, str) or not data_url.startswith("data:image/png;base64,"):
                QMessageBox.warning(self, "Export", "PNG capture failed.")
                return
            b64 = data_url.split(",", 1)[1]
            try:
                with open(path, "wb") as f:
                    f.write(base64.b64decode(b64))
            except Exception as e:
                QMessageBox.critical(self, "Export", f"Failed to write file:\n{e}")
                return
            # Optional: small success toast
            print(f"[Studio] PNG saved: {path}")

        # Run JS and get the data URL back
        self.web.page().runJavaScript(js, _write_png)
