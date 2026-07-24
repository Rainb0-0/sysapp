# Schematic_View_tab/schematic_view_tab.py
"""
Thin Qt container for the schematic view.

Mirrors ComponentTreeTab in Component_Tree_Window.py: this class owns NO
drawing logic. It just hosts a QWebEngineView, wires up SchematicBridge
through a QWebChannel, and exposes a small toolbar (Refresh / Save Layout /
Export PDF / Export PNG).

Expected web assets (next file to build), sitting next to this file in
Schematic_View_tab/web_assets/:
    schematic_view.html   - page shell, loads qwebchannel.js + the scripts below
    schematic_render.js   - draws modules/connectors/pins as SVG, handles drag
    schematic_routing.js  - orthogonal routing ported from smart_connection.py
    d3.min.js             - reused for d3-zoom / d3-drag (same file already
                             used by Component_Tree_Window.py)

The JS side must expose these on `window`, matching the same contract
Component_Tree_Window.py already uses successfully for the tree:
    fitView()                 - fit/center the current scene in the viewport
    getExportBounds()         - JSON.stringify({widthPx, heightPx}) of the
                                 full scene's bounding box
    setExportOverlayVisible() - show/hide any on-screen overlay (zoom %, etc.)
    triggerSaveLayout()       - gather current module/connector positions and
                                 call bridge.save_module_positions(...) /
                                 bridge.save_connector_positions(...) itself
"""

import os
import math
import tempfile
import shutil

from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QFileDialog,
    QMessageBox,
    QProgressDialog,
    QSplitter,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtCore import Qt, QUrl, QTimer, QSizeF, QMarginsF, QByteArray
from PyQt5.QtGui import QPageSize, QPageLayout

from styles.style_manager import style_manager, create_styled_button
from styles.design_system import BorderRadius
from styles.theme_manager import theme_manager

from Schematic_View_tab.schematic_bridge import SchematicBridge
from Schematic_View_tab.schematic_tree_selector import SchematicTreeSelector
from Schematic_View_tab.mode.mode_web_panel import ModeWebPanel

# ---------------------------------------------------------------------------
# Grid layout constants for auto-positioning new modules (mirrors
# schematic_scene_model.py to avoid circular imports).
# ---------------------------------------------------------------------------
_GRID_MARGIN = 40.0
_GRID_CELL_W = 380.0
_GRID_CELL_H = 280.0
_GRID_COLUMNS = 4
_DEFAULT_MODULE_W = 160.0
_DEFAULT_MODULE_H = 100.0


def _place_module_without_overlap(module_id: int, module_name: str):
    """
    Find a grid-aligned position that does NOT overlap any existing module
    in the current project, then update the module's pos_x/pos_y in the DB.
    Uses the same grid layout as _grid_fallback_position() in
    schematic_scene_model.py.
    """
    from database import get_connection, get_current_project_id

    project_id = get_current_project_id()
    if project_id is None:
        return

    # Load bounding boxes of all existing modules
    existing = []  # list of (x, y, w, h)
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT pos_x, pos_y, width, height FROM modules "
                "WHERE project_id = %s AND id != %s AND pos_x IS NOT NULL",
                (project_id, module_id),
            )
            for row in cur.fetchall():
                rx, ry, rw, rh = row
                if rx is not None and ry is not None:
                    existing.append((float(rx), float(ry), float(rw or _DEFAULT_MODULE_W), float(rh or _DEFAULT_MODULE_H)))
    except Exception:
        existing = []

    def _rects_overlap(ax, ay, aw, ah, bx, by, bw, bh):
        """Return True if two axis-aligned rectangles overlap (with 10px margin)."""
        M = 10
        return not (ax + aw + M <= bx or bx + bw + M <= ax or ay + ah + M <= by or by + bh + M <= ay)

    # Try grid positions in order
    best_x, best_y = _GRID_MARGIN, _GRID_MARGIN
    found = False
    for row in range(50):  # limit to 50 rows
        for col in range(_GRID_COLUMNS):
            cx = _GRID_MARGIN + col * _GRID_CELL_W
            cy = _GRID_MARGIN + row * _GRID_CELL_H

            # Check against all existing modules
            overlap = False
            for ex, ey, ew, eh in existing:
                if _rects_overlap(cx, cy, _DEFAULT_MODULE_W, _DEFAULT_MODULE_H, ex, ey, ew, eh):
                    overlap = True
                    break

            if not overlap:
                best_x, best_y = cx, cy
                found = True
                break
        if found:
            break

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE modules SET pos_x = %s, pos_y = %s WHERE id = %s AND project_id = %s",
                (best_x, best_y, module_id, project_id),
            )
            conn.commit()
    except Exception:
        pass


