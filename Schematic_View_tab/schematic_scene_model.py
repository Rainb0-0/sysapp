# Schematic_View_tab/schematic_scene_model.py
"""
Pure data model for the schematic scene.

This module has ZERO dependency on QGraphicsScene / QGraphicsItem / any
PyQt Graphics-View class. It only knows how to load the schematic scene
from the database and turn it into a plain, JSON-serializable dict, and
how to persist changes (module positions, connector positions, new/deleted
interfaces) back.

Schema below is verified against the real queries used in
schematic_graphics.py (not guessed):

    modules(id, project_id, name, photo, mass, power, min_temp, max_temp,
            color, pos_x, pos_y, width, height)
    connectors(id, project_id, module_id, name, color, side)
    pins(id, project_id, connector_id, name, pin_number)
    Interfaces(id, project_id, pin1_id, pin2_id, color)
    interface_points(interface_id, project_id, point_index, x, y, description)
        -- unchanged, owned by routing_persistence.py

get_complete_layout(module_ids, connector_ids, interface_ids) returns:
    (module_positions, connector_positions, interface_positions, interface_points)
  where:
    module_positions[mod_id]    = (x, y, width, height)
    connector_positions[cid]    = (x, y, width, height, side)
    interface_positions[iid]    = (x, y, rotation)   -- graphics-item transform,
                                   not used by the SVG/path-based renderer
    interface_points[iid]       = [(x, y), ...]       -- same as routing_persistence.py
"""

from typing import Dict, List, Any, Optional, Tuple, Set

from database import (
    get_connection,
    get_current_project_id,
    get_complete_layout,
    pins_connectable,
)
from auth_manager import auth

# Matches the defaults actually used in schematic_graphics.py's ModuleGraphics(...)
DEFAULT_MODULE_WIDTH = 160.0
DEFAULT_MODULE_HEIGHT = 100.0
GRID_MARGIN = 40.0
GRID_CELL_W = 380.0
GRID_CELL_H = 280.0
GRID_COLUMNS = 4

# Sentinel for update_module(): distinguishes "field not passed" (leave the
# stored value alone) from "explicitly set to None" (clear to NULL). Used
# for min_temp/max_temp where NULL is a legitimate stored value.
NOT_SET = object()



# Distinct colors for subsystems (matches tree view visual hierarchy)
SUBSYSTEM_COLORS = [
    "#e67e22",  # orange (matches tree view module color)
    "#3a7bd5",  # blue
    "#27ae60",  # green
    "#9b59b6",  # purple
    "#e74c3c",  # red
    "#1abc9c",  # teal
    "#f39c12",  # amber
    "#3498db",  # sky blue
    "#2ecc71",  # emerald
    "#8e44ad",  # dark purple
    "#d35400",  # burnt orange
    "#16a085",  # dark teal
]

# Semi-transparent versions for subsystem halos
SUBSYSTEM_HALO_COLORS = [
    "rgba(230, 126, 34, 0.08)",   # orange (matches tree view module color)
    "rgba(58, 123, 213, 0.08)",   # blue
    "rgba(39, 174, 96, 0.08)",    # green
    "rgba(155, 89, 182, 0.08)",   # purple
    "rgba(231, 76, 60, 0.08)",    # red
    "rgba(26, 188, 156, 0.08)",   # teal
    "rgba(243, 156, 18, 0.08)",   # amber
    "rgba(52, 152, 219, 0.08)",   # sky blue
    "rgba(46, 204, 113, 0.08)",   # emerald
    "rgba(142, 68, 173, 0.08)",   # dark purple
    "rgba(211, 84, 0, 0.08)",     # burnt orange
    "rgba(22, 160, 133, 0.08)",   # dark teal
]


def _grid_fallback_position(index: int) -> Tuple[float, float]:
    """Deterministic fallback layout for modules with no saved position yet."""
    col = index % GRID_COLUMNS
    row = index // GRID_COLUMNS
    return GRID_MARGIN + col * GRID_CELL_W, GRID_MARGIN + row * GRID_CELL_H


