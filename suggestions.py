# suggestions.py
"""
Change-suggestion workflow.

Non-system-admin users never write to the schema tables directly. Every
create / update / delete they attempt anywhere (Architecture tree, wiring
matrix, schematic canvas, modes) is recorded here as a *pending change*
instead. The system admin reviews the queue and approves or rejects each
suggestion; approval *applies* the change to the DB (via the same persist
functions the system admin uses), and rejections/stale changes are resolved
without touching the data.

Optimistic preview: pending suggestions from the current user are merged on
top of the real DB state when the UI loads (see pending_preview_scene() for
the schematic and pending_tree_rows() for the Architecture tree), so the
proposer immediately sees their own pending changes in place, clearly
marked as awaiting approval.

Conflict handling: approval re-validates every reference (target entity and
its parents still exist, wiring rules still hold). If a suggestion references
an item that was deleted — or an approved change made it invalid — it is
marked ``stale`` with a review note instead of being applied.

Temp ids: pending-created entities get deterministic negative ids so other
pending suggestions can reference them before approval:
    temp_id = -(suggestion_id * 10 + ENTITY_OFFSET[entity_type])
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from psycopg2.extras import Json

from database import (
    get_connection,
    get_current_project_id,
    pins_connectable,
    check_pin_change_interfaces,
    save_interface_points,
)
from auth_manager import auth

# ---------------------------------------------------------------------------
# Entity type registry + temp-id encoding
# ---------------------------------------------------------------------------
ENTITY_OFFSETS = {
    "module": 1,
    "connector": 2,
    "pin": 3,
    "interface": 4,
    "mode": 5,
}
ENTITY_LABELS = {
    "module": "Module",
    "connector": "Connector",
    "pin": "Pin",
    "interface": "Connection",
    "mode": "Mode",
}
ACTION_LABELS = {"create": "Add", "update": "Edit", "delete": "Delete"}

DEFAULT_MODULE_W = 160.0
DEFAULT_MODULE_H = 100.0


def temp_id_for(suggestion_id: int, entity_type: str) -> int:
    """Deterministic negative temp id for a pending-created entity."""
    return -(suggestion_id * 10 + ENTITY_OFFSETS.get(entity_type, 0))


def suggestion_id_from_temp(temp_id: int) -> int:
    """Recover the suggestion id encoded in a temp id."""
    return (-temp_id) // 10


def is_temp_id(value) -> bool:
    return isinstance(value, int) and value < 0


# ---------------------------------------------------------------------------
# Recording suggestions
# ---------------------------------------------------------------------------
def _default_dedupe_key(entity_type: str, payload: dict) -> str:
    """A stable key used to avoid stacking duplicate pending creates."""
    if entity_type == "module":
        return f"module|{payload.get('subsystem_id')}|{payload.get('name')}"
    if entity_type == "connector":
        return f"connector|{payload.get('module_id')}|{payload.get('name')}"
    if entity_type == "pin":
        return f"pin|{payload.get('connector_id')}|{payload.get('name')}"
    if entity_type == "interface":
        pins = sorted([str(payload.get("pin1_id")), str(payload.get("pin2_id"))])
        return f"interface|{'|'.join(pins)}"
    if entity_type == "mode":
        return f"mode|{payload.get('name')}"
    return f"{entity_type}|{payload}"


def suggest_change(
    entity_type: str,
    action: str,
    entity_id: Optional[int],
    subsystem_id: Optional[int],
    payload: dict,
    summary: str,
    user_id: Optional[int] = None,
    dedupe: bool = True,
) -> Optional[int]:
    """
    Record a change suggestion for the current project and user.

    Returns the pending-change id, or None if there is no open project.
    For creates, a deterministic temp id is assigned so other suggestions
    and the preview layer can reference the item before it exists.

    When ``dedupe`` is True and the same user already has a *pending*
    suggestion for the same target, the existing one is replaced instead of
    stacking (so repeated drags/edits keep a single fresh entry).
    """
    project_id = get_current_project_id()
    if project_id is None:
        return None
    user_id = user_id if user_id is not None else auth.user_id
    if not user_id:
        return None
    if entity_type not in ENTITY_OFFSETS:
        return None

    with get_connection() as conn:
        cur = conn.cursor()

        # --- dedupe against an existing pending suggestion from this user ---
        if dedupe:
            existing = None
            if entity_id is not None:
                cur.execute(
                    "SELECT id FROM pending_changes "
                    "WHERE project_id=%s AND user_id=%s AND entity_type=%s "
                    "AND action=%s AND entity_id=%s AND status='pending' "
                    "ORDER BY id LIMIT 1",
                    (project_id, user_id, entity_type, action, entity_id),
                )
                row = cur.fetchone()
                existing = row[0] if row else None
            else:
                key = _default_dedupe_key(entity_type, payload)
                cur.execute(
                    "SELECT id, payload FROM pending_changes "
                    "WHERE project_id=%s AND user_id=%s AND entity_type=%s "
                    "AND action=%s AND entity_id IS NULL AND status='pending' "
                    "ORDER BY id",
                    (project_id, user_id, entity_type, action),
                )
                for pid, other_payload in cur.fetchall():
                    if _default_dedupe_key(entity_type, other_payload or {}) == key:
                        existing = pid
                        break
            if existing is not None:
                # For update actions, MERGE field-level changes instead of
                # replacing them wholesale, so a later drag (position fields)
                # never clobbers an earlier pending property edit (name,
                # colour, …) on the same entity — and vice versa.
                if action == "update" and entity_id is not None:
                    cur.execute(
                        "SELECT payload FROM pending_changes WHERE id=%s",
                        (existing,),
                    )
                    old_row = cur.fetchone()
                    if old_row:
                        old_payload = old_row[0] or {}
                        incoming_fields = payload.get("fields")
                        merged_fields = dict(old_payload.get("fields") or {})
                        merged_fields.update(incoming_fields or {})
                        new_payload = dict(payload)
                        # Preserve keys the incoming payload doesn't re-specify
                        # (e.g. a routing-only update must not erase a pending
                        # fields edit, and a fields edit must not erase a
                        # pending routing change).
                        for k, v in old_payload.items():
                            if k not in new_payload:
                                new_payload[k] = v
                        if merged_fields or incoming_fields:
                            new_payload["fields"] = merged_fields
                        else:
                            new_payload.pop("fields", None)
                        payload = new_payload
                cur.execute(
                    "UPDATE pending_changes SET payload=%s, summary=%s "
                    "WHERE id=%s",
                    (Json(payload), summary, existing),
                )
                conn.commit()
                return existing

        # --- insert a fresh suggestion ---
        cur.execute(
            "INSERT INTO pending_changes "
            "(project_id, user_id, action, entity_type, entity_id, "
            " subsystem_id, payload, summary, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending') RETURNING id",
            (project_id, user_id, action, entity_type, entity_id,
             subsystem_id, Json(payload), summary),
        )
        change_id = cur.fetchone()[0]
        if action == "create":
            cur.execute(
                "UPDATE pending_changes SET temp_id=%s WHERE id=%s",
                (temp_id_for(change_id, entity_type), change_id),
            )
        conn.commit()
        return change_id


def withdraw_change(change_id: int, user_id: Optional[int] = None) -> bool:
    """Delete one of the current user's own pending suggestions."""
    user_id = user_id if user_id is not None else auth.user_id
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM pending_changes WHERE id=%s AND user_id=%s AND status='pending'",
            (change_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Interface change routing (wiring matrix + interfaces list)
# ---------------------------------------------------------------------------
def _pin_subsystem_id(pin_id) -> Optional[int]:
    """Subsystem that owns a pin — used to scope interface suggestions."""
    if pin_id is None or is_temp_id(pin_id):
        return None
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT m.subsystem_id FROM pins p "
            "JOIN connectors c ON p.connector_id = c.id "
            "JOIN modules m ON c.module_id = m.id "
            "WHERE p.id = %s",
            (pin_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _pin_label_for(pin_id) -> str:
    """Short 'Connector.Pin' label for a pin id (used in summaries)."""
    if pin_id is None:
        return "?"
    if is_temp_id(pin_id):
        return f"pending pin {pin_id}"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT p.name, c.name FROM pins p "
            "JOIN connectors c ON p.connector_id = c.id "
            "WHERE p.id = %s",
            (pin_id,),
        )
        row = cur.fetchone()
    if row:
        return f"{row[1]}.{row[0]}"
    return f"pin#{pin_id}"


def propose_interface_change(
    action: str,
    pin1_id: Optional[int],
    pin2_id: Optional[int],
    color: Optional[str] = None,
    current: Optional[float] = None,
    iface_id: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Route an interface create / update / delete through the approval workflow.

    System admins apply immediately to the DB (same guarded helpers as
    before). Everyone else gets the change recorded as a pending suggestion,
    validated up-front with the same rules so invalid proposals are caught
    right away. Returns (ok, message).
    """
    from database import (
        add_interface_guarded, update_interface_guarded, delete_interface_guarded,
    )

    project_id = get_current_project_id()
    if project_id is None:
        return False, "No project selected."
    user_id = auth.user_id
    if not user_id:
        return False, "You must be logged in."

    if action == "delete":
        if not iface_id:
            return False, "No connection selected."
        if auth.is_system():
            return delete_interface_guarded(user_id, iface_id, project_id)
        sid = _pin_subsystem_id(pin1_id)
        if sid is None:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT pin1_id FROM interfaces WHERE id=%s AND project_id=%s",
                    (iface_id, project_id),
                )
                row = cur.fetchone()
            if row:
                sid = _pin_subsystem_id(row[0])
        suggest_change(
            "interface", "delete", iface_id, sid,
            {"id": iface_id}, f"Delete connection #{iface_id}",
            user_id=user_id,
        )
        return True, "Change submitted for system-admin approval."

    if not pin1_id or not pin2_id or pin1_id == pin2_id:
        return False, "Select two different pins."

    if action == "update":
        if not iface_id:
            return False, "No connection selected."
        # Mirror update_interface_guarded: only re-check the wiring rule when
        # the pin pair actually changes (legacy mismatched pairs must still
        # allow harmless colour/current-only edits).
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT pin1_id, pin2_id FROM interfaces WHERE id=%s AND project_id=%s",
                (iface_id, project_id),
            )
            row = cur.fetchone()
        pair_changed = row is None or not (
            (row[0] == pin1_id and row[1] == pin2_id)
            or (row[0] == pin2_id and row[1] == pin1_id)
        )
        if pair_changed:
            ok, reason = pins_connectable(pin1_id, pin2_id)
            if not ok:
                return False, reason
        if auth.is_system():
            return update_interface_guarded(
                user_id, iface_id, pin1_id, pin2_id, color, project_id, current or 0.0
            )
        suggest_change(
            "interface", "update", iface_id, _pin_subsystem_id(pin1_id),
            {"id": iface_id, "fields": {
                "pin1_id": pin1_id, "pin2_id": pin2_id,
                "color": color, "current": current or 0.0,
            }},
            f"Edit connection {_pin_label_for(pin1_id)} ↔ {_pin_label_for(pin2_id)}",
            user_id=user_id,
        )
        return True, "Change submitted for system-admin approval."

    # create
    ok, reason = pins_connectable(pin1_id, pin2_id)
    if not ok:
        return False, reason
    if auth.is_system():
        return add_interface_guarded(user_id, pin1_id, pin2_id, color, project_id, current or 0.0)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM interfaces WHERE project_id=%s AND "
            "((pin1_id=%s AND pin2_id=%s) OR (pin1_id=%s AND pin2_id=%s))",
            (project_id, pin1_id, pin2_id, pin2_id, pin1_id),
        )
        if cur.fetchone():
            return False, "Interface already exists."
    suggest_change(
        "interface", "create", None, _pin_subsystem_id(pin1_id),
        {"pin1_id": pin1_id, "pin2_id": pin2_id,
         "color": color, "current": current or 0.0},
        f"Add connection {_pin_label_for(pin1_id)} ↔ {_pin_label_for(pin2_id)}",
        user_id=user_id,
    )
    return True, "Change submitted for system-admin approval."


def propose_mode_change(
    action: str,
    mode_name: Optional[str],
    module_ids: Optional[list] = None,
) -> Tuple[bool, str]:
    """
    Route a mode create / save / delete through the approval workflow.

    System admins apply immediately to the DB; everyone else gets the change
    recorded as a pending suggestion. Returns (ok, message).
    """
    from database import create_mode, delete_mode

    project_id = get_current_project_id()
    if project_id is None:
        return False, "No project selected."
    user_id = auth.user_id
    if not user_id:
        return False, "You must be logged in."
    if not mode_name:
        return False, "Mode name is required."

    if action == "delete":
        if auth.is_system():
            ok = delete_mode(mode_name)
            return (ok, "Mode deleted.") if ok else (False, "Failed to delete mode.")
        suggest_change(
            "mode", "delete", None, None,
            {"name": mode_name}, f"Delete mode '{mode_name}'",
            user_id=user_id,
        )
        return True, "Change submitted for system-admin approval."

    # create / save (upsert semantics)
    if not module_ids:
        return False, "Select at least one module."
    if auth.is_system():
        ok = create_mode(mode_name, module_ids)
        return (ok, "Mode saved.") if ok else (False, "Could not save mode.")
    suggest_change(
        "mode", "create", None, None,
        {"name": mode_name, "module_ids": list(module_ids)},
        f"Save mode '{mode_name}' with {len(module_ids)} module(s)",
        user_id=user_id,
    )
    return True, "Change submitted for system-admin approval."


# ---------------------------------------------------------------------------
# Reading the queue
# ---------------------------------------------------------------------------
_SELECT_COLS = (
    "id, project_id, user_id, action, entity_type, entity_id, "
    "subsystem_id, temp_id, payload, summary, status, review_note, "
    "resolved_id, created_at, resolved_at, resolved_by"
)


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "project_id": row[1],
        "user_id": row[2],
        "action": row[3],
        "entity_type": row[4],
        "entity_id": row[5],
        "subsystem_id": row[6],
        "temp_id": row[7],
        "payload": row[8] or {},
        "summary": row[9],
        "status": row[10],
        "review_note": row[11],
        "resolved_id": row[12],
        "created_at": row[13],
        "resolved_at": row[14],
        "resolved_by": row[15],
    }


def list_changes(
    project_id: Optional[int] = None,
    statuses: Optional[List[str]] = None,
    user_id: Optional[int] = None,
    include_username: bool = True,
) -> List[dict]:
    """List pending changes for a project, newest first."""
    project_id = project_id if project_id is not None else get_current_project_id()
    if project_id is None:
        return []
    statuses = list(statuses) if statuses else ["pending"]
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_SELECT_COLS} FROM pending_changes "
            "WHERE project_id=%s AND status = ANY(%s) "
            + ("AND user_id=%s " if user_id is not None else "")
            + "ORDER BY created_at DESC, id DESC",
            (project_id, statuses) + ((user_id,) if user_id is not None else ()),
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]

    if include_username and rows:
        user_ids = sorted({r["user_id"] for r in rows})
        usernames = {}
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username FROM users WHERE id = ANY(%s)", (user_ids,))
            for uid, uname in cur.fetchall():
                usernames[uid] = uname
        for r in rows:
            r["username"] = usernames.get(r["user_id"]) or f"user#{r['user_id']}"
    return rows


def pending_counts(project_id: Optional[int] = None) -> dict:
    """Return {'pending': <all pending>, 'mine': <current user's pending>}."""
    project_id = project_id if project_id is not None else get_current_project_id()
    out = {"pending": 0, "mine": 0}
    if project_id is None:
        return out
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM pending_changes WHERE project_id=%s AND status='pending'",
            (project_id,),
        )
        out["pending"] = cur.fetchone()[0]
        if auth.user_id:
            cur.execute(
                "SELECT COUNT(*) FROM pending_changes WHERE project_id=%s "
                "AND user_id=%s AND status='pending'",
                (project_id, auth.user_id),
            )
            out["mine"] = cur.fetchone()[0]
    return out


# ---------------------------------------------------------------------------
# Approval / rejection
# ---------------------------------------------------------------------------
def _resolve_temp(project_id: int, temp_id: int, _applying: Optional[Set[int]] = None) -> Optional[int]:
    """
    Resolve a temp id to a real id.

    If the creating suggestion is already approved, its resolved_id is used.
    If it is still pending, it is applied first (so a chain of pending
    creates approves in dependency order). Returns None if unresolvable.
    """
    _applying = _applying if _applying is not None else set()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, action, status, resolved_id FROM pending_changes "
            "WHERE project_id=%s AND temp_id=%s",
            (project_id, temp_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    cid, action, status, resolved_id = row
    if status == "approved" and resolved_id is not None:
        return resolved_id
    if status == "pending" and action == "create" and cid not in _applying:
        ok, _ = approve_change(cid, auth.user_id, _applying=_applying)
        if ok:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT resolved_id FROM pending_changes WHERE id=%s", (cid,))
                r = cur.fetchone()
                return r[0] if r else None
    return None


def _resolve_payload_refs(project_id: int, payload: dict, _applying=None) -> Tuple[dict, Optional[str]]:
    """Resolve every temp id inside a payload; returns (payload, error_reason)."""
    out = dict(payload)
    _applying = _applying if _applying is not None else set()
    for key in ("subsystem_id", "module_id", "connector_id", "pin1_id", "pin2_id"):
        if key in out and is_temp_id(out[key]):
            real = _resolve_temp(project_id, out[key], _applying)
            if real is None:
                return out, f"{key} no longer exists"
            out[key] = real
    fields = out.get("fields")
    if isinstance(fields, dict):
        fields = dict(fields)
        for key in ("subsystem_id", "module_id", "connector_id", "pin1_id", "pin2_id"):
            if key in fields and is_temp_id(fields[key]):
                real = _resolve_temp(project_id, fields[key], _applying)
                if real is None:
                    return out, f"{key} no longer exists"
                fields[key] = real
        out["fields"] = fields
    return out, None


def _entity_exists(project_id: int, entity_type: str, entity_id: int) -> bool:
    table = {"module": "modules", "connector": "connectors", "pin": "pins",
             "interface": "Interfaces"}.get(entity_type)
    if not table:
        return True
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT 1 FROM {table} WHERE id=%s AND project_id=%s",
            (entity_id, project_id),
        )
        return cur.fetchone() is not None


def _subsystem_exists(project_id: int, subsystem_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM subsystems WHERE id=%s AND project_id=%s",
            (subsystem_id, project_id),
        )
        return cur.fetchone() is not None


def approve_change(change_id: int, reviewer_id: int, note: str = "",
                   _applying: Optional[Set[int]] = None) -> Tuple[bool, str]:
    """
    Apply a pending change to the DB (system-admin authority).

    Returns (ok, message). On success the suggestion is marked 'approved'
    (creates record resolved_id). If the referenced items were deleted or
    the change now violates the wiring rules it is marked 'stale' with a
    review note and NOT applied.
    """
    _applying = _applying if _applying is not None else set()
    if change_id in _applying:
        return False, "Circular dependency between suggestions."
    _applying = set(_applying)
    _applying.add(change_id)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {_SELECT_COLS} FROM pending_changes WHERE id=%s", (change_id,))
        row = cur.fetchone()
    if not row:
        return False, "Suggestion not found."
    change = _row_to_dict(row)
    if change["status"] != "pending":
        return False, f"Suggestion is already {change['status']}."

    pid, action, etype = change["project_id"], change["action"], change["entity_type"]
    payload, ref_err = _resolve_payload_refs(pid, change["payload"], _applying)
    if ref_err:
        return _mark_resolved(change_id, "stale", reviewer_id, f"Referenced {ref_err}.")

    try:
        if etype == "module":
            ok, msg = _apply_module(pid, action, payload)
        elif etype == "connector":
            ok, msg = _apply_connector(pid, action, payload)
        elif etype == "pin":
            ok, msg = _apply_pin(pid, action, payload)
        elif etype == "interface":
            ok, msg = _apply_interface(pid, action, payload)
        elif etype == "mode":
            ok, msg = _apply_mode(pid, action, payload)
        else:
            ok, msg = False, f"Unknown entity type: {etype}"
    except Exception as e:
        return _mark_resolved(change_id, "stale", reviewer_id, f"Apply failed: {e}")

    if not ok:
        return _mark_resolved(change_id, "stale", reviewer_id, msg)

    resolved_id = msg if (action == "create" and isinstance(msg, int)) else None
    return _mark_resolved(change_id, "approved", reviewer_id, note or None,
                          resolved_id=resolved_id)


def _mark_resolved(change_id, status, reviewer_id, note, resolved_id=None) -> Tuple[bool, str]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE pending_changes SET status=%s, review_note=%s, resolved_id=%s, "
            "resolved_at=NOW(), resolved_by=%s WHERE id=%s",
            (status, note, resolved_id, reviewer_id, change_id),
        )
        conn.commit()
    if status == "approved":
        return True, "Applied to database."
    return False, note or f"Marked {status}."


def reject_change(change_id: int, reviewer_id: int, note: str = "") -> bool:
    """Reject (without applying) a pending change."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE pending_changes SET status='rejected', review_note=%s, "
            "resolved_at=NOW(), resolved_by=%s WHERE id=%s AND status='pending'",
            (note or None, reviewer_id, change_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Per-entity apply logic (approval time)
# ---------------------------------------------------------------------------
def _apply_module(pid, action, payload) -> Tuple[bool, str]:
    from Schematic_View_tab.schematic_scene_model import (
        NOT_SET,
        create_module, update_module as _update_module, delete_module,
    )

    if action == "create":
        if not _subsystem_exists(pid, payload.get("subsystem_id")):
            return False, "The subsystem no longer exists."
        new_id = create_module(
            payload.get("name", "Untitled"),
            x=float(payload.get("x", 40.0)),
            y=float(payload.get("y", 40.0)),
            width=float(payload.get("width", DEFAULT_MODULE_W)),
            height=float(payload.get("height", DEFAULT_MODULE_H)),
            color=payload.get("color"),
            subsystem_id=payload.get("subsystem_id"),
            mass=float(payload.get("mass", 0.0) or 0.0),
            power=float(payload.get("power", 0.0) or 0.0),
            min_temp=payload.get("min_temp"),
            max_temp=payload.get("max_temp"),
        )
        return (True, new_id) if new_id else (False, "Could not create module.")
    if action == "delete":
        eid = payload.get("id")
        if not _entity_exists(pid, "module", eid):
            return False, "The module no longer exists."
        delete_module(eid)
        return True, ""
    # update
    eid = payload.get("id")
    if not _entity_exists(pid, "module", eid):
        return False, "The module no longer exists."
    fields = payload.get("fields") or {}
    _update_module(
        eid,
        name=fields.get("name"),
        mass=fields.get("mass"),
        power=fields.get("power"),
        color=fields.get("color"),
        subsystem_id=fields.get("subsystem_id"),
        min_temp=fields.get("min_temp", NOT_SET),
        max_temp=fields.get("max_temp", NOT_SET),
    )
    pos_updates = {k: fields[k] for k in ("pos_x", "pos_y", "width", "height")
                   if k in fields and fields[k] is not None}
    if pos_updates:
        with get_connection() as conn:
            cur = conn.cursor()
            set_clause = ", ".join(f"{k} = %s" for k in pos_updates)
            cur.execute(
                f"UPDATE modules SET {set_clause} WHERE id=%s AND project_id=%s",
                (*pos_updates.values(), eid, pid),
            )
            conn.commit()
    return True, ""


def _apply_connector(pid, action, payload) -> Tuple[bool, str]:
    from Schematic_View_tab.schematic_scene_model import (
        NOT_SET,
        create_connector, update_connector as _update_connector, delete_connector,
    )

    if action == "create":
        if not _entity_exists(pid, "module", payload.get("module_id")):
            return False, "The parent module no longer exists."
        new_id = create_connector(
            payload.get("module_id"),
            payload.get("name", "Connector"),
            side=payload.get("side", "top"),
            color=payload.get("color"),
            number_of_pins=int(payload.get("number_of_pins", 0) or 0),
        )
        return (True, new_id) if new_id else (False, "Could not create connector.")
    if action == "delete":
        eid = payload.get("id")
        if not _entity_exists(pid, "connector", eid):
            return False, "The connector no longer exists."
        delete_connector(eid)
        return True, ""
    eid = payload.get("id")
    if not _entity_exists(pid, "connector", eid):
        return False, "The connector no longer exists."
    fields = payload.get("fields") or {}
    _update_connector(
        eid,
        name=fields.get("name"),
        color=fields.get("color"),
        side=fields.get("side"),
        number_of_pins=fields.get("number_of_pins"),
        collapsed=fields.get("collapsed", NOT_SET),
    )
    pos_updates = {k: fields[k] for k in ("pos_x", "pos_y")
                   if k in fields and fields[k] is not None}
    if pos_updates:
        with get_connection() as conn:
            cur = conn.cursor()
            set_clause = ", ".join(f"{k} = %s" for k in pos_updates)
            cur.execute(
                f"UPDATE connectors SET {set_clause} WHERE id=%s AND project_id=%s",
                (*pos_updates.values(), eid, pid),
            )
            conn.commit()
    return True, ""


def _apply_pin(pid, action, payload) -> Tuple[bool, str]:
    from Schematic_View_tab.schematic_scene_model import (
        create_pin, update_pin as _update_pin, delete_pin,
    )

    if action == "create":
        if not _entity_exists(pid, "connector", payload.get("connector_id")):
            return False, "The parent connector no longer exists."
        new_id = create_pin(
            payload.get("connector_id"),
            payload.get("name", "Pin"),
            pin_type=payload.get("pin_type"),
            is_ground=bool(payload.get("is_ground", False)),
            value=payload.get("value"),
            description=payload.get("description"),
        )
        return (True, new_id) if new_id else (False, "Could not create pin.")
    if action == "delete":
        eid = payload.get("id")
        if not _entity_exists(pid, "pin", eid):
            return False, "The pin no longer exists."
        delete_pin(eid)
        return True, ""
    eid = payload.get("id")
    if not _entity_exists(pid, "pin", eid):
        return False, "The pin no longer exists."
    fields = payload.get("fields") or {}
    # A type change must not break existing connections — re-check at apply
    # time, since the data may have moved since the suggestion was made.
    if any(k in fields for k in ("pin_type", "is_ground", "value")):
        ok, reason = check_pin_change_interfaces(
            eid,
            fields.get("pin_type"),
            bool(fields.get("is_ground")) if "is_ground" in fields else False,
            fields.get("value") if "value" in fields else None,
        )
        if not ok:
            return False, reason
    _update_pin(
        eid,
        name=fields.get("name"),
        pin_type=fields.get("pin_type"),
        is_ground=fields.get("is_ground"),
        value=fields.get("value"),
        current=fields.get("current"),
        description=fields.get("description"),
    )
    return True, ""


def _apply_interface(pid, action, payload) -> Tuple[bool, str]:
    from Schematic_View_tab.schematic_scene_model import (
        create_interface, delete_interface,
    )

    # Routing-only updates (points/lock flags) involve no pins. When the
    # suggestion was merged with field edits (color/current/pins), apply
    # those too instead of returning early.
    if action == "update" and payload.get("routing") is not None:
        eid = payload.get("id")
        if not _entity_exists(pid, "interface", eid):
            return False, "The connection no longer exists."
        from Schematic_View_tab.routing_persistence import save_enhanced_interface_data
        save_enhanced_interface_data({eid: payload["routing"]})
        if not payload.get("fields"):
            return True, ""

    if action == "create":
        p1, p2 = payload.get("pin1_id"), payload.get("pin2_id")
        if not (_entity_exists(pid, "pin", p1) and _entity_exists(pid, "pin", p2)):
            return False, "One of the pins no longer exists."
        ok, reason = pins_connectable(p1, p2)
        if not ok:
            return False, reason
        new_id = create_interface(p1, p2, color=payload.get("color"),
                                   current=payload.get("current"))
        return (True, new_id) if new_id else (False, "Could not create connection.")
    if action == "delete":
        eid = payload.get("id")
        if not _entity_exists(pid, "interface", eid):
            return False, "The connection no longer exists."
        delete_interface(eid)
        return True, ""
    # update (pins / color / current) — partial fields
    eid = payload.get("id")
    if not _entity_exists(pid, "interface", eid):
        return False, "The connection no longer exists."
    fields = payload.get("fields") or {}
    if ("pin1_id" in fields and "pin2_id" in fields):
        p1, p2 = fields["pin1_id"], fields["pin2_id"]
        if not (_entity_exists(pid, "pin", p1) and _entity_exists(pid, "pin", p2)):
            return False, "One of the pins no longer exists."
        ok, reason = pins_connectable(p1, p2)
        if not ok:
            return False, reason
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT pin1_id, pin2_id, color, current FROM Interfaces "
            "WHERE id=%s AND project_id=%s",
            (eid, pid),
        )
        cur_p1, cur_p2, cur_color, cur_current = cur.fetchone()
        cur.execute(
            "UPDATE Interfaces SET pin1_id=%s, pin2_id=%s, color=%s, current=%s "
            "WHERE id=%s AND project_id=%s",
            (
                fields.get("pin1_id", cur_p1),
                fields.get("pin2_id", cur_p2),
                fields.get("color", cur_color),
                fields.get("current", cur_current),
                eid, pid,
            ),
        )
        conn.commit()
    return True, ""


def _apply_mode(pid, action, payload) -> Tuple[bool, str]:
    from database import create_mode, delete_mode

    if action in ("create", "update"):
        name = payload.get("name")
        # Resolve pending-module temp ids to their real ids (creating the
        # referenced module suggestions first if needed), dropping any that
        # can no longer be resolved.
        module_ids = []
        for m in (payload.get("module_ids") or []):
            real = m if not is_temp_id(m) else _resolve_temp(pid, m)
            if real is not None:
                module_ids.append(real)
        if not create_mode(name, module_ids):
            return False, "Could not save mode (check that its modules exist)."
        return True, ""
    if action == "delete":
        name = payload.get("name")
        if not name or not delete_mode(name):
            return False, "The mode no longer exists."
        return True, ""
    return False, "Unsupported mode action."


# ---------------------------------------------------------------------------
# Optimistic preview: merge the current user's pending changes into the scene
# ---------------------------------------------------------------------------
def _load_my_pending(project_id: int) -> List[dict]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_SELECT_COLS} FROM pending_changes "
            "WHERE project_id=%s AND user_id=%s AND status='pending' ORDER BY id",
            (project_id, auth.user_id),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def _apply_fields(obj: dict, fields: dict):
    for k, v in (fields or {}).items():
        if v is None:
            continue
        if k == "pos_x":
            obj["x"] = v
        elif k == "pos_y":
            obj["y"] = v
        elif k == "width":
            obj["width"] = v
        elif k == "height":
            obj["height"] = v
        else:
            obj[k] = v


def pending_preview_scene(scene: dict, user_id: Optional[int] = None) -> dict:
    """
    Merge the current user's pending suggestions into a loaded scene dict so
    the schematic shows them optimistically, marked as pending.

    Purely in-memory — nothing touches the DB. The JS renderer styles
    `pending` / `pending-delete` entries distinctly.
    """
    project_id = get_current_project_id()
    if project_id is None or not auth.user_id:
        return scene
    if user_id is None:
        user_id = auth.user_id
    changes = _load_my_pending(project_id)
    if not changes:
        return scene

    scene = dict(scene)
    scene["modules"] = list(scene.get("modules", []))
    scene["connectors"] = list(scene.get("connectors", []))
    scene["interfaces"] = list(scene.get("interfaces", []))
    scene["pending_count"] = len(changes)

    mod_by_id = {m["id"]: m for m in scene["modules"]}
    conn_by_id = {c["id"]: c for c in scene["connectors"]}
    iface_by_id = {i["id"]: i for i in scene["interfaces"]}

    temp_mods, temp_conns, temp_pins = {}, {}, {}

    def _find_module(ref):
        return temp_mods.get(ref) or mod_by_id.get(ref)

    def _find_connector(ref):
        return temp_conns.get(ref) or conn_by_id.get(ref)

    def _mark_interface_deleted(pin_ids):
        for iface in scene["interfaces"]:
            if iface.get("pending"):
                continue
            if iface["from_pin"] in pin_ids or iface["to_pin"] in pin_ids:
                iface["pending"] = "delete"
                iface["pending_delete"] = True

    for ch in changes:
        etype, act, payload = ch["entity_type"], ch["action"], ch["payload"] or {}

        if act == "delete":
            if etype == "module":
                m = mod_by_id.get(ch["entity_id"])
                if m:
                    m["pending"] = "delete"
                    m["pending_delete"] = True
                    pin_ids = set()
                    for c in scene["connectors"]:
                        if c["module_id"] == ch["entity_id"] and not c.get("pending"):
                            c["pending"] = "delete"
                            c["pending_delete"] = True
                            for p in c.get("pins", []):
                                pin_ids.add(p["id"])
                    _mark_interface_deleted(pin_ids)
            elif etype == "connector":
                c = conn_by_id.get(ch["entity_id"])
                if c:
                    c["pending"] = "delete"
                    c["pending_delete"] = True
                    _mark_interface_deleted({p["id"] for p in c.get("pins", [])})
            elif etype == "pin":
                for c in scene["connectors"]:
                    for p in c.get("pins", []):
                        if p["id"] == ch["entity_id"]:
                            p["pending"] = "delete"
                            p["pending_delete"] = True
                            _mark_interface_deleted({p["id"]})
            elif etype == "interface":
                iface = iface_by_id.get(ch["entity_id"])
                if iface:
                    iface["pending"] = "delete"
                    iface["pending_delete"] = True
            continue

        if act == "update":
            if etype == "module":
                m = mod_by_id.get(ch["entity_id"])
                if m:
                    _apply_fields(m, payload.get("fields"))
                    m["pending"] = "update"
            elif etype == "connector":
                c = conn_by_id.get(ch["entity_id"])
                if c:
                    _apply_fields(c, payload.get("fields"))
                    c["pending"] = "update"
            elif etype == "pin":
                for c in scene["connectors"]:
                    for p in c.get("pins", []):
                        if p["id"] == ch["entity_id"]:
                            _apply_fields(p, payload.get("fields"))
                            p["pending"] = "update"
            elif etype == "interface":
                iface = iface_by_id.get(ch["entity_id"])
                if iface:
                    if payload.get("routing") is not None:
                        routing = payload["routing"]
                        if isinstance(routing, dict):
                            pts = routing.get("points")
                            if pts is not None:
                                iface["points"] = [[x, y] for x, y in pts]
                            for k, v in routing.items():
                                if k != "points":
                                    iface[k] = v
                        else:
                            iface["points"] = [[x, y] for x, y in routing]
                    _apply_fields(iface, payload.get("fields"))
                    iface["pending"] = "update"
            continue

        # ---- creates ----
        if etype == "module":
            tid = ch["temp_id"]
            m = {
                "id": tid,
                "name": payload.get("name", "Untitled"),
                "x": float(payload.get("x", 40.0)),
                "y": float(payload.get("y", 40.0)),
                "width": float(payload.get("width", DEFAULT_MODULE_W)),
                "height": float(payload.get("height", DEFAULT_MODULE_H)),
                "color": payload.get("color") or "#9b59b6",
                "subsystem_id": payload.get("subsystem_id"),
                "mass": payload.get("mass", 0.0),
                "power": payload.get("power", 0.0),
                "min_temp": payload.get("min_temp"),
                "max_temp": payload.get("max_temp"),
                "editable": False,
                "pending": "create",
                "pending_suggestion_id": ch["id"],
            }
            scene["modules"].append(m)
            temp_mods[tid] = m
        elif etype == "connector":
            tid = ch["temp_id"]
            mid = payload.get("module_id")
            if _find_module(mid) is None:
                continue
            c = {
                "id": tid,
                "module_id": mid,
                "name": payload.get("name", "Connector"),
                "color": payload.get("color"),
                "side": payload.get("side", "top"),
                "collapsed": bool(payload.get("collapsed", False)),
                "x": None,
                "y": None,
                "pins": [],
                "editable": False,
                "pending": "create",
                "pending_suggestion_id": ch["id"],
            }
            scene["connectors"].append(c)
            temp_conns[tid] = c
        elif etype == "pin":
            tid = ch["temp_id"]
            cid = payload.get("connector_id")
            parent = _find_connector(cid)
            if parent is None:
                continue
            pin = {
                "id": tid,
                "name": payload.get("name", "Pin"),
                "voltage": payload.get("value"),
                "pin_type": payload.get("pin_type"),
                "is_ground": bool(payload.get("is_ground", False)),
                "pending": "create",
                "pending_suggestion_id": ch["id"],
            }
            parent.setdefault("pins", []).append(pin)
            temp_pins[tid] = pin
        elif etype == "interface":
            tid = ch["temp_id"]
            p1, p2 = payload.get("pin1_id"), payload.get("pin2_id")
            if is_temp_id(p1) and p1 not in temp_pins:
                continue
            if is_temp_id(p2) and p2 not in temp_pins:
                continue
            iface = {
                "id": tid,
                "from_pin": p1,
                "to_pin": p2,
                "color": payload.get("color"),
                "current": float(payload.get("current") or 0.0),
                "points": [],
                "editable": False,
                "pending": "create",
                "pending_suggestion_id": ch["id"],
            }
            scene["interfaces"].append(iface)

    return scene


# ---------------------------------------------------------------------------
# Optimistic preview helpers for the Architecture tree
# ---------------------------------------------------------------------------
def pending_tree_rows(project_id: int) -> dict:
    """
    Summarise the current user's pending changes for the Architecture tree.

    Returns:
        {
          "module":  {"adds": [...], "updates": {id: fields}, "deletes": set(ids)},
          "connector": {...},
          "pin": {...},
        }
    """
    out = {et: {"adds": [], "updates": {}, "deletes": set()}
           for et in ("module", "connector", "pin", "interface")}
    project_id = project_id or get_current_project_id()
    if project_id is None or not auth.user_id:
        return out
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_SELECT_COLS} FROM pending_changes "
            "WHERE project_id=%s AND user_id=%s AND status='pending' "
            "AND entity_type IN ('module','connector','pin') ORDER BY id",
            (project_id, auth.user_id),
        )
        for row in cur.fetchall():
            ch = _row_to_dict(row)
            bucket = out.get(ch["entity_type"])
            if not bucket:
                continue
            payload = ch["payload"] or {}
            if ch["action"] == "delete":
                if ch["entity_id"] is not None:
                    bucket["deletes"].add(ch["entity_id"])
            elif ch["action"] == "update":
                if ch["entity_id"] is not None:
                    bucket["updates"][ch["entity_id"]] = payload.get("fields") or {}
            elif ch["action"] == "create":
                entry = dict(payload)
                entry["temp_id"] = ch["temp_id"]
                entry["pending_suggestion_id"] = ch["id"]
                bucket["adds"].append(entry)
    return out
