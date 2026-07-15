# -----------------------------------------------------------------------------
# schematic_tree_selector.py - با سیستم استایل یکپارچه و پشتیبانی کامل از تم‌ها
# -----------------------------------------------------------------------------
import sys, os
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
                             QHBoxLayout, QPushButton, QLabel)
from PyQt5.QtGui import QFont, QPalette, QColor
from PyQt5.QtCore import Qt, pyqtSignal

# Import unified style system
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from styles.style_manager import (style_manager, register_widget, 
                                create_styled_button, auto_style_widget)
from styles.design_system import Colors, Typography, Spacing, BorderRadius
from styles.theme_manager import theme_manager, ThemeType

from database import get_connection, get_current_project_id

class TransparentTreeWidget(QTreeWidget):
    """Tree widget with proper transparency and theme support"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # Set transparency BEFORE applying styles
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        
        # Apply initial theme styles
        self.apply_theme_styles()
        
        # Connect to theme changes
        style_manager.theme_changed.connect(self.apply_theme_styles)

    def apply_theme_styles(self):
        """Apply theme-based transparent styles"""
        # Get theme colors
        bg_color = theme_manager.get_color('primary_dark')
        text_color = theme_manager.get_color('text_primary')
        text_secondary = theme_manager.get_color('text_secondary')
        accent_color = theme_manager.get_color('accent')
        
        # Convert hex to rgba for transparency
        def hex_to_rgba(hex_color, alpha=15):
            color = QColor(hex_color)
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        
        tree_style = f"""
            QTreeWidget {{
                background: {hex_to_rgba(bg_color, 15)};  /* Very transparent background */
                color: {text_color};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_MEDIUM};
                border: none;
                outline: none;
                selection-background-color: {hex_to_rgba(accent_color, 40)};
                show-decoration-selected: 1;
                alternate-background-color: transparent;
            }}

            QTreeWidget QScrollBar:vertical, QTreeWidget QScrollBar:horizontal {{
                width: 8px;
                height: 8px;
                background: {hex_to_rgba(bg_color, 30)};
                border-radius: 6px;
            }}
            
            QTreeWidget QScrollBar::handle:vertical, QTreeWidget QScrollBar::handle:horizontal {{
                background: {hex_to_rgba(accent_color, 60)};
                border-radius: 6px;
                min-height: 20px;
                min-width: 20px;
            }}
            
            QTreeWidget::item {{
                background: {hex_to_rgba("#ffffff", 5)};  /* Almost transparent */
                border: 1px solid {hex_to_rgba(accent_color, 8)};
                padding: {Spacing.MD} {Spacing.SM};
                margin: 1px 0px;
                border-radius: {BorderRadius.MEDIUM};
                min-height: 18px;
            }}
            
            QTreeWidget::item:hover {{
                background: {hex_to_rgba(accent_color, 20)};  /* Light hover */
                border: 1px solid {hex_to_rgba(accent_color, 40)};
                color: white;
            }}
            
            QTreeWidget::item:selected {{
                background: {hex_to_rgba(accent_color, 35)};  /* Semi-transparent selection */
                border: 1px solid {hex_to_rgba(accent_color, 70)};
                color: white;
                font-weight: {Typography.WEIGHT_BOLD};
            }}
            
            QTreeWidget::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 6px;
                border: 2px solid {hex_to_rgba(text_secondary, 80)};
                background: {hex_to_rgba("#ffffff", 25)};
                margin: 2px;
            }}
            
            QTreeWidget::indicator:unchecked {{
                border: 2px solid {hex_to_rgba(text_secondary, 60)};
                background: {hex_to_rgba("#ffffff", 15)};
            }}
            
            QTreeWidget::indicator:unchecked:hover {{
                border: 2px solid {hex_to_rgba(accent_color, 80)};
                background: {hex_to_rgba(accent_color, 30)};
            }}
            
            QTreeWidget::indicator:checked {{
                border: 2px solid {hex_to_rgba(accent_color, 80)};
                background: {accent_color};
            }}
            
            QTreeWidget::indicator:checked:hover {{
                border: 2px solid {accent_color};
                background: {accent_color};
            }}
            
            QTreeWidget::indicator:indeterminate {{
                border: 2px solid {hex_to_rgba("#f39c12", 80)};
                background: {hex_to_rgba("#f1c40f", 50)};
            }}
            
            QTreeWidget::branch {{
                background: transparent;
                border: none;
            }}
        """
        
        self.setStyleSheet(tree_style)

    def paintEvent(self, event):
        """Custom paint to ensure transparency"""
        super().paintEvent(event)

class SchematicTreeSelector(QWidget):
    """Tree selector with unified theme system and proper transparency"""
    selectionChanged = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Set transparency attributes FIRST
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        
        self._index = {'subsystem': {}, 'module': {}, 'connector': {}, 'pin': {}}

        # Initialize data
        self.pin_connections = {}
        
        # Apply initial theme styles
        self.apply_theme_styles()
        
        # Connect to theme changes
        style_manager.theme_changed.connect(self.apply_theme_styles)
        
        # Setup UI
        self.init_ui()
        
        # Load data and connect signals
        self.refresh_tree()
        self.connect_signals()
        self.update_buttons()

    def _ensure_project_selected(self):
        project_id = get_current_project_id()
        if project_id is None:
            return False
        return True

    def apply_theme_styles(self):
        """Apply theme-based styles to the widget"""
        # Get theme colors
        bg_color = theme_manager.get_color('primary_medium')
        border_color = theme_manager.get_color('primary_light')
        
        # Convert hex to rgba for transparency
        def hex_to_rgba(hex_color, alpha=20):
            color = QColor(hex_color)
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        
        main_style = f"""
            SchematicTreeSelector {{
                background: {hex_to_rgba(bg_color, 20)};  /* Very transparent */
                border: 1px solid {hex_to_rgba(border_color, 60)};
                border-radius: {BorderRadius.XLARGE};
            }}
        """
        self.setStyleSheet(main_style)

    def init_ui(self):
        """Initialize UI with theme support"""
        font = QFont("Roboto Mono", 13, QFont.Medium)
        self.setFont(font)

        self.setObjectName("TreeContainer")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Header widget
        self.create_header(layout)
        
        # Tree widget
        self.create_tree(layout)
        
        # Control buttons
        self.create_controls(layout)

    def create_header(self, layout):
        """Create header widget with theme support"""
        header_widget = QWidget()
        header_widget.setFixedHeight(32)
        header_widget.setAttribute(Qt.WA_TranslucentBackground, True)
        
        # Apply theme-based header style
        header_widget.setStyleSheet(f"""
            QWidget {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                border-radius: {BorderRadius.LARGE};
                border: 1px solid {theme_manager.get_color('primary_light')};
            }}
            QLabel {{
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                background: transparent;
                border: none;
            }}
        """)
        
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(10, 0, 10, 0)
        
        header_label = QLabel("🌳 Component Tree")
        header_label.setFont(QFont("Roboto Mono", 15, QFont.Bold))
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        
        layout.addWidget(header_widget)

    def create_tree(self, layout):
        """Create tree widget with theme support"""
        self.tree = TransparentTreeWidget()
        self.tree.setFont(QFont("Roboto Mono", 13, QFont.Medium))
        self.tree.setHeaderLabels([""])
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setIndentation(20)
        self.tree.setAnimated(True)
        
        # Ensure transparency is properly set
        self.tree.setAttribute(Qt.WA_TranslucentBackground, True)
        self.tree.setAutoFillBackground(False)
        self.tree.setFrameStyle(0)
        
        # Remove any opaque backgrounds
        self.tree.viewport().setAttribute(Qt.WA_TranslucentBackground, True)
        self.tree.viewport().setAutoFillBackground(False)
        
        layout.addWidget(self.tree)

    def create_controls(self, layout):
        """Create control buttons with theme support"""
        control_widget = QWidget()
        control_widget.setFixedHeight(45)
        control_widget.setAttribute(Qt.WA_TranslucentBackground, True)
        
        # Apply theme-based control style
        control_widget.setStyleSheet(f"""
            QWidget {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                border-radius: {BorderRadius.LARGE};
                border: 1px solid {theme_manager.get_color('primary_light')};
            }}
        """)
        
        control_layout = QHBoxLayout(control_widget)
        control_layout.setContentsMargins(8, 6, 8, 6)
        control_layout.setSpacing(6)

        # Create styled buttons
        self.expand_toggle_btn = create_styled_button("", "small")
        self.expand_toggle_btn.setFixedSize(35, 28)
        self.expand_toggle_btn.clicked.connect(self.toggle_expand)
        
        self.selection_toggle_btn = create_styled_button("", "small")
        self.selection_toggle_btn.setFixedSize(35, 28)
        self.selection_toggle_btn.clicked.connect(self.toggle_selection)

        control_layout.addStretch(1)
        control_layout.addWidget(self.expand_toggle_btn)
        control_layout.addWidget(self.selection_toggle_btn)
        control_layout.addStretch(1)

        layout.addWidget(control_widget)

    def connect_signals(self):
        """Connect all necessary signals"""
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemExpanded.connect(self.update_expand_button)
        self.tree.itemCollapsed.connect(self.update_expand_button)

    def update_buttons(self):
        """Update button states and text"""
        self.update_expand_button()
        self.update_selection_button()

    def refresh_tree(self):
        """Refresh the tree with data from the database"""
        if not self.tree:
            return
        
        if not self._ensure_project_selected():
            self.tree.clear()
            return
            
        project_id = get_current_project_id()
        
        self.tree.blockSignals(True)
        self.tree.clear()

        subsystem_font = QFont("Roboto Mono", 14, QFont.Bold)
        module_font = QFont("Roboto Mono", 13, QFont.Bold)
        connector_font = QFont("Roboto Mono", 12, QFont.Bold)
        pin_font = QFont("Roboto Mono", 11, QFont.Bold)

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, name FROM subsystems WHERE project_id = %s ORDER BY name", (project_id,))
                subsystems = cur.fetchall()

                # Fetch pin connections
                self.pin_connections.clear()
                cur.execute("SELECT pin1_id, pin2_id FROM interfaces WHERE project_id = %s", (project_id,))
                for pin1_id, pin2_id in cur.fetchall():
                    self.pin_connections.setdefault(pin1_id, set()).add(pin2_id)
                    self.pin_connections.setdefault(pin2_id, set()).add(pin1_id)

                # Build tree with icons and theme colors
                for ss_id, ss_name in subsystems:
                    ss_item = QTreeWidgetItem([f"🢞 {ss_name}"])
                    ss_item.setFont(0, subsystem_font)
                    ss_item.setFlags(ss_item.flags() | Qt.ItemIsUserCheckable)
                    ss_item.setCheckState(0, Qt.Unchecked)
                    ss_item.setData(0, Qt.UserRole, ("subsystem", ss_id))
                    ss_item.setForeground(0, QColor(theme_manager.get_color('text_primary')))
                    self.tree.addTopLevelItem(ss_item)

                    cur.execute("SELECT id, name FROM modules WHERE subsystem_id=%s AND project_id=%s ORDER BY name", (ss_id, project_id))
                    modules = cur.fetchall()
                    for mod_id, mod_name in modules:
                        mod_item = QTreeWidgetItem([f"⚙️ {mod_name}"])
                        mod_item.setFont(0, module_font)
                        mod_item.setFlags(mod_item.flags() | Qt.ItemIsUserCheckable)
                        mod_item.setCheckState(0, Qt.Unchecked)
                        mod_item.setData(0, Qt.UserRole, ("module", mod_id))
                        mod_item.setForeground(0, QColor(theme_manager.get_color('text_primary')))
                        ss_item.addChild(mod_item)

                        cur.execute("SELECT id, name FROM connectors WHERE module_id=%s AND project_id=%s ORDER BY name", (mod_id, project_id))
                        connectors = cur.fetchall()
                        for conn_id, conn_name in connectors:
                            conn_item = QTreeWidgetItem([f"🔌 {conn_name}"])
                            conn_item.setFont(0, connector_font)
                            conn_item.setFlags(conn_item.flags() | Qt.ItemIsUserCheckable)
                            conn_item.setCheckState(0, Qt.Unchecked)
                            conn_item.setData(0, Qt.UserRole, ("connector", conn_id))
                            conn_item.setDisabled(True)
                            conn_item.setForeground(0, QColor(theme_manager.get_color('text_primary')))
                            mod_item.addChild(conn_item)

                            cur.execute("SELECT id, name FROM pins WHERE connector_id=%s AND project_id=%s ORDER BY pin_number", (conn_id, project_id))
                            pins = cur.fetchall()
                            for pin_id, pin_name in pins:
                                pin_item = QTreeWidgetItem([f"📍 {pin_name}"])
                                pin_item.setFont(0, pin_font)
                                pin_item.setFlags(pin_item.flags() | Qt.ItemIsUserCheckable)
                                pin_item.setCheckState(0, Qt.Unchecked)
                                pin_item.setData(0, Qt.UserRole, ("pin", pin_id))
                                pin_item.setDisabled(True)
                                pin_item.setForeground(0, QColor(theme_manager.get_color('text_primary')))
                                conn_item.addChild(pin_item)
        
        except Exception as e:
            print(f"Error loading tree data: {e}")
            # در صورت خطا، tree خالی را نگه دار
            self.tree.clear()

        self.tree.blockSignals(False)
        self._rebuild_index()
        # Emit empty selection to reset the bridge on tab switch
        self.selectionChanged.emit(self.get_checked_ids())
        self.update_selection_button()
        self.update_expand_button()

    def _on_item_changed(self, item, column):
        """Handle check propagation and emit selectionChanged signal"""
        if not self.tree:
            return
        self.tree.blockSignals(True)
        self.propagate_checkstate_down(item, column)
        self.tree.blockSignals(False)
        self.selectionChanged.emit(self.get_checked_ids())
        self.update_selection_button()

    def find_pin_item(self, pin_id):
        """Find a pin item by ID in the tree"""
        if not self.tree:
            return None
        def search_tree(tree_item):
            for i in range(tree_item.childCount()):
                child = tree_item.child(i)
                child_type, child_id = child.data(0, Qt.UserRole)
                if child_type == "pin" and child_id == pin_id:
                    return child
                found = search_tree(child)
                if found:
                    return found
            return None

        for i in range(self.tree.topLevelItemCount()):
            found = search_tree(self.tree.topLevelItem(i))
            if found:
                return found
        return None

    def propagate_checkstate_down(self, item, column):
        """Propagate check state and enabled status to children"""
        if not self.tree:
            return
        state = item.checkState(column)
        enabled = (state == Qt.Checked)

        def set_children_checkstate_and_enabled(parent_item, parent_enabled):
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                item_type, _ = child.data(0, Qt.UserRole)
                should_enable = parent_enabled or item_type == "module"
                if item_type != "module":
                    child.setDisabled(not should_enable)
                if should_enable:
                    child.setCheckState(0, state)
                else:
                    child.setCheckState(0, Qt.Unchecked)
                next_parent_enabled = (should_enable and child.checkState(0) == Qt.Checked)
                set_children_checkstate_and_enabled(child, next_parent_enabled)

        set_children_checkstate_and_enabled(item, enabled)

    def get_checked_ids(self):
        """Return a dict with lists of checked IDs"""
        if not self.tree:
            return {'subsystems': [], 'modules': [], 'connectors': [], 'pins': []}
        checked = {'subsystems': [], 'modules': [], 'connectors': [], 'pins': []}

        def recurse(item):
            for i in range(item.childCount()):
                child = item.child(i)
                if child.checkState(0) == Qt.Checked:
                    t, id_ = child.data(0, Qt.UserRole)
                    checked[t + 's'].append(id_)
                recurse(child)

        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.checkState(0) == Qt.Checked:
                t, id_ = top.data(0, Qt.UserRole)
                checked['subsystems'].append(id_)
            recurse(top)
        return checked

    def select_all_items(self):
        """Select all items in the tree"""
        if not self.tree:
            return
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setCheckState(0, Qt.Checked)
            self._set_all_children_checked(item, Qt.Checked)
        self.selectionChanged.emit(self.get_checked_ids())
        self.update_selection_button()

    def deselect_all_items(self):
        """Deselect all items in the tree"""
        if not self.tree:
            return
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setCheckState(0, Qt.Unchecked)
            self._set_all_children_checked(item, Qt.Unchecked)
        self.selectionChanged.emit(self.get_checked_ids())
        self.update_selection_button()

    def _set_all_children_checked(self, parent_item, state):
        """Set check state for all children recursively"""
        if not self.tree:
            return
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            child.setDisabled(False)
            child.setCheckState(0, state)
            self._set_all_children_checked(child, state)

    def expand_all(self):
        """Expand all tree items"""
        if not self.tree:
            return
        self.tree.expandAll()
        self.update_expand_button()

    def collapse_all(self):
        """Collapse all tree items"""
        if not self.tree:
            return
        self.tree.collapseAll()
        self.update_expand_button()

    def toggle_selection(self):
        """Toggle between select all and deselect all"""
        if not self.tree:
            return
        if self.is_all_selected():
            self.deselect_all_items()
        else:
            self.select_all_items()

    def toggle_expand(self):
        """Toggle between expand all and collapse all"""
        if not self.tree:
            return
        if self.is_all_expanded():
            self.collapse_all()
        else:
            self.expand_all()

    def is_all_selected(self):
        """Check if all items are selected"""
        if not self.tree:
            return False
        def check_children(item):
            for i in range(item.childCount()):
                child = item.child(i)
                if child.checkState(0) != Qt.Checked:
                    return False
                if not check_children(child):
                    return False
            return True

        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) != Qt.Checked:
                return False
            if not check_children(item):
                return False
        return True

    def is_all_expanded(self):
        """Check if all items are expanded"""
        if not self.tree:
            return False
        def check_children(item):
            for i in range(item.childCount()):
                child = item.child(i)
                if not child.isExpanded():
                    return False
                if not check_children(child):
                    return False
            return True

        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if not item.isExpanded():
                return False
            if not check_children(item):
                return False
        return True

    def update_selection_button(self):
        """Update the selection toggle button based on selection state"""
        if not self.tree or not self.selection_toggle_btn:
            return
        if self.is_all_selected():
            self.selection_toggle_btn.setText("🟩")
            self.selection_toggle_btn.setToolTip("Deselect All")
        else:
            self.selection_toggle_btn.setText("✅")
            self.selection_toggle_btn.setToolTip("Select All")

    def update_expand_button(self):
        """Update the expand toggle button based on expansion state"""
        if not self.tree or not self.expand_toggle_btn:
            return
        if self.is_all_expanded():
            self.expand_toggle_btn.setText("📁")
            self.expand_toggle_btn.setToolTip("Collapse All")
        else:
            self.expand_toggle_btn.setText("📂")
            self.expand_toggle_btn.setToolTip("Expand All")

    def paintEvent(self, event):
        """Paint event to ensure proper transparency"""
        super().paintEvent(event)

    # Index building for fast lookup
    def _rebuild_index(self):
        """Build a fast index {type -> {id -> QTreeWidgetItem}} for quick lookup."""
        self._index = {'subsystem': {}, 'module': {}, 'connector': {}, 'pin': {}}

        def walk(item):
            t, id_ = item.data(0, Qt.UserRole)
            if t in self._index:
                self._index[t][id_] = item
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def apply_selection(self, selection):
        """
        Apply a selection dict like:
        {
          'subsystems': [..],
          'modules':    [..],
          'connectors': [..],
          'pins':       [..]
        }
        Clears current checks, applies requested items, enables children properly,
        and emits selectionChanged once at the end.
        """
        if not self.tree:
            return

        def _to_int_set(lst):
            out = set()
            for v in (lst or []):
                try:
                    out.add(int(v))
                except Exception:
                    out.add(v)  # در بدترین حالت همون مقدار قبلی
            return out

        subs  = _to_int_set(selection.get('subsystems'))
        mods  = _to_int_set(selection.get('modules'))
        conns = _to_int_set(selection.get('connectors'))
        pins  = _to_int_set(selection.get('pins'))

        # مطمئن شو ایندکس آماده است
        if not getattr(self, "_index", None) or not any(self._index.values()):
            self._rebuild_index()

        # Block signals while we manipulate the tree
        self.tree.blockSignals(True)

        # 1) Clear all checks without emitting
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top.setCheckState(0, Qt.Unchecked)
            self._set_all_children_checked(top, Qt.Unchecked)
            # همچنین تمام non-module ها رو disable کن (مثل حالت اولیه)
            self._set_all_children_enabled_by_parent(top, parent_enabled=False)

        # 2) Apply subsystems
        for ss_id in subs:
            item = self._index['subsystem'].get(ss_id)
            if item:
                item.setCheckState(0, Qt.Checked)
                self._enable_children(item, enable=True)

        # 3) Apply modules (and enable their children)
        for m_id in mods:
            item = self._index['module'].get(m_id)
            if item:
                item.setCheckState(0, Qt.Checked)
                self._enable_children(item, enable=True)

        # 4) Apply connectors (only if parent module is checked)
        for c_id in conns:
            item = self._index['connector'].get(c_id)
            if item:
                parent = item.parent()
                if parent and parent.data(0, Qt.UserRole)[0] == 'module' and parent.checkState(0) != Qt.Checked:
                    parent.setCheckState(0, Qt.Checked)
                    self._enable_children(parent, enable=True)
                item.setDisabled(False)
                item.setCheckState(0, Qt.Checked)

        # 5) Apply pins (only if parent connector is checked)
        for p_id in pins:
            item = self._index['pin'].get(p_id)
            if item:
                conn_item = item.parent()
                if conn_item and conn_item.data(0, Qt.UserRole)[0] == 'connector':
                    if conn_item.checkState(0) != Qt.Checked:
                        mod_item = conn_item.parent()
                        if mod_item and mod_item.data(0, Qt.UserRole)[0] == 'module' and mod_item.checkState(0) != Qt.Checked:
                            mod_item.setCheckState(0, Qt.Checked)
                            self._enable_children(mod_item, enable=True)
                        conn_item.setDisabled(False)
                        conn_item.setCheckState(0, Qt.Checked)

                    item.setDisabled(False)
                    item.setCheckState(0, Qt.Checked)

        # 6) Propagate up states (parent partials/checked)
        self._propagate_up_entire_tree()

        # Unblock and emit one shot
        self.tree.blockSignals(False)
        self.selectionChanged.emit(self.get_checked_ids())
        self.update_selection_button()
        self.update_expand_button()

    def _enable_children(self, parent_item, enable: bool):
        """Enable/disable all non-module descendants based on a parent being checked."""
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            t, _ = child.data(0, Qt.UserRole)
            if t != 'module':
                child.setDisabled(not enable)
            self._enable_children(child, enable)

    def _set_all_children_enabled_by_parent(self, parent_item, parent_enabled: bool):
        """Reset enabled state for non-module children according to parent state (used when clearing)."""
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            t, _ = child.data(0, Qt.UserRole)
            should_enable = parent_enabled or (t == 'module')
            if t != 'module':
                child.setDisabled(not should_enable)
            next_enabled = should_enable and (child.checkState(0) == Qt.Checked)
            self._set_all_children_enabled_by_parent(child, next_enabled)

    def _propagate_up_entire_tree(self):
        """Recompute parent (un)checked/partial states from bottom to top."""
        def recompute(item):
            # First, recompute children
            for i in range(item.childCount()):
                recompute(item.child(i))
            # Then set this item's state based on children
            parent = item
            total = parent.childCount()
            if total == 0:
                return
            checked = 0
            partial = 0
            for i in range(total):
                st = parent.child(i).checkState(0)
                if st == Qt.Checked:
                    checked += 1
                elif st == Qt.PartiallyChecked:
                    partial += 1
            if checked == total:
                parent.setCheckState(0, Qt.Checked)
            elif checked > 0 or partial > 0:
                parent.setCheckState(0, Qt.PartiallyChecked)
            else:
                # اگر خودش دستی چک نشده، unchecked
                if parent.checkState(0) != Qt.Checked:
                    parent.setCheckState(0, Qt.Unchecked)

        for i in range(self.tree.topLevelItemCount()):
            recompute(self.tree.topLevelItem(i))