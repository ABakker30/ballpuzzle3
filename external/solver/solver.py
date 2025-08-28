# solver.py — tiny driver around your fast engine + "world" exporters (JSON + layers.txt)
# Usage:
#   python solver.py [path\to\container.json]
# Defaults:
#   container = .\containers\Roof.json
#   pieces    = .\pieces\4sphere.orientations.py
#
# Stdout (minimal JSON):
#   { placed, total, solution: [...], attempts_per_sec, log_tail }
#
# Files written:
#   .\results\<ContainerName>.world.json
#   .\results\<ContainerName>.world_layers.txt
#   .\logs\progress.jsonl  (streaming snapshots every 5s, includes best_depth)
#   .\logs\progress.json   (final snapshot at end, includes best_depth)
#
# World coords follow your example:
#   u = j + k, v = i + k, w = i + j
#   (x,y,z) = d * (u, v, w)  where  d = r * sqrt(2)

from __future__ import annotations
import json, os, sys, time, math, hashlib, importlib.util, importlib.machinery
from collections import deque

# ---------- paths ----------
ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONTAINER = os.path.join(ROOT, "containers", "Roof.json")
PIECES_PATH       = os.path.join(ROOT, "pieces", "4sphere.orientations.py")
ENGINE_PATH       = os.path.join(ROOT, "solver_engine.py")  # your fast engine
RESULTS_DIR       = os.path.join(ROOT, "results")
LOGS_DIR          = os.path.join(ROOT, "logs")
PROGRESS_PATH     = os.path.join(LOGS_DIR, "progress.jsonl")
PROGRESS_FINAL_PATH = os.path.join(LOGS_DIR, "progress.json")

