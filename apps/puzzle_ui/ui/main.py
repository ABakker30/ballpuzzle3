# --- local package bootstrap (run directly OR as module) ---
import sys
import re
import os, json, time
from pathlib import Path

# Allow running this file directly: python apps\puzzle_ui\ui\main.py
import os, sys
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Precompiled regex for solver stdout lines like:
# [run 0 seed=42000] placed 22/25 | best 24 | rate 7301/s
STDOUT_PROGRESS_RE = re.compile(
    r"\[run\s+(?P<run>\d+)[^\]]*\]\s*placed\s+(?P<placed>\d+)\/(?P<total>\d+)\s*\|\s*best\s+(?P<best>\d+)\s*\|\s*rate\s+(?P<rate>[0-9.]+)\/s",
    re.IGNORECASE,
)

_UI_DIR = Path(__file__).resolve().parent
# If running as a script (no package), add the ui folder to sys.path
if __package__ is None or __package__ == "":
    if str(_UI_DIR) not in sys.path:
        sys.path.insert(0, str(_UI_DIR))
# Try relative imports first (module mode), fall back to local (script mode)
try:
    from .components.solve_tab import SolveTab  # type: ignore
    from .components.shape_tab import ShapeTab  # type: ignore
    from .utils import repo_root, win_quote       # type: ignore