def load_schematic_scene(
    module_ids: Optional[List[int]] = None,
    connector_ids: Optional[Set[int]] = None,
    pin_ids: Optional[Set[int]] = None,
) -> Dict[str, Any]:
    """
    Load the full schematic scene (modules, connectors, pins, interfaces)
    for the current project and return a JSON-serializable dict:

    {
      "subsystems": [
        {"id": 1, "name": "OBC", "color_index": 0},
        ...
      ],
      "modules": [
        {"id": 1, "name": "OBC", "x": 40.0, "y": 40.0,
         "width": 300.0, "height": 200.0,
         "color": "#3a7bd5", "subsystem_id": 1,
         "mass": 1.2, "power": 5.0},
        ...
      ],
      "connectors": [ ... ],
      "interfaces": [ ... ],
      "hidden_pins": [3, 7, ...]  # pin IDs to hide wiring for
    }

    Args:
        module_ids: If given, only these modules are shown. None = show all.
        connector_ids: If given, only these connectors within selected
            modules are shown. None = show all.
        pin_ids: If given, pins NOT in this set have their wiring hidden
            but remain visible. None = show all wiring.
    """
    scene: Dict[str, Any] = {"subsystems": [], "modules": [], "connectors": [], "interfaces": []}

    project_id = get_current_project_id()
    if project_id is None:
        return scene

    with get_connection() as conn:
        cur = conn.cursor()

        # ---------------- Managed data pin types ----------------
        cur.execute("SELECT name FROM pin_types ORDER BY name")
        scene["pin_types"] = [r[0] for r in cur.fetchall()]

        # ---------------- Subsystems ----------------
        cur.execute(
            "SELECT id, name FROM subsystems WHERE project_id = %s ORDER BY name",
            (project_id,),
        )
        subsystem_rows = cur.fetchall()
        subsystem_map = {}  # subsystem_id -> index into color list
        for idx, (ss_id, ss_name) in enumerate(subsystem_rows):
            color_idx = idx % len(SUBSYSTEM_COLORS)
            subsystem_map[ss_id] = color_idx
            scene["subsystems"].append({
                "id": ss_id,
                "name": ss_name,
                "color": SUBSYSTEM_COLORS[color_idx],
                "halo_color": SUBSYSTEM_HALO_COLORS[color_idx],
            })

        # ---------------- Modules ----------------
        query = (
            "SELECT id, name, color, mass, power, min_temp, max_temp, pos_x, pos_y, width, height, subsystem_id "
            "FROM modules WHERE project_id = %s"
        )
        params: tuple = (project_id,)
        if module_ids:
            placeholders = ",".join(["%s"] * len(module_ids))
            query += f" AND id IN ({placeholders})"
            params = (project_id, *module_ids)

        cur.execute(query, params)
        module_rows = cur.fetchall()
        module_id_list = [row[0] for row in module_rows]
        if not module_id_list:
            return scene

        # ---------------- Connectors ---------------
        placeholders = ",".join(["%s"] * len(module_id_list))
        cur.execute(
            f"SELECT id, module_id, name, color, side, collapsed FROM connectors "
            f"WHERE module_id IN ({placeholders}) AND project_id = %s",
            (*module_id_list, project_id),
        )
        all_connector_rows = cur.fetchall()

        # Filter connectors by connector_ids if specified
        if connector_ids is not None:
            connector_rows = [r for r in all_connector_rows if r[0] in connector_ids]
        else:
            connector_rows = all_connector_rows

        connector_id_list = [row[0] for row in connector_rows]

        # Fetch pins for all these connectors — always keep all pins visible
        pin_data_by_connector = {}
        all_pin_ids_set: Set[int] = set()
        if connector_id_list:
            conn_ph = ",".join(["%s"] * len(connector_id_list))
            cur.execute(
                f"SELECT id, connector_id, name, value, pin_type, is_ground FROM pins "
                f"WHERE connector_id IN ({conn_ph}) AND project_id = %s ORDER BY pin_number",
                (*connector_id_list, project_id),
            )
            for pin_id, conn_id, pin_name, pin_value, pin_type, is_ground in cur.fetchall():
                all_pin_ids_set.add(pin_id)
                pin_data_by_connector.setdefault(conn_id, []).append({
                    "id": pin_id,
                    "name": pin_name,
                    "voltage": pin_value,
                    # Raw pin_type (None when untyped) — the renderer applies
                    # the "Data" default only for display, so the same-type
                    # wiring rule sees the real stored value.
                    "pin_type": pin_type,
                    "is_ground": bool(is_ground) if is_ground is not None else False,
                })

        # Compute hidden_pins: if pin_ids is specified, pins NOT in it have wiring hidden
        if pin_ids is not None:
            scene["hidden_pins"] = [pid for pid in all_pin_ids_set if pid not in pin_ids]
        else:
            scene["hidden_pins"] = []

        # ---------------- Interfaces between the visible modules ----------------
        interface_id_list: List[int] = []
        interface_rows: List[tuple] = []
        if connector_id_list:
            mod_ph = ",".join(["%s"] * len(module_id_list))
            cur.execute(
                f"""
                SELECT DISTINCT i.id, i.pin1_id, i.pin2_id, i.color, i.current
                FROM Interfaces i
                JOIN pins p1 ON i.pin1_id = p1.id
                JOIN pins p2 ON i.pin2_id = p2.id
                JOIN connectors c1 ON p1.connector_id = c1.id
                JOIN connectors c2 ON p2.connector_id = c2.id
                WHERE c1.module_id IN ({mod_ph}) AND c2.module_id IN ({mod_ph})
                AND i.project_id = %s
                """,
                (*module_id_list, *module_id_list, project_id),
            )
            interface_rows = cur.fetchall()
            interface_id_list = [row[0] for row in interface_rows]

        # ---------------- Saved layout overrides ----------------
        saved_module_positions, saved_connector_positions, _saved_interface_positions, saved_interface_points = (
            get_complete_layout(module_id_list, connector_id_list, interface_id_list)
        )

        # ---------------- Assemble modules ----------------
        for idx, row in enumerate(module_rows):
            mod_id, name, color, mass, power, min_temp, max_temp, pos_x, pos_y, width, height, subsystem_id = row

            # Use DB width/height directly — no auto-sizing from connectors.
            # Connector sizes are determined purely by pin count in the JS renderer.
            effective_w = float(width) if width else DEFAULT_MODULE_WIDTH
            effective_h = float(height) if height else DEFAULT_MODULE_HEIGHT

            # Determine position: prefer raw DB pos_x/pos_y (these are set on
            # creation AND updated on every drag). Only fall back to grid when
            # pos_x IS NULL (module never explicitly positioned).
            if pos_x is not None and pos_y is not None:
                x, y = float(pos_x), float(pos_y)
                # If raw DB says (0,0), check saved_layout for a non-zero
                # override — user could have dragged to (0,0) but it's rare.
                if x == 0.0 and y == 0.0 and saved_module_positions:
                    sp = saved_module_positions.get(mod_id)
                    if sp and (float(sp[0]) != 0.0 or float(sp[1]) != 0.0):
                        x, y = float(sp[0]), float(sp[1])
                # Also use saved_layout width/height if present
                if saved_module_positions:
                    sp = saved_module_positions.get(mod_id)
                    if sp:
                        if sp[2]:
                            effective_w = float(sp[2])
                        if sp[3]:
                            effective_h = float(sp[3])
            else:
                # pos_x IS NULL — never explicitly positioned, use grid
                x, y = _grid_fallback_position(idx)

            # Choose a color: use DB color if set, otherwise derive from subsystem
            if color and color != "#C8C8FF":
                module_color = color
            elif subsystem_id and subsystem_id in subsystem_map:
                module_color = SUBSYSTEM_COLORS[subsystem_map[subsystem_id]]
            else:
                module_color = SUBSYSTEM_COLORS[idx % len(SUBSYSTEM_COLORS)]

            # The schematic view is read-only for everyone except the system
            # admin (subsystem admins included). `editable` drives every
            # interaction guard on the JS side (context menus, drag, resize,
            # re-routing), and the bridge re-checks auth on each write.
            # Pending-created entities are marked editable=False by the
            # suggestions overlay so they can't be dragged before approval.
            can_edit = auth.is_system()

            scene["modules"].append({
                "id": mod_id,
                "name": name,
                "x": x,
                "y": y,
                "width": effective_w,
                "height": effective_h,
                "color": module_color,
                "subsystem_id": subsystem_id,
                "mass": mass,
                "power": power,
                "min_temp": min_temp,
                "max_temp": max_temp,
                "editable": can_edit,
            })

        # ---------------- Assemble connectors + pins ----------------
        for conn_id, mod_id, name, color, side, collapsed in connector_rows:
            pins = pin_data_by_connector.get(conn_id, [])

            saved = saved_connector_positions.get(conn_id) if saved_connector_positions else None
            final_side = (saved[4] if saved and len(saved) > 4 else side) or "top"

            scene["connectors"].append({
                "id": conn_id,
                "module_id": mod_id,
                "name": name,
                "color": color,
                "side": final_side,
                "collapsed": bool(collapsed) if collapsed is not None else False,
                "x": float(saved[0]) if saved else None,
                "y": float(saved[1]) if saved else None,
                "pins": pins,
            })

        # ---------------- Assemble interfaces ----------------
        if interface_id_list:
            from Schematic_View_tab.routing_persistence import load_enhanced_interface_data
            routing_data = load_enhanced_interface_data(interface_id_list)

            for iface_id, pin1_id, pin2_id, color, iface_current in interface_rows:
                payload = routing_data.get(iface_id)
                if isinstance(payload, dict):
                    points = payload.get("points", [])
                    meta = {k: v for k, v in payload.items() if k != "points"}
                elif isinstance(payload, list):
                    points, meta = payload, {}
                else:
                    points, meta = [], {}

                # Read-only for everyone except the system admin (see the
                # module `editable` comment above).
                iface_can_edit = auth.is_system()

                scene["interfaces"].append({
                    "id": iface_id,
                    "from_pin": pin1_id,
                    "to_pin": pin2_id,
                    "color": color,
                    "current": float(iface_current or 0.0),
                    "points": [[x, y] for x, y in points],
                    "editable": iface_can_edit,
                    **meta,
                })

    return scene


