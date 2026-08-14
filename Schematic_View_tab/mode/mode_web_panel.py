# -----------------------------------------------------------------------------
# mode_web_panel.py - Mode system for the web-based SchematicViewTab
#
# Provides a compact panel with mode management (create, enter/exit, save,
# delete) that integrates with the web-based schematic bridge and tree
# selector. Uses the same database functions as the old Qt scene modes.
# -----------------------------------------------------------------------------

from PyQt5.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QListWidget, QListWidgetItem, QInputDialog,
    QMessageBox, QSizePolicy,
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize
from PyQt5.QtGui import QFont, QColor

from styles.style_manager import create_styled_button
from styles.theme_manager import theme_manager
from styles.design_system import BorderRadius, Typography, Spacing

from database import (
    get_all_modes, get_mode_modules,
    get_current_project_id, get_connection,
)
from auth_manager import auth
from suggestions import propose_mode_change


def _validate_module_ids(module_ids):
    """
    Filter out module IDs that no longer exist in the database.
    Returns the subset of IDs that still exist.
    """
    if not module_ids:
        return []
    try:
        from database import get_connection, get_current_project_id
        pid = get_current_project_id()
        if pid is None:
            return module_ids  # can't validate, pass through
        with get_connection() as conn:
            cur = conn.cursor()
            placeholders = ",".join("%s" for _ in module_ids)
            cur.execute(
                f"SELECT id FROM modules WHERE id IN ({placeholders}) AND project_id = %s",
                (*module_ids, pid),
            )
            valid = {row[0] for row in cur.fetchall()}
            return [mid for mid in module_ids if mid in valid]
    except Exception:
        return module_ids  # pass through on error


