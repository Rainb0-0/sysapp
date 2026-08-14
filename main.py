# main.py - PostgreSQL Version with Centralized Project Management - Enhanced UI
from util import profile
import sys
import os
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QMenuBar,
    QMenu,
    QAction,
    QActionGroup,
    QMessageBox,
    QStatusBar,
    QLabel,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QAction,
    QLabel,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QInputDialog,
    QFileDialog,
)
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtCore import Qt, pyqtSignal, QTimer

import json
import database
from database import (
    apply_auth_migration,
    seed_auth_basics,
    seed_subsystem_admins_from_db,
    export_project_data,
    import_project_data,
    get_current_project_id,
    ensure_database_initialized,
)

from project_dialogs import ProjectSelectionDialog, LoginDialog
from project_dialogs import UserProfileDialog, ActiveUsersDialog
from project_dialogs import SubsystemManagementDialog
from auth_manager import auth

from styles.style_manager import style_manager, register_widget
from styles.theme_manager import ThemeType
from styles.config_manager import config_manager

from Architecture_View_tab.Architecture_View_Window import ArchitectureViewTab
from Interface_Connectivity_tab.enhanced_wiring_matrix_tab import (
    EnhancedWiringMatrixTab,
)
from Component_Tree_tab.Component_Tree_Window import ComponentTreeTab
from Schematic_View_tab.schematic_view_tab import SchematicViewTab

os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"