def save_module_positions(positions: Dict[int, Dict[str, float]]) -> None:
    """
    Persist module positions after a drag in the frontend.
    Can also persist width/height if included in the position dict.

    positions example:
      { 1: {"x": 120.0, "y": 80.0}, 2: {"x": 340.0, "y": 80.0} }
    Or with size:
      { 1: {"x": 120.0, "y": 80.0, "width": 300.0, "height": 200.0} }
    """
    project_id = get_current_project_id()
    if project_id is None or not positions:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        for mod_id, pos in positions.items():
            x = pos.get("x")
            y = pos.get("y")
            w = pos.get("width")
            h = pos.get("height")
            if w is not None and h is not None:
                cur.execute(
                    "UPDATE modules SET pos_x = %s, pos_y = %s, width = %s, height = %s "
                    "WHERE id = %s AND project_id = %s",
                    (x, y, w, h, mod_id, project_id),
                )
            else:
                cur.execute(
                    "UPDATE modules SET pos_x = %s, pos_y = %s "
                    "WHERE id = %s AND project_id = %s",
                    (x, y, mod_id, project_id),
                )
        conn.commit()


def save_module_size(module_id: int, width: float, height: float) -> None:
    """
    Persist module dimensions after a resize operation.
    """
    project_id = get_current_project_id()
    if project_id is None:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE modules SET width = %s, height = %s "
            "WHERE id = %s AND project_id = %s",
            (width, height, module_id, project_id),
        )
        conn.commit()


