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
from typing import Any, Dict, Set

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from database import (
    get_current_project_id,
    get_module_subsystem_id,
    get_connector_subsystem_id,
    get_pin_subsystem_id,
    get_interface_subsystem_ids,
)
from auth_manager import auth
from access_control import can_edit_subsystem
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
    update_module as persist_update_module,
    update_connector as persist_update_connector,
    update_pin as persist_update_pin,
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
    scene_data_ready = pyqtSignal(str)  # JSON scene data
    theme_changed = pyqtSignal(str)  # JSON theme colors
    save_finished = pyqtSignal(
        bool, str
    )  # (success, message) -> JS can show a toast/status
    pin_reorder_requested = pyqtSignal(
        int, str, str
    )  # (connector_id, connector_name, pin_names_json)

    def __init__(self, parent=None):
        super().__init__(parent)
        # None = whole project scene; otherwise a list/set of ids,
        # set via set_selection() (mirrors SchematicTreeSelector).
        self._selected_module_ids = None
        self._selected_connector_ids = None
        self._selected_pin_ids = None
        self._host_widget = None

        style_manager.theme_changed.connect(self._on_theme_changed)

    # ------------------------------------------------------------------
    # Permission helpers
    # ------------------------------------------------------------------
    def _check_perm(self, perm_code: str, subsystem_id: int | None, action_desc: str) -> bool:
        """
        Check permission code + subsystem scope. Emits save_finished on
        failure so the JS front-end sees a toast / status message.
        Returns True if the operation is allowed.
        """
        if not auth.is_logged_in():
            self.save_finished.emit(False, "You must sign in first to edit.")
            return False
        if not auth.has_perm(perm_code):
            self.save_finished.emit(False, f"You don't have permission to {action_desc}.")
            return False
        if not can_edit_subsystem(subsystem_id):
            if subsystem_id is None:
                self.save_finished.emit(False, "Cannot determine subsystem for this operation.")
            else:
                self.save_finished.emit(False, "You don't have permission to modify this subsystem.")
            return False
        return True

    def _check_all_subsystems(self, perm_code: str, subsystem_ids: Set[int], action_desc: str) -> bool:
        """
        Check permission + ALL subsystems in the set. Fails if any one is
        not editable by the current user.
        """
        if not auth.is_logged_in():
            self.save_finished.emit(False, "You must sign in first to edit.")
            return False
        if not auth.has_perm(perm_code):
            self.save_finished.emit(False, f"You don't have permission to {action_desc}.")
            return False
        if auth.is_system():
            return True
        if not subsystem_ids:
            # No subsystem scope to check — could be unassigned items
            return True
        for sid in subsystem_ids:
            if not can_edit_subsystem(sid):
                self.save_finished.emit(
                    False, "You don't have permission to modify one or more subsystems."
                )
                return False
        return True

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    @pyqtSlot()
    def get_scene_data(self):
        """Send the current schematic scene to JavaScript."""
        if get_current_project_id() is None:
            empty_scene = {"subsystems": [], "modules": [], "connectors": [], "interfaces": []}
            self.scene_data_ready.emit(json.dumps(empty_scene))
            return

        try:
            mod_ids, conn_ids, pin_ids = self._get_selection()
            scene = load_schematic_scene(
                module_ids=mod_ids,
                connector_ids=conn_ids,
                pin_ids=pin_ids,
            )
            self.scene_data_ready.emit(json.dumps(scene))
        except Exception as e:
            self.scene_data_ready.emit(
                json.dumps(
                    {
                        "subsystems": [],
                        "modules": [],
                        "connectors": [],
                        "interfaces": [],
                        "error": str(e),
                    }
                )
            )

    @pyqtSlot()
    def refresh_scene_data(self):
        """Re-read from the database and push a fresh scene to JS."""
        self.get_scene_data()

    @pyqtSlot(str)
    def set_module_selection(self, module_ids_json: str):
        """
        Legacy single-category setter. Delegates to set_selection() with
        modules-only. Kept for backward compatibility.
        """
        try:
            ids = json.loads(module_ids_json) if module_ids_json else []
            self.set_selection(modules=[int(i) for i in ids] if ids else [])
        except (ValueError, TypeError):
            self.set_selection()

    def set_selection(self, modules=None, connectors=None, pins=None):
        """
        Store the full tree selection and reload the scene.

        Args:
            modules: list of selected module IDs (empty = show all)
            connectors: list of selected connector IDs (empty = show none unless modules is empty)
            pins: list of selected pin IDs (empty = show none unless connectors is empty)

        Note: An empty modules list means "show everything" (bridge shows
        all modules). When modules ARE specified, the connectors and pins
        lists further filter what's visible within those modules.
        """
        self._selected_module_ids = [int(i) for i in (modules or [])] if modules else None
        self._selected_connector_ids = set(int(i) for i in (connectors or [])) if connectors else None
        self._selected_pin_ids = set(int(i) for i in (pins or [])) if pins else None
        self.get_scene_data()

    def _get_selection(self):
        """Return the full selection tuple for the scene model."""
        return (
            self._selected_module_ids,
            self._selected_connector_ids,
            self._selected_pin_ids,
        )

    # ------------------------------------------------------------------
    # Saving (called from JS after a drag / edit)
    # ------------------------------------------------------------------
    @pyqtSlot(str)
    def save_module_positions(self, positions_json: str):
        """
        positions_json example:
          '{"1": {"x": 120.0, "y": 80.0}, "2": {"x": 340.0, "y": 80.0}}'
        (JS object keys are always strings, so we cast back to int here.)

        Only saves positions for modules whose subsystem the current user can
        edit.  Modules the user cannot edit are silently skipped so the save
        never fails with a misleading permission toast.
        """
        try:
            raw: Dict[str, Any] = json.loads(positions_json)
            all_positions = {int(mod_id): pos for mod_id, pos in raw.items()}

            allowed_positions: Dict[int, Any] = {}
            denied = 0
            for mod_id, pos in all_positions.items():
                sid = get_module_subsystem_id(mod_id)
                if auth.is_logged_in() and auth.has_perm("module.edit") and can_edit_subsystem(sid):
                    allowed_positions[mod_id] = pos
                else:
                    denied += 1

            if allowed_positions:
                persist_module_positions(allowed_positions)
                msg = f"{len(allowed_positions)} module position(s) saved"
                if denied:
                    msg += f" ({denied} skipped — no permission)"
                self.save_finished.emit(True, msg)
            elif denied:
                self.save_finished.emit(False, "You don't have permission to save any of these positions.")
        except Exception as e:
            self.save_finished.emit(False, f"Failed to save module positions: {e}")

    @pyqtSlot(str)
    def save_connector_positions(self, positions_json: str):
        """Save connector positions. Each entry can include x, y, and optionally side.
        Example: '{"1": {"x": 100, "y": 50, "side": "right"}}'

        Only saves positions for connectors whose subsystem the current user can
        edit.  Unauthorised connectors are silently skipped.
        """
        try:
            raw: Dict[str, Any] = json.loads(positions_json)
            all_positions = {int(conn_id): pos for conn_id, pos in raw.items()}

            allowed_positions: Dict[int, Any] = {}
            denied = 0
            for conn_id, pos in all_positions.items():
                sid = get_connector_subsystem_id(conn_id)
                if auth.is_logged_in() and auth.has_perm("connector.edit") and can_edit_subsystem(sid):
                    allowed_positions[conn_id] = pos
                else:
                    denied += 1

            if allowed_positions:
                persist_connector_positions(allowed_positions)
                msg = f"{len(allowed_positions)} connector position(s) saved"
                if denied:
                    msg += f" ({denied} skipped — no permission)"
                self.save_finished.emit(True, msg)
            elif denied:
                self.save_finished.emit(False, "You don't have permission to save any of these positions.")
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

        Only saves routing for interfaces the current user can edit.
        Interfaces belonging to uneditable subsystems are silently skipped
        so dragging an editable module never produces a spurious permission
        toast about a completely unrelated interface.
        """
        try:
            raw: Dict[str, Any] = json.loads(routing_json)
            all_interface_data: Dict[int, Any] = {}
            for iface_id, payload in raw.items():
                if isinstance(payload, dict) and "points" in payload:
                    payload = dict(payload)
                    payload["points"] = [tuple(p) for p in payload["points"]]
                    all_interface_data[int(iface_id)] = payload
                else:
                    all_interface_data[int(iface_id)] = [tuple(p) for p in payload]

            # Permission: check each interface individually, skip unauthorised ones
            allowed_data: Dict[int, Any] = {}
            denied = 0
            for iface_id, data in all_interface_data.items():
                sids = get_interface_subsystem_ids(iface_id)
                if auth.is_logged_in() and auth.has_perm("interface.edit") and all(can_edit_subsystem(s) for s in sids):
                    allowed_data[iface_id] = data
                else:
                    denied += 1

            if allowed_data:
                save_enhanced_interface_data(allowed_data)
                msg = f"{len(allowed_data)} route(s) saved"
                if denied:
                    msg += f" ({denied} skipped — no permission)"
                self.save_finished.emit(True, msg)
            elif denied:
                self.save_finished.emit(False, "You don't have permission to save any of these routes.")
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
            # Permission: check both pins' subsystem IDs
            sid1 = get_pin_subsystem_id(pin1_id)
            sid2 = get_pin_subsystem_id(pin2_id)
            if sid1 is not None and not self._check_perm("interface.create", sid1, "create connections"):
                return -1
            if sid2 is not None and not self._check_perm("interface.create", sid2, "create connections"):
                return -1

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
            # Permission: check all involved subsystem IDs
            sids = get_interface_subsystem_ids(interface_id)
            if not self._check_all_subsystems("interface.delete", sids, "delete connections"):
                return

            persist_delete_interface(interface_id)
            self.save_finished.emit(True, "Connection deleted")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to delete connection: {e}")

    @pyqtSlot(int)
    def delete_module(self, module_id: int):
        try:
            sid = get_module_subsystem_id(module_id)
            if not self._check_perm("module.delete", sid, "delete modules"):
                return

            persist_delete_module(module_id)
            self.save_finished.emit(True, "Module deleted")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to delete module: {e}")

    @pyqtSlot(int, str)
    def rename_module(self, module_id: int, new_name: str):
        try:
            sid = get_module_subsystem_id(module_id)
            if not self._check_perm("module.edit", sid, "rename modules"):
                return

            persist_rename_module(module_id, new_name)
            self.save_finished.emit(True, "Module renamed")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to rename module: {e}")

    @pyqtSlot(str, int, float, float, str, result=int)
    def create_module(self, name: str, subsystem_id: int = -1,
                       mass: float = 0.0, power: float = 0.0, color: str = ""):
        try:
            # Require a valid subsystem — modules cannot exist outside subsystems
            if subsystem_id < 0 or subsystem_id is None:
                self.save_finished.emit(False, "A subsystem must be selected to create a module.")
                return -1
            if not auth.is_logged_in():
                self.save_finished.emit(False, "You must sign in first to create modules.")
                return -1
            if not auth.has_perm("module.create"):
                self.save_finished.emit(False, "You don't have permission to create modules.")
                return -1

            c = color if color else None
            new_id = persist_create_module(name, subsystem_id=subsystem_id, mass=mass, power=power, color=c)
            if new_id is None:
                self.save_finished.emit(False, "Could not create module")
                return -1
            self.save_finished.emit(True, "Module created")
            self.get_scene_data()
            return new_id
        except Exception as e:
            self.save_finished.emit(False, f"Failed to create module: {e}")
            return -1

    @pyqtSlot(int, str, float, float, str, int)
    def update_module(self, module_id: int, name: str = "",
                       mass: float = -1, power: float = -1,
                       color: str = "", subsystem_id: int = -1):
        """Update module fields from JS."""
        try:
            sid = get_module_subsystem_id(module_id)
            if not self._check_perm("module.edit", sid, "edit modules"):
                return

            n = name if name else None
            m = mass if mass >= 0 else None
            p = power if power >= 0 else None
            c = color if color else None
            s = subsystem_id if subsystem_id >= 0 else None
            persist_update_module(module_id, name=n, mass=m, power=p, color=c, subsystem_id=s)
            self.save_finished.emit(True, "Module updated")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to update module: {e}")

    @pyqtSlot(int, str, str, str, int)
    def update_connector(self, connector_id: int, name: str = "",
                          color: str = "", side: str = "", number_of_pins: int = -1):
        """Update connector fields from JS."""
        try:
            sid = get_connector_subsystem_id(connector_id)
            if not self._check_perm("connector.edit", sid, "edit connectors"):
                return

            n = name if name else None
            c = color if color else None
            s = side if side else None
            npins = number_of_pins if number_of_pins >= 0 else None
            persist_update_connector(connector_id, name=n, color=c, side=s, number_of_pins=npins)
            self.save_finished.emit(True, "Connector updated")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to update connector: {e}")

    @pyqtSlot(int, str, str, bool, float, str)
    def update_pin(self, pin_id: int, name: str = "",
                    pin_type: str = "", is_ground: bool = False,
                    value: float = 0.0, description: str = ""):
        """Update pin fields from JS."""
        try:
            sid = get_pin_subsystem_id(pin_id)
            if not self._check_perm("pin.edit", sid, "edit pins"):
                return

            n = name if name else None
            pt = pin_type if pin_type else None
            desc = description if description else None
            val = value if value != 0.0 else None
            persist_update_pin(pin_id, name=n, pin_type=pt, is_ground=is_ground, value=val, description=desc)
            self.save_finished.emit(True, "Pin updated")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to update pin: {e}")

    # ------------------------------------------------------------------
    # Editing: connectors
    # ------------------------------------------------------------------
    @pyqtSlot(int, str, str, str, int, result=int)
    def create_connector(self, module_id: int, name: str, side: str,
                          color: str = "", number_of_pins: int = 0):
        try:
            sid = get_module_subsystem_id(module_id)
            if not self._check_perm("connector.create", sid, "create connectors"):
                return -1

            c = color if color else None
            new_id = persist_create_connector(module_id, name, side, color=c, number_of_pins=number_of_pins)
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
            sid = get_connector_subsystem_id(connector_id)
            if not self._check_perm("connector.edit", sid, "rename connectors"):
                return

            persist_rename_connector(connector_id, new_name)
            self.save_finished.emit(True, "Connector renamed")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to rename connector: {e}")

    @pyqtSlot(int, str)
    def set_connector_side(self, connector_id: int, side: str):
        try:
            sid = get_connector_subsystem_id(connector_id)
            if not self._check_perm("connector.edit", sid, "change connector side"):
                return

            persist_set_connector_side(connector_id, side)
            self.save_finished.emit(True, "Connector side updated")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to update connector side: {e}")

    @pyqtSlot(int)
    def delete_connector(self, connector_id: int):
        try:
            sid = get_connector_subsystem_id(connector_id)
            if not self._check_perm("connector.delete", sid, "delete connectors"):
                return

            persist_delete_connector(connector_id)
            self.save_finished.emit(True, "Connector deleted")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to delete connector: {e}")

    # ------------------------------------------------------------------
    # Editing: pins
    # ------------------------------------------------------------------
    @pyqtSlot(int, str, str, bool, float, float, str, result=int)
    def create_pin(self, connector_id: int, name: str,
                    pin_type: str = "", is_ground: bool = False,
                    value: float = 0.0, current: float = 0.0,
                    description: str = ""):
        try:
            sid = get_connector_subsystem_id(connector_id)
            if not self._check_perm("pin.create", sid, "create pins"):
                return -1

            pt = pin_type if pin_type else None
            desc = description if description else None
            val = value if value != 0.0 else None
            cur = current if current != 0.0 else None
            new_id = persist_create_pin(connector_id, name,
                                         pin_type=pt,
                                         is_ground=is_ground,
                                         value=val,
                                         current=cur,
                                         description=desc)
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
            sid = get_pin_subsystem_id(pin_id)
            if not self._check_perm("pin.edit", sid, "rename pins"):
                return

            persist_rename_pin(pin_id, new_name)
            self.save_finished.emit(True, "Pin renamed")
            self.get_scene_data()
        except Exception as e:
            self.save_finished.emit(False, f"Failed to rename pin: {e}")

    @pyqtSlot(int)
    def delete_pin(self, pin_id: int):
        try:
            sid = get_pin_subsystem_id(pin_id)
            if not self._check_perm("pin.delete", sid, "delete pins"):
                return

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
    def request_pin_order_dialog(
        self, connector_id: int, connector_name: str, pin_names_json: str
    ):
        try:
            pin_names = json.loads(pin_names_json)
        except Exception:
            pin_names = []
        if not pin_names:
            return

        # Permission check
        sid = get_connector_subsystem_id(connector_id)
        if not self._check_perm("pin.edit", sid, "reorder pins"):
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
