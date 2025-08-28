# run_solver.py (at repo root)
#!/usr/bin/env python3
from pathlib import Path
import sys, os, runpy

repo = Path(__file__).resolve().parent
solver = repo / "external" / "solver" / "solver.py"
if not solver.exists():
    sys.stderr.write(f"Solver not found: {solver}\n")
    sys.exit(2)

# Find the container: first token that isn't an option (doesn't start with '-')
args = sys.argv[1:]
container = None
rest = []
for tok in args:
    if container is None and not tok.startswith("-"):
        container = tok
    else:
        rest.append(tok)

if container is None:
    sys.stderr.write("Usage: run_solver.py <container.json> [args...]\n")
    sys.exit(2)

# Resolve container BEFORE changing cwd
container_abs = str((Path.cwd() / container).resolve())

# Run from the solver folder so relative paths behave
os.chdir(str(solver.parent))

# Build argv for the real solver: [solver.py, container, ...flags...]
sys.argv = [str(solver), container_abs] + rest

# Execute solver.py as __main__
runpy.run_path(str(solver), run_name="__main__")