def save_connector_positions(positions: Dict[int, Dict[str, float]]) -> None:
    """
    Persist connector positions after a drag in the frontend.
    Each entry can include x, y, and optionally side.
    Example: {1: {'x': 100, 'y': 50, 'side': 'right'}}
    """
    project_id = get_current_project_id()
    if project_id is None or not positions:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        for conn_id, pos in positions.items():
            side = pos.get("side")
            if side:
                cur.execute(
                    "UPDATE connectors SET pos_x = %s, pos_y = %s, side = %s "
                    "WHERE id = %s AND project_id = %s",
                    (pos["x"], pos["y"], side, conn_id, project_id),
                )
            else:
                cur.execute(
                    "UPDATE connectors SET pos_x = %s, pos_y = %s "
                    "WHERE id = %s AND project_id = %s",
                    (pos["x"], pos["y"], conn_id, project_id),
                )
        conn.commit()


def create_interface(pin1_id: int, pin2_id: int, color: Optional[str] = None,
                      current: Optional[float] = None) -> Optional[int]:
    """Create a new Interfaces row connecting two pins."""
    project_id = get_current_project_id()
    if project_id is None or pin1_id == pin2_id:
        return None

    # Same-type wiring rule — the bridge reports the reason to the user.
    ok, _reason = pins_connectable(pin1_id, pin2_id)
    if not ok:
        return None

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM Interfaces WHERE project_id = %s "
            "AND ((pin1_id = %s AND pin2_id = %s) OR (pin1_id = %s AND pin2_id = %s))",
            (project_id, pin1_id, pin2_id, pin2_id, pin1_id),
        )
        existing = cur.fetchone()
        if existing:
            return existing[0]

        cur.execute(
            "INSERT INTO Interfaces (project_id, pin1_id, pin2_id, color, current) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (project_id, pin1_id, pin2_id, color, current),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def delete_interface(interface_id: int) -> None:
    """Delete an interface and its saved routing points."""
    project_id = get_current_project_id()
    if project_id is None:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM interface_points WHERE interface_id = %s AND project_id = %s",
            (interface_id, project_id),
        )
        cur.execute(
            "DELETE FROM Interfaces WHERE id = %s AND project_id = %s",
            (interface_id, project_id),
        )
        conn.commit()


