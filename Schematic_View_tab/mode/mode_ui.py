# -----------------------------------------------------------------------------
# mode_ui.py - Mode UI Components and Dialogs (Enhanced)
# -----------------------------------------------------------------------------

import sys
import os
from PyQt5.QtWidgets import (
    QDialog, QLabel, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout,
    QFormLayout, QMessageBox, QCheckBox, QWidget, QScrollArea, QGroupBox,
    QTreeWidgetItem, QTreeWidget, QListWidget, QListWidgetItem
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize
from PyQt5.QtGui import QFont, QPalette, QColor

# Import unified style system
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from styles.style_manager import (style_manager, register_widget, 
                                create_styled_button, auto_style_widget)
from styles.design_system import Colors, Typography, Spacing, BorderRadius
from styles.theme_manager import theme_manager, ThemeType
from Schematic_View_tab.schematic_tree_selector import TransparentTreeWidget 

from database import get_connection, get_all_modes, create_mode
from auth_manager import auth


class SimpleNameDialog(QDialog):
    """Simple name dialog with enhanced theme support"""
    
    def __init__(self, parent=None, title="Create Mode"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedSize(360, 160)

        # Apply theme styles
        self.apply_theme_styles()
        
        # Connect to theme changes
        style_manager.theme_changed.connect(self.apply_theme_styles)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Mode name...")
        
        # Use styled buttons with smaller size
        ok_btn = create_styled_button("OK", "normal")
        cancel_btn = create_styled_button("Cancel", "normal")
        
        # Set consistent button sizes
        ok_btn.setFixedSize(70, 28)
        cancel_btn.setFixedSize(70, 28)
        
        ok_btn.clicked.connect(self._on_ok)
        cancel_btn.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("Name:", self.name_edit)

        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addStretch()
        lay.addLayout(btns)
        
        auth.auth_changed.connect(self.apply_access_policy)
        self.apply_access_policy()

    def apply_theme_styles(self):
        """Apply enhanced theme-based styles with transparency"""
        # Helper function for rgba colors
        def hex_to_rgba(hex_color, alpha=20):
            color = QColor(hex_color)
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        
        dialog_style = f"""
            QDialog {{
                background: {hex_to_rgba(theme_manager.get_color('primary_dark'), 95)};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('primary_light'), 60)};
                border-radius: {BorderRadius.LARGE};
            }}
            QLabel {{
                color: {theme_manager.get_color('text_primary')};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_BOLD};
                margin-bottom: 5px;
                background: transparent;
                border: none;
            }}
            QLineEdit {{
                background: {hex_to_rgba(theme_manager.get_color('primary_medium'), 80)};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('primary_light'), 60)};
                border-radius: {BorderRadius.MEDIUM};
                color: {theme_manager.get_color('text_primary')};
                padding: {Spacing.XL};
                font-size: {Typography.SIZE_MEDIUM};
                margin-bottom: 10px;
                min-height: 20px;
            }}
            QLineEdit:focus {{
                border: 2px solid {theme_manager.get_color('accent')};
            }}
            QTreeWidget::indicator {{
            width: 16px;
            height: 16px;
            border-radius: 6px;
            border: 2px solid {hex_to_rgba(theme_manager.get_color('text_secondary'), 80)};
            background: {hex_to_rgba("#ffffff", 25)};
            margin: 4px;
            }}
            QTreeWidget::indicator:unchecked {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('text_secondary'), 60)};
                background: {hex_to_rgba("#ffffff", 15)};
            }}
            QTreeWidget::indicator:unchecked:hover {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('accent'), 80)};
                background: {hex_to_rgba(theme_manager.get_color('accent'), 30)};
            }}
            QTreeWidget::indicator:checked {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('accent'), 80)};
                background: {theme_manager.get_color('accent')}; /* رنگ پررنگ */
            }}
            QTreeWidget::indicator:checked:hover {{
                border: 2px solid {theme_manager.get_color('accent')};
                background: {theme_manager.get_color('accent')};
            }}
        """
        self.setStyleSheet(dialog_style)

    def _on_ok(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Invalid", "Please enter a name.")
            return
        self._final_name = name
        self.accept()

    def get_name(self):
        return getattr(self, "_final_name", "").strip()


class ModeDialog(QDialog):
    """Enhanced unified dialog for creating and editing modes"""
    
    def __init__(self, mode_name=None, mode_description="", mode_manager=None, scene=None, parent=None):
        super().__init__(parent)
        self.mode_name = mode_name
        self.mode_manager = mode_manager
        self.scene = scene  # For real-time updates
        self.is_edit_mode = (mode_name is not None)
        
        self.setWindowTitle("New/Edit Mode")
        self.setFixedSize(550, 650)
        self.setModal(True)
        self.selected_modules = []
        
        # Apply initial theme styles
        self.apply_theme_styles()
        
        # Connect to theme changes
        style_manager.theme_changed.connect(self.apply_theme_styles)
        
        self.setup_ui(mode_name or "", mode_description)
        self.load_full_tree()

        auth.auth_changed.connect(self.apply_access_policy)
        self.apply_access_policy()
        
        if self.is_edit_mode:
            self.load_current_mode_selection()

    def apply_theme_styles(self):
        """Apply enhanced theme-based styles to dialog"""
        # Helper function for rgba colors
        def hex_to_rgba(hex_color, alpha=20):
            color = QColor(hex_color)
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        
        dialog_style = f"""
            QDialog {{
                background: {hex_to_rgba(theme_manager.get_color('primary_dark'), 95)};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('primary_light'), 60)};
                border-radius: {BorderRadius.LARGE};
            }}
            QLabel {{
                color: {theme_manager.get_color('text_primary')};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_BOLD};
                margin-bottom: 5px;
                background: transparent;
                border: none;
            }}
            QLineEdit {{
                background: {hex_to_rgba(theme_manager.get_color('primary_medium'), 80)};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('primary_light'), 60)};
                border-radius: {BorderRadius.MEDIUM};
                color: {theme_manager.get_color('text_primary')};
                padding: {Spacing.XL};
                font-size: {Typography.SIZE_MEDIUM};
                margin-bottom: 10px;
                min-height: 20px;
            }}
            QLineEdit:focus {{
                border: 2px solid {theme_manager.get_color('accent')};
            }}
            QTreeWidget {{
                background: {hex_to_rgba(theme_manager.get_color('primary_dark'), 15)};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_MEDIUM};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('primary_light'), 60)};
                outline: none;
                selection-background-color: {hex_to_rgba(theme_manager.get_color('accent'), 40)};
                show-decoration-selected: 1;
                alternate-background-color: transparent;
                border-radius: {BorderRadius.MEDIUM};
            }}
            QTreeWidget::item {{
                background: {hex_to_rgba("#ffffff", 5)};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('accent'), 8)};
                padding: {Spacing.MD} {Spacing.SM};
                margin: 1px 0px;
                border-radius: {BorderRadius.SMALL};
                min-height: 22px;
            }}
            QTreeWidget::item:hover {{
                background: {hex_to_rgba(theme_manager.get_color('accent'), 20)};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('accent'), 40)};
                color: white;
            }}
            QTreeWidget::item:selected {{
                background: {hex_to_rgba(theme_manager.get_color('accent'), 35)};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('accent'), 70)};
                color: white;
                font-weight: {Typography.WEIGHT_BOLD};
            }}
            QTreeWidget::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 6px;
                border: 2px solid {hex_to_rgba(theme_manager.get_color('text_secondary'), 80)};
                background: {hex_to_rgba("#ffffff", 25)};
                margin: 2px;
            }}
            QTreeWidget::indicator:unchecked {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('text_secondary'), 60)};
                background: {hex_to_rgba("#ffffff", 15)};
            }}
            QTreeWidget::indicator:unchecked:hover {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('accent'), 80)};
                background: {hex_to_rgba(theme_manager.get_color('accent'), 30)};
            }}
            QTreeWidget::indicator:checked {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('accent'), 80)};
                background: {hex_to_rgba(theme_manager.get_color('accent'), 50)};
            }}
            QTreeWidget::indicator:checked:hover {{
                border: 2px solid {theme_manager.get_color('accent')};
                background: {theme_manager.get_color('accent')};
            }}
        """
        self.setStyleSheet(dialog_style)
    
    def setup_ui(self, mode_name, mode_description):
        """Setup the user interface with enhanced styling"""
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(25, 25, 25, 25)
        
        # Header with enhanced styling like tree selector
        header_text = f"Edit Mode: {mode_name}" if self.is_edit_mode else "Create New Mode"
        header_label = QLabel(header_text)
        header_label.setStyleSheet(f"""
            font-size: 20px; 
            font-weight: bold; 
            color: {theme_manager.get_color('accent')}; 
            margin-bottom: 15px;
            background: transparent;
        """)
        main_layout.addWidget(header_label)
        
        # Form section
        form_widget = QWidget()
        form_layout = QFormLayout(form_widget)
        form_layout.setSpacing(15)
        
        self.name_input = QLineEdit(mode_name)
        self.name_input.setPlaceholderText("Enter unique mode name...")
        
        self.description_input = QLineEdit(mode_description)
        self.description_input.setPlaceholderText("Optional description...")

        form_layout.addRow("Mode Name:", self.name_input)
        form_layout.addRow("Description:", self.description_input)
        main_layout.addWidget(form_widget)

        # Module selection section
        modules_label = QLabel("Select Components:")
        modules_label.setStyleSheet(f"""
            font-size: 16px; 
            font-weight: bold; 
            color: {theme_manager.get_color('text_primary')}; 
            margin-top: 10px;
            background: transparent;
        """)
        main_layout.addWidget(modules_label)
        
        # Tree widget for full component selection
        self.component_tree = TransparentTreeWidget()
        self.component_tree.setHeaderLabels(["Components"])
        self.component_tree.setHeaderHidden(True)
        self.component_tree.setRootIsDecorated(True)
        self.component_tree.setMinimumHeight(300)
        self.component_tree.setMaximumHeight(300)
        self.component_tree.setIndentation(20)
        self.component_tree.setAnimated(True)
        main_layout.addWidget(self.component_tree)

        # Button layout - enhanced with consistent sizing
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)
        
        self.cancel_button = create_styled_button("✗ Cancel", "normal")
        action_text = "💾 Save Changes" if self.is_edit_mode else "✓ Create Mode"
        self.action_button = create_styled_button(action_text, "normal")
        
        # Set consistent button sizes like tree selector
        self.cancel_button.setFixedSize(100, 28)
        self.action_button.setFixedSize(120, 28)

        self.cancel_button.clicked.connect(self.reject)
        self.action_button.clicked.connect(self.validate_and_accept)

        button_layout.addStretch()
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.action_button)
        button_layout.addStretch()
        
        main_layout.addLayout(button_layout)

        # Set focus
        if self.is_edit_mode:
            self.name_input.selectAll()
        self.name_input.setFocus()

    # ... (rest of the methods remain the same as they're working correctly)
    def load_full_tree(self):
        """Load full component tree like schematic_tree_selector"""
        from database import get_connection, get_current_project_id
        
        self.component_tree.clear()
        
        current_project_id = get_current_project_id()
        if current_project_id is None:
            return
        
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                
                # Get subsystems
                cursor.execute("SELECT id, name FROM subsystems WHERE project_id = %s ORDER BY name", (current_project_id,))
                subsystems = cursor.fetchall()
                
                for subsystem_id, subsystem_name in subsystems:
                    # Create subsystem item
                    subsystem_item = QTreeWidgetItem([f"🏢 {subsystem_name}"])
                    subsystem_item.setFont(0, QFont("Roboto Mono", 14, QFont.Bold))
                    subsystem_item.setFlags(subsystem_item.flags() | Qt.ItemIsUserCheckable)
                    subsystem_item.setCheckState(0, Qt.Unchecked)
                    subsystem_item.setData(0, Qt.UserRole, ("subsystem", subsystem_id))
                    subsystem_item.setForeground(0, QColor(theme_manager.get_color('text_primary')))
                    self.component_tree.addTopLevelItem(subsystem_item)
                    
                    # Get modules for this subsystem
                    cursor.execute("SELECT id, name FROM modules WHERE subsystem_id = %s AND project_id = %s ORDER BY name", (subsystem_id, current_project_id))
                    modules = cursor.fetchall()
                    
                    for module_id, module_name in modules:
                        module_item = QTreeWidgetItem([f"⚙️ {module_name}"])
                        module_item.setFont(0, QFont("Roboto Mono", 13, QFont.Bold))
                        module_item.setFlags(module_item.flags() | Qt.ItemIsUserCheckable)
                        module_item.setCheckState(0, Qt.Unchecked)
                        module_item.setData(0, Qt.UserRole, ("module", module_id))
                        module_item.setForeground(0, QColor(theme_manager.get_color('text_primary')))
                        subsystem_item.addChild(module_item)
                        
                        # Get connectors for this module
                        cursor.execute("SELECT id, name FROM connectors WHERE module_id = %s AND project_id = %s ORDER BY name", (module_id, current_project_id))
                        connectors = cursor.fetchall()
                        
                        for connector_id, connector_name in connectors:
                            connector_item = QTreeWidgetItem([f"🔌 {connector_name}"])
                            connector_item.setFont(0, QFont("Roboto Mono", 12, QFont.Medium))
                            connector_item.setFlags(connector_item.flags() | Qt.ItemIsUserCheckable)
                            connector_item.setCheckState(0, Qt.Unchecked)
                            connector_item.setData(0, Qt.UserRole, ("connector", connector_id))
                            connector_item.setDisabled(True)  # Initially disabled
                            connector_item.setForeground(0, QColor(theme_manager.get_color('text_primary')))
                            module_item.addChild(connector_item)
                            
                            # Get pins for this connector
                            cursor.execute("SELECT id, name FROM pins WHERE connector_id = %s AND project_id = %s ORDER BY pin_number", (connector_id, current_project_id))
                            pins = cursor.fetchall()
                            
                            for pin_id, pin_name in pins:
                                pin_item = QTreeWidgetItem([f"🔌 {pin_name}"])
                                pin_item.setFont(0, QFont("Roboto Mono", 11, QFont.Normal))
                                pin_item.setFlags(pin_item.flags() | Qt.ItemIsUserCheckable)
                                pin_item.setCheckState(0, Qt.Unchecked)
                                pin_item.setData(0, Qt.UserRole, ("pin", pin_id))
                                pin_item.setDisabled(True)  # Initially disabled
                                pin_item.setForeground(0, QColor(theme_manager.get_color('text_primary')))
                                connector_item.addChild(pin_item)
            
            # Connect signals
            self.component_tree.itemChanged.connect(self.on_tree_item_changed)
            
        except Exception as e:
            print(f"Error loading component tree: {e}")
            
    def load_current_mode_selection(self):
        """Load current mode's module selection for edit mode"""
        if not self.is_edit_mode or not self.mode_manager:
            return
            
        try:
            current_modules = set(self.mode_manager.get_mode_modules(self.mode_name))
            
            def check_modules_in_tree(item):
                item_type, item_id = item.data(0, Qt.UserRole)
                if item_type == "module" and item_id in current_modules:
                    item.setCheckState(0, Qt.Checked)
                    # Enable and check children properly
                    self.propagate_checkstate_down(item, 0, Qt.Checked)
                    # Update parent states
                    self.propagate_checkstate_up(item)
                
                for i in range(item.childCount()):
                    check_modules_in_tree(item.child(i))
            
            for i in range(self.component_tree.topLevelItemCount()):
                check_modules_in_tree(self.component_tree.topLevelItem(i))
                
            self.update_selected_modules()
            self.update_scene_display()
            
        except Exception as e:
            pass  # Silent error handling

    def on_tree_item_changed(self, item, column):
        """Handle tree item check state changes with proper logic"""
        if not self.component_tree:
            return
            
        self.component_tree.blockSignals(True)
        
        item_type, item_id = item.data(0, Qt.UserRole)
        state = item.checkState(0)
        
        # Propagate down when checked
        if state == Qt.Checked:
            self.propagate_checkstate_down(item, column, state)
        else:
            # When unchecked, only uncheck children, don't disable them if parent allows
            self.uncheck_children_only(item)
        
        # Always propagate up to update parent states
        self.propagate_checkstate_up(item)
        
        self.component_tree.blockSignals(False)
        
        # Update selected modules and scene display
        self.update_selected_modules()
        self.update_scene_display()

    def propagate_checkstate_down(self, item, column, state):
        """Propagate check state down to children"""
        enabled = (state == Qt.Checked)
        
        for i in range(item.childCount()):
            child = item.child(i)
            child_type, _ = child.data(0, Qt.UserRole)
            
            # Enable/disable based on parent and type
            should_enable = enabled or child_type == "module"
            if child_type != "module":
                child.setDisabled(not should_enable)
            
            if should_enable:
                child.setCheckState(0, state)
                # Recurse to children
                self.propagate_checkstate_down(child, column, state)

    def uncheck_children_only(self, item):
        """Uncheck children but keep them enabled if they should be"""
        for i in range(item.childCount()):
            child = item.child(i)
            child_type, _ = child.data(0, Qt.UserRole)
            
            # Uncheck but don't disable modules
            child.setCheckState(0, Qt.Unchecked)
            
            # For non-modules, disable if no parent is checked
            if child_type != "module":
                # Check if any parent module is still checked
                parent_checked = self.is_any_parent_module_checked(child)
                child.setDisabled(not parent_checked)
            
            # Recurse
            self.uncheck_children_only(child)

    def is_any_parent_module_checked(self, item):
        """Check if any parent module is checked"""
        parent = item.parent()
        while parent:
            parent_type, _ = parent.data(0, Qt.UserRole)
            if parent_type == "module" and parent.checkState(0) == Qt.Checked:
                return True
            parent = parent.parent()
        return False

    def propagate_checkstate_up(self, item):
        """Propagate check state up to parent - but don't uncheck parents"""
        parent = item.parent()
        if not parent:
            return
        
        # Check all siblings
        total_children = parent.childCount()
        checked_children = 0
        partially_checked_children = 0
        
        for i in range(total_children):
            child = parent.child(i)
            child_state = child.checkState(0)
            
            if child_state == Qt.Checked:
                checked_children += 1
            elif child_state == Qt.PartiallyChecked:
                partially_checked_children += 1
        
        # Set parent state - but be careful about unchecking
        if checked_children == total_children:
            parent.setCheckState(0, Qt.Checked)
        elif checked_children > 0 or partially_checked_children > 0:
            parent.setCheckState(0, Qt.PartiallyChecked)
        else:
            # Only uncheck parent if it wasn't manually checked
            current_state = parent.checkState(0)
            if current_state != Qt.Checked:
                parent.setCheckState(0, Qt.Unchecked)
        
        # Recurse up
        self.propagate_checkstate_up(parent)

    def update_selected_modules(self):
        """Update the list of selected modules"""
        self.selected_modules = []
        
        def collect_checked_modules(item):
            item_type, item_id = item.data(0, Qt.UserRole)
            if item_type == "module" and item.checkState(0) == Qt.Checked:
                self.selected_modules.append(item_id)
            
            for i in range(item.childCount()):
                collect_checked_modules(item.child(i))
        
        for i in range(self.component_tree.topLevelItemCount()):
            collect_checked_modules(self.component_tree.topLevelItem(i))

    def update_scene_display(self):
        """Update scene display in real-time like tree selector"""
        if not self.scene:
            return
            
        # Get current selection
        selection_dict = self.get_current_selection()
        
        # Update scene
        self.scene.update_display_from_selection(selection_dict)

    def get_current_selection(self):
        """Get current selection in tree selector format"""
        selection = {
            'subsystems': [],
            'modules': [],
            'connectors': [],
            'pins': []
        }
        
        def collect_checked_items(item):
            item_type, item_id = item.data(0, Qt.UserRole)
            if item.checkState(0) == Qt.Checked:
                if item_type in selection:
                    selection[item_type + 's'].append(item_id)
            
            for i in range(item.childCount()):
                collect_checked_items(item.child(i))
        
        for i in range(self.component_tree.topLevelItemCount()):
            collect_checked_items(self.component_tree.topLevelItem(i))
        
        return selection

    def validate_and_accept(self):
        """Validate input and accept dialog"""
        new_name = self.name_input.text().strip()
        new_description = self.description_input.text().strip()
        
        if not new_name:
            QMessageBox.warning(self, "Invalid Input", 
                              "Mode name cannot be empty.")
            self.name_input.setFocus()
            return
            
        if len(new_name) > 50:
            QMessageBox.warning(self, "Name Too Long", 
                              "Mode name must be 50 characters or less.")
            self.name_input.setFocus()
            return
            
        # Check for invalid characters
        invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
        if any(char in new_name for char in invalid_chars):
            QMessageBox.warning(self, "Invalid Characters", 
                              "Mode name contains invalid characters.\n"
                              "Please avoid: / \\ : * ? \" < > |")
            self.name_input.setFocus()
            return
        
        # Check if mode already exists (for new modes or name changes)
        if not self.is_edit_mode or new_name != self.mode_name:
            try:
                existing_modes = get_all_modes()
                if new_name in existing_modes:
                    QMessageBox.warning(self, "Mode Exists", 
                                      f"A mode with name '{new_name}' already exists.\n"
                                      "Please choose a different name.")
                    self.name_input.setFocus()
                    return
            except Exception as e:
                QMessageBox.warning(self, "Database Error", 
                                  f"Could not check existing modes: {e}")
                return
        
        # Check if at least one module is selected
        self.update_selected_modules()
        if not self.selected_modules:
            QMessageBox.warning(self, "No Modules Selected", 
                              "Please select at least one module for the mode.")
            return
        
        # Store data for parent to access
        self.final_name = new_name
        self.final_description = new_description
        
        self.accept()

    def get_mode_info(self):
        """Get the mode name and description"""
        return getattr(self, 'final_name', ''), getattr(self, 'final_description', '')
    
    def apply_access_policy(self):
        def _set_enabled(name: str, enabled: bool):
            w = getattr(self, name, None)
            if w is not None:
                w.setEnabled(enabled)

        can_edit_modes = auth.has_perm("mode.edit")
        # Adjust names to your actual buttons:
        _set_enabled("btn_mode_new", can_edit_modes)
        _set_enabled("btn_mode_save", can_edit_modes)
        _set_enabled("btn_mode_delete", can_edit_modes)



class ModernListWidget(QListWidget):
    """Enhanced modern styled list widget with better theme support"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode_checkboxes = {}
        
        # Apply initial theme styles
        self.apply_theme_styles()
        
        # Connect to theme changes
        style_manager.theme_changed.connect(self.apply_theme_styles)

    def apply_theme_styles(self):
        """Apply enhanced theme-based styles to list widget"""
        # Helper function for rgba colors
        def hex_to_rgba(hex_color, alpha=20):
            color = QColor(hex_color)
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        
        list_style = f"""
            QListWidget {{
                background: {hex_to_rgba(theme_manager.get_color('primary_dark'), 15)};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_MEDIUM};
                border: none;
                outline: none;
                selection-background-color: {hex_to_rgba(theme_manager.get_color('accent'), 40)};
                show-decoration-selected: 1;
                alternate-background-color: transparent;
            }}
            QListWidget::item {{
                background: {hex_to_rgba("#ffffff", 5)};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('accent'), 8)};
                padding: {Spacing.MD} {Spacing.SM};
                margin: 1px 0px;
                border-radius: {BorderRadius.MEDIUM};
                min-height: 22px;
            }}
            QListWidget::item:hover {{
                background: {hex_to_rgba(theme_manager.get_color('accent'), 20)};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('accent'), 40)};
                color: white;
            }}
            QListWidget::item:selected {{
                background: {hex_to_rgba(theme_manager.get_color('accent'), 35)};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('accent'), 70)};
                color: white;
                font-weight: {Typography.WEIGHT_BOLD};
            }}
        """
        self.setStyleSheet(list_style)
        
        # Update checkbox styles for existing items
        self.update_checkbox_styles()

    def update_checkbox_styles(self):
        """Update enhanced checkbox styles for all items"""
        # Helper function for rgba colors
        def hex_to_rgba(hex_color, alpha=20):
            color = QColor(hex_color)
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        
        checkbox_style = f"""
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 6px;
                border: 2px solid {hex_to_rgba(theme_manager.get_color('text_secondary'), 80)};
                background: {hex_to_rgba("#ffffff", 25)};
                margin: 2px;
            }}
            QCheckBox::indicator:unchecked {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('text_secondary'), 60)};
                background: {hex_to_rgba("#ffffff", 15)};
            }}
            QCheckBox::indicator:unchecked:hover {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('accent'), 80)};
                background: {hex_to_rgba(theme_manager.get_color('accent'), 30)};
            }}
            QCheckBox::indicator:checked {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('accent'), 80)};
                background: {theme_manager.get_color('accent')}; /* رنگ اصلی accent بدون شفافیت */
            }}
            QCheckBox::indicator:checked:hover {{
                border: 2px solid {theme_manager.get_color('accent')};
                background: {theme_manager.get_color('accent')};
            }}
        """
        
        for mode_name, components in self.mode_checkboxes.items():
            if 'checkbox' in components:
                components['checkbox'].setStyleSheet(checkbox_style)
    
    def get_current_checkbox_style(self):
        """Get the current checkbox style string"""
        def hex_to_rgba(hex_color, alpha=20):
            color = QColor(hex_color)
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        
        return f"""
            QCheckBox::indicator {{
                width: 16px; 
                height: 16px; 
                border-radius: 6px; 
                border: 2px solid {hex_to_rgba(theme_manager.get_color('text_secondary'), 80)};
                background: {hex_to_rgba("#ffffff", 25)};
                margin: 4px; 
            }}
            QCheckBox::indicator:unchecked {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('text_secondary'), 60)};
                background: {hex_to_rgba("#ffffff", 15)};
            }}
            QCheckBox::indicator:unchecked:hover {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('accent'), 80)};
                background: {hex_to_rgba(theme_manager.get_color('accent'), 30)};
            }}
            QCheckBox::indicator:checked {{
                border: 2px solid {hex_to_rgba(theme_manager.get_color('accent'), 80)};
                background: {theme_manager.get_color('accent')}; 
            }}
            QCheckBox::indicator:checked:hover {{
                border: 2px solid {theme_manager.get_color('accent')};
                background: {theme_manager.get_color('accent')};
            }}
        """
        
    def add_mode_item(self, mode_name):
        """Add a mode item with enhanced checkbox styling"""
        item_widget = QWidget()
        item_layout = QHBoxLayout(item_widget)
        item_layout.setContentsMargins(6, 6, 6, 6)  # Increased vertical margins for bigger checkboxes
        
        # Checkbox with enhanced theme styling
        checkbox = QCheckBox()
        # Apply consistent checkbox styling
        checkbox.setStyleSheet(self.get_current_checkbox_style())
        
        # Label with theme colors
        label = QLabel(f"🎛️ {mode_name}")
        label.setFont(QFont("Roboto Mono", 14, QFont.Medium))
        label.setStyleSheet(f"""
            color: {theme_manager.get_color('text_primary')}; 
            background: transparent; 
            border: none; 
            margin-left: 8px;
        """)
        
        item_layout.addWidget(checkbox)
        item_layout.addWidget(label)
        item_layout.addStretch()
        
        # Create list item with proper height (increased for bigger checkboxes)
        list_item = QListWidgetItem()
        list_item.setSizeHint(QSize(0, 60))  # Increased from 35 to 50 for bigger checkboxes

        self.addItem(list_item)
        self.setItemWidget(list_item, item_widget)
        
        # Store checkbox reference with item widget reference
        self.mode_checkboxes[mode_name] = {
            'checkbox': checkbox,
            'label': label,
            'item_widget': item_widget,
            'list_item': list_item
        }
        
        return checkbox
    
    def highlight_current_mode(self, current_mode):
        """Highlight current mode with enhanced theme colors"""
        # Helper function for rgba colors
        def hex_to_rgba(hex_color, alpha=20):
            color = QColor(hex_color)
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        
        for mode_name, components in self.mode_checkboxes.items():
            checkbox = components['checkbox']
            label = components['label']
            item_widget = components['item_widget']
            
            if mode_name == current_mode:
                # Current mode: bright and checked
                checkbox.setChecked(True)
                checkbox.setEnabled(False)
                # Use the same style but ensure it's applied
                self.update_checkbox_styles()
                
                label.setStyleSheet(f"""
                    color: {theme_manager.get_color('accent')}; 
                    background: transparent; 
                    border: none; 
                    font-weight: bold;
                """)
                item_widget.setStyleSheet(f"""
                    QWidget {{
                        background: {hex_to_rgba(theme_manager.get_color('accent'), 35)};
                        border: 1px solid {hex_to_rgba(theme_manager.get_color('accent'), 70)};
                        border-radius: {BorderRadius.MEDIUM};
                    }}
                """)
            else:
                # Other modes: dimmed
                checkbox.setChecked(False)
                checkbox.setEnabled(True)
                self.update_checkbox_styles()  # Apply current theme
                
                # Get RGB values for dimming
                text_color = theme_manager.get_color('text_primary')
                color = QColor(text_color)
                dimmed_color = f"rgba({color.red()}, {color.green()}, {color.blue()}, 100)"
                
                label.setStyleSheet(f"""
                    color: {dimmed_color}; 
                    background: transparent; 
                    border: none;
                """)
                item_widget.setStyleSheet(f"""
                    QWidget {{
                        background: {hex_to_rgba("#ffffff", 2)};
                        border: 1px solid {hex_to_rgba(theme_manager.get_color('accent'), 4)};
                        border-radius: {BorderRadius.MEDIUM};
                    }}
                """)
            
            # Apply the updated checkbox style to this specific checkbox
            checkbox.setStyleSheet(self.get_current_checkbox_style())
    def clear_highlighting(self):
        """Clear all highlighting and restore normal appearance"""
        # Helper function for rgba colors
        def hex_to_rgba(hex_color, alpha=20):
            color = QColor(hex_color)
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        
        for mode_name, components in self.mode_checkboxes.items():
            checkbox = components['checkbox']
            label = components['label']
            item_widget = components['item_widget']
            
            checkbox.setChecked(False)
            checkbox.setEnabled(True)
            # Apply consistent checkbox styling
            checkbox.setStyleSheet(self.get_current_checkbox_style())
            
            label.setStyleSheet(f"""
                color: {theme_manager.get_color('text_primary')}; 
                background: transparent; 
                border: none;
            """)
            item_widget.setStyleSheet(f"""
                QWidget {{
                    background: {hex_to_rgba("#ffffff", 5)};
                    border: 1px solid {hex_to_rgba(theme_manager.get_color('accent'), 8)};
                    border-radius: {BorderRadius.MEDIUM};
                }}
            """)
    
    def get_selected_modes(self):
        """Get list of selected mode names (excluding currently active mode)"""
        return [mode_name for mode_name, components in self.mode_checkboxes.items() 
                if components['checkbox'].isChecked() and components['checkbox'].isEnabled()]
    
    def clear_selections(self):
        """Clear all checkbox selections (but keep current mode highlighting)"""
        for mode_name, components in self.mode_checkboxes.items():
            checkbox = components['checkbox']
            if checkbox.isEnabled():  # Only clear if not current mode
                checkbox.setChecked(False)
    
    def clear(self):
        """Override clear to also clear checkbox references"""
        super().clear()
        self.mode_checkboxes.clear()


class ModeGraphics(QWidget):
    """Enhanced mode graphics widget with improved styling"""
    
    modeEnterRequested = pyqtSignal()
    modeCreateRequested = pyqtSignal()
    modeEditRequested = pyqtSignal()
    modeDeleteRequested = pyqtSignal()
    modeExitRequested = pyqtSignal()
    modeSaveRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode_manager = None
        self.current_mode = None
        
        # Set transparency attributes
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        
        # Apply initial theme styles
        self.apply_theme_styles()
        
        # Connect to theme changes
        style_manager.theme_changed.connect(self.apply_theme_styles)
        
        self.init_ui()
        self.refresh_modes()

    def apply_theme_styles(self):
        """Apply enhanced theme-based styles to mode graphics"""
        # Helper function for rgba colors
        def hex_to_rgba(hex_color, alpha=20):
            color = QColor(hex_color)
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        
        main_style = f"""
            ModeGraphics {{
                background: {hex_to_rgba(theme_manager.get_color('primary_medium'), 20)};
                border: 1px solid {hex_to_rgba(theme_manager.get_color('primary_light'), 60)};
                border-radius: {BorderRadius.XLARGE};
            }}
        """
        self.setStyleSheet(main_style)

    def init_ui(self):
        """Initialize the user interface with enhanced styling"""
        font = QFont("Roboto Mono", 13, QFont.Medium)
        self.setFont(font)
        self.setObjectName("ModeContainer")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)  # Consistent with tree selector
        layout.setSpacing(6)  # Consistent spacing
        
        # Header widget
        self.create_header(layout)
        
        # Mode list with reduced height
        self.mode_list = ModernListWidget()
        self.mode_list.setFont(font)
        self.mode_list.setAlternatingRowColors(False)
        self.mode_list.setMaximumHeight(300)  # Limit the height
        self.mode_list.setMinimumHeight(200)  # Set minimum height
        layout.addWidget(self.mode_list)
        
        # Create mode buttons
        self.create_mode_buttons(layout)

    def create_header(self, layout):
        """Create enhanced header widget matching tree selector style"""
        header_widget = QWidget()
        header_widget.setFixedHeight(32)  # Match tree selector height
        header_widget.setAttribute(Qt.WA_TranslucentBackground, True)
        
        # Apply theme-based header style like tree selector
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
        header_layout.setContentsMargins(10, 0, 10, 0)  # Match tree selector
        
        header_label = QLabel("🎛️ Mode Manager")
        header_label.setFont(QFont("Roboto Mono", 15, QFont.Bold))  # Match tree selector
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        
        layout.addWidget(header_widget)

    def create_mode_buttons(self, layout):
        """Create enhanced control buttons matching tree selector style"""
        control_widget = QWidget()
        control_widget.setFixedHeight(45)  # Match tree selector height
        control_widget.setAttribute(Qt.WA_TranslucentBackground, True)
        
        # Apply theme-based control style like tree selector
        control_widget.setStyleSheet(f"""
            QWidget {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                border-radius: {BorderRadius.LARGE};
                border: 1px solid {theme_manager.get_color('primary_light')};
            }}
        """)
        
        control_layout = QHBoxLayout(control_widget)
        control_layout.setContentsMargins(6, 6, 6, 6)  # Match tree selector
        control_layout.setSpacing(6)  # Match tree selector spacing
        
        # Create styled buttons with consistent sizes like tree selector
        self.create_mode_btn = create_styled_button("➕", "small")
        self.edit_mode_btn = create_styled_button("✏️", "small")
        self.save_pos_btn = create_styled_button("💾", "small")
        self.enter_exit_btn = create_styled_button("🚪", "small")
        self.delete_mode_btn = create_styled_button("🗑️", "small")
        
        # Set consistent sizes matching tree selector (35, 28)
        buttons = [self.create_mode_btn, self.edit_mode_btn, self.save_pos_btn, 
                  self.enter_exit_btn, self.delete_mode_btn]
        for btn in buttons:
            btn.setFixedSize(35, 28)  # Match tree selector button sizes
        
        # Set tooltips
        self.create_mode_btn.setToolTip("Create new mode")
        self.edit_mode_btn.setToolTip("Edit current mode")
        self.save_pos_btn.setToolTip("Save current positions")
        self.enter_exit_btn.setToolTip("Enter selected mode")
        self.delete_mode_btn.setToolTip("Delete selected modes")
        
        # Connect signals
        self.create_mode_btn.clicked.connect(self.on_create_mode)
        self.edit_mode_btn.clicked.connect(self.on_edit_mode)
        self.save_pos_btn.clicked.connect(self.on_save_positions)
        self.enter_exit_btn.clicked.connect(self.on_enter_exit_mode)
        self.delete_mode_btn.clicked.connect(self.on_delete_mode)
        
        # Add buttons to row with center alignment like tree selector
        control_layout.addStretch(1)  # Left spacer
        control_layout.addWidget(self.create_mode_btn)
        control_layout.addWidget(self.edit_mode_btn)
        control_layout.addWidget(self.save_pos_btn)
        control_layout.addWidget(self.enter_exit_btn)
        control_layout.addWidget(self.delete_mode_btn)
        control_layout.addStretch(1)  # Right spacer
        
        layout.addWidget(control_widget)
        
        # Initial button state
        self.update_button_states(None)

    def set_mode_manager(self, mode_manager):
        """Set mode manager"""
        self.mode_manager = mode_manager
        self.refresh_modes()

    def refresh_modes(self):
        """Refresh the mode list"""
        from database import get_connection, get_current_project_id
        
        self.mode_list.clear()
        
        current_project_id = get_current_project_id()
        if current_project_id is None:
            return
        
        # Get modes from manager or database
        if not self.mode_manager:
            try:
                with get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM modes WHERE project_id = %s ORDER BY name", (current_project_id,))
                    modes = [row[0] for row in cursor.fetchall()]
            except Exception as e:
                print(f"Error loading modes: {e}")
                modes = []
        else:
            try:
                modes = self.mode_manager.get_all_modes()
            except Exception as e:
                print(f"Error getting modes from manager: {e}")
                modes = []
        
        # Add modes with checkboxes
        for mode_name in modes:
            self.mode_list.add_mode_item(mode_name)
            
    def get_selected_modes(self):
        """Get list of selected modes"""
        return self.mode_list.get_selected_modes()

    def clear_selections(self):
        """Clear all selections"""
        self.mode_list.clear_selections()

    def on_create_mode(self):
        """Handle create mode"""
        self.modeCreateRequested.emit()

    def on_edit_mode(self):
        """Handle edit mode"""
        self.modeEditRequested.emit()

    def on_save_positions(self):
        """Inside mode: request full save (modules + positions)."""
        self.modeSaveRequested.emit()

    def on_enter_exit_mode(self):
        """Handle enter/exit mode"""
        if self.current_mode:
            # Currently in a mode, so exit
            self.modeExitRequested.emit()
        else:
            # Not in a mode, so enter
            self.modeEnterRequested.emit()

    def on_delete_mode(self):
        """Handle delete mode"""
        self.modeDeleteRequested.emit()

    def update_button_states(self, current_mode):
        self.current_mode = current_mode
        in_mode = (current_mode is not None)

        # show/hide
        self.create_mode_btn.setVisible(not in_mode)   # New
        self.delete_mode_btn.setVisible(not in_mode)   # Delete

        self.save_pos_btn.setVisible(in_mode)          # Save only in mode
        self.edit_mode_btn.setVisible(False)           # HIDE Edit entirely

        self.enter_exit_btn.setVisible(True)
        self.enter_exit_btn.setToolTip(f"Exit mode '{current_mode}'" if in_mode else "Enter selected mode")

        # highlight current mode in list
        if in_mode:
            self.mode_list.highlight_current_mode(current_mode)
        else:
            self.mode_list.clear_highlighting()
        self.apply_access_policy()

    def paintEvent(self, event):
        """Paint event to ensure proper transparency"""
        super().paintEvent(event)


    def apply_access_policy(self):
        """
        Only 'system' can create/delete/save.
        Everyone can enter/exit and view.
        """
        try:
            is_sys = bool(getattr(auth, "is_system", lambda: False)())
        except Exception:
            is_sys = False

        # Create/Delete only for system
        if hasattr(self, "create_mode_btn"):
            self.create_mode_btn.setEnabled(is_sys)
        if hasattr(self, "delete_mode_btn"):
            self.delete_mode_btn.setEnabled(is_sys)

        if hasattr(self, "save_pos_btn"):
            self.save_pos_btn.setEnabled(is_sys)

        if hasattr(self, "enter_exit_btn"):
            self.enter_exit_btn.setEnabled(True)
