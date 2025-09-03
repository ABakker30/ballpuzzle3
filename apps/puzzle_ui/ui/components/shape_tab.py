# apps/puzzle_ui/ui/components/shape_tab.py

# import shim: support both direct-run and module-run
import sys
from pathlib import Path
import os, time, json, math
from collections import defaultdict
from typing import Optional, Dict, List, Tuple, Set
from PySide6.QtCore import QObject, Signal, Slot, QUrl, QTimer, Qt, Property
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QSizePolicy,
    QGroupBox, QGridLayout, QLabel, QPushButton, QMessageBox, QFormLayout,
    QSlider, QComboBox, QFileDialog, QSpinBox, QCheckBox
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

_pkg = __package__
if _pkg in (None, "", "components"):
    # Direct run or imported as top-level "components"
    _UI_DIR = Path(__file__).resolve().parents[1]  # .../apps/puzzle_ui/ui
    if str(_UI_DIR) not in sys.path:
        sys.path.insert(0, str(_UI_DIR))
    from utils import app_root, repo_root  # noqa: E402
else:
    # Module run: python -m apps.puzzle_ui.ui.main
    from ..utils import app_root, repo_root  # type: ignore


class ShapeTab(QWidget):
    """
    Shape editor tab for creating and editing puzzle container shapes.
    Features:
    - Open shape definition or solution files
    - Interactive 3D sphere editing (add/remove/toggle)
    - Hover preview for sphere placement
    - Export to solver-ready container JSON
    - FCC lattice system with parity constraints
    """
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        # FCC lattice constants
        self.SPACING = 1.0  # Unit spacing for integer world coordinates
        
        # FCC neighbor offsets (12 directions)
        self.FCC_OFFSETS = [
            (1, 1, 0), (1, -1, 0), (-1, 1, 0), (-1, -1, 0),
            (1, 0, 1), (1, 0, -1), (-1, 0, 1), (-1, 0, -1),
            (0, 1, 1), (0, 1, -1), (0, -1, 1), (0, -1, -1),
        ]
        
        # Calculate actual nearest neighbor distance in our coordinate system
        # Test with origin (0,0,0) and its nearest FCC neighbors
        origin = (0, 0, 0)
        min_distance = float('inf')
        
        for offset in self.FCC_OFFSETS:
            neighbor = (origin[0] + offset[0], origin[1] + offset[1], origin[2] + offset[2])
            # Convert both to display coordinates and measure distance (without shift)
            x1, y1, z1 = origin[0] * self.SPACING, origin[1] * self.SPACING, origin[2] * self.SPACING
            x2, y2, z2 = neighbor[0] * self.SPACING, neighbor[1] * self.SPACING, neighbor[2] * self.SPACING
            distance = ((x2-x1)**2 + (y2-y1)**2 + (z2-z1)**2)**0.5
            min_distance = min(min_distance, distance)
        
        # Set radius to half the nearest neighbor distance
        self.RADIUS = min_distance / 2
        print(f"[Shape] Calculated sphere radius: {self.RADIUS} (nearest neighbor distance: {min_distance})")
        
        # Shape state - all coordinates are in Viewer FCC space [X,Y,Z]
        self.active_spheres: Set[Tuple[int, int, int]] = set()  # Active sphere positions (X,Y,Z)
        self.frontier_spheres: Set[Tuple[int, int, int]] = set()  # Potential placements (X,Y,Z)
        self.shift = (0, 0, 0)  # Dynamic shift to keep coordinates positive
        self.parity_target = 0  # Even parity constraint
        
        # Current file info
        self._current_file: Optional[Path] = None
        self._shape_name = "untitled"
        
        self._build_ui()
        self._setup_viewer()
        self._reset_to_origin()
    
    def _build_ui(self):
        """Build the main UI layout: left panel + 3D viewer."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Create splitter
        splitter = QSplitter(Qt.Horizontal, self)
        layout.addWidget(splitter)
        
        # Left panel (controls)
        left = QWidget(self)
        left.setMinimumWidth(350)
        left.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(10, 10, 10, 10)
        lbox.setSpacing(8)
        
        # File operations
        file_group = QGroupBox("File Operations", left)
        file_layout = QVBoxLayout(file_group)
        
        btn_open = QPushButton("Open Shape/Solution File", file_group)
        btn_open.clicked.connect(self._on_open_file)
        file_layout.addWidget(btn_open)
        
        btn_save = QPushButton("Save Container JSON", file_group)
        btn_save.clicked.connect(self._on_save_file)
        file_layout.addWidget(btn_save)
        
        lbox.addWidget(file_group)
        
        # Shape info
        info_group = QGroupBox("Shape Info", left)
        info_layout = QFormLayout(info_group)
        
        self.lbl_file = QLabel("—", info_group)
        self.lbl_count = QLabel("0", info_group)
        
        info_layout.addRow("File:", self.lbl_file)
        info_layout.addRow("Sphere Count:", self.lbl_count)
        
        lbox.addWidget(info_group)
        
        # Editing controls
        edit_group = QGroupBox("Editing", left)
        edit_layout = QVBoxLayout(edit_group)
        
        btn_reset = QPushButton("Reset to Single Sphere", edit_group)
        btn_reset.clicked.connect(self._reset_to_origin)
        edit_layout.addWidget(btn_reset)
        
        # Color picker
        color_layout = QHBoxLayout()
        color_layout.addWidget(QLabel("Color:"))
        self.combo_color = QComboBox()
        self.combo_color.addItems(["blue", "red", "green", "orange", "purple", "yellow"])
        self.combo_color.currentTextChanged.connect(self._on_color_changed)
        color_layout.addWidget(self.combo_color)
        
        # Show neighbors toggle
        self.chk_show_neighbors = QCheckBox("Show Neighbors")
        self.chk_show_neighbors.setChecked(True)
        self.chk_show_neighbors.toggled.connect(self._on_neighbors_toggled)
        color_layout.addWidget(self.chk_show_neighbors)
        
        edit_layout.addLayout(color_layout)
        
        lbox.addWidget(edit_group)
        
        # Instructions
        help_group = QGroupBox("Instructions", left)
        help_layout = QVBoxLayout(help_group)
        help_text = QLabel(
            "• Click on frontier spheres (translucent) to add them\n"
            "• Click on active spheres (solid) to remove them\n"
            "• Origin sphere (0,0,0) cannot be removed\n"
            "• Hover to preview sphere placement\n"
            "• Shape maintains FCC lattice connectivity",
            help_group
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: #666; font-size: 11px;")
        help_layout.addWidget(help_text)
        
        lbox.addWidget(help_group)
        lbox.addStretch()
        
        # Right panel (3D viewer)
        self.viewer = QWebEngineView(self)
        self.viewer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        splitter.addWidget(left)
        splitter.addWidget(self.viewer)
        splitter.setSizes([350, 800])
    
    def _setup_viewer(self):
        """Initialize the 3D viewer with shape editing capabilities."""
        viewer_dir = app_root() / "viewer"
        index_path = viewer_dir / "index.html"
        
        if not index_path.exists():
            print(f"[Shape] Viewer not found at {index_path}")
            return
        
        # Create a separate bridge object for web channel to avoid property warnings
        self.bridge = ShapeEditorBridge(self)
        
        # Setup web channel for JS communication
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.viewer.page().setWebChannel(self.channel)
        
        # Connect to loadFinished signal to update viewer when page is ready
        self.viewer.loadFinished.connect(self._on_viewer_loaded)
        
        # Load viewer
        self.viewer.setUrl(QUrl.fromLocalFile(str(index_path)))
        
        print(f"[Shape] Viewer loaded from {index_path}")
    
    def _on_viewer_loaded(self, success: bool):
        """Called when the viewer page finishes loading."""
        if success:
            print("[Shape] Viewer page loaded successfully")
            # Delay the update to ensure JavaScript is fully initialized
            QTimer.singleShot(500, self._update_viewer)
        else:
            print("[Shape] Viewer page failed to load")
    
    @Slot(str)
    def onSphereClicked(self, click_data_json: str):
        """Handle sphere click events from JavaScript."""
        try:
            click_data = json.loads(click_data_json)
            print(f"[Shape] Click data received: {click_data}")
            print(f"[Shape] Click data keys: {list(click_data.keys())}")
            
            # Handle different possible data structures
            if "position" in click_data:
                pos = click_data["position"]
                x, y, z = pos.get("x", 0), pos.get("y", 0), pos.get("z", 0)
            elif "x" in click_data:
                x, y, z = click_data["x"], click_data["y"], click_data["z"]
            else:
                print(f"[Shape] Unknown click data structure: {click_data}")
                return
                
            print(f"[Shape] Click position: ({x}, {y}, {z})")
            
            # Convert display coordinates back to viewer coordinates
            viewer_coords = self._display_to_viewer(x, y, z)
            print(f"[Shape] Converted to viewer coords: {viewer_coords}")
            
            # Toggle sphere
            if viewer_coords in self.active_spheres:
                if self._deactivate_sphere(viewer_coords):
                    print(f"[Shape] Removed sphere at {viewer_coords}")
                else:
                    print(f"[Shape] Sphere removal failed for {viewer_coords}")
            else:
                if viewer_coords in self.frontier_spheres:
                    print(f"[Shape] Added sphere at {viewer_coords}")
                    self.active_spheres.add(viewer_coords)
                    self.frontier_spheres.remove(viewer_coords)
                    self._rebuild_frontier()
                    self._recompute_shift()
                    self._update_ui()
                    # Use timer to delay viewer update and prevent race conditions
                    QTimer.singleShot(100, self._update_viewer)
                else:
                    print(f"[Shape] Failed to add sphere at {viewer_coords}")
            
        except Exception as e:
            print(f"[Shape] Error handling sphere click: {e}")
            import traceback
            traceback.print_exc()
    
    def _viewer_eval_js(self, js_code: str):
        """Execute JavaScript in the viewer."""
        try:
            print(f"[Shape] Executing JS: {js_code[:100]}...")
            self.viewer.page().runJavaScript(js_code)
        except Exception as e:
            print(f"[Shape] JS eval error: {e}")
    
    # Coordinate conversion functions
    def _engine_to_viewer(self, engine_coords: Tuple[int, int, int]) -> Tuple[int, int, int]:
        """Convert Engine FCC [i,j,k] to Viewer FCC [X,Y,Z]."""
        i, j, k = engine_coords
        X = j + k
        Y = i + k
        Z = i + j
        return (X, Y, Z)
    
    def _viewer_to_engine(self, viewer_coords: Tuple[int, int, int]) -> Tuple[int, int, int]:
        """Convert Viewer FCC [X,Y,Z] to Engine FCC [i,j,k]."""
        X, Y, Z = viewer_coords
        # Check parity constraint
        if (X + Y + Z) % 2 != 0:
            raise ValueError(f"Invalid viewer coordinates {viewer_coords}: X+Y+Z must be even")
        
        i = (Y + Z - X) // 2
        j = (Z + X - Y) // 2
        k = (X + Y - Z) // 2
        return (i, j, k)
    
    # FCC lattice math (now works in Viewer FCC space)
    def _parity_ok(self, viewer_coords: Tuple[int, int, int]) -> bool:
        """Check if viewer coordinates satisfy parity constraint (even sum)."""
        X, Y, Z = viewer_coords
        return ((X + Y + Z) & 1) == self.parity_target
    
    def _add_idx(self, a: Tuple[int, int, int], b: Tuple[int, int, int]) -> Tuple[int, int, int]:
        """Add two index tuples."""
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
    
    def _recompute_shift(self):
        """Compute shift to keep all viewer coordinates non-negative with correct parity."""
        if not self.active_spheres and not self.frontier_spheres:
            self.shift = (0, 0, 0)
            return
        
        # All coordinates are already in Viewer FCC space [X,Y,Z]
        all_coords = self.active_spheres.union(self.frontier_spheres)
        
        if not all_coords:
            self.shift = (0, 0, 0)
            return
        
        # Find minimum viewer coordinates
        min_X = min(X for (X, _, _) in all_coords)
        min_Y = min(Y for (_, Y, _) in all_coords)
        min_Z = min(Z for (_, _, Z) in all_coords)
        
        # Shift to make all viewer coordinates non-negative
        shift_X = -min_X if min_X < 0 else 0
        shift_Y = -min_Y if min_Y < 0 else 0
        shift_Z = -min_Z if min_Z < 0 else 0
        
        # Adjust for parity constraint
        if ((shift_X + shift_Y + shift_Z) - self.parity_target) & 1:
            shift_Z += 1
        
        self.shift = (shift_X, shift_Y, shift_Z)
    
    def _viewer_to_display(self, viewer_coords: Tuple[int, int, int]) -> Tuple[float, float, float]:
        """Convert Viewer FCC [X,Y,Z] to 3D display coordinates."""
        X, Y, Z = viewer_coords
        
        # Apply shift to keep coordinates positive
        shift_X, shift_Y, shift_Z = self.shift
        X_shifted = X + shift_X
        Y_shifted = Y + shift_Y
        Z_shifted = Z + shift_Z
        
        # Scale by SPACING for 3D display
        x = X_shifted * self.SPACING
        y = Y_shifted * self.SPACING
        z = Z_shifted * self.SPACING
        
        return (x, y, z)
    
    def _display_to_viewer(self, x: float, y: float, z: float) -> Tuple[int, int, int]:
        """Convert 3D display coordinates back to Viewer FCC [X,Y,Z]."""
        # Convert scaled display coordinates back to integer viewer coordinates
        s = self.SPACING
        X_shifted = round(x / s)
        Y_shifted = round(y / s)
        Z_shifted = round(z / s)
        
        # Remove shift to get original viewer coordinates
        shift_X, shift_Y, shift_Z = self.shift
        X = X_shifted - shift_X
        Y = Y_shifted - shift_Y
        Z = Z_shifted - shift_Z
        
        return (X, Y, Z)
    
    def _rebuild_frontier(self):
        """Rebuild frontier spheres around active spheres."""
        self.frontier_spheres.clear()
        
        for active_idx in self.active_spheres:
            for offset in self.FCC_OFFSETS:
                neighbor_idx = self._add_idx(active_idx, offset)
                
                if (self._parity_ok(neighbor_idx) and 
                    neighbor_idx not in self.active_spheres and
                    neighbor_idx != (0, 0, 0)):  # Origin always active
                    self.frontier_spheres.add(neighbor_idx)
    
    def _activate_sphere(self, idx: Tuple[int, int, int]) -> bool:
        """Add sphere to active set."""
        if idx in self.active_spheres:
            return False
        
        self.active_spheres.add(idx)
        if idx in self.frontier_spheres:
            self.frontier_spheres.remove(idx)
        
        self._rebuild_frontier()
        self._recompute_shift()
        return True
    
    def _deactivate_sphere(self, idx: Tuple[int, int, int]) -> bool:
        """Remove sphere from active set (except origin)."""
        if idx == (0, 0, 0) or idx not in self.active_spheres:
            return False
        
        self.active_spheres.remove(idx)
    def _reset_to_origin(self):
        """Reset to single origin sphere."""
        self.active_spheres.clear()
        self.frontier_spheres.clear()
        
        # Start with origin sphere (in Viewer FCC space)
        self.active_spheres.add((0, 0, 0))
        
        self._rebuild_frontier()
        self._recompute_shift()
        
        self._current_file = None
        self._shape_name = "untitled"
        
        self._update_ui()
        
        # Force viewer reload to ensure it's responsive
        self._setup_viewer()
        QTimer.singleShot(1000, self._update_viewer)
    
    def _update_ui(self):
        """Update UI labels and counters."""
        self.lbl_count.setText(str(len(self.active_spheres)))
        
        if self._current_file:
            self.lbl_file.setText(self._current_file.name)
        else:
            self.lbl_file.setText("—")
    
    def _update_viewer(self):
        """Update 3D viewer with current shape state."""
        if not hasattr(self, 'viewer'):
            print("[Shape] Viewer not available, skipping update")
            return
            
        print(f"[Shape] Updating viewer: {len(self.active_spheres)} active, {len(self.frontier_spheres)} frontier")
        
        # Build sphere data for viewer
        active_spheres = []
        for viewer_coords in self.active_spheres:
            x, y, z = self._viewer_to_display(viewer_coords)
            active_spheres.append({"x": x, "y": y, "z": z})
            if len(active_spheres) <= 3:  # Debug first few spheres
                print(f"[Shape] Viewer {viewer_coords} -> Display ({x}, {y}, {z})")
        
        frontier_spheres = []
        for viewer_coords in self.frontier_spheres:
            x, y, z = self._viewer_to_display(viewer_coords)
            frontier_spheres.append({"x": x, "y": y, "z": z})
        
        # Get current color (with fallback)
        color_name = "blue"  # default
        if hasattr(self, 'combo_color'):
            color_name = self.combo_color.currentText()
        
        # Send to viewer
        js_code = f"""
        console.log('[Shape] Updating viewer with', {len(active_spheres)}, 'active spheres');
        if (window.viewer && window.viewer.loadShapeEditor) {{
           data = {{
            'active_spheres': {json.dumps(active_spheres)},
            'frontier_spheres': {json.dumps(frontier_spheres if self.chk_show_neighbors.isChecked() else [])},
            'all_frontier_spheres': {json.dumps(frontier_spheres)},
            'radius': {self.RADIUS},
            'edit_color': "{color_name}"
        }};
        window.viewer.loadShapeEditor(data);
        }} else {{
            console.error('[Shape] loadShapeEditor function not available');
        }}
        """
        
        self._viewer_eval_js(js_code)
    
    # Event handlers
    def _on_open_file(self):
        """Open shape definition or solution file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Shape or Solution File",
            str(repo_root() / "data" / "containers"),
            "JSON Files (*.json);;All Files (*)"
        )
        
        if not file_path:
            return
        
        try:
            self._load_file(Path(file_path))
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load file:\n{e}")
    
    def _load_file(self, file_path: Path):
        """Load shape from JSON file."""
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        self.active_spheres.clear()
        self.frontier_spheres.clear()
        
        # Handle different file formats
        if "cells" in data:
            # Container format: {"cells": [[i,j,k], ...]} - Engine FCC coordinates
            for cell in data["cells"]:
                if len(cell) >= 3:
                    engine_coords = (int(cell[0]), int(cell[1]), int(cell[2]))
                    # Convert Engine FCC to Viewer FCC
                    viewer_coords = self._engine_to_viewer(engine_coords)
                    if self._parity_ok(viewer_coords):
                        self.active_spheres.add(viewer_coords)
        
        elif "pieces" in data:
            # Solution format: extract engine coordinates from pieces
            pieces = data.get("pieces", [])
            if pieces:
                # Look for engine coordinates in piece metadata or reconstruct from world
                piece = pieces[0]
                
                # Try to find engine coordinates directly
                if "engine_coords" in piece:
                    for engine_coord in piece["engine_coords"]:
                        if len(engine_coord) >= 3:
                            engine_coords = (int(engine_coord[0]), int(engine_coord[1]), int(engine_coord[2]))
                            viewer_coords = self._engine_to_viewer(engine_coords)
                            if self._parity_ok(viewer_coords):
                                self.active_spheres.add(viewer_coords)
                else:
                    # Fallback: convert world coordinates to engine, then to viewer
                    centers = piece.get("centers", [])
                    for center in centers:
                        # Convert world coordinates to engine coordinates first
                        world_pos = (center["x"], center["y"], center["z"])
                        try:
                            # Assume world coordinates are already in viewer space, convert to engine
                            # This is a fallback - ideally solution files would have engine coords
                            X, Y, Z = round(world_pos[0]), round(world_pos[1]), round(world_pos[2])
                            if (X + Y + Z) % 2 == 0:  # Valid viewer coordinates
                                engine_coords = self._viewer_to_engine((X, Y, Z))
                                viewer_coords = self._engine_to_viewer(engine_coords)  # Round trip for validation
                                if self._parity_ok(viewer_coords):
                                    self.active_spheres.add(viewer_coords)
                        except:
                            continue
        
        # Ensure origin is always active (in Viewer FCC space)
        self.active_spheres.add((0, 0, 0))
        
        self._rebuild_frontier()
        self._recompute_shift()
        
        self._current_file = file_path
        self._shape_name = file_path.stem
        
        self._update_ui()
        # Force viewer reload to ensure it's responsive after file load
        self._setup_viewer()
        QTimer.singleShot(1000, self._update_viewer)
        
        print(f"[Shape] Loaded {len(self.active_spheres)} spheres from {file_path.name}")
        print(f"[Shape] Sample loaded spheres: {list(self.active_spheres)[:5]}")
        print(f"[Shape] Current shift: {self.shift}")
    
    def _on_save_file(self):
        """Save current shape as container JSON."""
        if not self.active_spheres:
            QMessageBox.warning(self, "Warning", "No spheres to save!")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Container JSON",
            str(repo_root() / "data" / "containers" / f"{self._shape_name}.json"),
            "JSON Files (*.json)"
        )
        
        if not file_path:
            return
        
        try:
            self._save_container_json(Path(file_path))
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save file:\n{e}")
    
    def _save_container_json(self, file_path: Path):
        """Export current shape as solver-ready container JSON."""
        # Convert container to engine coordinates for saving
        cells = []
        for viewer_coords in self.active_spheres:
            # Convert Viewer FCC to Engine FCC
            engine_coords = self._viewer_to_engine(viewer_coords)
            i, j, k = engine_coords
            cells.append([i, j, k])      
        container_data = {
            "lattice": "FCC",
            "version": 1,
            "r": float(self.RADIUS),
            "meta": {
                "name": self._shape_name,
                "created_by": "shape_editor",
                "sphere_count": len(cells)
            },
            "cells": cells
        }
        
        with open(file_path, 'w') as f:
            json.dump(container_data, f, indent=2)
        
        print(f"[Shape] Saved {len(cells)} spheres to {file_path}")
        QMessageBox.information(self, "Success", f"Saved container with {len(cells)} spheres")
    
    def _on_color_changed(self, color_name: str):
        """Update viewer color when selection changes."""
        self._update_viewer()
    
    def _on_neighbors_toggled(self, checked: bool):
        """Toggle visibility of frontier spheres."""
        self._update_viewer()


class ShapeEditorBridge(QObject):
    """Separate bridge object for web channel communication to avoid property warnings."""
    
    def __init__(self, shape_tab):
        super().__init__()
        self.shape_tab = shape_tab
    
    @Slot(str)
    def onSphereClicked(self, click_data_json: str):
        """Handle sphere click events from JavaScript."""
        self.shape_tab.onSphereClicked(click_data_json)