def delete_module(module_id: int) -> None:
    """Delete a module and everything that hangs off it."""
    project_id = get_current_project_id()
    if project_id is None:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM pins WHERE connector_id IN "
            "(SELECT id FROM connectors WHERE module_id = %s AND project_id = %s)",
            (module_id, project_id),
        )
        pin_ids = [row[0] for row in cur.fetchall()]

        if pin_ids:
            ph = ",".join(["%s"] * len(pin_ids))
            cur.execute(
                f"SELECT id FROM Interfaces WHERE project_id = %s "
                f"AND (pin1_id IN ({ph}) OR pin2_id IN ({ph}))",
                (project_id, *pin_ids, *pin_ids),
            )
            iface_ids = [row[0] for row in cur.fetchall()]
            for iface_id in iface_ids:
                delete_interface(iface_id)

        cur.execute(
            "DELETE FROM pins WHERE connector_id IN "
            "(SELECT id FROM connectors WHERE module_id = %s AND project_id = %s)",
            (module_id, project_id),
        )
        cur.execute(
            "DELETE FROM connectors WHERE module_id = %s AND project_id = %s",
            (module_id, project_id),
        )
        cur.execute(
            "DELETE FROM modules WHERE id = %s AND project_id = %s",
            (module_id, project_id),
        )
        conn.commit()


def rename_module(module_id: int, new_name: str) -> None:
    project_id = get_current_project_id()
    if project_id is None or not new_name.strip():
        return
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE modules SET name = %s WHERE id = %s AND project_id = %s",
            (new_name.strip(), module_id, project_id),
        )
        conn.commit()


def create_module(name: str, x: float = 40.0, y: float = 40.0,
                   width: float = DEFAULT_MODULE_WIDTH,
                   height: float = DEFAULT_MODULE_HEIGHT,
                   color: Optional[str] = None,
                   subsystem_id: Optional[int] = None,
                   mass: float = 0.0,
                   power: float = 0.0,
                   min_temp: Optional[float] = None,
                   max_temp: Optional[float] = None) -> Optional[int]:
    """Insert a new module and return its id."""
    project_id = get_current_project_id()
    if project_id is None or not name.strip():
        return None
    if min_temp is not None and max_temp is not None and min_temp > max_temp:
        raise ValueError("Min operating temp cannot exceed max operating temp.")

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO modules (project_id, name, color, pos_x, pos_y, width, height, mass, power, min_temp, max_temp, subsystem_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (project_id, name.strip(), color, x, y, width, height, mass, power, min_temp, max_temp, subsystem_id),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def create_connector(module_id: int, name: str, side: str = "top",
                      color: Optional[str] = None,
                      number_of_pins: int = 0) -> Optional[int]:
    project_id = get_current_project_id()
    if project_id is None or not name.strip():
        return None
    VALID_SIDES = {"left", "right", "top", "bottom"}
    if side not in VALID_SIDES:
        side = "top"

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO connectors (project_id, module_id, name, color, side, number_of_pins) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (project_id, module_id, name.strip(), color, side, number_of_pins),
        )
        new_id = cur.fetchone()[0]
        # Update the module's connector count
        cur.execute(
            "UPDATE modules SET num_connectors = num_connectors + 1 WHERE id = %s AND project_id = %s",
            (module_id, project_id),
        )
        conn.commit()
        return new_id


