# access_control.py
from PyQt5.QtWidgets import QMessageBox
from auth_manager import auth
from database import guard_or_raise, UnauthorizedError

def guard_write(perm_code: str, subsystem_id: int | None, parent=None) -> bool:
    """
    UI-friendly guard. Shows a polite message box on denial.
    """
    if not auth.is_logged_in():
        QMessageBox.warning(parent, "Access denied", "You must sign in first.")
        return False
    try:
        guard_or_raise(auth.user["id"], perm_code, subsystem_id, action_label="ui")
        return True
    except UnauthorizedError:
        QMessageBox.warning(parent, "Access denied",
                            "You do not have permission to modify this subsystem.")
        return False

def can_edit_subsystem(subsystem_id: int | None) -> bool:
    """
    Check whether current user can edit the given subsystem in the *current project*.
    """
    if not auth.is_logged_in():
        return False
    if auth.is_system():
        return True
    if subsystem_id is None:
        return False

    # prefer project-scoped set if present
    cur = getattr(auth, "allowed_subsystems_current", set()) or set()
    if cur:
        return subsystem_id in cur

    # fallback: legacy raw IDs (if any)
    raw = getattr(auth, "allowed_subsystems", set()) or set()
    return subsystem_id in raw

def guard_export(parent=None) -> bool:
    if not auth.is_logged_in():
        QMessageBox.warning(parent, "Access denied", "You must sign in first.")
        return False
    # by policy, all roles can export; if you later restrict, check a perm here.
    return True
