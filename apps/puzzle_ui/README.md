# Puzzle Solver UI — v0.1 Skeleton

Desktop UI for lattice puzzle solver. No solver/engine changes. Offline-first. Local three.js.

## Layout
- Top bar: Start / Pause / Stop / Refresh now, active file path, status chip
- Tabs:
  1) Solve (active): Controls + Stats (left), 3D Viewer (right, orthographic)
  2) Mint Solution (placeholder, disabled)
  3) Container Builder (placeholder, disabled)

## File contracts
See `apps/puzzle_ui/ui/schema.md`.

## Local three.js
Vendor ESM files into `viewer/libs/three/` (no CDN).

## Dev env
Create a venv and install requirements:
```
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install PySide6 PySide6-Addons
```

(Implementation comes later.)