def rename_connector(connector_id: int, new_name: str) -> None:
    project_id = get_current_project_id()
    if project_id is None or not new_name.strip():
        return
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE connectors SET name = %s WHERE id = %s AND project_id = %s",
            (new_name.strip(), connector_id, project_id),
        )
        conn.commit()


def set_connector_side(connector_id: int, side: str) -> None:
    VALID_SIDES = {"left", "right", "top", "bottom"}
    project_id = get_current_project_id()
    if project_id is None or side not in VALID_SIDES:
        return
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE connectors SET side = %s WHERE id = %s AND project_id = %s",
            (side, connector_id, project_id),
        )
        conn.commit()


def delete_connector(connector_id: int) -> None:
    """Delete a connector, its pins, and any interface touching those pins."""
    project_id = get_current_project_id()
    if project_id is None:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        # Grab the module_id before deleting so we can update num_connectors
        cur.execute(
            "SELECT module_id FROM connectors WHERE id = %s AND project_id = %s",
            (connector_id, project_id),
        )
        row = cur.fetchone()
        module_id = row[0] if row else None

        cur.execute(
            "SELECT id FROM pins WHERE connector_id = %s AND project_id = %s",
            (connector_id, project_id),
        )
        pin_ids = [row[0] for row in cur.fetchall()]

        if pin_ids:
            ph = ",".join(["%s"] * len(pin_ids))
            cur.execute(
                f"SELECT id FROM Interfaces WHERE project_id = %s "
                f"AND (pin1_id IN ({ph}) OR pin2_id IN ({ph}))",
                (project_id, *pin_ids, *pin_ids),
            )
            iface_ids = [row[0] for row in cur.fetchall()]
            for iface_id in iface_ids:
                delete_interface(iface_id)

        cur.execute(
            "DELETE FROM pins WHERE connector_id = %s AND project_id = %s",
            (connector_id, project_id),
        )
        cur.execute(
            "DELETE FROM connectors WHERE id = %s AND project_id = %s",
            (connector_id, project_id),
        )
        # Decrement the module's connector count
        if module_id is not None:
            cur.execute(
                "UPDATE modules SET num_connectors = GREATEST(0, num_connectors - 1) WHERE id = %s AND project_id = %s",
                (module_id, project_id),
            )
        conn.commit()


# ---------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------

def create_pin(connector_id: int, name: str,
               pin_type: Optional[str] = None,
               is_ground: bool = False,
               value: Optional[float] = None,
               current: Optional[float] = None,
               description: Optional[str] = None) -> Optional[int]:
    project_id = get_current_project_id()
    if project_id is None or not name.strip():
        return None

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(MAX(pin_number), -1) + 1 FROM pins "
            "WHERE connector_id = %s AND project_id = %s",
            (connector_id, project_id),
        )
        next_number = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO pins (project_id, connector_id, name, pin_number, "
            "pin_type, is_ground, value, current, description) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (project_id, connector_id, name.strip(), next_number,
             pin_type, is_ground, value, current, description),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def rename_pin(pin_id: int, new_name: str) -> None:
    project_id = get_current_project_id()
    if project_id is None or not new_name.strip():
        return
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE pins SET name = %s WHERE id = %s AND project_id = %s",
            (new_name.strip(), pin_id, project_id),
        )
        conn.commit()


def delete_pin(pin_id: int) -> None:
    """Delete a pin and any interface that touches it."""
    project_id = get_current_project_id()
    if project_id is None:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM Interfaces WHERE project_id = %s "
            "AND (pin1_id = %s OR pin2_id = %s)",
            (project_id, pin_id, pin_id),
        )
        iface_ids = [row[0] for row in cur.fetchall()]
        for iface_id in iface_ids:
            delete_interface(iface_id)

        cur.execute(
            "DELETE FROM pins WHERE id = %s AND project_id = %s",
            (pin_id, project_id),
        )
        conn.commit()


