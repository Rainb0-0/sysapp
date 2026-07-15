# Schematic_View_tab/schematic_bridge.py
"""
Python <-> JavaScript bridge for the schematic view.

Mirrors ComponentTreeBridge in Component_Tree_Window.py: Python owns data
and persistence, JS (loaded in a QWebEngineView via QWebChannel) owns
rendering and interaction.

Wiring (same pattern as the component tree):

    self.bridge = SchematicBridge()
    self.channel = QWebChannel()
    self.channel.registerObject("bridge", self.bridge)
    self.web_view.page().setWebChannel(self.channel)

On the JS side, after `new QWebChannel(qt.webChannelTransport, ...)`,
`window.bridge` exposes every @pyqtSlot below, and the page listens to
`bridge.scene_data_ready.connect(json => ...)` etc.
"""

import json
from typing import Any, Dict

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from database import get_current_project_id
from styles.style_manager import style_manager
from styles.theme_manager import theme_manager

from Schematic_View_tab.schematic_scene_model import (
    load_schematic_scene,
    save_module_positions as persist_module_positions,
    save_connector_positions as persist_connector_positions,
    create_interface as persist_create_interface,
    delete_interface as persist_delete_interface,
    delete_module as persist_delete_module,
    rename_module as persist_rename_module,
    create_module as persist_create_module,
    create_connector as persist_create_connector,
    rename_connector as persist_rename_connector,
    set_connector_side as persist_set_connector_side,
    delete_connector as persist_delete_connector,
    create_pin as persist_create_pin,
    rename_pin as persist_rename_pin,
    delete_pin as persist_delete_pin,
    reorder_pins as persist_reorder_pins,
)
from Schematic_View_tab.routing_persistence import save_enhanced_interface_data


