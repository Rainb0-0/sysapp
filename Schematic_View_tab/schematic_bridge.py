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

Read-only policy: the schematic view is read-only for everyone except the
SYSTEM ADMIN (subsystem admins included). The admin's edits hit the DB
directly; everyone else sees the scene with `readonly` set and all edit
affordances disabled (the bridge also rejects their write calls). Pending
changes they created from other tabs (see suggestions.py) are still shown
on the canvas as an optimistic preview via pending_preview_scene().
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
    pins_connectable,
    check_pin_change_interfaces,
)
from auth_manager import auth
from access_control import can_edit_subsystem
from styles.style_manager import style_manager
from styles.theme_manager import theme_manager

from suggestions import suggest_change, pending_preview_scene

from Schematic_View_tab.schematic_scene_model import (
    NOT_SET,
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


SUBMITTED_MSG = (
    "Change submitted for approval — it will apply once the system admin "
    "approves it."
)


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
    @staticmethod
    def _require_system_admin() -> bool:
        """
        The schematic view is read-only for everyone except the system
        admin (subsystem admins included). Returns True only for a logged-in
        system admin.
        """
        return auth.is_logged_in() and auth.is_system()

    def _deny_read_only(self) -> bool:
        """Emit the read-only denial toast and return False."""
        if not auth.is_logged_in():
            self.save_finished.emit(False, "You must sign in first to edit.")
        else:
            self.save_finished.emit(
                False, "The schematic view is read-only for your account."
            )
        return False

    def _check_perm(self, perm_code: str, subsystem_id: int | None, action_desc: str) -> bool:
        """
        Check the caller may edit the schematic view (system admin only),
        then the permission code + subsystem scope. Emits save_finished on
        failure so the JS front-end sees a toast / status message.
        Returns True if the operation is allowed.
        """
        if not self._require_system_admin():
            return self._deny_read_only()
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
        Check the caller may edit the schematic view (system admin only),
        then permission + ALL subsystems in the set.
        """
        if not self._require_system_admin():
            return self._deny_read_only()
        if not auth.has_perm(perm_code):
            self.save_finished.emit(False, f"You don't have permission to {action_desc}.")
            return False
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

    def _route_edit(self, entity_type: str, action: str, entity_id,
                    subsystem_id, payload: dict, summary: str, apply_direct):
        """
        Central suggest-or-apply router.

        system admin -> apply_direct() runs (persists to the DB, returns the
                       new id for creates / None otherwise).
        everyone else -> the change is recorded as a pending suggestion, the
                       scene is refreshed so the optimistic preview shows it,
                       and None is returned.

        apply_direct must be a zero-arg closure.
        """
        if auth.is_system():
            return apply_direct()
        cid = suggest_change(entity_type, action, entity_id, subsystem_id,
                             payload, summary)
        if cid is not None:
            self.save_finished.emit(True, SUBMITTED_MSG)
        else:
            self.save_finished.emit(False, "Could not submit the change for approval.")
        self.get_scene_data()
        return None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    @pyqtSlot()
    def get_scene_data(self):
        """Send the current schematic scene (plus the user's pending preview) to JS."""
        # The schematic view is read-only for everyone except the system
        # admin (subsystem admins included): the `readonly` flag makes the
        # JS renderer disable every edit affordance, and `editable` flags on
        # modules/interfaces back that up per item.
        readonly = not self._require_system_admin()
        if get_current_project_id() is None:
            empty_scene = {
                "subsystems": [],
                "modules": [],
                "connectors": [],
                "interfaces": [],
                "readonly": readonly,
            }
            self.scene_data_ready.emit(json.dumps(empty_scene))
            return

        try:
            mod_ids, conn_ids, pin_ids = self._get_selection()
            scene = load_schematic_scene(
                module_ids=mod_ids,
                connector_ids=conn_ids,
                pin_ids=pin_ids,
            )
            # Non-system users see their own pending suggestions merged on
            # top of the real scene (optimistic preview).
            if not auth.is_system():
                scene = pending_preview_scene(scene)
            scene["readonly"] = readonly
            self.scene_data_ready.emit(json.dumps(scene))
        except Exception as e:
            self.scene_data_ready.emit(
                json.dumps(
                    {
                        "subsystems": [],
                        "modules": [],
                        "connectors": [],
                        "interfaces": [],
                        "readonly": readonly,
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

        System admin only — the schematic view is read-only for everyone
        else (subsystem admins included).
        """
        if not self._require_system_admin():
            self._deny_read_only()
            return
        try:
            raw: Dict[str, Any] = json.loads(positions_json)
            all_positions = {int(mod_id): pos for mod_id, pos in raw.items()}
        except Exception as e:
            self.save_finished.emit(False, f"Failed to save module positions: {e}")
            return

        allowed: Dict[int, Any] = {}
        denied = 0
        for mod_id, pos in all_positions.items():
            sid = get_module_subsystem_id(mod_id)
            if auth.is_logged_in() and auth.has_perm("module.edit") and can_edit_subsystem(sid):
                allowed[mod_id] = pos
            else:
                denied += 1

        if auth.is_system():
            if allowed:
                persist_module_positions(allowed)
                msg = f"{len(allowed)} module position(s) saved"
                if denied:
                    msg += f" ({denied} skipped — no permission)"
                self.save_finished.emit(True, msg)
            elif denied:
                self.save_finished.emit(False, "You don't have permission to save any of these positions.")
            return

        # Non-admin: record one suggestion per module (dedupe keeps one fresh
        # entry per module so drags don't flood the queue).
        suggested = 0
        for mod_id, pos in allowed.items():
            fields = {}
            if "x" in pos:
                fields["pos_x"] = pos["x"]
            if "y" in pos:
                fields["pos_y"] = pos["y"]
            if "width" in pos:
                fields["width"] = pos["width"]
            if "height" in pos:
                fields["height"] = pos["height"]
            if fields:
                cid = suggest_change("module", "update", mod_id,
                                     get_module_subsystem_id(mod_id),
                                     {"id": mod_id, "fields": fields},
                                     f"Move module #{mod_id}")
                if cid is not None:
                    suggested += 1
        if suggested:
            self.save_finished.emit(True, SUBMITTED_MSG)
        elif denied:
            self.save_finished.emit(False, "You don't have permission to save any of these positions.")
        self.get_scene_data()

    @pyqtSlot(str)
    def save_connector_positions(self, positions_json: str):
        """Save connector positions. Each entry can include x, y, and optionally side.
        Example: '{"1": {"x": 100, "y": 50, "side": "right"}}'

        System admin only — the schematic view is read-only for everyone
        else (subsystem admins included).
        """
        if not self._require_system_admin():
            self._deny_read_only()
            return
        try:
            raw: Dict[str, Any] = json.loads(positions_json)
            all_positions = {int(conn_id): pos for conn_id, pos in raw.items()}
        except Exception as e:
            self.save_finished.emit(False, f"Failed to save connector positions: {e}")
            return

        allowed: Dict[int, Any] = {}
        denied = 0
        for conn_id, pos in all_positions.items():
            sid = get_connector_subsystem_id(conn_id)
            if auth.is_logged_in() and auth.has_perm("connector.edit") and can_edit_subsystem(sid):
                allowed[conn_id] = pos
            else:
                denied += 1

        if auth.is_system():
            if allowed:
                persist_connector_positions(allowed)
                msg = f"{len(allowed)} connector position(s) saved"
                if denied:
                    msg += f" ({denied} skipped — no permission)"
                self.save_finished.emit(True, msg)
            elif denied:
                self.save_finished.emit(False, "You don't have permission to save any of these positions.")
            return

        suggested = 0
        for conn_id, pos in allowed.items():
            fields = {}
            if "x" in pos:
                fields["pos_x"] = pos["x"]
            if "y" in pos:
                fields["pos_y"] = pos["y"]
            if "side" in pos:
                fields["side"] = pos["side"]
            if fields:
                cid = suggest_change("connector", "update", conn_id,
                                     get_connector_subsystem_id(conn_id),
                                     {"id": conn_id, "fields": fields},
                                     f"Move connector #{conn_id}")
                if cid is not None:
                    suggested += 1
        if suggested:
            self.save_finished.emit(True, SUBMITTED_MSG)
        elif denied:
            self.save_finished.emit(False, "You don't have permission to save any of these positions.")
        self.get_scene_data()

    @pyqtSlot(str)
    def save_routing(self, routing_json: str):
        """
        routing_json is the JSON-encoded version of exactly the structure
        save_enhanced_interface_data() in routing_persistence.py already
        expects, e.g.:
          '{"12": {"points": [[x,y], [x,y]], "manual_override": true,
                    "edit_count": 3, "locked": false}}'

        System admin only — the schematic view is read-only for everyone
        else (subsystem admins included).
        """
        if not self._require_system_admin():
            self._deny_read_only()
            return
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
        except Exception as e:
            self.save_finished.emit(False, f"Failed to save routing: {e}")
            return

        allowed_data: Dict[int, Any] = {}
        denied = 0
        for iface_id, data in all_interface_data.items():
            sids = get_interface_subsystem_ids(iface_id)
            if auth.is_logged_in() and auth.has_perm("interface.edit") and all(can_edit_subsystem(s) for s in sids):
                allowed_data[iface_id] = data
            else:
                denied += 1

        if auth.is_system():
            if allowed_data:
                save_enhanced_interface_data(allowed_data)
                msg = f"{len(allowed_data)} route(s) saved"
                if denied:
                    msg += f" ({denied} skipped — no permission)"
                self.save_finished.emit(True, msg)
            elif denied:
                self.save_finished.emit(False, "You don't have permission to save any of these routes.")
            return

        suggested = 0
        for iface_id, data in allowed_data.items():
            if isinstance(data, list):
                data = {"points": data}
            cid = suggest_change("interface", "update", iface_id,
                                 None,
                                 {"id": iface_id, "routing": data},
                                 f"Re-route connection #{iface_id}")
            if cid is not None:
                suggested += 1
        if suggested:
            self.save_finished.emit(True, SUBMITTED_MSG)
        elif denied:
            self.save_finished.emit(False, "You don't have permission to save any of these routes.")
        self.get_scene_data()

    # ------------------------------------------------------------------
    # Editing: new connections, deletes, rename
    # ------------------------------------------------------------------
    @pyqtSlot(int, int, str, float, result=int)
    def create_interface(self, pin1_id: int, pin2_id: int, color: str = "", current: float = 0.0):
        """
        Called when the user drags from one pin and drops on another.
        Returns the new (or existing, if already connected) interface id,
        or -1 on failure -- JS should refresh the scene either way to pick
        up the real routed path. `current` is the connection's current in mA.
        """
        try:
            sid1 = get_pin_subsystem_id(pin1_id)
            sid2 = get_pin_subsystem_id(pin2_id)
            if sid1 is not None and not self._check_perm("interface.create", sid1, "create connections"):
                return -1
            if sid2 is not None and not self._check_perm("interface.create", sid2, "create connections"):
                return -1

            # Same-type wiring rule (ground↔ground, matching VCC, same data type)
            ok, reason = pins_connectable(pin1_id, pin2_id)
            if not ok:
                self.save_finished.emit(False, reason)
                return -1

            def _direct():
                new_id = persist_create_interface(pin1_id, pin2_id, color or None, current or None)
                if new_id is None:
                    self.save_finished.emit(False, "Could not create connection")
                    return -1
                self.save_finished.emit(True, "Connection created")
                return new_id

            result = self._route_edit(
                "interface", "create", None, sid1 or sid2,
                {"pin1_id": pin1_id, "pin2_id": pin2_id,
                 "color": color or None, "current": current or None},
                f"Connect pins {pin1_id} ↔ {pin2_id}",
                _direct,
            )
            return result if result is not None else -1
        except Exception as e:
            self.save_finished.emit(False, f"Failed to create connection: {e}")
            return -1

    @pyqtSlot(int)
    def delete_interface(self, interface_id: int):
        try:
            sids = get_interface_subsystem_ids(interface_id)
            if not self._check_all_subsystems("interface.delete", sids, "delete connections"):
                return

            def _direct():
                persist_delete_interface(interface_id)
                self.save_finished.emit(True, "Connection deleted")
                self.get_scene_data()

            self._route_edit("interface", "delete", interface_id, None,
                             {"id": interface_id}, f"Delete connection #{interface_id}",
                             _direct)
        except Exception as e:
            self.save_finished.emit(False, f"Failed to delete connection: {e}")

    @pyqtSlot(int)
    def delete_module(self, module_id: int):
        try:
            sid = get_module_subsystem_id(module_id)
            if not self._check_perm("module.delete", sid, "delete modules"):
                return

            def _direct():
                persist_delete_module(module_id)
                self.save_finished.emit(True, "Module deleted")
                self.get_scene_data()

            self._route_edit("module", "delete", module_id, sid,
                             {"id": module_id}, f"Delete module #{module_id}",
                             _direct)
        except Exception as e:
            self.save_finished.emit(False, f"Failed to delete module: {e}")

    @pyqtSlot(int, str)
    def rename_module(self, module_id: int, new_name: str):
        try:
            sid = get_module_subsystem_id(module_id)
            if not self._check_perm("module.edit", sid, "rename modules"):
                return

            def _direct():
                persist_rename_module(module_id, new_name)
                self.save_finished.emit(True, "Module renamed")
                self.get_scene_data()

            self._route_edit("module", "update", module_id, sid,
                             {"id": module_id, "fields": {"name": new_name}},
                             f"Rename module to \"{new_name}\"",
                             _direct)
        except Exception as e:
            self.save_finished.emit(False, f"Failed to rename module: {e}")

    @staticmethod
    def _parse_temp(value) -> float | None:
        """Parse a temperature sent from JS ('' = unset -> None)."""
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    @pyqtSlot(str, int, float, float, str, str, str, result=int)
    def create_module(self, name: str, subsystem_id: int = -1,
                       mass: float = 0.0, power: float = 0.0, color: str = "",
                       min_temp: str = "", max_temp: str = ""):
        try:
            if subsystem_id < 0 or subsystem_id is None:
                self.save_finished.emit(False, "A subsystem must be selected to create a module.")
                return -1
            if not self._check_perm("module.create", subsystem_id, "create modules"):
                return -1

            mt = self._parse_temp(min_temp)
            xt = self._parse_temp(max_temp)

            def _direct():
                c = color if color else None
                new_id = persist_create_module(name, subsystem_id=subsystem_id, mass=mass,
                                               power=power, color=c, min_temp=mt, max_temp=xt)
                if new_id is None:
                    self.save_finished.emit(False, "Could not create module")
                    return -1
                self.save_finished.emit(True, "Module created")
                self.get_scene_data()
                return new_id

            result = self._route_edit(
                "module", "create", None, subsystem_id,
                {"name": name, "subsystem_id": subsystem_id, "mass": mass,
                 "power": power, "color": color or None,
                 "min_temp": mt, "max_temp": xt},
                f"Add module \"{name}\"",
                _direct,
            )
            return result if result is not None else -1
        except Exception as e:
            self.save_finished.emit(False, f"Failed to create module: {e}")
            return -1

    @pyqtSlot(int, str, float, float, str, int, str, str)
    def update_module(self, module_id: int, name: str = "",
                       mass: float = -1, power: float = -1,
                       color: str = "", subsystem_id: int = -1,
                       min_temp: str = "", max_temp: str = ""):
        """Update module fields from JS."""
        try:
            sid = get_module_subsystem_id(module_id)
            if not self._check_perm("module.edit", sid, "edit modules"):
                return

            fields = {}
            if name:
                fields["name"] = name
            if mass >= 0:
                fields["mass"] = mass
            if power >= 0:
                fields["power"] = power
            if color:
                fields["color"] = color
            if subsystem_id >= 0:
                fields["subsystem_id"] = subsystem_id
            # Temps are always sent ('' = clear to NULL, number = set).
            fields["min_temp"] = self._parse_temp(min_temp)
            fields["max_temp"] = self._parse_temp(max_temp)

            def _direct():
                persist_update_module(
                    module_id,
                    name=fields.get("name"),
                    mass=fields.get("mass"),
                    power=fields.get("power"),
                    color=fields.get("color"),
                    subsystem_id=fields.get("subsystem_id"),
                    min_temp=fields.get("min_temp", NOT_SET),
                    max_temp=fields.get("max_temp", NOT_SET),
                )
                self.save_finished.emit(True, "Module updated")
                self.get_scene_data()

            self._route_edit("module", "update", module_id, sid,
                             {"id": module_id, "fields": fields},
                             f"Edit module #{module_id}",
                             _direct)
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

            fields = {}
            if name:
                fields["name"] = name
            if color:
                fields["color"] = color
            if side:
                fields["side"] = side
            if number_of_pins >= 0:
                fields["number_of_pins"] = number_of_pins

            def _direct():
                persist_update_connector(
                    connector_id,
                    name=fields.get("name"),
                    color=fields.get("color"),
                    side=fields.get("side"),
                    number_of_pins=fields.get("number_of_pins"),
                )
                self.save_finished.emit(True, "Connector updated")
                self.get_scene_data()

            self._route_edit("connector", "update", connector_id, sid,
                             {"id": connector_id, "fields": fields},
                             f"Edit connector #{connector_id}",
                             _direct)
        except Exception as e:
            self.save_finished.emit(False, f"Failed to update connector: {e}")

    @pyqtSlot(int, str, str, bool, float, float, str)
    def update_pin(self, pin_id: int, name: str = "",
                    pin_type: str = "", is_ground: bool = False,
                    value: float = 0.0, current: float = 0.0,
                    description: str = ""):
        """Update pin fields from JS."""
        try:
            sid = get_pin_subsystem_id(pin_id)
            if not self._check_perm("pin.edit", sid, "edit pins"):
                return

            fields = {}
            if name:
                fields["name"] = name
            if pin_type:
                fields["pin_type"] = pin_type
            fields["is_ground"] = bool(is_ground)
            if value != 0.0:
                fields["value"] = value
            if current != 0.0:
                fields["current"] = current
            if description:
                fields["description"] = description

            # Re-validate existing connections against the NEW type: a
            # GND↔GND link must not silently become GND↔VCC (or VCC at a
            # different voltage, or mismatched data types).
            if "pin_type" in fields or "is_ground" in fields or "value" in fields:
                ok_change, reason = check_pin_change_interfaces(
                    pin_id, pin_type, bool(is_ground), value if value != 0.0 else None
                )
                if not ok_change:
                    self.save_finished.emit(False, f"Cannot change this pin's type: {reason}")
                    return

            def _direct():
                persist_update_pin(
                    pin_id,
                    name=fields.get("name"),
                    pin_type=fields.get("pin_type"),
                    is_ground=fields.get("is_ground"),
                    value=fields.get("value"),
                    current=fields.get("current"),
                    description=fields.get("description"),
                )
                self.save_finished.emit(True, "Pin updated")
                self.get_scene_data()

            self._route_edit("pin", "update", pin_id, sid,
                             {"id": pin_id, "fields": fields},
                             f"Edit pin #{pin_id}",
                             _direct)
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

            def _direct():
                c = color if color else None
                new_id = persist_create_connector(module_id, name, side, color=c, number_of_pins=number_of_pins)
                if new_id is None:
                    self.save_finished.emit(False, "Could not create connector")
                    return -1
                self.save_finished.emit(True, "Connector created")
                self.get_scene_data()
                return new_id

            result = self._route_edit(
                "connector", "create", None, sid,
                {"module_id": module_id, "name": name, "side": side,
                 "color": color or None, "number_of_pins": number_of_pins},
                f"Add connector \"{name}\"",
                _direct,
            )
            return result if result is not None else -1
        except Exception as e:
            self.save_finished.emit(False, f"Failed to create connector: {e}")
            return -1

    @pyqtSlot(int, str)
    def rename_connector(self, connector_id: int, new_name: str):
        try:
            sid = get_connector_subsystem_id(connector_id)
            if not self._check_perm("connector.edit", sid, "rename connectors"):
                return

            def _direct():
                persist_rename_connector(connector_id, new_name)
                self.save_finished.emit(True, "Connector renamed")
                self.get_scene_data()

            self._route_edit("connector", "update", connector_id, sid,
                             {"id": connector_id, "fields": {"name": new_name}},
                             f"Rename connector to \"{new_name}\"",
                             _direct)
        except Exception as e:
            self.save_finished.emit(False, f"Failed to rename connector: {e}")

    @pyqtSlot(int, str)
    def set_connector_side(self, connector_id: int, side: str):
        try:
            sid = get_connector_subsystem_id(connector_id)
            if not self._check_perm("connector.edit", sid, "change connector side"):
                return

            def _direct():
                persist_set_connector_side(connector_id, side)
                self.save_finished.emit(True, "Connector side updated")
                self.get_scene_data()

            self._route_edit("connector", "update", connector_id, sid,
                             {"id": connector_id, "fields": {"side": side}},
                             f"Move connector #{connector_id} to {side}",
                             _direct)
        except Exception as e:
            self.save_finished.emit(False, f"Failed to update connector side: {e}")

    @pyqtSlot(int)
    def delete_connector(self, connector_id: int):
        try:
            sid = get_connector_subsystem_id(connector_id)
            if not self._check_perm("connector.delete", sid, "delete connectors"):
                return

            def _direct():
                persist_delete_connector(connector_id)
                self.save_finished.emit(True, "Connector deleted")
                self.get_scene_data()

            self._route_edit("connector", "delete", connector_id, sid,
                             {"id": connector_id}, f"Delete connector #{connector_id}",
                             _direct)
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

            def _direct():
                pt = pin_type if pin_type else None
                desc = description if description else None
                val = value if value != 0.0 else None
                cur = current if current != 0.0 else None
                new_id = persist_create_pin(connector_id, name, pin_type=pt,
                                             is_ground=is_ground, value=val,
                                             current=cur, description=desc)
                if new_id is None:
                    self.save_finished.emit(False, "Could not create pin")
                    return -1
                self.save_finished.emit(True, "Pin added")
                self.get_scene_data()
                return new_id

            result = self._route_edit(
                "pin", "create", None, sid,
                {"connector_id": connector_id, "name": name,
                 "pin_type": pin_type or None, "is_ground": bool(is_ground),
                 "value": value if value != 0.0 else None,
                 "description": description or None},
                f"Add pin \"{name}\"",
                _direct,
            )
            return result if result is not None else -1
        except Exception as e:
            self.save_finished.emit(False, f"Failed to add pin: {e}")
            return -1

    @pyqtSlot(int, str)
    def rename_pin(self, pin_id: int, new_name: str):
        try:
            sid = get_pin_subsystem_id(pin_id)
            if not self._check_perm("pin.edit", sid, "rename pins"):
                return

            def _direct():
                persist_rename_pin(pin_id, new_name)
                self.save_finished.emit(True, "Pin renamed")
                self.get_scene_data()

            self._route_edit("pin", "update", pin_id, sid,
                             {"id": pin_id, "fields": {"name": new_name}},
                             f"Rename pin to \"{new_name}\"",
                             _direct)
        except Exception as e:
            self.save_finished.emit(False, f"Failed to rename pin: {e}")

    @pyqtSlot(int)
    def delete_pin(self, pin_id: int):
        try:
            sid = get_pin_subsystem_id(pin_id)
            if not self._check_perm("pin.delete", sid, "delete pins"):
                return

            def _direct():
                persist_delete_pin(pin_id)
                self.save_finished.emit(True, "Pin deleted")
                self.get_scene_data()

            self._route_edit("pin", "delete", pin_id, sid,
                             {"id": pin_id}, f"Delete pin #{pin_id}",
                             _direct)
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

        if not auth.is_system():
            self.save_finished.emit(True, SUBMITTED_MSG)
            self.get_scene_data()
            return

        from Schematic_View_tab.shapes.connector_pin_graphics import PinOrderDialog
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
