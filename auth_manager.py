# auth_manager.py
from PyQt5.QtCore import QObject, pyqtSignal
from database import (
    verify_credentials, record_login, get_user_permissions, get_user_subsystems,
    record_audit, start_user_session, touch_user_session, end_user_session,
    get_user_roles, get_user_subsystem_names, get_subsystem_ids_by_names_in_project, get_current_project_id
)

import socket


class AuthManager(QObject):
    auth_changed = pyqtSignal()
    def __init__(self):
        super().__init__()
        self.user = None
        self.permissions = set()
        self.allowed_subsystems = set()
        self.roles = set()  # <-- ADD
        self.allowed_subsystem_names = set()      # e.g., {"obc", "adcs"}
        self.allowed_subsystems_current = set() 
        self.session_id = None

    @property
    def user_id(self) -> int:
        return self.user["id"] if self.user else 0

    def login(self, username: str, password: str) -> bool:
        u = verify_credentials(username, password)
        if not u:
            return False
        self.user = u
        self.permissions = get_user_permissions(u["id"]) or set()
        self.allowed_subsystems = get_user_subsystems(u["id"]) or set()
        self.allowed_subsystem_names = get_user_subsystem_names(u["id"]) or set()
        try:
            pid = get_current_project_id()
        except Exception:
            pid = None
        if pid:
            self.refresh_scope_for_project(pid)
        else:
            self.allowed_subsystems_current = set()
        self.roles = set(get_user_roles(u["id"]) or [])
        record_login(u["id"])
        record_audit(u["id"], "login", {"username": username})
        # start session
        try:
            self.session_id = start_user_session(u["id"], socket.gethostname())
        except Exception:
            self.session_id = None
        self.auth_changed.emit()
        return True

    def heartbeat(self):
        if self.session_id:
            try:
                touch_user_session(self.session_id)
            except Exception:
                pass

    def logout(self):
        if self.user:
            try:
                record_audit(self.user["id"], "logout", {"username": self.user["username"]})
            except Exception:
                pass
        if self.session_id:
            try:
                end_user_session(self.session_id)
            except Exception:
                pass
        self.session_id = None
        self.user = None
        self.permissions = set()
        self.allowed_subsystems = set()
        self.allowed_subsystems_current = set()      # <-- ADD
        self.allowed_subsystem_names = set()
        self.roles = set()
        self.auth_changed.emit()

    def has_perm(self, code: str) -> bool:
        return code in self.permissions or ("*" in self.permissions)
    
    def is_logged_in(self) -> bool:
        return self.user is not None
    
    def is_logged_in(self) -> bool:
        return self.user is not None

    def is_system(self) -> bool:
        """
        True for full system admin.
        """
        # role-based check
        if "system_admin" in self.roles:
            return True
        # wildcard permission (if you ever use it)
        if "*" in self.permissions:
            return True
        # username fallback (optional)
        if self.user and self.user.get("username", "").lower() == "system":
            return True
        return False

    def is_subsystem_admin(self) -> bool:
        return "subsystem_admin" in self.roles
    
    def refresh_scope_for_project(self, project_id: int):
        """
        Recompute allowed_subsystems_current for the given project
        based on allowed_subsystem_names (case-insensitive).
        """
        if self.is_system():
            # full access, but keep set empty to mean "no restriction" if you prefer
            self.allowed_subsystems_current = set()
            return
        names = list(self.allowed_subsystem_names or [])
        self.allowed_subsystems_current = get_subsystem_ids_by_names_in_project(names, project_id)
        self.auth_changed.emit()  # notify UI to re-apply policies



# singleton
auth = AuthManager()


