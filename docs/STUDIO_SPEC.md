# Studio (Animation Tab) — v1 Spec

## Purpose
A separate tab in the existing app for loading any geometry snapshot (solution, partial, container) and rendering it with its own viewer and (later) animations/exports. No file watcher. No coupling to the main viewer.

## Separation of Concerns
- **Input**: a self-contained JSON (“Anim Snapshot v1”), or an existing solution/container that an adapter normalizes.
- **Studio**: its own HTML/JS (scene, camera, controls, render loop), its own color strategies, bonds, and later animations/exports.
- **Main Viewer**: unchanged.

## Input Contract (Anim Snapshot v1)
```json
{
  "version": "anim-1",
  "pieces": [
    {
      "id": "P01",
      "name": "piece_01",
      "centers": [ {"x":0,"y":0,"z":0}, {"x":1,"y":0,"z":0}, {"x":0,"y":1,"z":0}, {"x":0,"y":0,"z":1} ],
      "material_key": "P01"
    }
  ],
  "palette": { "strategy": "golden-3band" },
  "meta": { "source":"solution", "timestamp":"ISO-8601", "seed":12345 }
}
```

Bonds: inferred by nearest-neighbor distance with tolerance; can be omitted.
Partial solutions: supported (fewer pieces).

## Viewer Parity (Studio)
- Orthographic (or perspective), one-time fit on first load.
- Pivot = snapshot bbox center. No auto-refits on redraws.
- Scroll-wheel zoom & orbit persist; window resize adjusts aspect only.

## Color & Bonds
- Color strategies: same set as Viewer; selectable in Studio.
- Bonds: cylinders between neighbor pairs (distance ≈ min non-zero pair), same material as the piece.

## Animations (architecture)
- Director: starts/stops a preset.
- Tracks: camera, piece reveal/transform, bond growth, lights.
- Presets (v1 candidates):
  - Symmetry Orbit (camera only)
  - Weave Reveal (piece sequence + bond grow)
  - Light Sweep (moving rim/key)
  - Counter-phase Rotations (two subsets)
- Each preset = small param schema (duration, speed/ease, seed).

## Exports
- Still: PNG at scale (e.g., 2×).
- Video: PNG sequence + ffmpeg → MP4 (QProcess). MediaRecorder/WebCodecs optional later.