class ModeWebPanel(QFrame):
    """
    Compact mode management panel for the web-based schematic view tab.
    
    Shows a list of modes + buttons: Create, Enter, Save, Exit, Delete.
    Emits signals so the parent tab can react (filter scene, save positions, etc.).
    """
    
    # Emitted when a mode is entered/exited/created/deleted
    modeEntered = pyqtSignal(str)      # mode_name
    modeExited = pyqtSignal()
    modeCreated = pyqtSignal(str)      # mode_name
    modeDeleted = pyqtSignal(str)      # mode_name
    modeSaved = pyqtSignal(str)        # mode_name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ModeWebPanel")
        self._current_mode = None
        self._normal_selection = None  # cached tree selection before entering mode
        self._tree_selector = None     # set by parent tab
        self._bridge = None            # set by parent tab

        self.setFixedWidth(240)
        self.apply_styles()

        self._build_ui()
        self._connect_buttons()
        self.refresh_modes()

        # Re-apply styles on theme change
        from styles.style_manager import style_manager
        style_manager.theme_changed.connect(self.apply_styles)

        auth.auth_changed.connect(self._apply_access_policy)
        self._apply_access_policy()

    # ------------------------------------------------------------------
    # Public API — called by parent tab
    # ------------------------------------------------------------------
    def set_tree_selector(self, tree_selector):
        """Provide the SchematicTreeSelector for module filtering."""
        self._tree_selector = tree_selector

    def set_bridge(self, bridge):
        """Provide the SchematicBridge for scene data reload."""
        self._bridge = bridge

    def get_current_mode(self):
        return self._current_mode

    def get_normal_selection(self):
        return self._normal_selection

    def set_normal_selection(self, sel):
        self._normal_selection = sel

    # ------------------------------------------------------------------
    # UI building
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header
        header = QLabel("🗂️ Modes")
        header.setObjectName("modeHeader")
        header.setStyleSheet(f"""
            color: {theme_manager.get_color('accent')};
            font-size: 14px;
            font-weight: bold;
            background: transparent;
            border: none;
            padding: 4px 0;
        """)
        layout.addWidget(header)

        # Status label
        self._status_label = QLabel("No mode active")
        self._status_label.setObjectName("modeStatus")
        self._status_label.setStyleSheet(f"""
            color: {theme_manager.get_color('text_secondary')};
            font-size: 11px;
            background: transparent;
            border: none;
            padding: 2px 0 6px 0;
        """)
        layout.addWidget(self._status_label)

        # Mode list
        self._mode_list = QListWidget()
        self._mode_list.setObjectName("modeList")
        self._mode_list.setMaximumHeight(180)
        self._mode_list.setSpacing(2)
        layout.addWidget(self._mode_list)

        # Button row 1: Create | Delete
        btn_row1 = QHBoxLayout()
        btn_row1.setSpacing(4)
        self._btn_create = create_styled_button("+ New", "normal")
        self._btn_create.setFixedHeight(26)
        self._btn_delete = create_styled_button("✕ Delete", "normal")
        self._btn_delete.setFixedHeight(26)
        btn_row1.addWidget(self._btn_create)
        btn_row1.addWidget(self._btn_delete)
        layout.addLayout(btn_row1)

        # Button row 2: Enter | Save | Exit
        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(4)
        self._btn_enter = create_styled_button("▶ Enter", "normal")
        self._btn_enter.setFixedHeight(26)
        self._btn_save = create_styled_button("💾 Save", "normal")
        self._btn_save.setFixedHeight(26)
        self._btn_exit = create_styled_button("◀ Exit", "normal")
        self._btn_exit.setFixedHeight(26)
        btn_row2.addWidget(self._btn_enter)
        btn_row2.addWidget(self._btn_save)
        btn_row2.addWidget(self._btn_exit)
        layout.addLayout(btn_row2)

        layout.addStretch()

    def _connect_buttons(self):
        self._btn_create.clicked.connect(self._on_create)
        self._btn_delete.clicked.connect(self._on_delete)
        self._btn_enter.clicked.connect(self._on_enter)
        self._btn_save.clicked.connect(self._on_save)
        self._btn_exit.clicked.connect(self._on_exit)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------
    def _on_create(self):
        """Create a new mode from the current tree selection."""
        if not auth.is_logged_in():
            QMessageBox.warning(self, "Access Denied", "Please log in first.")
            return

        name, ok = QInputDialog.getText(self, "New Mode", "Mode name:")
        if not ok or not name.strip():
            return
        name = name.strip()

        # Check if already exists
        existing = get_all_modes()
        if name in existing:
            QMessageBox.warning(self, "Mode Exists", f"Mode '{name}' already exists.")
            return

        # Collect module IDs from current tree selection
        module_ids = []
        if self._tree_selector and hasattr(self._tree_selector, "get_checked_ids"):
            selection = self._tree_selector.get_checked_ids()
            module_ids = selection.get("modules", []) or []

        if not module_ids:
            QMessageBox.warning(self, "No Modules", "Check at least one module in the tree first.")
            return

        # Filter out any module IDs that no longer exist in the database
        valid_ids = _validate_module_ids(module_ids)
        if len(valid_ids) < len(module_ids):
            QMessageBox.information(
                self, "Modules Removed",
                f"{len(module_ids) - len(valid_ids)} module(s) were deleted "
                f"and have been removed from the mode selection.",
            )
            if not valid_ids:
                return

        ok, msg = propose_mode_change("create", name, valid_ids)
        if ok:
            self.modeCreated.emit(name)
            self.refresh_modes()
            QMessageBox.information(self, "Mode Created", msg)
        else:
            QMessageBox.warning(self, "Error", msg)

    def _on_delete(self):
        """Delete selected mode(s)."""
        if not auth.is_logged_in():
            QMessageBox.warning(self, "Access Denied", "Please log in first.")
            return

        selected = self._get_selected_mode_names()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Select a mode to delete.")
            return

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete mode '{selected[0]}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        mode_name = selected[0]
        ok, msg = propose_mode_change("delete", mode_name)
        if ok:
            # If current mode was deleted, exit it
            if self._current_mode == mode_name:
                self._current_mode = None
                self._update_status()
                self.modeExited.emit()
            self.modeDeleted.emit(mode_name)
            self.refresh_modes()
        else:
            QMessageBox.warning(self, "Error", msg)

    def _on_enter(self):
        """Enter the selected mode — filter tree to that mode's modules."""
        selected = self._get_selected_mode_names()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Select a mode to enter.")
            return

        mode_name = selected[0]

        # Cache normal selection
        if self._tree_selector and hasattr(self._tree_selector, "get_checked_ids"):
            self._normal_selection = self._tree_selector.get_checked_ids()

        # Get mode modules and apply to tree
        mode_modules = get_mode_modules(mode_name)
        if not mode_modules:
            QMessageBox.information(self, "Empty Mode",
                                    f"Mode '{mode_name}' has no modules defined.")
            return

        if self._tree_selector and hasattr(self._tree_selector, "refresh_tree"):
            self._tree_selector.refresh_tree()
        if self._tree_selector and hasattr(self._tree_selector, "apply_selection"):
            self._tree_selector.apply_selection({
                "subsystems": [],
                "modules": mode_modules,
                "connectors": [],
                "pins": [],
            })

        self._current_mode = mode_name
        self._update_status()
        self.modeEntered.emit(mode_name)

    def _on_save(self):
        """Save current tree selection (modules) and positions into the mode."""
        if not auth.is_logged_in():
            QMessageBox.warning(self, "Access Denied", "Please log in first.")
            return

        if not self._current_mode:
            QMessageBox.information(self, "No Mode", "Enter a mode first, then save.")
            return

        # Save current module selection
        module_ids = []
        if self._tree_selector and hasattr(self._tree_selector, "get_checked_ids"):
            selection = self._tree_selector.get_checked_ids()
            module_ids = selection.get("modules", []) or []

        if not module_ids:
            QMessageBox.warning(self, "No Modules", "Check at least one module in the tree.")
            return

        # Filter out any module IDs that no longer exist
        valid_ids = _validate_module_ids(module_ids)
        if len(valid_ids) < len(module_ids):
            QMessageBox.information(
                self, "Modules Removed",
                f"{len(module_ids) - len(valid_ids)} module(s) were deleted "
                f"and have been removed from the mode selection.",
            )
            if not valid_ids:
                return

        ok, msg = propose_mode_change("create", self._current_mode, valid_ids)
        if ok:
            self.modeSaved.emit(self._current_mode)
            self.refresh_modes()
            QMessageBox.information(self, "Mode Saved", msg)
        else:
            QMessageBox.warning(self, "Error", msg)

    def _on_exit(self):
        """Exit the current mode — restore the previous tree selection."""
        if not self._current_mode:
            QMessageBox.information(self, "No Mode", "No mode is currently active.")
            return

        # Restore normal selection
        if self._tree_selector and hasattr(self._tree_selector, "apply_selection"):
            if self._normal_selection:
                self._tree_selector.apply_selection(self._normal_selection)
            elif hasattr(self._tree_selector, "deselect_all_items"):
                self._tree_selector.deselect_all_items()

        self._current_mode = None
        self._update_status()
        self.modeExited.emit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_selected_mode_names(self):
        """Return list of mode names selected in the list widget."""
        names = []
        for item in self._mode_list.selectedItems():
            name = item.data(Qt.UserRole)
            if name:
                names.append(name)
        return names

    def refresh_modes(self):
        """Reload the mode list from the database."""
        self._mode_list.clear()
        modes = get_all_modes()
        if not modes:
            item = QListWidgetItem("(no modes)")
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            self._mode_list.addItem(item)
            return

        for name in modes:
            item = QListWidgetItem(f"  {name}")
            item.setData(Qt.UserRole, name)
            item.setSizeHint(QSize(0, 28))
            self._mode_list.addItem(item)

        self._update_status()

    def _update_status(self):
        """Update the status label and highlight active mode in the list."""
        if self._current_mode:
            self._status_label.setText(f"✅ Active: {self._current_mode}")
            # Highlight the active mode
            for i in range(self._mode_list.count()):
                item = self._mode_list.item(i)
                name = item.data(Qt.UserRole)
                if name == self._current_mode:
                    self._mode_list.setCurrentItem(item)
                    break
        else:
            self._status_label.setText("No mode active")

    def _apply_access_policy(self):
        """Enable/disable edit buttons based on auth."""
        # Every logged-in user can propose mode changes; the system admin
        # approves them in the Reviews tab.
        can_propose = auth.is_logged_in()
        self._btn_create.setEnabled(can_propose)
        self._btn_delete.setEnabled(can_propose)
        self._btn_save.setEnabled(can_propose)

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------
    def apply_styles(self):
        """Apply unified theme styling."""
        bg = theme_manager.get_color("primary_dark")
        border = theme_manager.get_color("primary_light")
        text = theme_manager.get_color("text_primary")
        accent = theme_manager.get_color("accent")

        self.setStyleSheet(f"""
            QFrame#ModeWebPanel {{
                background: {bg};
                border: 1px solid {border};
                border-radius: {BorderRadius.LARGE};
            }}
            QListWidget#modeList {{
                background: rgba(255,255,255,0.03);
                color: {text};
                font-size: 12px;
                border: 1px solid {border};
                border-radius: {BorderRadius.MEDIUM};
                padding: 2px;
                outline: none;
            }}
            QListWidget#modeList::item {{
                padding: 4px 8px;
                border-radius: 4px;
                margin: 1px 0;
            }}
            QListWidget#modeList::item:hover {{
                background: rgba(91,141,239,0.15);
            }}
            QListWidget#modeList::item:selected {{
                background: rgba(91,141,239,0.3);
                color: {accent};
                font-weight: bold;
            }}
        """)