class DatabaseConfigDialog(QDialog):
    """Dialog for database connection configuration"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🛠️ Database Configuration")
        self.setModal(True)
        self.resize(400, 300)

        self._testing = False  # guard against re-entrant accept
        self.setup_ui()
        self.load_current_config()

    def setup_ui(self):
        layout = QVBoxLayout()

        # Connection settings group
        conn_group = QGroupBox("🔗 Database Connection")
        conn_layout = QFormLayout()

        self.host_edit = QLineEdit()
        self.port_edit = QLineEdit()
        self.database_edit = QLineEdit()
        self.user_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)

        conn_layout.addRow("🏠 Host:", self.host_edit)
        conn_layout.addRow("🚪 Port:", self.port_edit)
        conn_layout.addRow("🗄️ Database:", self.database_edit)
        conn_layout.addRow("👤 Username:", self.user_edit)
        conn_layout.addRow("🔐 Password:", self.password_edit)

        conn_group.setLayout(conn_layout)
        layout.addWidget(conn_group)

        # Buttons
        button_layout = QHBoxLayout()
        self.test_button = QPushButton("🧪 Test Connection")
        self.test_button.clicked.connect(self.test_connection)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        button_layout.addWidget(self.test_button)
        button_layout.addWidget(buttons)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def load_current_config(self):
        """Load config from saved settings (app_config.json) or DB_CONFIG defaults."""
        # Try saved config first
        saved = config_manager.config_data.get("database_connection", {})
        if saved.get("configured", False):
            self.host_edit.setText(str(saved.get("host", "localhost")))
            self.port_edit.setText(str(saved.get("port", 5432)))
            self.database_edit.setText(str(saved.get("database", "systemarchitecture")))
            self.user_edit.setText(str(saved.get("user", "postgres")))
            self.password_edit.setText(str(saved.get("password", "")))
        else:
            # Fall back to DB_CONFIG defaults
            cfg = getattr(database, "DB_CONFIG", {})
            self.host_edit.setText(str(cfg.get("host", "localhost")))
            self.port_edit.setText(str(cfg.get("port", 5432)))
            self.database_edit.setText(str(cfg.get("database", "systemarchitecture")))
            self.user_edit.setText(str(cfg.get("user", "postgres")))

    def test_connection(self):
        """Test database connection"""
        try:
            database.set_db_config(
                host=self.host_edit.text(),
                database=self.database_edit.text(),
                user=self.user_edit.text(),
                password=self.password_edit.text(),
                port=int(self.port_edit.text() or 5432),
            )

            success, message = database.test_connection()
            if success:
                QMessageBox.information(self, "✅ Success", "Connection successful!")
            else:
                QMessageBox.warning(
                    self, "⚠️ Connection Failed", f"Connection failed:\n{message}"
                )
        except Exception as e:
            QMessageBox.critical(self, "❌ Error", f"Configuration error:\n{str(e)}")

    def _save_config(self):
        """Persist current dialog fields to app_config.json via config_manager."""
        config_manager.set("database_connection.host", self.host_edit.text())
        config_manager.set(
            "database_connection.port", int(self.port_edit.text() or 5432)
        )
        config_manager.set("database_connection.database", self.database_edit.text())
        config_manager.set("database_connection.user", self.user_edit.text())
        config_manager.set("database_connection.password", self.password_edit.text())
        config_manager.set("database_connection.configured", True)

    def accept(self):
        """Override accept: test connection first, only accept on success."""
        if self._testing:
            return
        self._testing = True

        try:
            database.set_db_config(
                host=self.host_edit.text(),
                database=self.database_edit.text(),
                user=self.user_edit.text(),
                password=self.password_edit.text(),
                port=int(self.port_edit.text() or 5432),
            )

            success, message = database.test_connection()
            if success:
                self._save_config()
                super().accept()
            else:
                QMessageBox.warning(
                    self,
                    "⚠️ Connection Failed",
                    f"Cannot save configuration — connection failed:\n{message}\n\n"
                    f"Please fix the settings and try again.",
                )
        except Exception as e:
            QMessageBox.critical(self, "❌ Error", f"Configuration error:\n{str(e)}")
        finally:
            self._testing = False

    def get_config(self):
        """Get configuration values"""
        return {
            "host": self.host_edit.text(),
            "database": self.database_edit.text(),
            "user": self.user_edit.text(),
            "password": self.password_edit.text(),
            "port": int(self.port_edit.text() or 5432),
        }


class ModuleWiringApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🗂️ System Architecture - No Project")
        self.setGeometry(100, 100, 1300, 900)

        self.current_project = None
        self.tabs_initialized = False
        self.db_configured = False

        auth.auth_changed.connect(self.apply_access_policy_all)
        self.apply_access_policy_all()

        # Create menu bar BEFORE applying styles
        self._build_menu_bar()

        # Create status bar
        self.statusBar().showMessage("⚙️ Initializing application...", 5000)

        # Create empty tab widget (will be populated after project selection)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Show welcome message initially
        self._show_welcome_widget()

        # Apply theme and styles
        theme = config_manager.get_theme()
        style_manager.apply_theme(theme)
        style_manager.apply_style_to_widget(self, "main_window")
        self._apply_menubar_style()

        # Connect signals
        config_manager.theme_changed.connect(self.on_theme_changed)

        # user banner on status bar (or title)
        self.user_label = QLabel(" ")
        self.statusBar().addPermanentWidget(self.user_label)

        # profile menu
        menubar = self.menuBar()
        self.menu_profile = menubar.addMenu("👤 Profile")
        self.act_edit_profile = QAction(
            QIcon.fromTheme("user-properties"), "Edit Profile", self
        )
        self.act_active_users = QAction(
            QIcon.fromTheme("system-users"), "Active Users", self
        )
        self.act_logout = QAction(QIcon.fromTheme("system-log-out"), "Logout", self)

        self.menu_profile.addAction(self.act_edit_profile)
        self.menu_profile.addAction(self.act_active_users)
        self.menu_profile.addSeparator()
        self.menu_profile.addAction(self.act_logout)

        self.act_edit_profile.triggered.connect(self.on_edit_profile)
        self.act_active_users.triggered.connect(self.on_active_users)
        self.act_logout.triggered.connect(self.on_logout)

        # reflect auth on UI
        auth.auth_changed.connect(self.apply_access_policy_all)
        auth.auth_changed.connect(self.update_user_banner)

        # heartbeat timer (online presence)
        self._hb = QTimer(self)
        self._hb.setInterval(20000)  # 20s
        self._hb.timeout.connect(auth.heartbeat)
        auth.auth_changed.connect(self._on_auth_changed_timer)
        # Start the timer immediately if already logged in (e.g. from LoginDialog
        # shown before ModuleWiringApp was created — auth_changed fired before
        # we connected the handler above, so _on_auth_changed_timer was skipped).
        self._on_auth_changed_timer()
        self.update_user_banner()

        # Initialize database connection and show project selection
        self._initialize_database()

    def _seed_auth(self):
        """Create auth tables and seed default users (safe to call repeatedly)."""
        try:
            apply_auth_migration()
            seed_auth_basics()
            seed_subsystem_admins_from_db("Aa123456")
        except Exception as e:
            print(f"Auth seeding deferred (DB not ready yet): {e}")

    def _show_login_dialog(self) -> bool:
        """Show the login dialog. Returns True if user logged in."""
        dlg = LoginDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            return True
        return False

    def _initialize_database(self):
        """Initialize database connection — try saved config first, else show dialog.

        Flow:
          1. Try saved connection from app_config.json → ensure schema → seed + login + project selection
          2. If no saved config or connection fails → show DB config dialog
          3. On dialog accept → run DB init (creates DB/schema if missing) → seed auth → login → project selection
          4. On dialog cancel → stay on welcome screen (user can configure later via menu)
        """
        # 1. Try saved connection from app_config.json
        db_conn = config_manager.config_data.get("database_connection", {})
        if db_conn.get("configured", False):
            try:
                database.set_db_config(
                    host=db_conn.get("host", "localhost"),
                    database=db_conn.get("database", "systemarchitecture"),
                    user=db_conn.get("user", "postgres"),
                    password=db_conn.get("password", ""),
                    port=db_conn.get("port", 5432),
                )
                success, message = ensure_database_initialized()
                if success:
                    self.db_configured = True
                    self.statusBar().showMessage(
                        "✅ Database connected successfully", 3000
                    )
                    self._seed_auth()
                    if self._show_login_dialog():
                        self._show_project_selection()
                    else:
                        self.statusBar().showMessage(
                            "🔒 Please sign in via Profile > Logout to access projects.",
                            5000,
                        )
                    return
                self.statusBar().showMessage(f"⚠️ DB init failed: {message}", 5000)
            except Exception:
                pass  # Saved config failed, fall through to dialog

        # 2. No saved working config — show DB configuration dialog
        self._show_database_config()

        # 3. If user configured successfully via dialog, seed auth and proceed.
        #    (DB creation + schema init already ran inside _show_database_config.)
        if self.db_configured:
            self._seed_auth()
            if self._show_login_dialog():
                self._show_project_selection()
            else:
                self.statusBar().showMessage(
                    "🔒 Please sign in via Profile > Logout to access projects.",
                    5000,
                )

    def _show_database_config(self):
        """Show database configuration dialog (from menu or initial setup).

        The dialog now handles connection testing and saving config itself
        (see DatabaseConfigDialog.accept). On Cancel the app stays open.
        """
        config_dialog = DatabaseConfigDialog(self)
        if config_dialog.exec_() == QDialog.Accepted:
            self.db_configured = True
            # First launch: the DB may not exist yet — create DB + schema now.
            success, message = ensure_database_initialized()
            if success:
                self.statusBar().showMessage(
                    "✅ Database configured successfully", 3000
                )
            else:
                self.statusBar().showMessage(f"⚠️ DB init failed: {message}", 5000)
        else:
            # User cancelled — don't close the app
            if not self.db_configured:
                self.statusBar().showMessage(
                    "⚠️ Database not configured. "
                    "Use File > Database Configuration to set up.",
                    5000,
                )

    def _show_welcome_widget(self):
        """Show a welcome widget when no project is loaded."""
        welcome_label = QLabel("""
        <div style="text-align: center; padding: 50px;">
            <h1>🗂️ System Architecture</h1>
            <h3>🎉 Welcome!</h3>
            <p style="color: #666; font-size: 14px;">
                Create a new project or open an existing one to get started.<br><br>
                Use the <b>📁 File</b> menu to create or open a project.
            </p>
        </div>
        """)
        welcome_label.setAlignment(Qt.AlignCenter)
        self.tabs.addTab(welcome_label, "🏠 Welcome")

    def _show_project_selection(self):
        """Show the project selection dialog."""
        if not self.db_configured:
            return

        dialog = ProjectSelectionDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            if dialog.selected_project:
                self.current_project = dialog.selected_project
                self.setWindowTitle(f"🗂️ System Architecture - {self.current_project}")

                if dialog.action_type == "new":
                    self.statusBar().showMessage(
                        f"🎉 Created new project: {self.current_project}", 5000
                    )
                else:
                    self.statusBar().showMessage(
                        f"📂 Opened project: {self.current_project}", 3000
                    )

                self._initialize_project_tabs()
        else:
            # User cancelled - close application
            self.close()
        try:
            pid = database.get_current_project_id()
            auth.refresh_scope_for_project(pid)
        except Exception:
            pass

    def _initialize_project_tabs(self):
        """Initialize the tabs after a project is loaded."""
        # Clear existing tabs
        self.tabs.clear()
        try:
            self.architecture_view_tab = ArchitectureViewTab(self)
            self.wiring_matrix_tab = EnhancedWiringMatrixTab(self)
            self.component_tree_tab = ComponentTreeTab(self)
            self.schematic_view_tab = SchematicViewTab(self)

            self.tabs.addTab(self.architecture_view_tab, "🗂️ Architecture")
            self.tabs.addTab(self.wiring_matrix_tab, "🔌 Interface")
            self.tabs.addTab(self.schematic_view_tab, "📋 Schematic View")
            self.tabs.addTab(self.component_tree_tab, "🌳 Component Tree")

            # Connect tab change signal
            self.tabs.currentChanged.connect(self.on_tab_changed)

            self.tabs_initialized = True
            self._update_menu_states(True)
            self.statusBar().showMessage("✅ Project loaded successfully", 3000)

            # Initial refresh
            self.on_tab_changed(0)

        except Exception as e:
            QMessageBox.critical(
                self,
                "❌ Initialization Error",
                f"Failed to initialize project tabs:\n{str(e)}",
            )
            self.current_project = None
            self.setWindowTitle("🗂️ System Architecture - No Project")
            self._show_welcome_widget()

    def _build_menu_bar(self):
        """Builds the complete menu bar."""
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("📁 &File")

        # New Project
        self.new_project_action = QAction("🆕 &New Project", self)
        self.new_project_action.setShortcut("Ctrl+N")
        self.new_project_action.setStatusTip("Create a new project")
        self.new_project_action.triggered.connect(self._new_project)
        file_menu.addAction(self.new_project_action)

        # lock by permission
        self.new_project_action.setEnabled(auth.has_perm("project.create"))
        auth.auth_changed.connect(
            lambda: self.new_project_action.setEnabled(auth.has_perm("project.create"))
        )

        # Open Project
        open_action = QAction("📂 &Open Project", self)
        open_action.setShortcut("Ctrl+O")
        open_action.setStatusTip("Open an existing project")
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)

        # Delete Project (system only)
        self.delete_project_action = QAction("🗑️ &Delete Project…", self)
        self.delete_project_action.setStatusTip("Delete a project (system only)")
        self.delete_project_action.triggered.connect(self._delete_project_via_dialog)
        file_menu.addAction(self.delete_project_action)

        # enable only for system (has project.delete)
        self.delete_project_action.setEnabled(auth.has_perm("project.delete"))
        auth.auth_changed.connect(
            lambda: self.delete_project_action.setEnabled(
                auth.has_perm("project.delete")
            )
        )

        # Manage Subsystems (system only)
        self.manage_subsystems_action = QAction("🗂️ &Manage Subsystems…", self)
        self.manage_subsystems_action.setStatusTip(
            "Add or remove subsystems (system only)"
        )
        self.manage_subsystems_action.triggered.connect(self._manage_subsystems)
        file_menu.addAction(self.manage_subsystems_action)

        # enabled only for system admins with a project open
        self.manage_subsystems_action.setEnabled(False)
        auth.auth_changed.connect(self._update_manage_subsystems_enabled)

        file_menu.addSeparator()

        # Close Project
        self.close_project_action = QAction("🔒 &Close Project", self)
        self.close_project_action.setShortcut("Ctrl+W")
        self.close_project_action.setStatusTip("Close current project")
        self.close_project_action.triggered.connect(self._close_project)
        self.close_project_action.setEnabled(False)
        file_menu.addAction(self.close_project_action)

        file_menu.addSeparator()

        # Database Config
        db_config_action = QAction("🛠️ Database &Configuration", self)
        db_config_action.setStatusTip("Configure database connection")
        db_config_action.triggered.connect(self._show_database_config)
        file_menu.addAction(db_config_action)

        file_menu.addSeparator()

        # Exit
        exit_action = QAction("🚪 E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.setStatusTip("Exit application")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View Menu
        view_menu = menubar.addMenu("👁️ &View")

        # Refresh All
        self.refresh_action = QAction("🔄 &Refresh All", self)
        self.refresh_action.setShortcut("F5")
        self.refresh_action.setStatusTip("Refresh all tabs")
        self.refresh_action.triggered.connect(self._refresh_everything)
        self.refresh_action.setEnabled(False)
        view_menu.addAction(self.refresh_action)

        view_menu.addSeparator()

        # Full Screen
        fullscreen_action = QAction("🖥️ &Full Screen", self)
        fullscreen_action.setShortcut("F11")
        fullscreen_action.setCheckable(True)
        fullscreen_action.setStatusTip("Toggle full screen mode")
        fullscreen_action.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(fullscreen_action)

        # Theme Menu
        theme_menu = menubar.addMenu("🎨 &Theme")
        self._setup_theme_menu(theme_menu)

        # Tools Menu
        tools_menu = menubar.addMenu("🔧 &Tools")
        self._setup_tools_menu(tools_menu)

        # Help Menu
        help_menu = menubar.addMenu("❓ &Help")
        self._setup_help_menu(help_menu)

    def _setup_theme_menu(self, theme_menu):
        """Setup theme menu."""
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions = {}

        current_theme = config_manager.get_theme()

        themes = [
            ("🌙 &Dark Theme", ThemeType.DARK, "Ctrl+1", "Switch to dark theme"),
            ("☀️ &Light Theme", ThemeType.LIGHT, "Ctrl+2", "Switch to light theme"),
            ("🔴 &Red Theme", ThemeType.RED, "Ctrl+3", "Switch to red theme"),
            ("🌿 &Green Theme", ThemeType.GREEN, "Ctrl+4", "Switch to green theme"),
        ]

        for theme_name, theme_type, shortcut, status_tip in themes:
            action = QAction(theme_name, self)
            action.setCheckable(True)
            action.setShortcut(shortcut)
            action.setStatusTip(status_tip)
            action.setData(theme_type.value)

            if theme_type == current_theme:
                action.setChecked(True)

            self._theme_group.addAction(action)
            theme_menu.addAction(action)
            self._theme_actions[theme_type] = action

        self._theme_group.triggered.connect(self._on_theme_selected)

    def _setup_tools_menu(self, tools_menu):
        """Setup tools menu."""
        # Export submenu
        export_menu = tools_menu.addMenu("📤 &Export")

        self.export_csv_action = QAction("📄 Export to CSV", self)
        self.export_csv_action.setStatusTip("Export data to CSV format")
        self.export_csv_action.triggered.connect(self._export_csv)
        self.export_csv_action.setEnabled(False)
        export_menu.addAction(self.export_csv_action)

        self.export_excel_action = QAction("📊 Export to Excel", self)
        self.export_excel_action.setStatusTip("Export data to Excel format")
        self.export_excel_action.triggered.connect(self._export_excel)
        self.export_excel_action.setEnabled(False)
        export_menu.addAction(self.export_excel_action)

        export_menu.addSeparator()

        self.export_json_action = QAction("📦 Export Project (JSON)", self)
        self.export_json_action.setStatusTip("Export entire project to a JSON file")
        self.export_json_action.triggered.connect(self._export_project_json)
        self.export_json_action.setEnabled(False)
        export_menu.addAction(self.export_json_action)

        # Import submenu
        import_menu = tools_menu.addMenu("📥 &Import")

        self.import_csv_action = QAction("📄 Import from CSV", self)
        self.import_csv_action.setStatusTip("Import data from CSV format")
        self.import_csv_action.triggered.connect(self._import_csv)
        self.import_csv_action.setEnabled(False)
        import_menu.addAction(self.import_csv_action)

        self.import_excel_action = QAction("📊 Import from Excel", self)
        self.import_excel_action.setStatusTip("Import data from Excel format")
        self.import_excel_action.triggered.connect(self._import_excel)
        self.import_excel_action.setEnabled(False)
        import_menu.addAction(self.import_excel_action)

        import_menu.addSeparator()

        self.import_json_action = QAction("📦 Import Project (JSON)", self)
        self.import_json_action.setStatusTip("Import entire project from a JSON file")
        self.import_json_action.triggered.connect(self._import_project_json)
        self.import_json_action.setEnabled(False)
        import_menu.addAction(self.import_json_action)

        tools_menu.addSeparator()

        # Settings
        settings_action = QAction("⚙️ &Settings", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.setStatusTip("Open application settings")
        settings_action.triggered.connect(self._show_settings)
        tools_menu.addAction(settings_action)

        # Reset Settings
        reset_action = QAction("🔄 &Reset Settings", self)
        reset_action.setStatusTip("Reset all settings to default")
        reset_action.triggered.connect(self._reset_settings)
        tools_menu.addAction(reset_action)

    def _setup_help_menu(self, help_menu):
        """Setup help menu."""
        # User Guide
        guide_action = QAction("📖 &User Guide", self)
        guide_action.setShortcut("F1")
        guide_action.setStatusTip("Open user guide")
        guide_action.triggered.connect(self._show_user_guide)
        help_menu.addAction(guide_action)

        # Keyboard Shortcuts
        shortcuts_action = QAction("⌨️ &Keyboard Shortcuts", self)
        shortcuts_action.setStatusTip("Show keyboard shortcuts")
        shortcuts_action.triggered.connect(self._show_shortcuts)
        help_menu.addAction(shortcuts_action)

        help_menu.addSeparator()

        # About
        about_action = QAction("ℹ️ &About", self)
        about_action.setStatusTip("About this application")
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _update_menu_states(self, has_project):
        """Update menu items based on whether a project is loaded."""
        self.close_project_action.setEnabled(has_project)
        self.refresh_action.setEnabled(has_project)
        self._update_manage_subsystems_enabled(has_project)
        self.export_csv_action.setEnabled(has_project)
        self.export_excel_action.setEnabled(has_project)
        self.export_json_action.setEnabled(has_project)
        self.import_csv_action.setEnabled(has_project)
        self.import_excel_action.setEnabled(has_project)
        self.import_json_action.setEnabled(has_project)

    def _apply_menubar_style(self):
        """Applies style to the menu bar."""
        menubar_style = style_manager.get_style("menu_bar")
        if menubar_style:
            self.menuBar().setStyleSheet(menubar_style)

    def _on_theme_selected(self, action: QAction):
        """Handles theme selection."""
        try:
            theme_value = action.data()
            new_theme = ThemeType(theme_value)
            config_manager.set_theme(new_theme)

            theme_names = {
                ThemeType.DARK: "Dark",
                ThemeType.LIGHT: "Light",
                ThemeType.RED: "Red",
                ThemeType.GREEN: "Green",
            }
            theme_name = theme_names.get(new_theme, "Unknown")
            self.statusBar().showMessage(f"🎨 '{theme_name}' theme applied", 3000)

        except Exception as e:
            QMessageBox.warning(self, "⚠️ Error", f"Error changing theme: {str(e)}")

    def on_theme_changed(self, theme_name: str):
        """Handles theme change from the signal."""
        try:
            theme = ThemeType(theme_name)
        except ValueError:
            theme = ThemeType.DARK

        style_manager.apply_theme(theme)
        style_manager.apply_style_to_widget(self, "main_window")
        self._apply_menubar_style()

        for t_type, action in self._theme_actions.items():
            action.setChecked(t_type == theme)

    def on_tab_changed(self, index):
        """Refreshes when tab is changed."""
        if not self.tabs_initialized:
            return

        current_tab = self.tabs.widget(index)
        tab_name = self.tabs.tabText(index)

        try:
            if isinstance(current_tab, ArchitectureViewTab):
                current_tab.load_data_tree()
            elif isinstance(current_tab, SchematicViewTab):
                if hasattr(current_tab, "tree_selector"):
                    current_tab.tree_selector.refresh_tree()
                current_tab.refresh_all()
            elif isinstance(current_tab, EnhancedWiringMatrixTab):
                current_tab.refresh_all()
            elif isinstance(current_tab, ComponentTreeTab):
                current_tab.refresh_tree()

            self.statusBar().showMessage(f"🔄 Switched to {tab_name}", 2000)

        except Exception as e:
            QMessageBox.warning(
                self, "⚠️ Tab Error", f"Error refreshing {tab_name}:\n{str(e)}"
            )

        if hasattr(current_tab, "apply_access_policy"):
            current_tab.apply_access_policy()

    # ==================================================================
    # MENU ACTION HANDLERS
    # ==================================================================

    def _new_project(self):
        """Creates a new project."""
        # HARD GUARD: only 'system' (project.create)
        if not auth.has_perm("project.create"):
            QMessageBox.warning(
                self, "Access denied", "Only 'system' can create a new project."
            )
            return

        if not self.db_configured:
            self._show_database_config()
            return

        project_name, ok = QInputDialog.getText(
            self, "🆕 New Project", "Enter project name:"
        )
        if ok and project_name.strip():
            try:
                success, message = database.create_new_project_guarded(
                    auth.user_id, project_name.strip()
                )
                if success:
                    self.current_project = project_name.strip()
                    self.setWindowTitle(
                        f"🗂️ System Architecture - {self.current_project}"
                    )
                    self.statusBar().showMessage(f"🎉 {message}", 5000)
                    self._initialize_project_tabs()
                else:
                    QMessageBox.warning(self, "⚠️ Creation Failed", message)
            except Exception as e:
                QMessageBox.critical(
                    self, "❌ Error", f"Failed to create project:\n{str(e)}"
                )

        try:
            pid = database.get_current_project_id()
            auth.refresh_scope_for_project(pid)
        except Exception:
            pass

    def _open_project(self):
        """Opens a project."""
        if not self.db_configured:
            self._show_database_config()
            return

        try:
            projects = database.get_all_projects()
            if not projects:
                QMessageBox.information(
                    self,
                    "ℹ️ No Projects",
                    "No projects found. Create a new project first.",
                )
                return

            from PyQt5.QtWidgets import QInputDialog

            project, ok = QInputDialog.getItem(
                self, "📂 Open Project", "Select project:", projects, 0, False
            )
            if ok and project:
                success, message = database.open_existing_project(project)
                if success:
                    self.current_project = project
                    self.setWindowTitle(
                        f"🗂️ System Architecture - {self.current_project}"
                    )
                    self.statusBar().showMessage(f"📂 {message}", 3000)
                    self._initialize_project_tabs()
                else:
                    QMessageBox.warning(self, "⚠️ Open Failed", message)
        except Exception as e:
            QMessageBox.critical(self, "❌ Error", f"Failed to open project:\n{str(e)}")

        try:
            pid = database.get_current_project_id()
            auth.refresh_scope_for_project(pid)
        except Exception:
            pass

    def _close_project(self):
        """Close current project."""
        if not self.current_project:
            return

        reply = QMessageBox.question(
            self,
            "🔒 Close Project",
            f'Close project "{self.current_project}"?',
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self.current_project = None
            self.tabs_initialized = False
            self.setWindowTitle("🗂️ System Architecture - No Project")
            self.tabs.clear()
            self._show_welcome_widget()
            self._update_menu_states(False)
            self.statusBar().showMessage("🔒 Project closed", 3000)

    def _refresh_everything(self):
        """Refreshes all tabs."""
        if not self.tabs_initialized:
            return

        try:
            self.architecture_view_tab.load_data_tree()
            if hasattr(self.schematic_view_tab, "refresh_tree"):
                self.schematic_view_tab.refresh_tree()
            self.schematic_view_tab.refresh_all()
            self.wiring_matrix_tab.refresh_all()
            self.component_tree_tab.refresh_tree()

            self.statusBar().showMessage("🔄 All tabs refreshed", 3000)
        except Exception as e:
            QMessageBox.warning(self, "⚠️ Error", f"Error refreshing: {str(e)}")

    def _toggle_fullscreen(self):
        """Toggles full screen mode."""
        if self.isFullScreen():
            self.showNormal()
            self.statusBar().showMessage("🖥️ Exited full screen mode", 2000)
        else:
            self.showFullScreen()
            # Force the menu bar to stay visible — on some platforms (Linux/X11
            # in particular) showFullScreen() can hide the menu bar behind the
            # central widget while leaving its mouse-area interactive, producing
            # an invisible-but-clickable menu bar.
            self.menuBar().show()
            self.statusBar().showMessage("🖥️ Entered full screen mode", 2000)

    def _export_csv(self):
        """Exports to CSV."""
        QMessageBox.information(
            self,
            "📄 Export CSV",
            "CSV export options are available in the Architecture tab.",
        )

    def _export_excel(self):
        """Exports to Excel."""
        QMessageBox.information(
            self,
            "📊 Export Excel",
            "Excel export options are available in the Architecture tab.",
        )

    def _import_csv(self):
        """Imports from CSV."""
        QMessageBox.information(
            self, "📄 Import CSV", "CSV import functionality is not yet implemented."
        )

    def _import_excel(self):
        """Imports from Excel."""
        QMessageBox.information(
            self,
            "📊 Import Excel",
            "Excel import functionality is not yet implemented.",
        )

    # ── JSON Project Export ────────────────────────────────────────────────

    def _export_project_json(self):
        """Export the entire current project as a structured JSON file."""
        if not self.current_project:
            QMessageBox.warning(self, "⚠️ No Project", "Please open a project first.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "📦 Export Project — Choose destination",
            f"{self.current_project.replace(' ', '_')}_export.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_path:
            return  # user cancelled

        try:
            project_id = get_current_project_id()
            if not project_id:
                raise RuntimeError("No project is currently selected.")

            data = export_project_data(project_id)

            # Write with nice formatting
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

            QMessageBox.information(
                self,
                "✅ Export Successful",
                f"Project **{self.current_project}** exported to:\n{file_path}",
            )
            self.statusBar().showMessage(f"📦 Project exported to {file_path}", 5000)

        except Exception as e:
            QMessageBox.critical(
                self,
                "❌ Export Error",
                f"Failed to export project:\n\n{str(e)}",
            )

    # ── JSON Project Import ────────────────────────────────────────────────

    def _import_project_json(self):
        """Import project data from a JSON file, replacing current project data."""
        if not self.current_project:
            QMessageBox.warning(self, "⚠️ No Project", "Please open a project first.")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "📦 Import Project — Choose JSON file",
            "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_path:
            return  # user cancelled

        # Confirm — this is destructive
        reply = QMessageBox.warning(
            self,
            "⚠️ Confirm Import",
            "This will **replace all current data** for project\n"
            + f'"{self.current_project}" with the data from:\n'
            + f"{file_path}\n\n"
            + "This action cannot be undone. Proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Top-level validation
            if not isinstance(data, dict):
                raise ValueError("Invalid format: expected a JSON object at root.")
            if "_export_meta" not in data:
                raise ValueError(
                    "This file does not appear to be a valid System Architecture "
                    "project export (missing _export_meta)."
                )

            project_id = get_current_project_id()
            if not project_id:
                raise RuntimeError("No project is currently selected.")

            success, message = import_project_data(project_id, data)

            if success:
                QMessageBox.information(
                    self,
                    "✅ Import Successful",
                    f"Project **{self.current_project}** restored from:\n{file_path}",
                )
                self.statusBar().showMessage(
                    f"📦 Project imported from {file_path}", 5000
                )
                # Refresh all tabs so the user sees the imported data
                self._refresh_everything()
            else:
                QMessageBox.critical(
                    self,
                    "❌ Import Failed",
                    f"Could not import project data:\n\n{message}",
                )

        except json.JSONDecodeError as e:
            QMessageBox.critical(
                self,
                "❌ Invalid JSON",
                f"The selected file is not valid JSON:\n\n{str(e)}",
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "❌ Import Error",
                f"Failed to import project:\n\n{str(e)}",
            )

    def _show_settings(self):
        """Shows settings window."""
        QMessageBox.information(
            self, "⚙️ Settings", "Settings window is not yet implemented."
        )

    def _reset_settings(self):
        """Resets settings to default."""
        reply = QMessageBox.question(
            self,
            "🔄 Reset Settings",
            "Do you want to reset all settings to their default values?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            try:
                config_manager.reset_to_defaults()
                self.statusBar().showMessage("🔄 Settings reset", 3000)
            except Exception as e:
                QMessageBox.critical(
                    self, "❌ Error", f"Error resetting settings: {str(e)}"
                )

    def _show_user_guide(self):
        """Shows the user guide."""
        guide_text = """📖 Quick User Guide:

🎨 Change Theme:
   • Theme menu or Ctrl+1,2,3,4

📁 Project Management:
   • Ctrl+N: New Project
   • Ctrl+O: Open Project  
   • Ctrl+W: Close Project

🔄 Refresh:
   • F5: Refresh all tabs

👁️ View:
   • F11: Full Screen

🗄️ Database:
   • All projects stored centrally in PostgreSQL
   • Multi-user support with real-time collaboration

🔧 Tools:
   • Ctrl+,: Settings
   • Export/Import data

❓ Help:
   • F1: User Guide
   • Show keyboard shortcuts"""

        QMessageBox.information(self, "📖 User Guide", guide_text)

    def _show_shortcuts(self):
        """Shows keyboard shortcuts."""
        shortcuts_text = """⌨️ Keyboard Shortcuts:

📁 File:
   Ctrl+N    New Project
   Ctrl+O    Open Project
   Ctrl+W    Close Project
   Ctrl+Q    Exit

🎨 Theme:
   Ctrl+1    Dark Theme
   Ctrl+2    Light Theme
   Ctrl+3    Red Theme
   Ctrl+4    Green Theme

👁️ View:
   F5        Refresh All
   F11       Full Screen

🔧 Tools:
   Ctrl+,    Settings

❓ Help:
   F1        User Guide"""

        QMessageBox.information(self, "⌨️ Keyboard Shortcuts", shortcuts_text)

    def _show_about(self):
        """Shows the about dialog."""
        about_text = """🗂️ System Architecture Application