def update_module(module_id: int, name: Optional[str] = None,
                   mass: Optional[float] = None,
                   power: Optional[float] = None,
                   color: Optional[str] = None,
                   subsystem_id: Optional[int] = None,
                   min_temp: Any = NOT_SET,
                   max_temp: Any = NOT_SET) -> None:
    """
    Update a module's fields. Only non-None values are updated for the
    regular fields. min_temp/max_temp use the NOT_SET sentinel: pass a float
    to set, None to clear (NULL), or omit to leave unchanged.
    """
    project_id = get_current_project_id()
    if project_id is None:
        return

    updates = {}
    if name is not None:
        updates["name"] = name.strip()
    if mass is not None:
        updates["mass"] = mass
    if power is not None:
        updates["power"] = power
    if color is not None:
        updates["color"] = color if color else None
    if subsystem_id is not None:
        updates["subsystem_id"] = subsystem_id
    if min_temp is not NOT_SET:
        updates["min_temp"] = min_temp
    if max_temp is not NOT_SET:
        updates["max_temp"] = max_temp

    if not updates:
        return

    # Enforce min operating temp <= max operating temp (only when both are
    # set). If just one side is being updated, validate against the stored
    # value of the other.
    if "min_temp" in updates or "max_temp" in updates:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT min_temp, max_temp FROM modules WHERE id=%s AND project_id=%s",
                (module_id, project_id),
            )
            row = cur.fetchone()
        stored_min, stored_max = (row if row else (None, None))
        new_min = updates.get("min_temp", stored_min)
        new_max = updates.get("max_temp", stored_max)
        if new_min is not None and new_max is not None and new_min > new_max:
            raise ValueError("Min operating temp cannot exceed max operating temp.")

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [module_id, project_id]

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE modules SET {set_clause} WHERE id = %s AND project_id = %s",
            values,
        )
        conn.commit()


def update_connector(connector_id: int, name: Optional[str] = None,
                     color: Optional[str] = None,
                     side: Optional[str] = None,
                     number_of_pins: Optional[int] = None,
                     collapsed: Any = NOT_SET) -> None:
    """
    Update a connector's fields. Only non-None values are updated.
    collapsed uses the NOT_SET sentinel (True/False set it explicitly).
    """
    project_id = get_current_project_id()
    if project_id is None:
        return

    VALID_SIDES = {"left", "right", "top", "bottom"}
    updates = {}
    if name is not None:
        updates["name"] = name.strip()
    if color is not None:
        updates["color"] = color if color else None
    if side is not None and side in VALID_SIDES:
        updates["side"] = side
    if number_of_pins is not None:
        updates["number_of_pins"] = number_of_pins
    if collapsed is not NOT_SET:
        updates["collapsed"] = bool(collapsed)

    if not updates:
        return

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [connector_id, project_id]

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE connectors SET {set_clause} WHERE id = %s AND project_id = %s",
            values,
        )
        conn.commit()


def update_pin(pin_id: int, name: Optional[str] = None,
               pin_type: Optional[str] = None,
               is_ground: Optional[bool] = None,
               value: Optional[float] = None,
               current: Optional[float] = None,
               description: Optional[str] = None) -> None:
    """Update a pin's fields. Only non-None values are updated."""
    project_id = get_current_project_id()
    if project_id is None:
        return

    updates = {}
    if name is not None:
        updates["name"] = name.strip()
    if pin_type is not None:
        updates["pin_type"] = pin_type if pin_type else None
    if is_ground is not None:
        updates["is_ground"] = is_ground
    if value is not None:
        updates["value"] = value
    if current is not None:
        updates["current"] = current
    if description is not None:
        updates["description"] = description if description else None

    if not updates:
        return

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [pin_id, project_id]

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE pins SET {set_clause} WHERE id = %s AND project_id = %s",
            values,
        )
        conn.commit()


def reorder_pins(connector_id: int, new_order: List[str]) -> None:
    """Renumber a connector's pins to match new_order (a list of pin *names*)."""
    project_id = get_current_project_id()
    if project_id is None or not new_order:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        for index, pin_label in enumerate(new_order):
            cur.execute(
                "UPDATE pins SET pin_number = %s "
                "WHERE connector_id = %s AND name = %s AND project_id = %s",
                (index, connector_id, pin_label, project_id),
            )
        conn.commit()
