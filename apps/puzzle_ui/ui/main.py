# --- local package bootstrap (run directly OR as module) ---
import sys
import re
from pathlib import Path

# Precompiled regex for solver stdout lines like:
# [run 0 seed=42000] placed 22/25 | best 24 | rate 7301/s
STDOUT_PROGRESS_RE = re.compile(
    r"\[run\s+(?P<run>\d+)[^\]]*\]\s*placed\s+(?P<placed>\d+)\/(?P<total>\d+)\s*\|\s*best\s+(?P<best>\d+)\s*\|\s*rate\s+(?P<rate>[0-9.]+)\/s",
    re.IGNORECASE,
)

_UI_DIR = Path(__file__).resolve().parent
# If running as a script (no package), add the ui folder to sys.path
if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    if str(_UI_DIR) not in sys.path:
        sys.path.insert(0, str(_UI_DIR))
# Try relative imports first (module mode), fall back to local (script mode)
try:
    from .components.solve_tab import SolveTab  # type: ignore
    from .utils import repo_root, win_quote       # type: ignore
except Exception:  # ImportError or ValueError (no package)
    from components.solve_tab import SolveTab
    from utils import repo_root, win_quote
# ---------------------------------------------------------------------------

from typing import Optional
from PySide6.QtCore import Qt, QProcess
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QTabWidget
)

