# engine/exact_cover_dlx.py
from __future__ import annotations
from typing import Dict, List, Tuple, Iterable, Optional
from dataclasses import dataclass

from engine.pieceset import PieceSet, PieceDef
from engine.subset_dfs import (
    enumerate_orientations,
    enumerate_placements_for_piece,
    Placement,
    SolveResult,
    Cell,
)
from engine.components import is_canonical_first_placement

# --- Tiny DLX (Knuth's Algorithm X with dancing links) ------------------------

@dataclass
class _Node:
    L: "_Node"; R: "_Node"; U: "_Node"; D: "_Node"
    C: "_Col" | None
    row_id: int

@dataclass
class _Col(_Node):
    name: str
    size: int

def _new_header() -> _Node:
    h = _Node(None,None,None,None,None,-1)  # type: ignore
    h.L = h.R = h.U = h.D = h
    return h

def _link_right(left: _Node, right: _Node):
    right.R = left.R; right.L = left
    left.R.L = right; left.R = right

def _link_down(top: _Node, bottom: _Node):
    bottom.D = top.D; bottom.U = top
    top.D.U = bottom; top.D = bottom

def _cover(col: _Col):
    col.R.L = col.L; col.L.R = col.R
    i = col.D
    while i is not col:
        j = i.R
        while j is not i:
            j.D.U = j.U
            j.U.D = j.D
            j.C.size -= 1  # type: ignore
            j = j.R
        i = i.D

def _uncover(col: _Col):
    i = col.U
    while i is not col:
        j = i.L
        while j is not i:
            j.C.size += 1  # type: ignore
            j.D.U = j
            j.U.D = j
            j = j.L
        i = i.U
    col.R.L = col
    col.L.R = col

def _choose_col(header: _Node) -> _Col | None:
    # choose primary column with minimum size
    c = header.R
    best: _Col | None = None
    best_size = 1 << 30
    while isinstance(c, _Col) and c is not header:
        if c.size < best_size:
            best = c; best_size = c.size
        c = c.R
    return best

# --- Public API ---------------------------------------------------------------

