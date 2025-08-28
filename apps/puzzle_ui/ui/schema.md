# Viewer File Contracts (Locked for v0.1)

## 1) *.current.world.json  (in external/solver/results/)
Required:
- version: "0.1"
- space: "world"
- lattice: string (e.g., "FCC")
- r: number
- container_name: string
- run_id: string
- pieces: array of { id:int, name:string, centers:[[x,y,z], ...] }
- bbox: { min:[x,y,z], max:[x,y,z] }

Optional:
- t0: number (epoch seconds)
- container_cid_sha256, sid_state_sha256, sid_route_sha256, solution_sha256
- units, notes

Assumptions:
- Coordinates are already world-orthogonal. Viewer does no lattice math.
- One InstancedMesh per piece; one MeshBasicMaterial per piece (v0.1).

## 2) logs/progress.json and logs/progress.jsonl (in external/solver/logs/)
- progress.json: latest snapshot with keys:
  event, run (int) and/or run_id (string),
  placed, best_depth, total,
  attempts, attempts_per_sec, elapsed_sec (optional)

- progress.jsonl: append-only JSON lines; same shape per line.

Resilience:
- Missing fields → leave blank.
- Extra fields ignored.
- Debounce mtime/size before parsing to avoid partial writes.