🌟 Features:
• Centralized PostgreSQL database
• Multi-user collaborative environment
• Real-time project synchronization
• System architecture visualization
• Interface and connectivity management
• Interactive schematic view
• Component and module tree
• Beautiful theme system

🎨 Available Themes:
• Dark, Light, Red, Green

🗄️ Database: PostgreSQL (Multi-user)
📦 Version: 3.0.0 PostgreSQL Edition
👥 Developer: System Architecture Team

🔧 Built with: PyQt5 & Python"""

        QMessageBox.about(self, "ℹ️ About Application", about_text)

    def closeEvent(self, event):
        """Handles application close event."""
        # End user session so last_seen stops appearing online
        if auth.is_logged_in():
            auth.logout()

        # Save window position
        geometry = self.geometry()
        config_manager.set_window_geometry(
            geometry.x(), geometry.y(), geometry.width(), geometry.height()
        )

        # Update menu states when project is loaded
        if self.current_project:
            self._update_menu_states(True)

        event.accept()

    def apply_access_policy_all(self):
        if hasattr(self, "new_project_action"):
            self.new_project_action.setEnabled(auth.has_perm("project.create"))
        for name in (
            "architecture_view_tab",
            "wiring_matrix_tab",
            "schematic_view_tab",
            "component_tree_tab",
        ):
            w = getattr(self, name, None)
            if w and hasattr(w, "apply_access_policy"):
                try:
                    w.apply_access_policy()
                except Exception:
                    pass

    def _on_auth_changed_timer(self):
        if auth.is_logged_in():
            self._hb.start()
        else:
            self._hb.stop()

    def update_user_banner(self):
        if auth.is_logged_in():
            self.user_label.setText(f"User: {auth.user.get('username','')}")
            self.setWindowTitle(
                f"{self.windowTitle().split(' - ')[0]} - {auth.user.get('username','')}"
            )
        else:
            self.user_label.setText("Not signed in")

    def on_edit_profile(self):
        dlg = UserProfileDialog(self)
        dlg.exec_()

    def on_active_users(self):
        dlg = ActiveUsersDialog(self)
        dlg.exec_()

    def on_logout(self):
        # end session + clear UI banner
        auth.logout()
        self.update_user_banner()

        # show login dialog; if user cancels, exit app
        dlg = LoginDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            self.close()
            return

        # logged in again
        self.apply_access_policy_all()
        self.update_user_banner()

    def _delete_project_via_dialog(self):
        """
        Open the ProjectSelectionDialog so the user can delete a project there.
        The dialog already has the Delete button wired with guard & confirmations.
        """
        dialog = ProjectSelectionDialog(self)
        # use modal exec_ so user completes delete in the dialog
        dialog.exec_()

    def _update_manage_subsystems_enabled(self, has_project=None):
        """Enable 'Manage Subsystems' only for system admins with a project open."""
        if not hasattr(self, "manage_subsystems_action"):
            return
        if has_project is None:
            has_project = bool(getattr(self, "tabs_initialized", False))
        self.manage_subsystems_action.setEnabled(has_project and auth.is_system())

    def _manage_subsystems(self):
        """Open the subsystem management dialog (system admin only)."""
        if not auth.is_system():
            QMessageBox.warning(
                self, "Access denied", "Only the system admin can manage subsystems."
            )
            return
        if not getattr(self, "tabs_initialized", False):
            QMessageBox.warning(self, "No Project", "Please open a project first.")
            return

        dlg = SubsystemManagementDialog(self)
        dlg.exec_()
        # refresh every tab so subsystem changes propagate everywhere
        self._refresh_everything()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    theme = config_manager.get_theme()
    style_manager.apply_theme(theme)

    app.setApplicationName("System Architecture")
    app.setApplicationVersion("3.0.0")
    app.setOrganizationName("System Architecture Team")

    app.setFont(QFont("Segoe UI", 10))
    app.setStyle("Fusion")

    window = ModuleWiringApp()

    x, y, width, height = config_manager.get_window_geometry()
    window.setGeometry(x, y, width, height)

    window.show()

    sys.exit(app.exec_())
