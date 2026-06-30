# Schematic_View_tab/routing_persistence.py
import json
from typing import Dict, List, Tuple, Union, Any
from database import get_connection, get_current_project_id

# Type hints
Point = Tuple[float, float]
# Save payload can be a legacy list of points or a dict that includes points + metadata
SaveData = Union[List[Point], Dict[str, Any]]

def save_enhanced_interface_data(interface_data: Dict[int, SaveData]) -> None:
    """
    Persist routing points and optional metadata for each interface_id.
    The first point's 'description' column stores JSON metadata when present.

    interface_data example:
      {
        12: {
          "points": [(x1,y1), (x2,y2), ...],
          "manual_override": True,
          "edit_count": 3,
          "locked": False,
          "metadata": {"route": "orthogonal", "note": "user tweaked"},
          "pin_sides": {"pin1":"left","pin2":"right"},
          "version": "2.0"
        },
        13: [(x1,y1), (x2,y2)]   # legacy list without metadata
      }
    """
    if not interface_data:
        return

    project_id = get_current_project_id()
    if project_id is None:
        return

    with get_connection() as conn:
        cur = conn.cursor()

        for interface_id, payload in interface_data.items():
            # Clear old points
            cur.execute(
                "DELETE FROM interface_points WHERE interface_id = %s AND project_id = %s",
                (interface_id, project_id)
            )

            # Normalize input
            if isinstance(payload, dict):
                points = payload.get("points", [])
                meta = {
                    "manual_override": payload.get("manual_override", False),
                    "edit_count": payload.get("edit_count", 0),
                    "locked": payload.get("locked", False),
                    "metadata": payload.get("metadata", {}),
                    "pin_sides": payload.get("pin_sides", {}),
                    "version": payload.get("version", "2.0")
                }
                meta_json = json.dumps(meta)
            elif isinstance(payload, list):
                points = payload
                meta_json = json.dumps({"manual_override": False, "version": "1.0"})
            else:
                points = []
                meta_json = json.dumps({"manual_override": False, "version": "1.0"})

            # Insert points (store metadata JSON in description for the first point)
            for idx, pt in enumerate(points):
                x, y = float(pt[0]), float(pt[1])
                desc = meta_json if idx == 0 else None
                cur.execute(
                    "INSERT INTO interface_points (interface_id, project_id, point_index, x, y, description) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (interface_id, project_id, idx, x, y, desc)
                )

        conn.commit()


def load_enhanced_interface_data(interface_ids: List[int]) -> Dict[int, SaveData]:
    """
    Load routing points and optional metadata for a list of interface ids.
    If metadata JSON exists in the first point's 'description', returns a dict with
    points and metadata. Otherwise returns the legacy list of points.

    Returns:
      {
        12: {"points":[...], "manual_override":..., "version":"2.0", ...},
        13: [(x1,y1), (x2,y2)]
      }
    """
    result: Dict[int, SaveData] = {}
    if not interface_ids:
        return result

    project_id = get_current_project_id()
    if project_id is None:
        return result

    placeholders = ",".join(["%s"] * len(interface_ids))
    query = (
        f"SELECT interface_id, point_index, x, y, description "
        f"FROM interface_points "
        f"WHERE interface_id IN ({placeholders}) AND project_id = %s "
        f"ORDER BY interface_id, point_index"
    )

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, (*interface_ids, project_id))

        cur_iid = None
        pts: List[Point] = []
        meta: Dict[str, Any] = {}

        def flush(iid: int):
            if iid is None:
                return
            if meta:
                # v2 style: dict with points + metadata
                out: Dict[str, Any] = {"points": pts}
                out.update(meta)
                result[iid] = out
            else:
                # legacy style: list of points only
                result[iid] = pts

        for iid, idx, x, y, desc in cur.fetchall():
            if cur_iid != iid:
                flush(cur_iid)
                cur_iid = iid
                pts = []
                meta = {}

            pts.append((float(x), float(y)))
            if idx == 0 and desc:
                try:
                    meta = json.loads(desc) if isinstance(desc, str) else {}
                except Exception:
                    meta = {}

        flush(cur_iid)

    return result