# ---------- io helpers ----------
def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def sha1_file(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def load_py_module(path: str, name: str):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec   = importlib.util.spec_from_loader(loader.name, loader)
    mod    = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod

def ensure_dir(p: str):
    try: os.makedirs(p, exist_ok=True)
    except Exception: pass

# ---------- pieces adapter (accepts VARIANTS or a variants-like dict) ----------
def _looks_like_variants_dict(obj) -> bool:
    if not isinstance(obj, dict) or not obj: return False
    c = 0
    for k, v in obj.items():
        if not isinstance(k, (str,int)): return False
        if not isinstance(v, (list,tuple)) or not v: return False
        for ori in list(v)[:2]:
            if not isinstance(ori, (list,tuple)) or len(ori) != 4: return False
            for cell in ori:
                if not (isinstance(cell, (list,tuple)) and len(cell) == 3 and all(isinstance(x,int) for x in cell)):
                    return False
        c += 1
        if c >= 3: break
    return True

def extract_pieces(mod):
    # Preferred name
    if hasattr(mod, "VARIANTS") and _looks_like_variants_dict(mod.VARIANTS):
        variants = dict(mod.VARIANTS)
    else:
        # Heuristic scan
        found = None
        for name, val in vars(mod).items():
            if _looks_like_variants_dict(val):
                found = val; break
        if found is None:
            raise RuntimeError("pieces: could not find a variants-like dict")
        variants = dict(found)
    # Normalize to engine format: dict[id] -> tuple( tuple(dx,dy,dz), ... ) per orientation
    pieces = {}
    for pid, oris in variants.items():
        norm_oris = []
        for ori in oris:
            norm_oris.append(tuple((int(a),int(b),int(c)) for (a,b,c) in ori))
        pieces[str(pid)] = tuple(norm_oris)
    return pieces

# ---------- minimal JSON (stdout) ----------
def build_minimal_result(engine, tail):
    placed = engine.placed_count()
    total  = engine.total_pieces()
    t = max(engine.elapsed_seconds(), 1e-9)
    aps = engine.attempts / t

    idx2cell = engine.idx2cell
    out = []
    for pl in engine.placements:
        out.append({
            "piece": pl["piece"],
            "variant": pl["ori_idx"],
            "anchor": list(idx2cell[pl["origin_idx"]]),
            "cells_ijk": [list(idx2cell[i]) for i in pl["cells_idx"]],
        })
    return {
        "placed": placed,
        "total": total,
        "solution": out,
        "attempts_per_sec": aps,
        "log_tail": list(tail)[-100:]
    }

# ---------- world coordinate helpers ----------
def ijk_to_world(i:int, j:int, k:int, r:float):
    # u=j+k, v=i+k, w=i+j; scale by d=r*sqrt(2)
    d = r * math.sqrt(2.0)
    u = j + k
    v = i + k
    w = i + j
    return [u * d, v * d, w * d]

# ---------- world JSON (example-compatible) ----------
def write_world_json(container_path: str, container_name: str, r: float, engine, dst_path: str):
    data = {
        "schema": "tetra_spheres_solution/1.0",
        "container_name": container_name,
        "container_path": container_path,
        "container_hash": sha1_file(container_path),
        "present": "square",
        "presentation": {
            "mode": "square",
            "frame": { "R": [[1.0,0.0,0.0],[0.0,1.0,0.0],[0.0,0.0,1.0]], "t": [0.0,0.0,0.0] }
        },
        "r": r,
    }

    # pieces_order (order of final placements)
    order = [pl["piece"] for pl in engine.placements]
    data["pieces_order"] = order

    # pieces array with cells_ijk + world_centers
    idx2cell = engine.idx2cell
    pieces = []
    for pl in engine.placements:
        cells_idx = pl["cells_idx"]
        cells_ijk = [list(idx2cell[i]) for i in cells_idx]
        world_centers = [ijk_to_world(i,j,k,r) for (i,j,k) in cells_ijk]
        pieces.append({
            "id": pl["piece"],
            "cells_ijk": cells_ijk,
            "world_centers": world_centers
        })
    data["pieces"] = pieces

    data["depth"] = engine.placed_count()
    data["timestamp"] = time.time()

    with open(dst_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ---------- layers.txt writer (mirroring & spacing aligned to your reference) ----------
def write_world_layers(container_cells, engine, r: float, dst_path: str):
    # Build map: cell -> piece id letter
    idx2cell = engine.idx2cell
    occ = {}
    for pl in engine.placements:
        pid = pl["piece"]
        for ci in pl["cells_idx"]:
            occ[tuple(idx2cell[ci])] = pid

    # Collect occupied ijk
    used = list(occ.keys())
    if not used:
        with open(dst_path, "w", encoding="utf-8") as f:
            f.write("[empty]\n")
        return

    # uvw helpers
    def uvw(i, j, k):
        return (j + k, i + k, i + j)

    us = []
    vs = []
    ws = []
    for (i, j, k) in used:
        u, v, w = uvw(i, j, k)
        us.append(u); vs.append(v); ws.append(w)
    umin, umax = min(us), max(us)
    vmin, vmax = min(vs), max(vs)
    wmin, wmax = min(ws), max(ws)

    lines = []
    lines.append("[SOLUTION — world view (ALL layers)]")
    lines.append(f"Legend: rows=v (i+k: {vmin}..{vmax}), cols=u (j+k: {umin}..{umax}), layers=w (i+j: {wmin}..{wmax})")
    lines.append("")

    for w in range(wmin, wmax + 1):
        lines.append(f"Layer w=i+j={w}:")
        lines.append("")  # blank line after header

        # Print rows top-down: v = vmax .. vmin
        # Print columns right-to-left: u = umax .. umin (matches your reference mirroring)
        for v in range(vmax, vmin - 1, -1):
            row = []
            for u in range(umax, umin - 1, -1):
                # invert uvw -> ijk:
                # u=j+k, v=i+k, w=i+j
                i2 = (v + w - u)
                j2 = (u + w - v)
                k2 = (u + v - w)
                if (i2 | j2 | k2) & 1:
                    row.append("  ")
                    continue
                i = i2 // 2; j = j2 // 2; k = k2 // 2
                pid = occ.get((i, j, k))
                if pid is None:
                    row.append("  ")
                else:
                    ch = str(pid)[0]  # one-letter IDs expected; if longer, first char
                    row.append(ch + " ")
            lines.append("".join(row).rstrip())
        lines.append("")

    with open(dst_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")

# ---------- final progress writer (includes best_depth) ----------
def write_final_progress(engine, status: str, best_depth: int):
    # status: "solved" or "exhausted"
    t = max(engine.elapsed_seconds(), 1e-9)
    snap = {
        "status": status,
        "placed": engine.placed_count(),
        "best_depth": int(best_depth),
        "total": engine.total_pieces(),
        "attempts": engine.attempts,
        "attempts_per_sec": engine.attempts / t,
        "elapsed_sec": t,
    }
    with open(PROGRESS_FINAL_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, separators=(",", ":"))

# ---------- main ----------
def main():
    container_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONTAINER
    ensure_dir(RESULTS_DIR); ensure_dir(LOGS_DIR)

    # Load inputs
    cont = load_json(container_path)
    r = float(cont.get("r", 0.5))
    cells = cont["cells"]
    valid_set = set(tuple(x) for x in cells)

    pieces_mod = load_py_module(PIECES_PATH, "pieces_module")
    pieces = extract_pieces(pieces_mod)

    # Load your engine
    eng_mod = load_py_module(ENGINE_PATH, "engine_module")
    SolverEngine = eng_mod.SolverEngine

    # Init + run with 5s progress
    engine = SolverEngine(pieces, valid_set)

    # ---- minimal addition: enable hole-%4 prune via CLI flag ----
    if any(arg in sys.argv for arg in ("--hole4", "--hole-mod4")):
        setattr(engine, "hole_mod4", True)
    # -------------------------------------------------------------

    progress = open(PROGRESS_PATH, "a", encoding="utf-8")
    tail = deque(maxlen=256)
    last = time.perf_counter()
    tail.append("[start] engine driver")

    # Track the highest placement depth for the run
    best_depth = 0
    solved = False

    while True:
        progressed, solved = engine.step_once()

        # Update best_depth whenever we see a new high-water mark
        placed_now = engine.placed_count()
        if placed_now > best_depth:
            best_depth = placed_now

        now = time.perf_counter()
        if now - last >= 5.0:
            rate = engine.attempts / max(engine.elapsed_seconds(), 1e-9)
            snap = {
                "placed": placed_now,
                "best_depth": int(best_depth),
                "total": engine.total_pieces(),
                "attempts": engine.attempts,
                "attempts_per_sec": rate,
                "elapsed_sec": engine.elapsed_seconds()
            }
            line = json.dumps(snap, separators=(",", ":"))
            print(f"[{snap['elapsed_sec']:7.2f}s] placed={snap['placed']}/{snap['total']} best={snap['best_depth']} attempts={snap['attempts']} rate={rate:,.0f}/s")
            progress.write(line + "\n"); progress.flush()
            tail.append(line)
            last = now

        if solved:
            tail.append("[done] solved")
            write_final_progress(engine, "solved", best_depth)
            break

        # Exhaust detection: no progress, at root, nothing placed
        if (not progressed) and engine.cursor == 0 and not engine.placements:
            tail.append("[done] exhausted")
            write_final_progress(engine, "exhausted", best_depth)
            break

    progress.close()

    # Write world artifacts (JSON + layers)
    container_name = os.path.splitext(os.path.basename(container_path))[0]
    world_json_path   = os.path.join(RESULTS_DIR, f"{container_name}.world.json")
    world_layers_path = os.path.join(RESULTS_DIR, f"{container_name}.world_layers.txt")

    write_world_json(container_path, container_name, r, engine, world_json_path)
    write_world_layers(cells, engine, r, world_layers_path)

    # Print the minimal JSON to stdout (lean interface)
    print(json.dumps(build_minimal_result(engine, tail), separators=(",", ":")))

if __name__ == "__main__":
    main()