except Exception:  # ImportError or ValueError (no package)
    from components.solve_tab import SolveTab
    from components.shape_tab import ShapeTab
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

        # Clean up any leftover solver processes from previous sessions
        self._cleanup_existing_solvers()

        self.proc: Optional[QProcess] = None
        self.process_running = False
        self.solver_paused = False
        self._runctl_path: Optional[str] = None  # logs/runctl.json for the active run
        self._current_container_path: Optional[str] = None  # Track current container for companion file detection

        self._world_watch_timer = QTimer(self)
        self._world_watch_timer.setInterval(1000)  # 1s poll, cheap
        self._world_watch_timer.timeout.connect(self._poll_world_json)
        
        # Companion file detection timer
        self._companion_watch_timer = QTimer(self)
        self._companion_watch_timer.setInterval(2000)  # Check every 2 seconds during solver runs
        self._companion_watch_timer.timeout.connect(self._check_for_companion_file)
        self._last_world_mtime = 0.0
        self._world_path_cache = ""  # absolute path to *.current.world.json

        self._build_ui()

    def _cleanup_existing_solvers(self):
        """Kill any leftover solver processes from previous UI sessions."""
        import subprocess
        try:
            print("[UI DEBUG] Cleaning up existing solver processes...")
            
            # Find all python processes running run_solver.py
            result = subprocess.run([
                'wmic', 'process', 'where', 
                'name="python.exe" and CommandLine like "%run_solver.py%"', 
                'get', 'ProcessId,CommandLine'
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                pids_to_kill = []
                
                for line in lines[1:]:  # Skip header
                    if 'run_solver.py' in line and line.strip():
                        # Extract PID from the line
                        parts = line.strip().split()
                        if parts and parts[-1].isdigit():
                            pid = parts[-1]
                            pids_to_kill.append(pid)
                            print(f"[UI DEBUG] Found leftover solver process PID: {pid}")
                
                # Kill the processes
                if pids_to_kill:
                    for pid in pids_to_kill:
                        try:
                            subprocess.run(['taskkill', '/PID', pid, '/F'], 
                                         capture_output=True, timeout=5)
                            print(f"[UI DEBUG] Killed solver process PID: {pid}")
                        except Exception as e:
                            print(f"[UI DEBUG] Failed to kill PID {pid}: {e}")
                    print(f"[UI DEBUG] Cleaned up {len(pids_to_kill)} leftover solver processes")
                else:
                    print("[UI DEBUG] No leftover solver processes found")
            else:
                print("[UI DEBUG] No solver processes found to clean up")
                
        except Exception as e:
            print(f"[UI DEBUG] Error during solver cleanup: {e}")

    def _stop_solver(self):
        """Cooperative stop (runctl='stop') + process teardown."""
        print("[UI DEBUG] Stop button clicked")
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
            self.solve_tab.btnSolver.setText("▶ Start")
            self._set_status("Stopped")
            self._update_buttons()
            self._world_watch_timer.stop()
            self._companion_watch_timer.stop()

    # ---------- UI build ----------
    def _build_ui(self):
        central = QWidget(self); self.setCentralWidget(central)
        v = QVBoxLayout(central); v.setContentsMargins(8, 8, 8, 8); v.setSpacing(8)

        # Top bar - just status now
        top = QHBoxLayout()
        self.lblStatus = QLabel("Status: Idle"); self.lblStatus.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        top.addStretch(1); top.addWidget(self.lblStatus)

        # Tabs
        self.tabs = QTabWidget(self)
        self.solve_tab = SolveTab(self)
        self.tabs.addTab(self.solve_tab, "Solve")
        from apps.puzzle_ui.ui.components.studio_tab import StudioTab
        self.tabs.addTab(StudioTab(self), "Studio")
        
        # Shape editor tab
        self.shape_tab = ShapeTab(self)
        self.tabs.addTab(self.shape_tab, "Shape")

        mint = QWidget(self); mv = QVBoxLayout(mint)
        mv.addWidget(QLabel("Mint Solution (placeholder)\n\nWill collect metadata, connect wallet, submit tx, and show receipt.", mint)); mv.addStretch(1)
        self.tabs.addTab(mint, "Mint Solution")

        v.addLayout(top); v.addWidget(self.tabs, 1)

        # wiring - connect solve tab button to main window handler
        self.solve_tab.btnSolver.clicked.connect(self._handle_solver_button)

        self._update_buttons()

    def _handle_solver_button(self):
        """Handle single solver button click - starts, pauses, or resumes solver."""
        print("[UI DEBUG] Solver button clicked")
        
        if not self.process_running:
            # Not running - start the solver
            print("[UI DEBUG] Starting solver...")
            self.solve_tab.reset_progress_ui()
            self._start_solver()
        elif self.solver_paused:
            # Paused - resume the solver
            print("[UI DEBUG] Resuming solver...")
            self._write_runctl("run")
            self.solver_paused = False
            self._set_status("Resumed")
            self._update_buttons()
        else:
            # Running - pause the solver
            print("[UI DEBUG] Pausing solver...")
            self._write_runctl("pause")
            self.solver_paused = True
            self._set_status("Paused")
            self._update_buttons()

    # ---------- Start / Pause / Stop ----------
    def _start_solver(self):
        print("[UI DEBUG] _start_solver called")
        if self.process_running:
            print("[UI DEBUG] Process already running, returning")
            return

        program, argv, _ = self.solve_tab.opts.build_command()
        print(f"[UI DEBUG] Got command: {program} {argv}")

        vals = self.solve_tab.opts.values()
        container = vals.get("container")
        print(f"[UI DEBUG] Container: {container}")
        if not container:
            print("[UI DEBUG] No container selected, showing warning")
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Missing container", "Please select a Container JSON.")
            return

        # Track current container for companion file detection
        self._current_container_path = container

        from pathlib import Path as _P
        prog_abs = _P(program)
        if not prog_abs.is_absolute():
            prog_abs = (repo_root() / prog_abs).resolve()
        print(f"[UI DEBUG] Program path: {prog_abs}")
        
        if len(argv) >= 1:
            argv[0] = str(_P(argv[0]).expanduser().resolve())   # solver/wrapper
        if len(argv) >= 2 and not argv[1].startswith("-"):
            argv[1] = str(_P(argv[1]).expanduser().resolve())   # container

        pretty = " ".join([win_quote(str(prog_abs))] + [win_quote(a) for a in argv])
        print(f"[UI DEBUG] About to launch: {pretty}")
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
            print(f"[UI DEBUG] Starting process: {prog_abs} with args {argv}")
            self.proc.start(str(prog_abs), argv)
            print("[UI DEBUG] Process.start() called successfully")
        except Exception as e:
            print(f"[UI DEBUG] Exception during start: {e}")
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Launch error", f"Failed to start solver:\n{e}")
            self.proc = None; return

        print("[UI DEBUG] Waiting for process to start...")
        if not self.proc.waitForStarted(3000):
            print("[UI DEBUG] Process failed to start within timeout")
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Launch error", "Solver failed to start.")
            self.proc = None; return

        print("[UI DEBUG] Process started successfully, updating UI state")
        self.process_running = True
        self.solver_paused = False
        self.solve_tab.btnSolver.setText("⏸ Pause")
        self._set_status("Running")
        self._update_buttons()
        self._world_watch_timer.start()
        self._companion_watch_timer.start()  # Start watching for companion file creation
        print("[UI DEBUG] UI state updated, solver should be running")



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


    # ---------- Process I/O ----------
    def _proc_finished(self, code: int, status):
        print(f"[UI DEBUG] _proc_finished called: code={code}, status={status}")
        self.process_running = False
        self.solver_paused = False
        self.solve_tab.btnSolver.setText("▶ Start")
        self._set_status(f"Exited ({code})")
        self._update_buttons()
        self._world_watch_timer.stop()
        self._companion_watch_timer.stop()

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
        print(f"[UI DEBUG] _update_buttons: process_running={self.process_running}, solver_paused={self.solver_paused}")
        self.solve_tab.btnSolver.setEnabled(True)  # Always enabled - handles start/pause/resume
        print(f"[UI DEBUG] Button state: Solver={self.solve_tab.btnSolver.isEnabled()}")
        
        # Update solver button text based on state
        if not self.process_running:
            self.solve_tab.btnSolver.setText("▶ Start")
        elif self.solver_paused:
            self.solve_tab.btnSolver.setText("▶ Resume")
        else:
            self.solve_tab.btnSolver.setText("⏸ Pause")

    def _check_for_companion_file(self):
        """Check if companion file has been created during solver run and auto-load it."""
        if not self._current_container_path or not self.process_running:
            return
            
        try:
            from pathlib import Path
            container_file = Path(self._current_container_path)
            container_stem = container_file.stem
            companion_name = f"{container_stem}.current.world.json"
            
            # Check if companion file exists in solver results directory
            results_path = repo_root() / "external" / "solver" / "results" / companion_name
            
            if results_path.exists():
                # Check if this file is already loaded in the viewer
                current_world_file = getattr(self.solve_tab, '_current_world_file', None)
                if current_world_file != results_path:
                    print(f"[UI DEBUG] New companion file detected during solver run: {companion_name}")
                    self.solve_tab.open_world_file(results_path)
                    print(f"[UI DEBUG] Auto-loaded companion file: {results_path}")
                    
        except Exception as e:
            print(f"[UI DEBUG] Error checking for companion file: {e}")

    def _poll_world_json(self):
        p = self._current_world_path()
        if not p or not os.path.isfile(p):
            return
        try:
            mt = os.path.getmtime(p)
        except Exception:
            return
        if p != self._world_path_cache:
            # new file context - reset cache when file changes
            self._world_path_cache = p
            self._last_world_mtime = 0.0
        if mt > self._last_world_mtime:
            self._last_world_mtime = mt
            # Only refresh if we have a valid current world file loaded
            # This prevents refreshing old files after container selection
            current_world_file = getattr(self.solve_tab, '_current_world_file', None)
            if current_world_file and str(current_world_file) == p:
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
        # Return the path of the currently loaded world file in the solve tab
        if hasattr(self.solve_tab, '_current_world_file') and self.solve_tab._current_world_file:
            return str(self.solve_tab._current_world_file)
        return None


def main():
    app = QApplication(sys.argv)
    win = MainWindow(); win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
