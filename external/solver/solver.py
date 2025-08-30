# solver.py — FCC tetra-spheres puzzle driver
# rev 4.0 — adds cooperative run-control (pause/resume/stop via logs/runctl.json),
#           retains: fresh-engine-per-run, snapshots (atomic + retry, non-blocking),
#           deterministic shuffle, opener rotation, stall windows,
#           hole4 pruning (optional / conditional), layered ASCII/JSON outputs,
#           and console progress echo.

from __future__ import annotations
import argparse
import json
import os
import time
import hashlib
import importlib.util
import importlib.machinery
from collections import deque
from typing import Dict, List, Tuple, Set

# ---------- paths ----------
ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONTAINER = os.path.join(ROOT, "containers", "Roof.json")
PIECES_PATH = os.path.join(ROOT, "pieces", "4sphere.orientations.py")
ENGINE_PATH = os.path.join(ROOT, "solver_engine.py")

RESULTS_DIR = os.path.join(ROOT, "results")
LOGS_DIR = os.path.join(ROOT, "logs")
PROGRESS_PATH = os.path.join(LOGS_DIR, "progress.json")
PROGRESS_STREAM = os.path.join(LOGS_DIR, "progress.jsonl")
RUNCTL_PATH = os.environ.get("RUNCTL_OVERRIDE") or os.path.join(LOGS_DIR, "runctl.json")


def ensure_dir(p: str):
    if not os.path.isdir(p):
        os.makedirs(p, exist_ok=True)


# ---------- IO helpers ----------
def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _file_sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def sha1_file(path: str) -> str:
    return _file_sha1(path)

def load_py_module(path: str, name: str):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ---------- run control (pause/resume/stop) ----------
_runctl_cache_mtime = -1.0
_runctl_state = "run"

def _init_runctl():
    """Create runctl.json if missing with state=run (idempotent)."""
    ensure_dir(LOGS_DIR)
    if not os.path.exists(RUNCTL_PATH):
        try:
            with open(RUNCTL_PATH, "w", encoding="utf-8") as f:
                json.dump({"state": "run", "ts": time.time()}, f, ensure_ascii=False)
        except Exception:
            pass
    print(f"[runctl] solver RUNCTL_PATH = {RUNCTL_PATH}", flush=True)

def _read_runctl_state():
    """Cheap poll: only re-read file if mtime changed. Returns 'run'|'pause'|'stop'."""
    global _runctl_cache_mtime, _runctl_state
    try:
        m = os.path.getmtime(RUNCTL_PATH)
    except Exception:
        return _runctl_state
    if m != _runctl_cache_mtime:
        _runctl_cache_mtime = m
        try:
            with open(RUNCTL_PATH, "r", encoding="utf-8") as f:
                s = json.load(f)
            _runctl_state = str(s.get("state", "run")).lower()
        except Exception:
            _runctl_state = "run"
    return _runctl_state