class SchematicViewTab(QWidget):
    """New web-based schematic view tab (Python data + JS/SVG rendering)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SchematicViewTab")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.assets_dir = os.path.join(os.path.dirname(__file__), "web_assets")

        self.bridge = SchematicBridge()
        self.bridge.set_host_widget(self)
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.bridge.save_finished.connect(self._on_save_finished)

        self.apply_main_style()
        style_manager.theme_changed.connect(self.on_theme_changed)

        self.setup_ui()

        QTimer.singleShot(500, lambda: self.bridge.get_scene_data())

    # ------------------------------------------------------------------
    # Styling (same pattern as ComponentTreeTab)
    # ------------------------------------------------------------------
    def apply_main_style(self):
        self.setStyleSheet(f"""
            QWidget#SchematicViewTab {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}
        """)

    def on_theme_changed(self, theme_name):
        self.apply_main_style()
        self.update_toolbar_style()
        self.update_container_style()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------
    def setup_ui(self):
        if not self._check_assets_available():
            return

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        self.create_toolbar(main_layout)

        # ---- Splitter: tree | mode-panel | scene ----
        self.splitter = QSplitter(Qt.Horizontal)

        # Tree selector
        self.tree_selector = SchematicTreeSelector()
        self.tree_selector.selectionChanged.connect(self._on_tree_selection_changed)
        self.tree_selector.setMaximumWidth(280)
        self.splitter.addWidget(self.tree_selector)

        # Mode panel (hidden by default)
        self.mode_panel = ModeWebPanel()
        self.mode_panel.set_tree_selector(self.tree_selector)
        self.mode_panel.set_bridge(self.bridge)
        self.mode_panel.modeEntered.connect(self._on_mode_entered)
        self.mode_panel.modeExited.connect(self._on_mode_exited)
        self.mode_panel.modeCreated.connect(self._on_mode_list_changed)
        self.mode_panel.modeDeleted.connect(self._on_mode_list_changed)
        self.mode_panel.modeSaved.connect(self._on_mode_list_changed)
        self.mode_panel.hide()
        self.splitter.addWidget(self.mode_panel)

        self.splitter.addWidget(self.create_scene_container())

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 0)  # mode panel doesn't stretch
        self.splitter.setStretchFactor(2, 1)
        self.splitter.setSizes([260, 0, 900])

        main_layout.addWidget(self.splitter)

    def _on_tree_selection_changed(self, checked_ids: dict):
        """
        checked_ids comes from SchematicTreeSelector.get_checked_ids():
          {'subsystems': [...], 'modules': [...], 'connectors': [...], 'pins': [...]}
        Passes the full selection to the bridge so it can filter modules,
        connectors, and pins in the scene.
        """
        import json

        self.bridge.set_selection(
            modules=checked_ids.get("modules", []),
            connectors=checked_ids.get("connectors", []),
            pins=checked_ids.get("pins", []),
        )

    def _check_assets_available(self):
        html_path = os.path.join(self.assets_dir, "schematic_view.html")
        if not os.path.exists(html_path):
            QMessageBox.critical(
                self,
                "Error",
                f"schematic_view.html not found at:\n{html_path}\n\n"
                "Build the web_assets/ folder before using this tab.",
            )
            return False
        return True

    def create_toolbar(self, main_layout):
        self.toolbar = QFrame()
        self.toolbar.setFixedHeight(50)
        self.update_toolbar_style()

        layout = QHBoxLayout(self.toolbar)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        title = QLabel("Schematic View")
        title.setStyleSheet("font-weight: bold; font-size: 16px;")
        layout.addWidget(title)
        layout.addSpacing(30)

        self.add_module_btn = create_styled_button("+ Module", "normal")
        self.add_module_btn.setToolTip("Create a new module")
        self.add_module_btn.clicked.connect(self.add_module)
        layout.addWidget(self.add_module_btn)
        layout.addSpacing(30)

        self.fit_view_btn = create_styled_button("Fit View", "normal")
        self.fit_view_btn.setToolTip("Fit the schematic view to show all content")
        self.fit_view_btn.clicked.connect(self.fit_view)
        layout.addWidget(self.fit_view_btn)
        layout.addSpacing(8)

        self.refresh_btn = create_styled_button("Refresh", "normal")
        self.save_layout_btn = create_styled_button("Save Layout", "normal")
        self.refresh_btn.setToolTip("Reload scene from database")
        self.save_layout_btn.setToolTip("Save current module/connector positions")
        self.refresh_btn.clicked.connect(self.refresh_scene)
        self.save_layout_btn.clicked.connect(self.save_layout)
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.save_layout_btn)
        layout.addSpacing(8)

        # Modes toggle button
        self.modes_btn = create_styled_button("🗂️ Modes", "normal")
        self.modes_btn.setToolTip("Show/hide mode management panel")
        self.modes_btn.setCheckable(True)
        self.modes_btn.toggled.connect(self._toggle_mode_panel)
        layout.addWidget(self.modes_btn)
        layout.addSpacing(30)

        self.export_pdf_btn = create_styled_button("PDF", "normal")
        self.export_png_btn = create_styled_button("PNG", "normal")
        self.export_pdf_btn.setToolTip("Export schematic to vector PDF")
        self.export_png_btn.setToolTip("Export schematic to high-res PNG")
        self.export_pdf_btn.clicked.connect(self.export_to_pdf)
        self.export_png_btn.clicked.connect(self.export_to_png)
        layout.addWidget(self.export_pdf_btn)
        layout.addWidget(self.export_png_btn)

        layout.addStretch()
        main_layout.addWidget(self.toolbar)

        # Disabled until the page finishes loading
        for btn in (
            self.add_module_btn,
            self.fit_view_btn,
            self.refresh_btn,
            self.save_layout_btn,
            self.modes_btn,
            self.export_pdf_btn,
            self.export_png_btn,
        ):
            btn.setEnabled(False)

    def update_toolbar_style(self):
        if hasattr(self, "toolbar"):
            self.toolbar.setStyleSheet(f"""
                QFrame {{
                    background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                    border: 1px solid {theme_manager.get_color('primary_light')};
                    border-radius: {BorderRadius.LARGE};
                }}
                QLabel {{
                    color: {theme_manager.get_color('text_primary')};
                    background: transparent;
                    border: none;
                }}
            """)

    def create_scene_container(self):
        self.container = QFrame()
        self.update_container_style()

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(8, 8, 8, 8)

        self.web_view = QWebEngineView()
        self.web_view.page().setWebChannel(self.channel)
        self.web_view.setFocusPolicy(Qt.StrongFocus)

        self.page_ready = False
        self.web_view.loadFinished.connect(self._on_page_loaded)

        self.load_scene_html()

        layout.addWidget(self.web_view)
        return self.container

    def update_container_style(self):
        if hasattr(self, "container"):
            self.container.setStyleSheet(f"""
                QFrame {{
                    background: {theme_manager.get_color('primary_dark')};
                    border: 1px solid {theme_manager.get_color('primary_light')};
                    border-radius: {BorderRadius.XLARGE};
                }}
            """)

    # ------------------------------------------------------------------
    # Loading the HTML/JS assets
    # ------------------------------------------------------------------
    def load_scene_html(self):
        self.temp_dir = tempfile.mkdtemp()
        for filename in (
            "schematic_view.html",
            "schematic_render.js",
            "schematic_routing.js",
            "d3.min.js",
        ):
            src = os.path.join(self.assets_dir, filename)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(self.temp_dir, filename))

        html_path = os.path.join(self.temp_dir, "schematic_view.html")
        self.web_view.load(QUrl.fromLocalFile(html_path))
        self.temp_html_file = html_path

    def _on_page_loaded(self, ok: bool):
        self.page_ready = bool(ok)
        for btn in (
            self.add_module_btn,
            self.fit_view_btn,
            self.refresh_btn,
            self.save_layout_btn,
            self.modes_btn,
            self.export_pdf_btn,
            self.export_png_btn,
        ):
            btn.setEnabled(self.page_ready)
        if self.page_ready:
            self.bridge.get_scene_data()

    def _on_save_finished(self, success: bool, message: str):
        # Silent save — the JS side already shows a toast notification
        pass

    # ------------------------------------------------------------------
    # Mode panel integration
    # ------------------------------------------------------------------
    def _toggle_mode_panel(self, visible: bool):
        """Show or hide the mode management panel."""
        if visible:
            self.mode_panel.refresh_modes()
            self.mode_panel.show()
            # Adjust splitter sizes to make room
            sizes = self._get_splitter_sizes()
            if len(sizes) >= 3:
                sizes[1] = 240  # mode panel width
                # Shrink tree selector proportionally
                tree_w = sizes[0]
                if tree_w > 200:
                    sizes[0] = max(180, tree_w - 240)
                self.splitter.setSizes(sizes)
        else:
            self.mode_panel.hide()
            # Restore splitter
            sizes = self._get_splitter_sizes()
            if len(sizes) >= 3:
                sizes[1] = 0
                if sizes[0] < 200:
                    sizes[0] = 260
                self.splitter.setSizes(sizes)

    def _get_splitter_sizes(self):
        """Get splitter sizes (handle case where splitter doesn't exist yet)."""
        if hasattr(self, "splitter"):
            return list(self.splitter.sizes())
        return [260, 0, 900]

    def _on_mode_entered(self, mode_name: str):
        """Called when a mode is entered — refresh scene."""
        if self.page_ready:
            self.bridge.get_scene_data()

    def _on_mode_exited(self):
        """Called when mode is exited — refresh scene."""
        if self.page_ready:
            self.bridge.get_scene_data()

    def _on_mode_list_changed(self, mode_name: str = None):
        """Called when modes are created/deleted/saved — refresh scene."""
        if self.page_ready:
            self.bridge.get_scene_data()

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------
    def refresh_scene(self):
        """Refresh the scene data AND the tree selector from the database."""
        self.bridge.refresh_scene_data()

        # Refresh the tree selector silently, then re-apply saved selection
        # This avoids the flicker caused by refresh_tree() emitting an empty
        # selection and then apply_selection() emitting a second time.
        if hasattr(self, "tree_selector") and hasattr(self.tree_selector, "refresh_tree"):
            saved_selection = self.tree_selector.get_checked_ids()
            self.tree_selector.refresh_tree(emit_selection=False)
            self.tree_selector.apply_selection(saved_selection)

    def add_module(self):
        from PyQt5.QtWidgets import QInputDialog, QComboBox, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QDialogButtonBox
        from PyQt5.QtGui import QColor, QPixmap, QPainter
        from database import get_connection, get_current_project_id

        # Color palette matching the Architecture View tab
        COLOR_MAP = {
            "Default": "#33A444",
            "Red": "#FF0000",
            "Blue": "#0000FF",
            "Yellow": "#FFFF00",
            "Purple": "#800080",
            "Orange": "#FFA500",
            "Gray": "#5D5A5A",
        }

        # First, ask for the module name
        name, ok = QInputDialog.getText(self, "New Module", "Module name:")
        if not ok or not name.strip():
            return

        # Load subsystems for the dropdown
        project_id = get_current_project_id()
        subsystems = []
        selected_subsystem_id = None
        selected_color = "#33A444"  # Default color
        if project_id is not None:
            try:
                with get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT id, name FROM subsystems WHERE project_id = %s ORDER BY name",
                        (project_id,),
                    )
                    subsystems = cur.fetchall()
            except Exception:
                pass

        dialog = QDialog(self)
        dialog.setWindowTitle("New Module")
        layout = QVBoxLayout(dialog)

        label = QLabel('Create module "' + name.strip() + '":')
        layout.addWidget(label)

        # Subsystem row (only if subsystems exist)
        if subsystems:
            sub_layout = QHBoxLayout()
            sub_layout.addWidget(QLabel("Subsystem:"))
            combo = QComboBox()
            for ss_id, ss_name in subsystems:
                combo.addItem(ss_name, ss_id)
            sub_layout.addWidget(combo)
            sub_layout.addStretch()
            layout.addLayout(sub_layout)

        # Color row
        color_layout = QHBoxLayout()
        color_layout.addWidget(QLabel("Color:"))
        color_combo = QComboBox()
        color_swatch = QLabel()
        color_swatch.setFixedSize(20, 20)

        for color_name, color_hex in COLOR_MAP.items():
            color_combo.addItem(color_name, color_hex)

        def _update_swatch():
            c = QColor(color_combo.currentData())
            pm = QPixmap(20, 20)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setBrush(c)
            p.drawRoundedRect(0, 0, 19, 19, 4, 4)
            p.end()
            color_swatch.setPixmap(pm)

        color_combo.currentIndexChanged.connect(_update_swatch)
        _update_swatch()

        color_layout.addWidget(color_combo)
        color_layout.addWidget(color_swatch)
        color_layout.addStretch()
        layout.addLayout(color_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() != QDialog.Accepted:
            return  # User cancelled - abort entirely

        if subsystems:
            selected_subsystem_id = combo.currentData()
        selected_color = color_combo.currentData()

        # Create module via bridge with just name (matches @pyqtSlot(str))
        new_id = self.bridge.create_module(name.strip())

        # Update subsystem and color directly in the DB
        if new_id > 0:
            try:
                with get_connection() as conn:
                    cur = conn.cursor()
                    if selected_subsystem_id is not None:
                        cur.execute(
                            "UPDATE modules SET subsystem_id = %s, color = %s WHERE id = %s AND project_id = %s",
                            (selected_subsystem_id, selected_color, new_id, project_id),
                        )
                    else:
                        cur.execute(
                            "UPDATE modules SET color = %s WHERE id = %s AND project_id = %s",
                            (selected_color, new_id, project_id),
                        )
                    conn.commit()
            except Exception:
                pass

        # Position the new module to avoid overlapping existing modules
        if new_id > 0:
            _place_module_without_overlap(new_id, name.strip())

        # Force refresh the scene and the tree selector immediately.
        # The new module is injected into the saved selection so it gets
        # auto-checked and appears on screen.
        self.bridge.get_scene_data()

        if hasattr(self, "tree_selector") and hasattr(self.tree_selector, "refresh_tree"):
            saved_selection = self.tree_selector.get_checked_ids()
            # Include the newly created module so it shows up on screen
            if new_id > 0 and new_id not in saved_selection["modules"]:
                saved_selection["modules"].append(new_id)
            self.tree_selector.refresh_tree(emit_selection=False)
            self.tree_selector.apply_selection(saved_selection)

    def save_layout(self):
        """Ask the page to gather positions and push them through the bridge."""
        self.web_view.page().runJavaScript(
            "if (typeof triggerSaveLayout === 'function') triggerSaveLayout();"
        )

    def fit_view(self):
        """Fit the schematic view to show all content."""
        self.web_view.page().runJavaScript(
            "if (typeof fitView === 'function') fitView();"
        )

    def _get_timestamp(self):
        from datetime import datetime

        return datetime.now().strftime("%Y%m%d_%H%M%S")

    # ------------------------------------------------------------------
    # PDF export
    # (identical pipeline to the fixed Component_Tree_Window.py:
    #  no forced fitView() before capture, fitView() only AFTER resizing
    #  the offscreen export canvas, 800ms wait for its 650ms transition,
    #  and ceil()+buffer mm sizing to avoid a phantom extra page)
    # ------------------------------------------------------------------
    def export_to_pdf(self):
        try:
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Schematic to PDF",
                f"schematic_{self._get_timestamp()}.pdf",
                "PDF Files (*.pdf);;All Files (*)",
            )
            if not file_path:
                return

            progress = QProgressDialog("Creating PDF...", "Cancel", 0, 100, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            progress.setValue(20)

            self._pending_pdf_path = file_path
            self._pending_pdf_progress = progress
            self._pending_pdf_original_size = self.web_view.size()

            self.web_view.page().runJavaScript(
                "if (typeof setExportOverlayVisible === 'function') setExportOverlayVisible(false);"
            )
            QTimer.singleShot(
                800,
                lambda: self.web_view.page().runJavaScript(
                    "if (typeof getExportBounds === 'function') { return getExportBounds(); } "
                    "return JSON.stringify({widthPx: 1200, heightPx: 800});",
                    self._on_pdf_export_bounds_ready,
                ),
            )
            progress.setValue(60)
        except Exception as e:
            if "progress" in locals():
                progress.close()
            QMessageBox.critical(self, "PDF Export Error", f"Failed to export: {e}")

    def _on_pdf_export_bounds_ready(self, result):
        import json

        progress = getattr(self, "_pending_pdf_progress", None)
        file_path = getattr(self, "_pending_pdf_path", None)

        try:
            bounds = (
                json.loads(result)
                if isinstance(result, str)
                else json.loads(result or "{}")
            )

            width_px = max(320, int(bounds.get("widthPx", 1200)))
            height_px = max(240, int(bounds.get("heightPx", 800)))
            width_mm = max(120, math.ceil(width_px * 25.4 / 96) + 2)
            height_mm = max(120, math.ceil(height_px * 25.4 / 96) + 2)

            page_layout = QPageLayout(
                QPageSize(QSizeF(width_mm, height_mm), QPageSize.Unit.Millimeter),
                QPageLayout.Portrait,
                QMarginsF(0, 0, 0, 0),
            )

            self.web_view.resize(width_px, height_px)
            self.web_view.page().runJavaScript(
                "if (typeof fitView === 'function') fitView();"
            )
            QTimer.singleShot(
                800,
                lambda: self.web_view.page().printToPdf(
                    self._handle_pdf_export_finished,
                    page_layout,
                ),
            )
        except Exception as e:
            if progress is not None:
                progress.close()
            QMessageBox.critical(
                self, "PDF Export Error", f"Failed to size PDF page: {e}"
            )
            self._pending_pdf_path = None
            self._pending_pdf_progress = None

    def _handle_pdf_export_finished(self, data):
        progress = getattr(self, "_pending_pdf_progress", None)
        file_path = getattr(self, "_pending_pdf_path", None)

        try:
            pdf_bytes = bytes(data) if data is not None else b""
            if not file_path:
                raise RuntimeError("No output path available for PDF export")

            with open(file_path, "wb") as f:
                f.write(pdf_bytes)

            self.web_view.resize(
                getattr(self, "_pending_pdf_original_size", self.web_view.size())
            )
            self.web_view.page().runJavaScript(
                "if (typeof setExportOverlayVisible === 'function') setExportOverlayVisible(true);"
            )

            if progress is not None:
                progress.setValue(100)
                progress.close()

            file_size = os.path.getsize(file_path) / (1024 * 1024)
            QMessageBox.information(
                self,
                "PDF Export Successful",
                f"File: {os.path.basename(file_path)}\nSize: {file_size:.2f} MB",
            )
        except Exception as e:
            if progress is not None:
                progress.close()
            QMessageBox.critical(self, "PDF Export Error", f"Failed to write PDF: {e}")
        finally:
            self._pending_pdf_path = None
            self._pending_pdf_progress = None

    # ------------------------------------------------------------------
    # PNG export — grab the web view, scale down to a sane resolution to
    # avoid multi-MB files from high-DPI screen grabs, then save with
    # maximum zlib compression (quality=100 for lossless PNG).
    # ------------------------------------------------------------------
    def export_to_png(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Schematic to PNG",
            f"schematic_{self._get_timestamp()}.png",
            "PNG Files (*.png);;All Files (*)",
        )
        if not file_path:
            return

        progress = QProgressDialog("Creating PNG...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.show()
        progress.setValue(30)

        pixmap = self.web_view.grab()
        progress.setValue(60)

        # Scale down if the grab is very large (happens on high-DPI displays)
        # Limit longest side to 2400px — enough for crisp text, much smaller files.
        MAX_DIM = 2400
        if pixmap.width() > MAX_DIM or pixmap.height() > MAX_DIM:
            pixmap = pixmap.scaled(
                MAX_DIM, MAX_DIM,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        progress.setValue(80)

        # For PNG in Qt, higher quality = better zlib compression (smaller file)
        if not pixmap.save(file_path, "PNG", 100):
            progress.close()
            QMessageBox.critical(self, "PNG Export Error", "Failed to save PNG file.")
            return

        progress.setValue(100)
        progress.close()
        file_size = os.path.getsize(file_path) / (1024 * 1024)
        QMessageBox.information(
            self,
            "PNG Export Successful",
            f"File: {os.path.basename(file_path)}\nSize: {file_size:.2f} MB",
        )

    def refresh_all(self):
        """Refresh the scene data in the web view."""
        if self.page_ready:
            self.bridge.get_scene_data()

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if hasattr(self, "temp_dir"):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass
        super().closeEvent(event)
