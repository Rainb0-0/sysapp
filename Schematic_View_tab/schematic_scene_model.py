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

    modules(id, project_id, name, photo, mass, power, color,
            pos_x, pos_y, width, height)
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

from typing import Dict, List, Any, Optional, Tuple

from database import get_connection, get_current_project_id, get_complete_layout

# Matches the defaults actually used in schematic_graphics.py's ModuleGraphics(...)
DEFAULT_MODULE_WIDTH = 300.0
DEFAULT_MODULE_HEIGHT = 200.0
GRID_MARGIN = 40.0
GRID_CELL_W = 360.0
GRID_CELL_H = 260.0
GRID_COLUMNS = 4


def _grid_fallback_position(index: int) -> Tuple[float, float]:
    """Deterministic fallback layout for modules with no saved position yet."""
    col = index % GRID_COLUMNS
    row = index // GRID_COLUMNS
    return GRID_MARGIN + col * GRID_CELL_W, GRID_MARGIN + row * GRID_CELL_H


def load_schematic_scene(module_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """
    Load the full schematic scene (modules, connectors, pins, interfaces)
    for the current project and return a JSON-serializable dict:

    {
      "modules": [
        {"id": 1, "name": "OBC", "x": 40.0, "y": 40.0,
         "width": 300.0, "height": 200.0,
         "color": "#3a7bd5", "mass": 1.2, "power": 5.0},
        ...
      ],
      "connectors": [
        {"id": 10, "module_id": 1, "name": "J1", "side": "left",
         "color": "#5b8def",
         "pins": [{"id": 100, "name": "PIN1"}, ...]},
        ...
      ],
      "interfaces": [
        {"id": 500, "from_pin": 100, "to_pin": 200, "color": "#5b8def",
         "points": [[x1, y1], [x2, y2]], "manual_override": true, "locked": false},
        ...
      ]
    }

    If module_ids is given, only those modules (and their connectors/pins/
    interfaces between them) are included; otherwise the whole project
    scene is loaded.
    """
    scene: Dict[str, Any] = {"modules": [], "connectors": [], "interfaces": []}

    project_id = get_current_project_id()
    if project_id is None:
        return scene

    with get_connection() as conn:
        cur = conn.cursor()

        # ---------------- Modules ----------------
        query = (
            "SELECT id, name, color, mass, power, pos_x, pos_y, width, height "
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

        # ---------------- Connectors (needed before get_complete_layout) ----------------
        placeholders = ",".join(["%s"] * len(module_id_list))
        cur.execute(
            f"SELECT id, module_id, name, color, side FROM connectors "
            f"WHERE module_id IN ({placeholders}) AND project_id = %s",
            (*module_id_list, project_id),
        )
        connector_rows = cur.fetchall()
        connector_id_list = [row[0] for row in connector_rows]

        # ---------------- Interfaces between the visible modules ----------------
        interface_id_list: List[int] = []
        interface_rows: List[tuple] = []
        if connector_id_list:
            mod_ph = ",".join(["%s"] * len(module_id_list))
            cur.execute(
                f"""
                SELECT DISTINCT i.id, i.pin1_id, i.pin2_id, i.color
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
            mod_id, name, color, mass, power, pos_x, pos_y, width, height = row

            saved_pos = saved_module_positions.get(mod_id) if saved_module_positions else None
            if saved_pos:
                x, y = float(saved_pos[0]), float(saved_pos[1])
                width = saved_pos[2] or width
                height = saved_pos[3] or height
            elif pos_x is not None and pos_y is not None:
                x, y = float(pos_x), float(pos_y)
            else:
                x, y = _grid_fallback_position(idx)

            scene["modules"].append({
                "id": mod_id,
                "name": name,
                "x": x,
                "y": y,
                "width": float(width) if width else DEFAULT_MODULE_WIDTH,
                "height": float(height) if height else DEFAULT_MODULE_HEIGHT,
                "color": color,
                "mass": mass,
                "power": power,
            })

        # ---------------- Assemble connectors + pins ----------------
        for conn_id, mod_id, name, color, side in connector_rows:
            cur.execute(
                "SELECT id, name FROM pins "
                "WHERE connector_id = %s AND project_id = %s ORDER BY pin_number",
                (conn_id, project_id),
            )
            pin_rows = cur.fetchall()

            saved = saved_connector_positions.get(conn_id) if saved_connector_positions else None
            final_side = (saved[4] if saved and len(saved) > 4 else side) or "top"

            scene["connectors"].append({
                "id": conn_id,
                "module_id": mod_id,
                "name": name,
                "color": color,
                "side": final_side,
                "x": float(saved[0]) if saved else None,
                "y": float(saved[1]) if saved else None,
                "pins": [{"id": pin_id, "name": pin_name} for pin_id, pin_name in pin_rows],
            })

        # ---------------- Assemble interfaces ----------------
        if interface_id_list:
            routing_points = saved_interface_points or {}
            # routing_persistence.load_enhanced_interface_data() gives us
            # manual_override/locked/version metadata on top of raw points;
            # reuse it instead of duplicating that parsing here.
            from Schematic_View_tab.routing_persistence import load_enhanced_interface_data
            routing_data = load_enhanced_interface_data(interface_id_list)

            for iface_id, pin1_id, pin2_id, color in interface_rows:
                payload = routing_data.get(iface_id)
                if isinstance(payload, dict):
                    points = payload.get("points", [])
                    meta = {k: v for k, v in payload.items() if k != "points"}
                elif isinstance(payload, list):
                    points, meta = payload, {}
                else:
                    points, meta = [], {}

                scene["interfaces"].append({
                    "id": iface_id,
                    "from_pin": pin1_id,
                    "to_pin": pin2_id,
                    "color": color,
                    "points": [[x, y] for x, y in points],
                    **meta,
                })

    return scene


def save_module_positions(positions: Dict[int, Dict[str, float]]) -> None:
    """
    Persist module positions after a drag in the frontend.

    positions example:
      { 1: {"x": 120.0, "y": 80.0}, 2: {"x": 340.0, "y": 80.0} }
    """
    project_id = get_current_project_id()
    if project_id is None or not positions:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        for mod_id, pos in positions.items():
            cur.execute(
                "UPDATE modules SET pos_x = %s, pos_y = %s "
                "WHERE id = %s AND project_id = %s",
                (pos["x"], pos["y"], mod_id, project_id),
            )
        conn.commit()


def save_connector_positions(positions: Dict[int, Dict[str, float]]) -> None:
    """
    Persist connector positions after a drag in the frontend.

    NOTE: schematic_graphics.py reads connector position/size/side through
    get_complete_layout()'s connector_positions dict, which is a *separate*
    saved-layout table (not columns directly on `connectors`), the same way
    interface routing lives in interface_points rather than on Interfaces.
    This function assumes a parallel helper already exists for writing that
    table (mirroring how routing_persistence.py writes interface_points).
    TODO verify: point this at the real connector-layout persistence
    function/table used by get_complete_layout() instead of a raw UPDATE.
    """
    project_id = get_current_project_id()
    if project_id is None or not positions:
        return

    with get_connection() as conn:
        cur = conn.cursor()
        for conn_id, pos in positions.items():
            cur.execute(
                "UPDATE connectors SET pos_x = %s, pos_y = %s "
                "WHERE id = %s AND project_id = %s",
                (pos["x"], pos["y"], conn_id, project_id),
            )
        conn.commit()


def create_interface(pin1_id: int, pin2_id: int, color: Optional[str] = None) -> Optional[int]:
    """
    Create a new Interfaces row connecting two pins. Returns the new
    interface id, or None if the project context is missing or the pins
    are already connected.
    """
    project_id = get_current_project_id()
    if project_id is None or pin1_id == pin2_id:
        return None

    with get_connection() as conn:
        cur = conn.cursor()

        # Avoid duplicate connections between the same two pins (either order).
        cur.execute(
            "SELECT id FROM Interfaces WHERE project_id = %s "
            "AND ((pin1_id = %s AND pin2_id = %s) OR (pin1_id = %s AND pin2_id = %s))",
            (project_id, pin1_id, pin2_id, pin2_id, pin1_id),
        )
        existing = cur.fetchone()
        if existing:
            return existing[0]

        cur.execute(
            "INSERT INTO Interfaces (project_id, pin1_id, pin2_id, color) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (project_id, pin1_id, pin2_id, color),
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
    """
    Delete a module and everything that hangs off it: its connectors, their
    pins, and any interface touching those pins.
    """
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
                   color: Optional[str] = None) -> Optional[int]:
    """Insert a new module and return its id."""
    project_id = get_current_project_id()
    if project_id is None or not name.strip():
        return None

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO modules (project_id, name, color, pos_x, pos_y, width, height, mass, power) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 0) RETURNING id",
            (project_id, name.strip(), color, x, y, width, height),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def create_connector(module_id: int, name: str, side: str = "top",
                      color: Optional[str] = None) -> Optional[int]:
    project_id = get_current_project_id()
    if project_id is None or not name.strip():
        return None
    if side not in VALID_SIDES:
        side = "top"

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO connectors (project_id, module_id, name, color, side) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (project_id, module_id, name.strip(), color, side),
        )
        new_id = cur.fetchone()[0]
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
        conn.commit()


# ---------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------

def create_pin(connector_id: int, name: str) -> Optional[int]:
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
            "INSERT INTO pins (project_id, connector_id, name, pin_number) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (project_id, connector_id, name.strip(), next_number),
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


def reorder_pins(connector_id: int, new_order: List[str]) -> None:
    """
    Renumber a connector's pins to match new_order (a list of pin *names*
    in their new display order). Direct port of
    EditablePinOrderMixin._update_pin_order_in_database() from
    connector_pin_graphics.py -- same matching-by-name, same query.
    """
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