class SchematicBridge(QObject):
    """Bridge between Python and JavaScript for the schematic scene."""

    # ---- Signals to JavaScript ----
    scene_data_ready = pyqtSignal(str)      # JSON scene data
    theme_changed = pyqtSignal(str)         # JSON theme colors
    save_finished = pyqtSignal(bool, str)   # (success, message) -> JS can show a toast/status
    pin_reorder_requested = pyqtSignal(int, str, str)  # (connector_id, connector_name, pin_names_json)

    def __init__(self, parent=None):
        super().__init__(parent)
        # None = whole project scene; otherwise a list of module ids,
        # set via set_module_selection() (mirrors SchematicTreeSelector).
        self._selected_module_ids = None
        self._host_widget = None

        style_manager.theme_changed.connect(self._on_theme_changed)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    @pyqtSlot()
    def get_scene_data(self):
        """Send the current schematic scene to JavaScript."""
        if get_current_project_id() is None:
            empty_scene = {"modules": [], "connectors": [], "interfaces": []}
            self.scene_data_ready.emit(json.dumps(empty_scene))
            return

        try:
            scene = load_schematic_scene(self._selected_module_ids)
            self.scene_data_ready.emit(json.dumps(scene))
        except Exception as e:
            print(f"[SchematicBridge] Error loading scene: {e}")
            self.scene_data_ready.emit(json.dumps({
                "modules": [], "connectors": [], "interfaces": [],
                "error": str(e),
            }))

    @pyqtSlot()
    def refresh_scene_data(self):
        """Re-read from the database and push a fresh scene to JS."""
        self.get_scene_data()

    @pyqtSlot(str)
    def set_module_selection(self, module_ids_json: str):
        """
        Restrict the scene to a subset of modules (mirrors
        SchematicTreeSelector's checkbox tree). Call with "[]" or an
        empty string to show the whole project again.
        """
        try:
            ids = json.loads(module_ids_json) if module_ids_json else []
            self._selected_module_ids = [int(i) for i in ids] if ids else None
        except (ValueError, TypeError) as e:
            print(f"[SchematicBridge] Invalid module selection payload: {e}")
            self._selected_module_ids = None
        self.get_scene_data()

    # ------------------------------------------------------------------
    # Saving (called from JS after a drag / edit)
    # ------------------------------------------------------------------
    @pyqtSlot(str)
    def save_module_positions(self, positions_json: str):
        """
        positions_json example:
          '{"1": {"x": 120.0, "y": 80.0}, "2": {"x": 340.0, "y": 80.0}}'
        (JS object keys are always strings, so we cast back to int here.)
        """
        try:
            raw: Dict[str, Any] = json.loads(positions_json)
            positions = {int(mod_id): pos for mod_id, pos in raw.items()}
            persist_module_positions(positions)
            self.save_finished.emit(True, f"{len(positions)} module position(s) saved")
        except Exception as e:
            self.save_finished.emit(False, f"Failed to save module positions: {e}")

    @pyqtSlot(str)
    def save_connector_positions(self, positions_json: str):
        """Same shape as save_module_positions, keyed by connector id."""
        try:
            raw: Dict[str, Any] = json.loads(positions_json)
            positions = {int(conn_id): pos for conn_id, pos in raw.items()}
            persist_connector_positions(positions)
            self.save_finished.emit(True, f"{len(positions)} connector position(s) saved")
        except Exception as e:
            self.save_finished.emit(False, f"Failed to save connector positions: {e}")

    @pyqtSlot(str)
    def save_routing(self, routing_json: str):
        """
        routing_json is the JSON-encoded version of exactly the structure
        save_enhanced_interface_data() in routing_persistence.py already
        expects, e.g.:
          '{"12": {"points": [[x,y], [x,y]], "manual_override": true,
                    "edit_count": 3, "locked": false}}'
        routing_persistence.py itself needs NO changes for this to work.
        """
        try:
            raw: Dict[str, Any] = json.loads(routing_json)
            interface_data = {}
            for iface_id, payload in raw.items():
                if isinstance(payload, dict) and "points" in payload:
                    payload = dict(payload)
                    payload["points"] = [tuple(p) for p in payload["points"]]
                    interface_data[int(iface_id)] = payload
                else:
                    interface_data[int(iface_id)] = [tuple(p) for p in payload]

            save_enhanced_interface_data(interface_data)
            self.save_finished.emit(True, f"{len(interface_data)} route(s) saved")
        except Exception as e:
            self.save_finished.emit(False, f"Failed to save routing: {e}")

    # ------------------------------------------------------------------
    # Editing: new connections, deletes, rename
    # ------------------------------------------------------------------
    @pyqtSlot(int, int, str, result=int)
    def create_interface(self, pin1_id: int, pin2_id: int, color: str = ""):
        """
        Called when the user drags from one pin and drops on another.
        Returns the new (or existing, if already connected) interface id,
        or -1 on failure -- JS should refresh the scene either way to pick
        up the real routed path.
        """
        try:
            new_id = persist_create_interface(pin1_id, pin2_id, color or None)
            if new_id is None:
                self.save_finished.emit(False, "Could not create connection")
                return -1
            self.save_finished.emit(True, "Connection created")
            return new_id
        except Exception as e:
            self.save_finished.emit(False, f"Failed to create connection: {e}")
            return -1

    @pyqtSlot(int)
    def delete_interface(self, interface_id: int):
        try:
            persist_delete_interface(interface_id)
            self.save_finished.emit(True, "Connection deleted")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to delete connection: {e}")

    @pyqtSlot(int)
    def delete_module(self, module_id: int):
        try:
            persist_delete_module(module_id)
            self.save_finished.emit(True, "Module deleted")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to delete module: {e}")

    @pyqtSlot(int, str)
    def rename_module(self, module_id: int, new_name: str):
        try:
            persist_rename_module(module_id, new_name)
            self.save_finished.emit(True, "Module renamed")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to rename module: {e}")

    @pyqtSlot(str, result=int)
    def create_module(self, name: str):
        try:
            new_id = persist_create_module(name)
            if new_id is None:
                self.save_finished.emit(False, "Could not create module")
                return -1
            self.save_finished.emit(True, "Module created")
            self.get_scene_data()
            return new_id
        except Exception as e:
            self.save_finished.emit(False, f"Failed to create module: {e}")
            return -1

    # ------------------------------------------------------------------
    # Editing: connectors
    # ------------------------------------------------------------------
    @pyqtSlot(int, str, str, result=int)
    def create_connector(self, module_id: int, name: str, side: str):
        try:
            new_id = persist_create_connector(module_id, name, side)
            if new_id is None:
                self.save_finished.emit(False, "Could not create connector")
                return -1
            self.save_finished.emit(True, "Connector created")
            self.get_scene_data()
            return new_id
        except Exception as e:
            self.save_finished.emit(False, f"Failed to create connector: {e}")
            return -1

    @pyqtSlot(int, str)
    def rename_connector(self, connector_id: int, new_name: str):
        try:
            persist_rename_connector(connector_id, new_name)
            self.save_finished.emit(True, "Connector renamed")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to rename connector: {e}")

    @pyqtSlot(int, str)
    def set_connector_side(self, connector_id: int, side: str):
        try:
            persist_set_connector_side(connector_id, side)
            self.save_finished.emit(True, "Connector side updated")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to update connector side: {e}")

    @pyqtSlot(int)
    def delete_connector(self, connector_id: int):
        try:
            persist_delete_connector(connector_id)
            self.save_finished.emit(True, "Connector deleted")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to delete connector: {e}")

    # ------------------------------------------------------------------
    # Editing: pins
    # ------------------------------------------------------------------
    @pyqtSlot(int, str, result=int)
    def create_pin(self, connector_id: int, name: str):
        try:
            new_id = persist_create_pin(connector_id, name)
            if new_id is None:
                self.save_finished.emit(False, "Could not create pin")
                return -1
            self.save_finished.emit(True, "Pin added")
            self.get_scene_data()
            return new_id
        except Exception as e:
            self.save_finished.emit(False, f"Failed to add pin: {e}")
            return -1

    @pyqtSlot(int, str)
    def rename_pin(self, pin_id: int, new_name: str):
        try:
            persist_rename_pin(pin_id, new_name)
            self.save_finished.emit(True, "Pin renamed")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to rename pin: {e}")

    @pyqtSlot(int)
    def delete_pin(self, pin_id: int):
        try:
            persist_delete_pin(pin_id)
            self.save_finished.emit(True, "Pin deleted")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to delete pin: {e}")

    # ------------------------------------------------------------------
    # Pin reordering: opens the existing native PinOrderDialog.
    # This is the one editing action that is NOT fire-and-forget -- it
    # blocks (modal dialog) on the Qt/GUI thread, which is fine since it
    # is only ever triggered by a deliberate user double-click in JS.
    # ------------------------------------------------------------------
    def set_host_widget(self, widget):
        """Called once by SchematicViewTab so dialogs parent correctly."""
        self._host_widget = widget

    @pyqtSlot(int, str, str)
    def request_pin_order_dialog(self, connector_id: int, connector_name: str, pin_names_json: str):
        try:
            pin_names = json.loads(pin_names_json)
        except Exception:
            pin_names = []
        if not pin_names:
            return

        host = getattr(self, "_host_widget", None)
        dialog = PinOrderDialog(pin_names, connector_label=connector_name, parent=host)
        if dialog.exec_():
            new_order = dialog.get_new_order()
            try:
                persist_reorder_pins(connector_id, new_order)
                self.save_finished.emit(True, "Pin order updated")
            except Exception as e:
                self.save_finished.emit(False, f"Failed to save pin order: {e}")
        self.get_scene_data()

    # ------------------------------------------------------------------
    # Theming (identical pattern to ComponentTreeBridge._on_theme_changed)
    # ------------------------------------------------------------------
    def _on_theme_changed(self, theme_name):
        theme_colors = {
            "primary_dark": theme_manager.get_color("primary_dark"),
            "primary_light": theme_manager.get_color("primary_light"),
            "accent": theme_manager.get_color("accent"),
            "text_primary": theme_manager.get_color("text_primary"),
        }
        self.theme_changed.emit(json.dumps(theme_colors))
