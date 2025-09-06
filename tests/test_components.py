from engine.components import find_components_fcc, first_anchor_cell, is_canonical_first_placement

def test_components_two_disconnected():
    a = [(0,0,0),(1,1,0),(1,0,1),(0,1,1)]
    b = [(10,0,0),(11,1,0),(11,0,1),(10,1,1)]
    comps = find_components_fcc(a + b)
    sizes = sorted(len(c) for c in comps)
    assert sizes == [4,4]

def test_first_anchor_cell_stable():
    cells = [(5,2,9),(0,0,1),(0,0,0),(7,3,2)]
    assert first_anchor_cell(cells) == (0,0,0)

def test_canonical_first_placement_kills_duplicates():
    # two equivalent placements of a tetra under rotation/reflection
    p1 = [(0,0,0),(1,1,0),(1,0,1),(0,1,1)]
    # mirror across X
    p2 = [(-x,y,z) for (x,y,z) in p1]
    assert is_canonical_first_placement(p1, p1, mirror_invariant=True) in (True, False)
    # exactly one of these should pass canonical test (the lexicographically smaller)
    assert is_canonical_first_placement(p1, p1, True) != is_canonical_first_placement(p2, p2, True)
