# --- local package bootstrap (run directly OR as module) ---
import sys
import re
import os, json, time
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
from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer
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
        self.solver_paused = False
        self._runctl_path: Optional[str] = None  # logs/runctl.json for the active run

        self._world_watch_timer = QTimer(self)
        self._world_watch_timer.setInterval(1000)  # 1s poll, cheap
        self._world_watch_timer.timeout.connect(self._poll_world_json)
        self._last_world_mtime = 0.0
        self._world_path_cache = ""  # absolute path to *.current.world.json

        self._build_ui()

    def _stop_solver(self):
        """Cooperative stop (runctl='stop') + process teardown."""
        if not (self.proc and self.process_running):
            return
        # tell the solver to stop at the next safe point
        self._write_runctl("stop")
        self._set_status("Stopping solver...")

        # terminate the child; kill if needed
        try:
            self.proc.terminate()
            if not self.proc.waitForFinished(5000):
                self._set_status("Force-killing solver...")
                self.proc.kill()
                self.proc.waitForFinished(2000)
        finally:
            self.process_running = False
            self.solver_paused = False
            self.btnPause.setText("Pause")
            self._set_status("Stopped")
            self._update_buttons()

    # ---------- UI build ----------
    def _build_ui(self):
        central = QWidget(self); self.setCentralWidget(central)
        v = QVBoxLayout(central); v.setContentsMargins(8, 8, 8, 8); v.setSpacing(8)

        # Top bar
        top = QHBoxLayout()
        self.btnStart = QPushButton("Start")
        self.btnPause = QPushButton("Pause")      # solver-only Pause/Resume
        self.btnStop  = QPushButton("Stop")
        self.btnRefreshTop = QPushButton("Refresh viewer")
        self.btnOpen = QPushButton("Open .current.world.json…")
        self.lblStatus = QLabel("Status: Idle"); self.lblStatus.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        for w in (self.btnStart, self.btnPause, self.btnStop, self.btnRefreshTop, self.btnOpen):
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
        self.btnRefreshTop.clicked.connect(self.solve_tab.refresh_all)  # callable slot is fine
        self.btnOpen.clicked.connect(self._pick_world_file)
        self.btnStart.clicked.connect(self._start_solver)
        self.btnStop.clicked.connect(self._stop_solver)
        self.btnPause.clicked.connect(self._toggle_pause_resume)

        self._update_buttons()

    # ---------- Start / Pause / Stop ----------
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
        self._set_status(f"Launching: {pretty}")

        # logs dir for follower
        logs_val = vals.get("logs_dir")
        logs_path = (_P(logs_val).resolve() if logs_val else (_P(argv[0]).parent / "logs"))
        self.solve_tab.set_logs_dir(logs_path)

        # working dir = parent of script (wrapper or real solver)
        workdir = str(_P(argv[0]).parent)
        self._runctl_path = self._compute_runctl_path(workdir)
        # ensure solver is in run mode (init/create file)
        self._write_runctl("run")

        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("RUNCTL_OVERRIDE", self._runctl_path)  # this is <workdir>\logs\runctl.json
        self.proc.setProcessEnvironment(env)
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
        self.solver_paused = False
        self.btnPause.setText("Pause")
        self._set_status("Running")
        self._update_buttons()
        self._world_watch_timer.start()

    def _stop_solver(self):
        if not (self.proc and self.process_running):
            return
        self._set_status("Stopping solver...")
        # cooperative stop for the new solver
        self._write_runctl("stop")
        # then normal process teardown
        self.proc.terminate()
        if not self.proc.waitForFinished(5000):
            self._set_status("Force-killing solver...")
            self.proc.kill(); self.proc.waitForFinished(2000)
        self.process_running = False
        self.solver_paused = False
        self.btnPause.setText("Pause")
        self._set_status("Stopped")
        self._update_buttons()
        self._world_watch_timer.stop()

    def _toggle_pause_resume(self):
        if not self.process_running:
            return
        want_pause = not self.solver_paused
        ok = self._write_runctl("pause" if want_pause else "run")
        if ok:
            self.solver_paused = want_pause
            self.btnPause.setText("Resume" if self.solver_paused else "Pause")
            self._set_status("Paused puzzling" if self.solver_paused else "Resumed puzzling")
        self._update_buttons()

    # ---------- Small helpers ----------
    def _set_status(self, msg: str, transient_ms: int = 0):
        try:
            self.statusBar().showMessage(msg, transient_ms)
        except Exception:
            pass
        self.lblStatus.setText(f"Status: {msg}")

    def _compute_runctl_path(self, workdir: str) -> str:
        # write runctl next to the solver script (workdir/logs/runctl.json)
        self._runctl_path = os.path.join(workdir, "logs", "runctl.json")
        # Optional: surface the path for debugging
        self._set_status(f"UI runctl = {self._runctl_path}", 1200)
        return self._runctl_path

    def _write_runctl(self, state: str) -> bool:
        """Write {"state": ...} to logs/runctl.json only if it changes."""
        path = self._runctl_path or os.path.join(str(repo_root()), "logs", "runctl.json")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)

            # Read current state (if any)
            cur = None
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cur = (json.load(f) or {}).get("state")
            except Exception:
                cur = None

            if cur == state:
                try: self.statusBar().showMessage(f"runctl unchanged ({state})", 800)
                except Exception: pass
                return True

            with open(path, "w", encoding="utf-8") as f:
                json.dump({"state": str(state), "ts": time.time()}, f, ensure_ascii=False)
            try: self.statusBar().showMessage(f"runctl → {state}", 1200)
            except Exception: pass
            return True
        except Exception as e:
            try: self.statusBar().showMessage(f"runctl write failed: {e}", 3000)
            except Exception: pass
            return False

    def _pick_world_file(self):
        start_dir = str((repo_root() / "samples").resolve())
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open world JSON", start_dir,
            "World JSON (*.current.world.json *.world.json *.json);;All Files (*)",
        )
        if not file_path:
            return
        from pathlib import Path as _P
        self.solve_tab.open_world_file(_P(file_path))
        self._set_status("File loaded")

    # ---------- Process I/O ----------
    def _proc_finished(self, code: int, status):
        self.process_running = False
        self.solver_paused = False
        self.btnPause.setText("Pause")
        self._set_status(f"Exited ({code})")
        self._update_buttons()
        self._world_watch_timer.stop()

    def _drain_output(self, which: str):
        if not self.proc:
            return
        stream = self.proc.readAllStandardOutput() if which == "stdout" else self.proc.readAllStandardError()
        text = bytes(stream).decode(errors="ignore")
        if text.strip():
            last = text.strip().splitlines()[-1]
            self._set_status(last)
            self._parse_and_apply_stdout(text)

    def _parse_and_apply_stdout(self, text: str):
        # Parse newest-to-oldest; stop on first match
        for ln in text.splitlines()[::-1]:
            m = STDOUT_PROGRESS_RE.search(ln)
            if not m:
                continue
            try:
                self.solve_tab.lblRun.setText(m.group("run"))
                self.solve_tab.lblPlaced.setText(f"{m.group('placed')} / {m.group('total')}")
                self.solve_tab.lblBest.setText(m.group("best"))
                self.solve_tab.lblRate.setText(m.group("rate"))
            except Exception:
                pass
            break

    # ---------- Button states ----------
    def _update_buttons(self):
        self.btnStart.setEnabled(not self.process_running)
        self.btnPause.setEnabled(self.process_running)
        self.btnStop.setEnabled(self.process_running or self.solver_paused)
        # Normalize Pause label when leaving running state
        if not self.process_running and self.btnPause.text() != "Pause":
            self.btnPause.setText("Pause")

    def _poll_world_json(self):
        p = self._current_world_path()
        if not p or not os.path.isfile(p):
            return
        try:
            mt = os.path.getmtime(p)
        except Exception:
            return
        if p != self._world_path_cache:
            # new file context
            self._world_path_cache = p
            self._last_world_mtime = 0.0
        if mt > self._last_world_mtime:
            self._last_world_mtime = mt
            # Use the same path your Refresh viewer button/slot uses
            try:
                # reuse your existing refresh path to avoid refits
                if hasattr(self.solve_tab, "refresh_all"):
                    self.solve_tab.refresh_all()
                else:
                    # fallback to the top-bar button if wired there
                    self.btnRefreshTop.click()
            except Exception:
                pass

    def _current_world_path(self):
        # implement logic to get the current world path
        pass


def main():
    app = QApplication(sys.argv)
    win = MainWindow(); win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