APP_TITLE = "Puzzle Solver UI — v0.1"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1400, 880)

        self.proc: Optional[QProcess] = None
        self.process_running = False
        self.viewer_paused = False
        self._tb_paused = False

        self._build_ui()

    def _build_ui(self):
        central = QWidget(self); self.setCentralWidget(central)
        v = QVBoxLayout(central); v.setContentsMargins(8,8,8,8); v.setSpacing(8)

        # Top bar
        top = QHBoxLayout()
        self.btnStart = QPushButton("Start")
        self.actPause = QPushButton("Pause viewer")
        self.btnStop  = QPushButton("Stop")
        self.btnRefreshTop = QPushButton("Refresh viewer")
        self.btnOpen = QPushButton("Open .current.world.json…")
        self.lblStatus = QLabel("Status: Idle"); self.lblStatus.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        for w in (self.btnStart, self.actPause, self.btnStop, self.btnRefreshTop, self.btnOpen):
            top.addWidget(w)
        top.addStretch(1); top.addWidget(self.lblStatus)

        # Tabs
        tabs = QTabWidget(self)
        self.solve_tab = SolveTab(self)
        tabs.addTab(self.solve_tab, "Solve")

        mint = QWidget(self); mv = QVBoxLayout(mint)
        mv.addWidget(QLabel("Mint Solution (placeholder)\n\nWill collect metadata, connect wallet, submit tx, and show receipt.", mint)); mv.addStretch(1)
        tabs.addTab(mint, "Mint Solution")

        builder = QWidget(self); bv = QVBoxLayout(builder)
        bv.addWidget(QLabel("Container Builder (placeholder)\n\nWill author/validate container JSON.", builder)); bv.addStretch(1)
        tabs.addTab(builder, "Container Builder")

        v.addLayout(top); v.addWidget(tabs, 1)

        # wiring
        self.btnRefreshTop.clicked.connect(self.solve_tab.refresh_all)
        self.btnOpen.clicked.connect(self._pick_world_file)
        self.btnStart.clicked.connect(self._start_solver)
        self.btnStop.clicked.connect(self._stop_solver)
        self.actPause.clicked.connect(self._toggle_viewer_pause)

        self.actStart = QPushButton("Start")
        self.actStop = QPushButton("Stop")
        self.actStart.clicked.connect(self._start_solver)
        self.actStop.clicked.connect(self._stop_solver)

        self.actStart.triggered.connect(self._tb_onStart)
        self.actStop.triggered.connect(self._tb_onStop)

        try:
            self.actPause.triggered.disconnect()
        except Exception:
            pass
        self.actPause.triggered.connect(self._tb_onPauseResume)

        self._update_buttons()

    # ---------- Actions ----------
    def _pick_world_file(self):
        start_dir = str((repo_root() / "samples").resolve())
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open world JSON", start_dir,
            "World JSON (*.current.world.json *.json);;All Files (*)",
        )
        if not file_path: return
        from pathlib import Path as _P
        self.solve_tab.open_world_file(_P(file_path))
        self.lblStatus.setText("Status: File loaded")

    def _start_solver(self):
        if self.process_running:
            return

        program, argv, _ = self.solve_tab.opts.build_command()

        vals = self.solve_tab.opts.values()
        container = vals.get("container")
        if not container:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Missing container", "Please select a Container JSON.")
            return

        from pathlib import Path as _P
        prog_abs = _P(program)
        if not prog_abs.is_absolute():
            prog_abs = (repo_root() / prog_abs).resolve()
        if len(argv) >= 1:
            argv[0] = str(_P(argv[0]).expanduser().resolve())   # solver/wrapper
        if len(argv) >= 2 and not argv[1].startswith("-"):
            argv[1] = str(_P(argv[1]).expanduser().resolve())   # container

        pretty = " ".join([win_quote(str(prog_abs))] + [win_quote(a) for a in argv])
        self._append_status(f"Launching: {pretty}")

        # logs dir for follower
        logs_val = vals.get("logs_dir")
        logs_path = (_P(logs_val).resolve() if logs_val else (_P(argv[0]).parent / "logs"))
        self.solve_tab.set_logs_dir(logs_path)

        # working dir = parent of script (wrapper or real solver)
        workdir = str(_P(argv[0]).parent)

        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(workdir)
        self.proc.readyReadStandardOutput.connect(lambda: self._drain_output("stdout"))
        self.proc.readyReadStandardError.connect(lambda: self._drain_output("stderr"))
        self.proc.finished.connect(self._proc_finished)

        try:
            self.proc.start(str(prog_abs), argv)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Launch error", f"Failed to start solver:\n{e}")
            self.proc = None; return

        if not self.proc.waitForStarted(3000):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Launch error", "Solver failed to start.")
            self.proc = None; return

        self.process_running = True
        self._tb_onStart()
        self.lblStatus.setText("Status: Running")
        self._update_buttons()

    def _stop_solver(self):
        if not (self.proc and self.process_running):
            return
        self._append_status("Stopping solver...")
        self.proc.terminate()
        if not self.proc.waitForFinished(5000):
            self._append_status("Force-killing solver...")
            self.proc.kill(); self.proc.waitForFinished(2000)
        self.process_running = False
        self._tb_onStop()
        self.lblStatus.setText("Status: Stopped")
        self._update_buttons()

    def _toggle_viewer_pause(self):
        self._tb_onPauseResume()

    def _proc_finished(self, code: int, status):
        self.process_running = False
        self._tb_onStop()
        self.lblStatus.setText(f"Status: Exited ({code})")
        self._update_buttons()
        self._append_status(f"Solver exited with code {code}")

    def _drain_output(self, which: str):
        if not self.proc: return
        stream = self.proc.readAllStandardOutput() if which == "stdout" else self.proc.readAllStandardError()
        text = bytes(stream).decode(errors="ignore")
        if text.strip():
            last = text.strip().splitlines()[-1]
            self._append_status(last)
            self._parse_and_apply_stdout(text)

    def _parse_and_apply_stdout(self, text: str):
        for ln in text.splitlines()[::-1]:  # search most recent first
            m = STDOUT_PROGRESS_RE.search(ln)
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
        self.actPause.setEnabled(self.process_running)

    def _tb_onStart(self):
        # a run has been requested; enable Pause, reset label
        self._tb_paused = False
        self.actPause.setText("Pause")
        self.actPause.setEnabled(True)

    def _tb_onStop(self):
        # run ended; disable Pause and reset label
        self._tb_paused = False
        self.actPause.setText("Pause")
        self.actPause.setEnabled(False)

    def _tb_onPauseResume(self):
        # Toggle paused flag, call existing solver pause/resume, flip label
        if not self.actPause.isEnabled():
            return
        self._tb_paused = not self._tb_paused

        # Call whichever pause hook you already have (no viewer calls here)
        try:
            if hasattr(self, "pause_solver"):
                self.pause_solver(self._tb_paused)
            elif hasattr(self, "solve_tab") and hasattr(self.solve_tab, "pause_solver"):
                self.solve_tab.pause_solver(self._tb_paused)
            # else: no-op (keeps UI consistent)
        except Exception:
            pass

        self.actPause.setText("Resume" if self._tb_paused else "Pause")


def main():
    app = QApplication(sys.argv)
    win = MainWindow(); win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
