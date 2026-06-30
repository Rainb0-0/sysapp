# project_dialogs.py
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget,
    QMessageBox, QFrame, QTableWidget,
    QTableWidgetItem, QMessageBox, QGroupBox,
      QListWidgetItem, QTabWidget, QWidget, QInputDialog
)
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt, QTimer

from database import (get_all_projects, create_new_project_guarded,
                      open_existing_project, get_active_users,
                      verify_credentials, set_user_password, set_user_full_name,
                      get_login_audit, get_all_users_simple,
                      get_all_projects, open_existing_project, create_new_project_guarded,
                     get_project_id_by_name, delete_project_guarded)  # added

from project_config import project_config
from auth_manager import auth

try:
    from styles.theme_manager import get_button_style, get_main_window_style
except ImportError:
    from styles.theme_manager import get_button_style, get_main_window_style
try:
    from styles.style_manager import style_manager
except ImportError:
    from styles.style_manager import style_manager


class ProjectSelectionDialog(QDialog):
    """
    DB-backed project selection dialog.
    - Lists projects from PostgreSQL (projects table)
    - Allows creating a new project
    - Integrates with project_config to show/manage recent project names
    Emits no custom signals; use exec_() and .selected_project after accept().
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select or Create Project")
        self.setModal(True)
        self.setFixedSize(560, 520)
        self.selected_project = None  # str
        self.action_type = None
        self._build_ui()
        self._load_projects()
        self._load_recent()

    # ---------------------------
    # UI
    # ---------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Header
        header = QLabel("System Architecture Projects (PostgreSQL)")
        hf = QFont(); hf.setPointSize(14); hf.setBold(True)
        header.setFont(hf)
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        sub = QLabel("Choose an existing project or create a new one")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color:#666;")
        layout.addWidget(sub)

        # Separator
        sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        # Recent projects group
        recent_group = QGroupBox("Recent Projects")
        recent_layout = QVBoxLayout()
        self.recent_list = QListWidget()
        self.recent_list.setMinimumHeight(120)
        recent_layout.addWidget(self.recent_list)

        btn_row_recent = QHBoxLayout()
        self.btn_recent_refresh = QPushButton("Refresh")
        self.btn_recent_clear = QPushButton("Clear All")
        btn_row_recent.addWidget(self.btn_recent_refresh)
        btn_row_recent.addWidget(self.btn_recent_clear)
        btn_row_recent.addStretch(1)
        recent_layout.addLayout(btn_row_recent)

        recent_group.setLayout(recent_layout)
        layout.addWidget(recent_group)

        # All projects group
        all_group = QGroupBox("All Projects (from database)")
        all_layout = QVBoxLayout()
        self.all_list = QListWidget()
        self.all_list.setMinimumHeight(200)
        all_layout.addWidget(self.all_list)

        # New project row
        new_row = QHBoxLayout()
        self.new_name_edit = QLineEdit()
        self.new_name_edit.setPlaceholderText("New project name...")
        self.btn_new_create = QPushButton("Create")
        new_row.addWidget(self.new_name_edit)
        new_row.addWidget(self.btn_new_create)
        all_layout.addLayout(new_row)

        # Buttons row
        btn_row = QHBoxLayout()
        self.btn_refresh = QPushButton("Reload")
        self.btn_open = QPushButton("Open")
        self.btn_open.setDefault(True); self.btn_open.setEnabled(False)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setToolTip("Delete selected project (system only)")
        btn_row.addWidget(self.btn_refresh)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_open)
        btn_row.addWidget(self.btn_delete)
        all_layout.addLayout(btn_row)

        all_group.setLayout(all_layout)
        layout.addWidget(all_group)

        # Connections
        self.recent_list.itemDoubleClicked.connect(self._open_recent)
        self.all_list.itemDoubleClicked.connect(self._open_selected)
        self.recent_list.itemSelectionChanged.connect(self._on_recent_sel)
        self.recent_list.itemSelectionChanged.connect(self.apply_access_policy)
        self.all_list.itemSelectionChanged.connect(self._on_all_sel)
        self.all_list.itemSelectionChanged.connect(self.apply_access_policy)

        self.btn_recent_refresh.clicked.connect(self._load_recent)
        self.btn_recent_clear.clicked.connect(self._clear_recent)

        self.btn_refresh.clicked.connect(self._load_projects)
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_new_create.clicked.connect(self._create_new)
        self.btn_delete.clicked.connect(self._delete_selected_project)


        self.apply_access_policy()
        auth.auth_changed.connect(self.apply_access_policy)

    def apply_access_policy(self):
        can_create = auth.has_perm("project.create")
        can_delete = auth.has_perm("project.delete")
        if hasattr(self, "btn_new_create"):
            self.btn_new_create.setEnabled(can_create)
        if hasattr(self, "new_name_edit"):
            self.new_name_edit.setEnabled(can_create)
        if hasattr(self, "btn_delete"):
            self.btn_delete.setEnabled(can_delete and self._selected_project_name() is not None)

    # ---------------------------
    # Data loading
    # ---------------------------
    def _load_projects(self):
        self.all_list.clear()
        try:
            names = get_all_projects() or []
            for n in names:
                self.all_list.addItem(n)
            last = project_config.get_last_project_name()
            if last:
                matches = self.all_list.findItems(last, Qt.MatchExactly)
                if matches:
                    self.all_list.setCurrentItem(matches[0])
                    self.btn_open.setEnabled(True)
            self.apply_access_policy()   # <— add
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load projects:\n{e}")

    def _load_recent(self):
        self.recent_list.clear()
        try:
            names = project_config.get_recent_projects()
            if not names:
                it = QListWidgetItem("(no recent projects)")
                it.setFlags(Qt.NoItemFlags)
                self.recent_list.addItem(it)
                self.apply_access_policy()   # <— add
                return
            for n in names:
                self.recent_list.addItem(n)
            self.apply_access_policy()       # <— add
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load recent list:\n{e}")

    # ---------------------------
    # Helpers
    # ---------------------------
    def _on_recent_sel(self):
        # no open button here; double-click to open
        self.apply_access_policy() 

    def _on_all_sel(self):
        self.btn_open.setEnabled(self.all_list.currentItem() is not None)
        self.apply_access_policy() 

    def _clear_recent(self):
        if QMessageBox.question(self, "Clear recent", "Clear all recent projects?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            project_config.clear_recent_projects()
            self._load_recent()

    def _create_new(self):
        # HARD GUARD
        if not auth.has_perm("project.create"):
            QMessageBox.warning(self, "Access denied", "Only system admin can create new project.")
            return

        name = self.new_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Invalid name", "Please enter a project name.")
            return

        ok, msg = create_new_project_guarded(auth.user_id, name)
        if not ok:
            QMessageBox.critical(self, "Create failed", msg)
            return

        self.action_type = 'new'
        project_config.add_recent_project(name)
        self._load_projects()
        self._load_recent()
        matches = self.all_list.findItems(name, Qt.MatchExactly)
        if matches:
            self.all_list.setCurrentItem(matches[0])
            self.btn_open.setEnabled(True)
        self.new_name_edit.clear()

    def _open_recent(self, item):
        if not item or not item.text().strip():
            return
        name = item.text().strip()
        self._open_project_by_name(name)

    def _open_selected(self):
        item = self.all_list.currentItem()
        if not item:
            return
        name = item.text().strip()
        self._open_project_by_name(name)

    def _open_project_by_name(self, name: str):
        ok, msg = open_existing_project(name)
        if not ok:
            QMessageBox.critical(self, "Open failed", msg)
            return
        self.action_type = 'open'
        # success: save recent + last, and close
        project_config.add_recent_project(name)
        self.selected_project = name
        self.accept()

    def _delete_selected_project(self):
        if not auth.has_perm("project.delete"):
            QMessageBox.warning(self, "Access denied", "Only 'system' can delete projects.")
            return

        name = self._selected_project_name()
        if not name:
            QMessageBox.information(self, "Delete Project", "Select a project first.")
            return

        # step 1: confirm
        r = QMessageBox.question(
            self, "Confirm delete", f"Delete project '{name}'?\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if r != QMessageBox.Yes:
            return

        # step 2: typing confirmation
        text, ok = QInputDialog.getText(self, "Type to confirm",
                                        f"Type the project name exactly to confirm:\n{name}")
        if not ok or text.strip() != name:
            QMessageBox.information(self, "Cancelled", "Project deletion cancelled.")
            return

        pid = get_project_id_by_name(name)
        if not pid:
            QMessageBox.critical(self, "Error", "Project not found.")
            return

        ok, msg = delete_project_guarded(auth.user_id, pid)
        if not ok:
            QMessageBox.critical(self, "Delete failed", msg)
            return

        QMessageBox.information(self, "Deleted", msg)
        # refresh lists
        self._load_projects()
        self._load_recent()
        self.apply_access_policy()

    def _selected_project_name(self) -> str | None:
        w = None
        if self.all_list.hasFocus():
            w = self.all_list.currentItem()
        elif self.recent_list.hasFocus():
            w = self.recent_list.currentItem()
        else:
            # fallback
            w = self.all_list.currentItem() or self.recent_list.currentItem()
        return w.text() if w else None



class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in")
        self.setMinimumWidth(360)

        self.user_edit = QLineEdit()
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)

        form = QVBoxLayout()
        row1 = QHBoxLayout(); row1.addWidget(QLabel("Username")); row1.addWidget(self.user_edit)
        row2 = QHBoxLayout(); row2.addWidget(QLabel("Password")); row2.addWidget(self.pass_edit)

        btn_login = QPushButton("Login")
        btn_cancel = QPushButton("Cancel")
        btn_login.clicked.connect(self.handle_login)
        btn_cancel.clicked.connect(self.reject)

        btns = QHBoxLayout(); btns.addStretch(); btns.addWidget(btn_login); btns.addWidget(btn_cancel)

        form.addLayout(row1); form.addLayout(row2); form.addSpacing(8); form.addLayout(btns)
        self.setLayout(form)
        btn_login.setDefault(True)
        self.user_edit.setFocus()

        try:
            style_manager.apply_style_to_widget(self, "main_window")
            style_manager.apply_style_to_widget(self.user_edit, "line_edit")
            style_manager.apply_style_to_widget(self.pass_edit, "line_edit")
            style_manager.apply_style_to_widget(btn_login, "button_large")
            style_manager.apply_style_to_widget(btn_cancel, "button")
        except Exception:
            pass

        # Optional: prefill for first run (system/system)
        self.user_edit.setPlaceholderText("Username")
        self.pass_edit.setPlaceholderText("Password")

    def handle_login(self):
        u = self.user_edit.text().strip()
        p = self.pass_edit.text().strip()
        if not u or not p:
            QMessageBox.warning(self, "Warning", "Please enter username and password.")
            return
        ok = auth.login(u, p)
        if ok:
            self.accept()
        else:
            QMessageBox.warning(self, "Access denied", "Invalid credentials or inactive user.")



class UserProfileDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("User Profile")
        self.setMinimumWidth(380)

        self.ed_user = QLineEdit(); self.ed_user.setReadOnly(True)
        self.ed_name = QLineEdit()
        self.ed_old = QLineEdit(); self.ed_old.setEchoMode(QLineEdit.Password)
        self.ed_new = QLineEdit(); self.ed_new.setEchoMode(QLineEdit.Password)
        self.ed_new2 = QLineEdit(); self.ed_new2.setEchoMode(QLineEdit.Password)

        if auth.is_logged_in():
            self.ed_user.setText(auth.user.get("username",""))
            self.ed_name.setText(auth.user.get("full_name") or "")

        form = QVBoxLayout()
        def row(lbl, w): 
            h=QHBoxLayout(); h.addWidget(QLabel(lbl)); h.addWidget(w); return h
        form.addLayout(row("Username", self.ed_user))
        form.addLayout(row("Full name", self.ed_name))
        form.addSpacing(8)
        form.addLayout(row("Current password", self.ed_old))
        form.addLayout(row("New password", self.ed_new))
        form.addLayout(row("Confirm new password", self.ed_new2))

        bsave = QPushButton("Save"); bcancel = QPushButton("Cancel")
        b = QHBoxLayout(); b.addStretch(); b.addWidget(bsave); b.addWidget(bcancel)
        form.addSpacing(10); form.addLayout(b)
        self.setLayout(form)
        bsave.clicked.connect(self.on_save); bcancel.clicked.connect(self.reject)

    def on_save(self):
        if not auth.is_logged_in():
            QMessageBox.warning(self, "Warning", "You must sign in first.")
            return
        full_name = self.ed_name.text().strip()
        oldp = self.ed_old.text().strip()
        newp = self.ed_new.text().strip()
        newp2 = self.ed_new2.text().strip()

        # update full name (optional)
        try:
            set_user_full_name(auth.user_id, full_name)
            auth.user["full_name"] = full_name
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to update name: {e}")
            return

        # password change if provided
        if newp or newp2 or oldp:
            if not oldp:
                QMessageBox.warning(self, "Error", "Enter current password.")
                return
            if newp != newp2:
                QMessageBox.warning(self, "Error", "New passwords do not match.")
                return
            # verify old
            u = verify_credentials(auth.user["username"], oldp)
            if not u:
                QMessageBox.warning(self, "Error", "Current password is incorrect.")
                return
            try:
                set_user_password(auth.user_id, newp)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to change password: {e}")
                return

        QMessageBox.information(self, "Done", "Profile updated.")
        self.accept()


class ActiveUsersDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Active Users & Audit")
        self.setMinimumWidth(620)

        self.tabs = QTabWidget(self)

        # --- Tab 1: Presence (who is online) ---
        self.pres_widget = QWidget(self)
        v1 = QVBoxLayout(self.pres_widget)

        # summary row
        self.lbl_summary = QLabel("")
        v1.addWidget(self.lbl_summary)

        # presence table
        self.tbl_presence = QTableWidget(0, 4, self)
        self.tbl_presence.setHorizontalHeaderLabels(["", "Username", "Full name", "Last seen"])
        v1.addWidget(self.tbl_presence)

        self.tabs.addTab(self.pres_widget, "Presence")

        # --- Tab 2: Audit (login/logout) ---
        self.audit_widget = QWidget(self)
        v2 = QVBoxLayout(self.audit_widget)

        self.tbl_audit = QTableWidget(0, 4, self)
        self.tbl_audit.setHorizontalHeaderLabels(["Time", "Username", "Full name", "Action"])
        v2.addWidget(self.tbl_audit)

        self.tabs.addTab(self.audit_widget, "Audit")

        # layout
        root = QVBoxLayout(self)
        root.addWidget(self.tabs)
        self.setLayout(root)

        # auto-refresh timers
        self.t = QTimer(self); self.t.setInterval(10000)  # 10s
        self.t.timeout.connect(self.refresh_all)
        self.refresh_all()
        self.t.start()

    def _dot_item(self, online: bool) -> QTableWidgetItem:
        ch = "●"
        it = QTableWidgetItem(ch)
        it.setTextAlignment(Qt.AlignCenter)
        if online:
            it.setForeground(self.palette().brightText())
        else:
            it.setForeground(self.palette().mid())
        return it

    def refresh_all(self):
        self._refresh_presence()
        self._refresh_audit()

    def _refresh_presence(self):
        # active sessions (last_seen within 60s)
        try:
            active_rows = get_active_users(60)
        except Exception:
            active_rows = []

        # all users for totals
        try:
            all_users = get_all_users_simple()
        except Exception:
            all_users = []

        # Build a quick lookup for last_seen/online
        last_by_user = {r["username"]: (r["last_seen"], r["online"]) for r in active_rows}

        # Fill table with ALL users (to show grey for offline)
        self.tbl_presence.setRowCount(len(all_users))
        online_count = 0
        for i, u in enumerate(all_users):
            last_seen, online = last_by_user.get(u["username"], (None, False))
            if online:
                online_count += 1
            self.tbl_presence.setItem(i, 0, self._dot_item(online))
            self.tbl_presence.setItem(i, 1, QTableWidgetItem(u["username"]))
            self.tbl_presence.setItem(i, 2, QTableWidgetItem(u.get("full_name","")))
            self.tbl_presence.setItem(i, 3, QTableWidgetItem("" if last_seen is None else str(last_seen)))
        self.tbl_presence.resizeColumnsToContents()

        total_users = len(all_users)
        self.lbl_summary.setText(f"Total users: {total_users}   •   Online: {online_count}   •   Offline: {total_users - online_count}")

    def _refresh_audit(self):
        try:
            rows = get_login_audit(200)
        except Exception:
            rows = []
        self.tbl_audit.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.tbl_audit.setItem(i, 0, QTableWidgetItem(str(r["time"])))
            self.tbl_audit.setItem(i, 1, QTableWidgetItem(r["username"]))
            self.tbl_audit.setItem(i, 2, QTableWidgetItem(r.get("full_name","")))
            self.tbl_audit.setItem(i, 3, QTableWidgetItem(r["action"]))
        self.tbl_audit.resizeColumnsToContents()
    
