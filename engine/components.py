# engine/components.py
from __future__ import annotations
from typing import Iterable, Tuple, List, Set
import itertools

Cell = Tuple[int, int, int]

# --- Reuse FCC neighbor logic identical to coords_cid ---
_FCC_NEIGH: List[Cell] = []
seen = set()
for vec in set(itertools.permutations((1, 1, 0), 3)):
    for sx in ((-1, 1) if vec[0] != 0 else (1,)):
        for sy in ((-1, 1) if vec[1] != 0 else (1,)):
            for sz in ((-1, 1) if vec[2] != 0 else (1,)):
                d = (sx * vec[0], sy * vec[1], sz * vec[2])
                if d not in seen:
                    seen.add(d)
                    _FCC_NEIGH.append(d)
assert len(_FCC_NEIGH) == 12

def neighbors_fcc(p: Cell) -> List[Cell]:
    px, py, pz = p
    return [(px + dx, py + dy, pz + dz) for (dx, dy, dz) in _FCC_NEIGH]

def find_components_fcc(cells: Iterable[Cell]) -> List[Set[Cell]]:
    """FCC-connected components; sorted for determinism."""
    s = set((int(x), int(y), int(z)) for (x, y, z) in cells)
    comps: List[Set[Cell]] = []
    while s:
        start = s.pop()
        comp = {start}
        stack = [start]
        while stack:
            cur = stack.pop()
            for nb in neighbors_fcc(cur):
                if nb in s:
                    s.remove(nb)
                    comp.add(nb)
                    stack.append(nb)
        comps.append(comp)
    comps.sort(key=lambda c: (len(c), min(c)))
    return comps

# --- symmetry transforms (24 rotations, optional mirrors) ---

def _perm_parity(perm: Tuple[int, int, int]) -> int:
    inv = 0
    p = list(perm)
    for i in range(3):
        for j in range(i + 1, 3):
            if p[i] > p[j]:
                inv += 1
    return 1 if (inv % 2 == 0) else -1

def generate_transforms(include_mirror: bool) -> List[Tuple[Tuple[int,int,int], Tuple[int,int,int]]]:
    mats = []
    for perm in itertools.permutations((0,1,2), 3):
        parity = _perm_parity(perm)
        for signs in itertools.product((-1,1), repeat=3):
            det = parity * signs[0] * signs[1] * signs[2]
            if det == 1 or (include_mirror and det == -1):
                mats.append((perm, signs))
    # unique, stable
    mats = list(dict.fromkeys(mats))
    return mats

def apply_transform(p: Cell, tfm) -> Cell:
    perm, signs = tfm
    v = (p[0], p[1], p[2])
    return (
        signs[0] * v[perm[0]],
        signs[1] * v[perm[1]],
        signs[2] * v[perm[2]],
    )

# --- tiny helpers used by subset-mode symmetry breaking ---

def first_anchor_cell(cells: Iterable[Cell]) -> Cell:
    """Lexicographically smallest cell; stable and cheap."""
    return min(cells)

def is_canonical_first_placement(
    placement_cells: Iterable[Cell],
    container_cells: Iterable[Cell],
    mirror_invariant: bool = True,
) -> bool:
    """
    Symmetry-breaker for the *first* placement:
      - Shift placement so its min cell is (0,0,0)
      - Apply all cube symmetries (± mirrors)
      - Keep only if this placement's shifted tuple is lexicographically minimal
    This kills equivalent branches caused by global symmetries in tiny containers.
    """
    pts = [tuple(map(int, p)) for p in placement_cells]
    if not pts:
        return True
    # normalize by own min
    minx = min(p[0] for p in pts)
    miny = min(p[1] for p in pts)
    minz = min(p[2] for p in pts)
    base = [(x - minx, y - miny, z - minz) for (x,y,z) in pts]
    base.sort()
    base_t = tuple(base)

    best = None
    for tfm in generate_transforms(mirror_invariant):
        rot = [apply_transform(p, tfm) for p in pts]
        rx = min(p[0] for p in rot); ry = min(p[1] for p in rot); rz = min(p[2] for p in rot)
        sh = [(x - rx, y - ry, z - rz) for (x,y,z) in rot]
        sh.sort()
        tup = tuple(sh)
        if best is None or tup < best:
            best = tup

    return base_t == best