def _emit_event_line(payload: dict):
    """Append a single JSON line to progress.jsonl (best effort)."""
    ensure_dir(LOGS_DIR)
    try:
        with open(PROGRESS_STREAM, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------- pieces adapter ----------
def _looks_like_variants_dict(obj) -> bool:
    if not isinstance(obj, dict) or not obj:
        return False
    for _, v in obj.items():
        if not isinstance(v, (list, tuple)) or not v:
            return False
        ori = v[0]
        if not (isinstance(ori, (list, tuple)) and len(ori) == 4 and
                all(isinstance(x, (list, tuple)) and len(x) == 3 for x in ori)):
            return False
    return True

def extract_pieces(pieces_mod) -> Dict[str, List[List[Tuple[int, int, int]]]]:
    """
    Accepts either:
      1) { 'A': [ [(i,j,k)x4], [(i,j,k)x4], ... ], 'B': [...], ... }
      2) { 'A__0': [(i,j,k)x4], 'A__1': [...], 'B__0': [...], ... }  -> bucket by prefix
    """
    data = getattr(pieces_mod, "PIECES", None)
    if data is None:
        raise ValueError("pieces module must define PIECES")
    out: Dict[str, List[List[Tuple[int, int, int]]]] = {}
    if _looks_like_variants_dict(data):
        for pid, variants in data.items():
            out[pid] = [[tuple(c) for c in v] for v in variants]
        return out
    for key, cells in data.items():
        pid = key.split("__", 1)[0] if "__" in key else key
        out.setdefault(pid, []).append([tuple(c) for c in cells])
    return out


# ---------- world presentation ----------
def ijk_to_world(i: int, j: int, k: int, r: float) -> List[float]:
    # square-frame presentation
    s = r * 2.0 ** 0.5
    x = (j + k) * s
    y = (i + k) * s
    z = (i + j) * s
    return [x, y, z]


# ---------- canonical CID+SID helpers (orientation + translation invariant) ----------
def _rotations24():
    """Generate the 24 proper cubic rotations as signed permutations with det=+1."""
    from itertools import permutations
    perms = list(permutations((0, 1, 2)))  # 6
    signs = [(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)]  # product = +1
    rots = []
    for p in perms:
        for s in signs:
            rots.append((p, s))
    return rots  # each rot is (perm, signs)

def _apply_rot(v: Tuple[int, int, int], rot) -> Tuple[int, int, int]:
    (p, s) = rot
    w = (v[p[0]], v[p[1]], v[p[2]])
    return (s[0] * w[0], s[1] * w[1], s[2] * w[2])

def _normalize_cells(cells: List[Tuple[int, int, int]]) -> List[Tuple[int, int, int]]:
    mi = min(i for i, _, _ in cells)
    mj = min(j for _, j, _ in cells)
    mk = min(k for _, _, k in cells)
    return sorted([(i - mi, j - mj, k - mk) for (i, j, k) in cells])

def _canonicalize_cells(cells: List[Tuple[int, int, int]]):
    """Return (canon_cells, chosen_rot, delta) where canon_cells are sorted, delta is (mi,mj,mk)."""
    best_str = None
    best = None
    best_rot = None
    best_delta = None
    for rot in _rotations24():
        rot_cells = [_apply_rot(c, rot) for c in cells]
        mi = min(i for i, _, _ in rot_cells)
        mj = min(j for _, j, _ in rot_cells)
        mk = min(k for _, _, k in rot_cells)
        norm = sorted([(i - mi, j - mj, k - mk) for (i, j, k) in rot_cells])
        s = ";".join(f"{i},{j},{k}" for (i, j, k) in norm)
        if best_str is None or s < best_str:
            best_str = s
            best = norm
            best_rot = rot
            best_delta = (mi, mj, mk)
    return best, best_rot, best_delta, best_str  # best_str is the canonical serialization

def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _cells_to_piece_string(cells: List[Tuple[int, int, int]]) -> str:
    # i:j:k comma-separated 4-tuples
    return ",".join(f"{i}:{j}:{k}" for (i, j, k) in cells)

def _transform_cells(cells: List[Tuple[int, int, int]], rot, delta: Tuple[int, int, int]) -> List[Tuple[int, int, int]]:
    """Apply chosen rotation and translation-normalization to given cells."""
    mi, mj, mk = delta
    out = []
    for c in cells:
        r = _apply_rot(c, rot)
        out.append((r[0] - mi, r[1] - mj, r[2] - mk))
    return sorted(out)


# ---------- outputs ----------
def write_world_layers(engine, path: str, meta: dict = None):
    """
    Write the human-readable world view TXT. If `meta` is provided, include a short
    metadata header (timestamp, container_cid_sha256, sid_state_sha256, sid_route_sha256).
    """
    txt = write_world_layers_str(
        engine,
        container_cid_sha256=(meta.get("container_cid_sha256") if meta else None),
        sid_state_sha256=(meta.get("sid_state_sha256") if meta else None),
        sid_route_sha256=(meta.get("sid_route_sha256") if meta else None),
        timestamp=(meta.get("timestamp") if meta else None),
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)

def write_world_json(engine, dst_path: str, container_path: str, container_name: str, r: float):
    idx2cell = engine.idx2cell
    data = {
        "schema": "tetra_spheres_solution/1.0",
        "container_name": container_name,
        "container_path": container_path,
        "present": "square",
        "presentation": {
            "mode": "square",
            "frame": {"R": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], "t": [0.0, 0.0, 0.0]}
        },
        "r": r,
        "pieces_order": [pl["piece"] for pl in engine.placements],
        "pieces": [],
        "depth": engine.placed_count(),
        "timestamp": time.time()
    }

    # --- Compute canonical, orientation/translation-invariant CID over container cells ---
    container_cells = [tuple(c) for c in idx2cell]
    canon_cells, chosen_rot, delta, canon_str = _canonicalize_cells(container_cells)
    container_cid_sha256 = _sha256_hex(canon_str)
    data["container_cid_sha256"] = container_cid_sha256

    # Build pieces section (original presentation) and collect canonicalized per-piece cells for SIDs
    piece_to_cells_canon: Dict[str, List[Tuple[int, int, int]]] = {}
    for pl in engine.placements:
        cells_idx = pl["cells_idx"]
        cells_ijk = [list(idx2cell[i]) for i in cells_idx]
        world_centers = [ijk_to_world(i, j, k, r) for (i, j, k) in cells_ijk]
        data["pieces"].append({
            "id": pl["piece"],
            "cells_ijk": cells_ijk,
            "world_centers": world_centers
        })
        # canonicalize this piece's cells using the container's chosen rotation+delta
        cells_raw = [tuple(idx2cell[i]) for i in cells_idx]
        cells_canon = _transform_cells(cells_raw, chosen_rot, delta)
        piece_to_cells_canon[pl["piece"]] = cells_canon

    # --- SID.state (order-agnostic final arrangement) ---
    state_parts = []
    for pid in sorted(piece_to_cells_canon.keys()):
        state_parts.append(f"{pid}=" + _cells_to_piece_string(piece_to_cells_canon[pid]))
    sid_state_preimage = f"{container_cid_sha256}|{'|'.join(state_parts)}"
    sid_state_sha256 = _sha256_hex(sid_state_preimage)
    data["sid_state_sha256"] = sid_state_sha256

    # --- SID.route (order-aware; uses pieces_order exactly) ---
    route_parts = []
    for pid in data["pieces_order"]:
        cells_canon = piece_to_cells_canon.get(pid, [])
        route_parts.append(f"{pid}=" + _cells_to_piece_string(cells_canon))
    sid_route_preimage = f"{container_cid_sha256}|{'->'.join(route_parts)}"
    sid_route_sha256 = _sha256_hex(sid_route_preimage)
    data["sid_route_sha256"] = sid_route_sha256

    with open(dst_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- atomic snapshot helpers (Windows-safe) ----------
def _atomic_replace(src, dst, retries=12, delay=0.1):
    """
    Windows-safe replace with retries. Returns True on success, False on final failure.
    Retries PermissionError/OSError (file temporarily locked by another process).
    """
    for _ in range(retries):
        try:
            os.replace(src, dst)
            return True
        except (PermissionError, OSError):
            time.sleep(delay)
    return False

def _atomic_write(path: str, data: str):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    if not _atomic_replace(tmp, path):
        try:
            os.remove(tmp)
        except Exception:
            pass

def _atomic_write_world_json(path: str, engine, container_path: str, container_name: str, r: float):
    tmp = path + ".tmp"
    write_world_json(engine, tmp, container_path, container_name, r)
    if not _atomic_replace(tmp, path):
        try:
            os.remove(tmp)
        except Exception:
            pass

def write_world_layers_str(engine, container_cid_sha256=None, sid_state_sha256=None, sid_route_sha256=None, timestamp=None):
    idx2cell = engine.idx2cell
    cell_to_piece = {}
    for pl in engine.placements:
        pid = pl["piece"]
        for ci in pl["cells_idx"]:
            cell_to_piece[idx2cell[ci]] = pid

    all_uvws = []
    for (i, j, k) in idx2cell:
        u = j + k
        v = i + k
        w = i + j
        all_uvws.append((u, v, w))
    if not all_uvws:
        # still include a minimal header if provided
        header_lines = []
        header_lines.append("[SOLUTION METADATA]")
        if timestamp is not None:
            header_lines.append(f"timestamp: {timestamp}")
        if container_cid_sha256 is not None:
            header_lines.append(f"container_cid_sha256: {container_cid_sha256}")
        if sid_state_sha256 is not None:
            header_lines.append(f"sid_state_sha256: {sid_state_sha256}")
        if sid_route_sha256 is not None:
            header_lines.append(f"sid_route_sha256: {sid_route_sha256}")
        header = "\n".join(header_lines + [""]) if len(header_lines) > 1 else ""
        return header + "[SOLUTION — world view (ALL layers)]\n(empty)\n"

    u_min = min(u for (u, _, _) in all_uvws)
    u_max = max(u for (u, _, _) in all_uvws)
    v_min = min(v for (_, v, _) in all_uvws)
    v_max = max(v for (_, v, _) in all_uvws)
    w_min = min(w for (_, _, w) in all_uvws)
    w_max = max(w for (_, _, w) in all_uvws)

    layer_to_grid = {}
    for (i, j, k), pid in cell_to_piece.items():
        u = j + k
        v = i + k
        w = i + j
        layer_to_grid.setdefault(w, {})[(u, v)] = pid

    lines = []
    # --- NEW: metadata header (only prints keys that are provided) ---
    lines.append("[SOLUTION METADATA]")
    if timestamp is not None:
        lines.append(f"timestamp: {timestamp}")
    if container_cid_sha256 is not None:
        lines.append(f"container_cid_sha256: {container_cid_sha256}")
    if sid_state_sha256 is not None:
        lines.append(f"sid_state_sha256: {sid_state_sha256}")
    if sid_route_sha256 is not None:
        lines.append(f"sid_route_sha256: {sid_route_sha256}")
    lines.append("")  # blank line between header and view

    # --- Existing world view ---
    lines.append("[SOLUTION — world view (ALL layers)]")
    lines.append(f"Legend: rows=v (i+k: {v_min}..{v_max}), cols=u (j+k: {u_min}..{u_max}), layers=w (i+j: {w_min}..{w_max})")
    lines.append("")
    INDENT_PER_ROW = 2
    for w in range(w_min, w_max + 1):
        lines.append(f"Layer w=i+j={w}:")
        lines.append("")
        grid = layer_to_grid.get(w, {})
        for v in range(v_min, v_max + 1):
            indent = " " * (INDENT_PER_ROW * (v_max - v))
            row = []
            for u in range(u_min, u_max + 1):
                ch = grid.get((u, v), " ")
                row.append(ch)
            lines.append((indent + " ".join(row)).rstrip())
        lines.append("")
    return "\n".join(lines)

def write_snapshot_atomic(container_path, container_name, r, engine, results_dir):
    json_path = os.path.join(results_dir, f"{container_name}.current.world.json")
    _atomic_write_world_json(json_path, engine, container_path, container_name, r)

    # Read hashes + timestamp from the JSON snapshot
    meta = {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        meta = {}

    txt = write_world_layers_str(
        engine,
        container_cid_sha256=meta.get("container_cid_sha256"),
        sid_state_sha256=meta.get("sid_state_sha256"),
        sid_route_sha256=meta.get("sid_route_sha256"),
        timestamp=meta.get("timestamp")
    )

    txt_path = os.path.join(results_dir, f"{container_name}.current.world_layers.txt")
    _atomic_write(txt_path, txt)

def safe_snapshot(args, engine):
    # Never let snapshotting interrupt the solve.
    try:
        write_snapshot_atomic(args.container_path, args.container_name, args.r, engine, RESULTS_DIR)
    except Exception:
        pass


# ---------- progress emitters ----------
def make_emit_progress(tail_deque: deque):
    ensure_dir(RESULTS_DIR)
    ensure_dir(LOGS_DIR)
    _init_runctl()  # ensure control file exists

    def emit_progress_to_streams(payload: dict, tail: deque):
        # stream line
        with open(PROGRESS_STREAM, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        # tail mem
        tail.append(payload)
        # overwrite summary
        summary = payload.copy()
        try:
            aps_val = round(payload.get("attempts_per_sec", 0), 1)
        except Exception:
            aps_val = payload.get("attempts_per_sec", 0)
        summary["attempts_per_sec"] = aps_val
        with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        # console echo (concise)
        try:
            if payload.get("event") == "progress":
                placed = payload.get("placed", 0)
                best = payload.get("best_depth", placed)
                total = payload.get("total", 25)
                aps = payload.get("attempts_per_sec", 0)
                run = payload.get("run", 0)
                seed = payload.get("seed", "")
                status = payload.get("status", "")
                line = f"[run {run} seed={seed}] placed {placed}/{total} | best {best} | rate {aps}/s"
                if status:
                    line += f" | {status}"
                print(line, flush=True)
        except Exception:
            pass

    def emit_progress(engine, run_idx, seed_label, aps=0.0, placed_only=False):
        cur = engine.placed_count()
        best = getattr(engine, "best_depth_ever", cur)
        payload = {
            "event": "progress",
            "run": run_idx,
            "seed": seed_label,
            "placed": cur,
            "best_depth": best,
            "total": engine.total_pieces(),
            "attempts": getattr(engine, "attempts", 0),
            "attempts_per_sec": aps,
        }
        if placed_only:
            payload = {"event": "progress", "run": run_idx, "placed": cur, "best_depth": best}
        emit_progress_to_streams(payload, tail_deque)
    return emit_progress


# ---------- engine builder (fresh per attempt) ----------
def build_engine(SolverEngine,
                 pieces,
                 valid_set,
                 rng_seed=None,
                 shuffle="none",
                 rotate_first=0,
                 hole4=True):
    """
    Construct a fresh engine configured for this attempt:
      - sets RNG_SEED (if supported)
      - deterministic piece order via engine._shuffle_order(shuffle)
      - optional rotation of the opening piece
      - toggles hole_mod4 pruning
    """
    eng = SolverEngine(pieces, valid_set)

    # Seed
    try:
        if rng_seed is not None:
            eng.RNG_SEED = int(rng_seed)
    except Exception:
        pass

    # Shuffle mode
    try:
        eng.shuffle_mode = shuffle
        eng._shuffle_order(shuffle)
    except Exception:
        pass

    # Rotate opening piece
    try:
        if rotate_first and hasattr(eng, "order") and len(eng.order) > 0:
            r = int(rotate_first) % len(eng.order)
            if r:
                o = list(eng.order)
                eng.order = tuple(o[r:] + o[:r])
    except Exception:
        pass

    # Hole-mod-4 pruning
    try:
        eng.hole_mod4 = bool(hole4)
    except Exception:
        pass

    return eng


# ---------- empties%4 gate helper ----------
def _empties_mod4_ok_now(engine) -> bool:
    """Return True if engine thinks current empty-region sizes are all %4==0.
       If the method/field isn't available, return True (don't block)."""
    try:
        return bool(engine._empties_mod4_ok(engine.occ_bits))
    except Exception:
        return True


# ---------- runner (single attempt) ----------
def run_once_with_engine(engine,
                         run_idx: int,
                         seed_label,
                         effective_stall_limit_fn,
                         emit_progress,
                         args):
    """
    Returns: (status, progressed_any)
      status ∈ {"solved", "exhausted_root", "stalled_or_exhausted", "stopped_by_user"}
    """
    from time import monotonic

    LOG_PERIOD = 5.0
    last_log_t = monotonic()
    prev_t = last_log_t
    prev_att = getattr(engine, "attempts", 0)

    # Snapshot cadence
    last_snap_t = monotonic()
    SNAP_IVL = args.snapshot_interval

    # Cooperative run-control state
    paused = False

    # hole4 conditional gate: start with hole4 OFF if requested
    deferred_hole4 = False
    try:
        want_hole4 = bool(engine.hole_mod4)
    except Exception:
        want_hole4 = bool(args.hole4)

    if args.hole4 and args.hole4_conditional:
        try:
            engine.hole_mod4 = False
            deferred_hole4 = True
        except Exception:
            deferred_hole4 = False  # cannot gate

    emit_progress(engine, run_idx, seed_label, aps=0.0)

    # initial snapshot so external monitors see something immediately
    if SNAP_IVL is not None or args.snapshot_on_depth:
        safe_snapshot(args, engine)
        last_snap_t = monotonic()

    progressed_any = False
    last_best = getattr(engine, "best_depth_ever", engine.placed_count())
    last_improve_t = monotonic()

    while True:
        # --- cooperative run control (check first) ---
        ctl = _read_runctl_state()
        if ctl == "stop":
            _emit_event_line({"event": "stopped", "run": run_idx, "seed": seed_label, "ts": time.time()})
            return "stopped_by_user", progressed_any

        if ctl == "pause":
            if not paused:
                paused = True
                _emit_event_line({"event": "paused", "run": run_idx, "seed": seed_label, "ts": time.time()})
            # wait here until run or stop
            while True:
                time.sleep(0.05)
                ctl2 = _read_runctl_state()
                if ctl2 == "stop":
                    _emit_event_line({"event": "stopped", "run": run_idx, "seed": seed_label, "ts": time.time()})
                    return "stopped_by_user", progressed_any
                if ctl2 == "run":
                    paused = False
                    _emit_event_line({"event": "resumed", "run": run_idx, "seed": seed_label, "ts": time.time()})
                    # reset APS baseline so resumed interval is accurate
                    prev_t = monotonic()
                    prev_att = getattr(engine, "attempts", 0)
                    last_log_t = prev_t
                    break
            continue  # skip work on the iteration we just resumed

        # --- one search step ---
        progressed_step, solved = engine.step_once()
        if progressed_step:
            progressed_any = True

        now = monotonic()
        attempts = getattr(engine, "attempts", 0)
        dt = max(1e-6, now - prev_t)
        aps = int((attempts - prev_att) / dt)

        # Enable hole4 once empties are safe (if gated)
        if deferred_hole4:
            if _empties_mod4_ok_now(engine):
                try:
                    engine.hole_mod4 = want_hole4
                    deferred_hole4 = False
                except Exception:
                    deferred_hole4 = False

        # periodic log + snapshot
        if now - last_log_t >= LOG_PERIOD:
            emit_progress(engine, run_idx, seed_label, aps=aps)
            last_log_t = now
            prev_t = now
            prev_att = attempts
            if SNAP_IVL is not None and (now - last_snap_t) >= SNAP_IVL:
                safe_snapshot(args, engine)
                last_snap_t = now

        # on best-depth improvement
        cur_best2 = getattr(engine, "best_depth_ever", engine.placed_count())
        if cur_best2 > last_best:
            last_best = cur_best2
            last_improve_t = now
            emit_progress(engine, run_idx, seed_label, aps=aps)
            if args.snapshot_on_depth:
                safe_snapshot(args, engine)

        # stop conditions
        if solved:
            return "solved", progressed_any

        # stall / exhaustion
        stall_limit = effective_stall_limit_fn(cur_best2)
        if stall_limit is not None and (now - last_improve_t) >= stall_limit:
            if engine.placed_count() == 0:
                return "exhausted_root", progressed_any
            return "stalled_or_exhausted", progressed_any


# ---------- CLI ----------
def build_argparser():
    p = argparse.ArgumentParser(
        description=(
            "FCC ball puzzle solver — place 4-sphere pieces into a container lattice.\n\n"
            "Examples:\n"
            "  python solver.py containers/firstbox.py.json\n"
            "  python solver.py containers/firstbox.py.json --hole4\n"
            "  python solver.py containers/firstbox.py.json --rng-seed 42\n"
            "  python solver.py containers/firstbox.py.json --restart-on-stall 900\n"
            "  python solver.py containers/firstbox.py.json --rng-seed 42 --max-results 3\n"
            "  python solver.py containers/firstbox.py.json --rng-seed 42 --shuffle-pieces within-buckets\n"
            "  python solver.py containers/firstbox.py.json --stall-below-23 300 --stall-at-23 900 --stall-at-24 1800\n"
            "  python solver.py containers/firstbox.py.json --snapshot-interval 5 --snapshot-on-depth\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter
    )

    p.add_argument("container", nargs="?", default=DEFAULT_CONTAINER,
                   help="Path to container JSON (default: containers/Roof.json)")

    p.add_argument("--rng-seed", type=int, default=None,
                   help="Set RNG seed (affects candidate/ori order & TT keys). Omit for default engine seed = 1337.")

    p.add_argument("--restart-on-stall", type=int, default=None, metavar="SECONDS",
                   help="Fallback stall window if depth-specific options are not provided.")

    p.add_argument("--max-results", type=int, default=1, metavar="N",
                   help="Write up to N distinct solutions (seed increments). Default: 1.")

    p.add_argument("--shuffle-pieces", choices=["none", "within-buckets", "full"], default="none",
                   help="Shuffle piece order deterministically from RNG seed.")

    p.add_argument("--stall-below-23", type=int, default=None, metavar="SECONDS",
                   help="Stall window when best depth < 23 (overrides --restart-on-stall).")

    p.add_argument("--stall-at-23", type=int, default=None, metavar="SECONDS",
                   help="Stall window when best depth >= 23 (overrides --restart-on-stall).")

    p.add_argument("--stall-at-24", type=int, default=None, metavar="SECONDS",
                   help="Stall window when best depth >= 24 (overrides --restart-on-stall).")

    p.add_argument("--check-thickness", action="store_true",
                   help="Debug: print counts of cells that have all 6 axial neighbors vs all 6 diagonal neighbors.")

    p.add_argument("--try-openers", type=int, default=6, metavar="N",
                   help="If a run exhausts at depth 0, rotate the opening piece up to N times before changing seed (default: 6).")

    # Hole pruning (escape % as %% to avoid argparse formatting error)
    p.add_argument("--hole4", "--hole-mod4", action="store_true", dest="hole4",
                   help="Enable hole-detect pruning. Reject states where an empty region size %% 4 != 0.")

    p.add_argument("--hole4-conditional", action="store_true",
                   help="Only enable hole-detect once the current empties already satisfy size %% 4 == 0.")

    # Snapshots
    p.add_argument("--snapshot-interval", type=int, default=None, metavar="SECONDS",
                   help="Write rolling snapshots of current placements every N seconds to results/<Name>.current.world.json and .current.world_layers.txt")

    p.add_argument("--snapshot-on-depth", action="store_true",
                   help="Also write a snapshot whenever best depth improves.")

    return p


# ---------- driver ----------
def main():
    ensure_dir(RESULTS_DIR)
    ensure_dir(LOGS_DIR)
    _init_runctl()

    p = build_argparser()
    args = p.parse_args()

    # container
    container_path = args.container
    container = load_json(container_path)
    cells = [tuple(c) for c in container["cells"]]
    valid_set: Set[Tuple[int, int, int]] = set(cells)
    r = float(container.get("r", 0.5))
    container_name = os.path.splitext(os.path.basename(container_path))[0]

    # stash for snapshot helper
    args.container_path = container_path
    args.container_name = container_name
    args.r = r

    if args.check_thickness:
        # quick structural diagnostic
        axial = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
        diag6 = [(1, -1, 0), (-1, 1, 0), (1, 0, -1), (-1, 0, 1), (0, 1, -1), (0, -1, 1)]
        v = valid_set
        axial_full = 0
        diag_full = 0
        for (i, j, k) in v:
            if all((i + di, j + dj, k + dk) in v for (di, dj, dk) in axial):
                axial_full += 1
            if all((i + di, j + dj, k + dk) in v for (di, dj, dk) in diag6):
                diag_full += 1
        print(f"[thickness] cells with all 6 axial neighbors: {axial_full}")
        print(f"[thickness] cells with all 6 diagonal (FCC) neighbors: {diag_full}")

    # pieces + engine class
    pieces_mod = load_py_module(PIECES_PATH, "pieces_module")
    pieces = extract_pieces(pieces_mod)
    eng_mod = load_py_module(ENGINE_PATH, "engine_module")
    SolverEngine = getattr(eng_mod, "SolverEngine")

    # progress emitter
    tail = deque(maxlen=256)
    emit_progress = make_emit_progress(tail)

    # seeds / loop
    base_seed = args.rng_seed
    run_idx = 0
    max_results = max(1, int(args.max_results))
    results_found = 0
    seen_sigs = set()
    best_depth_ever = 0

    # output paths
    def base_paths():
        world_json_path = os.path.join(RESULTS_DIR, f"{container_name}.world.json")
        world_layers_path = os.path.join(RESULTS_DIR, f"{container_name}.world_layers.txt")
        return world_json_path, world_layers_path

    def indexed_paths(k: int):
        world_json_path = os.path.join(RESULTS_DIR, f"{container_name}.result{k}.world.json")
        world_layers_path = os.path.join(RESULTS_DIR, f"{container_name}.result{k}.world_layers.txt")
        return world_json_path, world_layers_path

    # stall logic
    def effective_stall_limit(best_depth: int):
        if args.stall_at_24 is not None and best_depth >= 24:
            return args.stall_at_24
        if args.stall_at_23 is not None and best_depth >= 23:
            return args.stall_at_23
        if args.stall_below_23 is not None and best_depth < 23:
            return args.stall_below_23
        return args.restart_on_stall

    # solution signature to dedup identical solutions
    def solution_signature(engine) -> Tuple:
        bag = []
        for pl in engine.placements:
            bag.append((pl["piece"], tuple(sorted(pl["cells_idx"]))))
        bag.sort()
        return tuple(bag)

    # main multi-run loop
    while results_found < max_results:
        run_seed = (base_seed + run_idx) if base_seed is not None else None
        seed_label = ("default" if run_seed is None else run_seed)

        tried = 0
        max_try_openers = max(0, int(args.try_openers))
        rotated_solved = False
        status = None  # track last run status

        while tried <= max_try_openers:
            # fresh engine for this attempt
            engine = build_engine(
                SolverEngine,
                pieces,
                valid_set,
                rng_seed=run_seed,
                shuffle=args.shuffle_pieces,
                rotate_first=tried,
                hole4=args.hole4
            )

            status, progressed_any = run_once_with_engine(
                engine, run_idx, seed_label, effective_stall_limit, emit_progress, args
            )

            best_depth = getattr(engine, "best_depth_ever", engine.placed_count())
            if best_depth > best_depth_ever:
                best_depth_ever = best_depth

            if status == "solved" and engine.placed_count() == engine.total_pieces():
                sig = solution_signature(engine)
                if sig not in seen_sigs:
                    seen_sigs.add(sig)
                    if max_results == 1:
                        wjson, wlayers = base_paths()
                    else:
                        wjson, wlayers = indexed_paths(results_found + 1)
                    write_world_json(engine, wjson, container_path, container_name, r)

                    # Load hashes + timestamp from the just-written JSON so the TXT header matches
                    _meta = {}
                    try:
                        with open(wjson, "r", encoding="utf-8") as _f:
                            _meta = json.load(_f)
                    except Exception:
                        _meta = {}

                    write_world_layers(engine, wlayers, meta=_meta)
                    results_found += 1
                rotated_solved = True
                break

            if status == "exhausted_root":
                tried += 1
                if tried <= max_try_openers:
                    continue
                else:
                    break
            else:
                # stalled mid-depth / stopped_by_user / or exhausted after some depth
                break

        # write a final progress event for this run
        def _write_final(status_label):
            payload = {
                "event": "progress",
                "run": run_idx,
                "seed": seed_label,
                "status": status_label,
                "placed": engine.placed_count(),
                "best_depth": getattr(engine, "best_depth_ever", engine.placed_count()),
                "total": engine.total_pieces(),
                "attempts": getattr(engine, "attempts", 0),
                "attempts_per_sec": 0,
            }
            with open(PROGRESS_STREAM, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

        if rotated_solved:
            _write_final("solved")
        else:
            if status == "stopped_by_user":
                _write_final("stopped_by_user")
                break  # stop the whole driver cleanly
            _write_final("stalled")

        run_idx += 1
        if results_found >= max_results:
            break

    return


if __name__ == "__main__":
    main()