def solve_exact_cover_dlx(
    container_cells: Iterable[Cell],
    pieceset: PieceSet,
    k_pieces: Optional[int] = None,
    mirror_invariant: bool = True,
    max_results: int = 1,
) -> List[SolveResult]:
    """
    DLX modeling:
      Primary columns  = container cells (must be covered once)
      Secondary columns= piece-usage tokens (A#1..A#m), optional (at most once)
      Rows             = legal placements (4 cells + one piece token)
    We also enforce a first-placement symmetry-break by filtering candidate rows
    that cover the lexicographically first container cell.
    """
    cells = sorted(set((int(x), int(y), int(z)) for (x,y,z) in container_cells))
    n = len(cells)
    if n == 0: return []
    if k_pieces is None:
        if n % 4 != 0: raise ValueError("Container not multiple of 4 and k_pieces not provided")
        k = n // 4
    else:
        k = int(k_pieces)
        if k < 0: raise ValueError("k_pieces must be >=0")

    # Build placement list (same as DFS helper)
    stock = pieceset.resolved_stock(default=1)
    all_rows: List[Placement] = []
    row_piece: List[str] = []
    for pid, defn in pieceset.library.items():
        if stock.get(pid, 0) <= 0: continue
        oris = enumerate_orientations(defn, mirror_invariant=mirror_invariant)
        pls  = enumerate_placements_for_piece(set(cells), oris, pid)
        all_rows.extend(pls)
        row_piece.extend([pid] * len(pls))

    if not all_rows: return []

    # Map cells to column indices
    # --- Columns ---
    header = _new_header()
    cols: List[_Col] = []

    # 1) Primary columns: cells (link into header ring)
    cell_to_col: Dict[Cell, int] = {c: i for i, c in enumerate(cells)}
    for i, cval in enumerate(cells):
        c = _Col(None, None, None, None, None, -1, name=f"C:{cval}", size=0)  # type: ignore
        c.C = c
        c.L = c.R = c.U = c.D = c
        cols.append(c)
        _link_right(header.L, c)  # insert before header => part of primary ring

    num_cell_cols = len(cells)

    # 2) Primary columns: must_use requirements (one per piece id)
    must_use = set(pieceset.must_use)
    req_cols_index: Dict[str, int] = {}
    for pid in sorted(must_use):
        idx = len(cols)
        rc = _Col(None, None, None, None, None, -1, name=f"REQ:{pid}", size=0)  # type: ignore
        rc.C = rc
        rc.L = rc.R = rc.U = rc.D = rc
        cols.append(rc)
        req_cols_index[pid] = idx
        _link_right(header.L, rc)  # primary ring

    num_primary_cols = len(cols)  # everything linked so far is primary

    # 3) Secondary columns: piece tokens (A#0..A#m-1). DO NOT link to header ring.
    stock = pieceset.resolved_stock(default=1)
    piece_tokens: List[Tuple[str, int]] = []
    piece_token_col_index: Dict[Tuple[str, int], int] = {}
    for pid, cnt in stock.items():
        for j in range(cnt):
            piece_tokens.append((pid, j))
            col_idx = len(cols)
            pc = _Col(None, None, None, None, None, -1, name=f"P:{pid}#{j}", size=0)  # type: ignore
            pc.C = pc
            pc.L = pc.R = pc.U = pc.D = pc
            cols.append(pc)
            piece_token_col_index[(pid, j)] = col_idx
    # Note: token columns exist (for conflict) but are not part of the primary header loop.

    # Build rows
    # First-placement symmetry-break: only rows that would be canonical if they include the first cell
    # --- Rows ---
    first_cell = cells[0]

    def row_is_allowed(pl: Placement) -> bool:
        if first_cell in pl.cells:
            return is_canonical_first_placement(pl.cells, cells, mirror_invariant=mirror_invariant)
        return True

    nodes_by_row: List[List[_Node]] = []

    for ridx, pl in enumerate(all_rows):
        if not row_is_allowed(pl):
            continue
        pid = row_piece[ridx]
        max_tokens = stock.get(pid, 0)
        if max_tokens <= 0:
            continue

        # Create one row per available token for this piece id
        for tok_j in range(max_tokens):
            tok = (pid, tok_j)
            tok_col = cols[piece_token_col_index[tok]]

            row_nodes: List[_Node] = []

            # cover the 4 primary cell columns
            for c in pl.cells:
                col = cols[cell_to_col[c]]
                nd = _Node(None, None, None, None, col, ridx)  # type: ignore
                _link_down(col, nd)
                col.size += 1
                row_nodes.append(nd)

            # cover the must_use primary column (if applicable)
            if pid in must_use:
                col = cols[req_cols_index[pid]]
                nd = _Node(None, None, None, None, col, ridx)  # type: ignore
                _link_down(col, nd)
                col.size += 1
                row_nodes.append(nd)

            # include the secondary token column (at-most-once constraint)
            nd = _Node(None, None, None, None, tok_col, ridx)  # type: ignore
            _link_down(tok_col, nd)
            tok_col.size += 1
            row_nodes.append(nd)

            # link row circularly
            for i in range(len(row_nodes)):
                a = row_nodes[i]
                b = row_nodes[(i + 1) % len(row_nodes)]
                a.R = b
                b.L = a
            nodes_by_row.append(row_nodes)

    # If no rows survived symmetry filtering, abort
    if not nodes_by_row:
        return []

    # Algorithm X search
    solution_rows: List[int] = []
    results: List[SolveResult] = []

    def search():
        nonlocal results
        if len(results) >= max_results:
            return
        # Check: we need exactly k piece-rows chosen; but exact cover of cell cols forces k automatically if all pieces are 4 cells
        # Choose column
        col = _choose_col(header)
        if col is None:
            # No primary columns left -> found cover. Collect selected rows.
            usage: Dict[str,int] = {}
            chosen_pls: List[Placement] = []
            for ridx in solution_rows:
                pl = all_rows[ridx]
                chosen_pls.append(pl)
                pid = row_piece[ridx]
                usage[pid] = usage.get(pid, 0) + 1
            # Quick piece count check (just in case)
            if sum(usage.values()) == k:
                results.append(SolveResult(placements=chosen_pls, piece_usage=usage))
            return

        _cover(col)
        r = col.D
        while r is not col:
            solution_rows.append(r.row_id)
            j = r.R
            while j is not r:
                _cover(j.C)  # type: ignore
                j = j.R
            search()
            # backtrack
            j = r.L
            while j is not r:
                _uncover(j.C)  # type: ignore
                j = j.L
            solution_rows.pop()
            r = r.D
        _uncover(col)

    search()
    return results
