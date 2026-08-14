# -----------------------------------------------------------------------------
# mode_manager.py - Core Mode Management Logic
# -----------------------------------------------------------------------------

import json
import sys
import os
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer
from PyQt5.QtWidgets import QMessageBox

from database import (
    get_connection, get_all_modes,
    get_mode_modules, save_mode_positions, get_mode_positions,
    clear_mode_positions
)
from auth_manager import auth
from suggestions import propose_mode_change


# Import here to avoid circular imports
from Schematic_View_tab.mode.mode_ui import SimpleNameDialog


class ModeController(QObject):
    """Mode management controller with theme support"""
    
    modeEntered = pyqtSignal(str)
    modeExited = pyqtSignal()
    modeCreated = pyqtSignal(str)
    modeUpdated = pyqtSignal(str)
    modeDeleted = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode_manager = None
        self.scene = None
        self.mode_graphics = None
        self.current_mode = None
        self.tree_selector = None    
        self._normal_selection = None 
        
    def set_mode_manager(self, mode_manager):
        """Set mode manager"""
        self.mode_manager = mode_manager
        
    def set_scene(self, scene):
        """Set scene"""
        self.scene = scene
    
    def set_tree_selector(self, tree_selector):
        """Provide access to the component tree (for blank scene, restore, etc.)."""
        self.tree_selector = tree_selector
        
    def set_mode_graphics(self, mode_graphics):
        """Set mode graphics widget"""
        self.mode_graphics = mode_graphics
        
    def connect_signals(self):
        """Connect mode graphics signals to controller"""
        if self.mode_graphics:
            self.mode_graphics.modeEnterRequested.connect(self.handle_mode_enter)
            self.mode_graphics.modeCreateRequested.connect(self.handle_mode_creation)
            self.mode_graphics.modeDeleteRequested.connect(self.handle_mode_delete)
            self.mode_graphics.modeExitRequested.connect(self.handle_mode_exit)
            self.mode_graphics.modeSaveRequested.connect(self.handle_mode_save)

    def handle_mode_enter(self):
        """Process mode enter based on selected modes"""
        if not self.mode_graphics:
            return
            
        selected_modes = self.mode_graphics.get_selected_modes()
        
        if len(selected_modes) == 0:
            QMessageBox.warning(self.parent(), "No Selection", 
                              "Please select a mode to enter.")
            return
            
        if len(selected_modes) > 1:
            QMessageBox.warning(self.parent(), "Multiple Selection", 
                              "Cannot enter multiple modes simultaneously.\n"
                              "Please select only one mode.")
            return
        
        mode_name = selected_modes[0]
        self._enter_mode(mode_name)
    
    def _enter_mode(self, mode_name):
        """Enter specified mode"""
        if not self.mode_manager:
            self._show_error("Mode manager not available")
            return
            
        try:
            if self.mode_manager.enter_mode(mode_name):
                self.current_mode = mode_name
                self._update_ui_state(mode_name)
                self.modeEntered.emit(mode_name)

                # Cache normal selection, then set tree to mode selection
                if self.tree_selector and hasattr(self.tree_selector, "get_checked_ids"):
                    self._normal_selection = self.tree_selector.get_checked_ids()
                try:
                    mode_modules = self.mode_manager.get_mode_modules(mode_name)
                    if self.tree_selector and hasattr(self.tree_selector, "refresh_tree"):
                        self.tree_selector.refresh_tree()
                    # apply selection on tree
                    if self.tree_selector and hasattr(self.tree_selector, "apply_selection"):
                        self.tree_selector.apply_selection({
                            'subsystems': [],
                            'modules': mode_modules,
                            'connectors': [],
                            'pins': []
                        })
                except Exception:
                    pass

                def _fit():
                    if self.scene:
                        for v in self.scene.views():
                            br = self.scene.itemsBoundingRect().adjusted(-50, -50, 50, 50)
                            if not br.isNull():
                                v.fitInView(br, Qt.KeepAspectRatio)
                QTimer.singleShot(120, _fit)

                self._show_success_message(mode_name)
            else:
                self._show_error(f"Failed to enter mode: {mode_name}")
        except Exception as e:
            self._show_error(f"Error entering mode: {e}")
    
    def handle_mode_creation(self):
        """New: ask only for name, then blank scene for live build."""
        if not auth.is_logged_in():
            QMessageBox.warning(self.parent(), "Access denied", "Please log in first.")
            return

        if not self.mode_manager:
            self._show_error("Mode manager not available")
            return
        
        # Only ask for name
        name_dlg = SimpleNameDialog(parent=self.parent(), title="Create Mode")
        if name_dlg.exec_() != name_dlg.Accepted:
            return
        mode_name = name_dlg.get_name()

        # Validate not exists
        try:
            existing = self.mode_manager.get_all_modes()
            if mode_name in existing:
                self._show_error(f"Mode '{mode_name}' already exists.")
                return
        except Exception as e:
            self._show_error(f"DB error: {e}")
            return

        self.current_mode = mode_name

        if self.mode_manager:
            self.mode_manager.current_mode = mode_name

        if self.tree_selector and hasattr(self.tree_selector, "get_checked_ids"):
            self._normal_selection = self.tree_selector.get_checked_ids()

        if self.tree_selector and hasattr(self.tree_selector, "deselect_all_items"):
            self.tree_selector.deselect_all_items()

        if self.scene and hasattr(self.scene, "update_display_from_selection"):
            self.scene.update_display_from_selection({'modules': [], 'connectors': [], 'pins': []})

        try:
            if self.scene:
                def _fit_blank():
                    for v in self.scene.views():
                        br = self.scene.itemsBoundingRect().adjusted(-50, -50, 50, 50)
                        if not br.isNull():
                            v.fitInView(br, Qt.KeepAspectRatio)
                QTimer.singleShot(50, _fit_blank)
        except Exception:
            pass

        # Update UI to "inside mode" (Save + Exit visible)
        self._update_ui_state(self.current_mode)
        QMessageBox.information(self.parent(), "Build Mode",
                                f"Now building mode: {mode_name}\n"
                                f"Select modules in the tree and arrange them; click 💾 to save.")
    
    def handle_mode_delete(self):
        """Process mode deletion"""
        if not auth.is_logged_in():
            QMessageBox.warning(self.parent(), "Access denied", "Please log in first.")
            return

        if not self.mode_manager:
            self._show_error("Mode manager not available")
            return
        
        if not self.mode_graphics:
            return
            
        selected_modes = self.mode_graphics.get_selected_modes()
        
        if len(selected_modes) == 0:
            QMessageBox.warning(self.parent(), "No Selection", 
                              "Please select a mode to delete.")
            return
        
        if len(selected_modes) == 1:
            mode_name = selected_modes[0]
            reply = QMessageBox.question(self.parent(), "Confirm Delete", 
                                       f"Are you sure you want to delete mode '{mode_name}'?",
                                       QMessageBox.Yes | QMessageBox.No,
                                       QMessageBox.No)
        else:
            mode_names = ", ".join(selected_modes)
            reply = QMessageBox.question(self.parent(), "Confirm Delete", 
                                       f"Are you sure you want to delete these {len(selected_modes)} modes?\n{mode_names}",
                                       QMessageBox.Yes | QMessageBox.No,
                                       QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            deleted_modes = []
            for mode_name in selected_modes:
                if self.mode_manager.delete_mode(mode_name):
                    deleted_modes.append(mode_name)
                    
                    # If current mode was deleted, exit it
                    if self.current_mode == mode_name:
                        self.current_mode = None
                        self._update_ui_state(None)
                        self.modeExited.emit()
                    
                    self.modeDeleted.emit(mode_name)
            
            if deleted_modes:
                if auth.is_system():
                    msg = (f"Mode '{deleted_modes[0]}' deleted successfully!"
                           if len(deleted_modes) == 1
                           else f"{len(deleted_modes)} modes deleted successfully!")
                else:
                    msg = (f"Mode '{deleted_modes[0]}' delete submitted for approval."
                           if len(deleted_modes) == 1
                           else f"{len(deleted_modes)} mode deletes submitted for approval.")
                QMessageBox.information(self.parent(), "Success", msg)
                self._refresh_mode_list()
            else:
                self._show_error("Failed to delete selected modes")
    
    def handle_mode_exit(self):
        """Process exit from current mode"""
        if not self.mode_manager:
            self._show_error("Mode manager not available")
            return

        if self.current_mode:
            current_mode_name = self.current_mode
            if self.mode_manager.exit_mode():
                self.current_mode = None
                self._update_ui_state(None)
                self.modeExited.emit()

                try:
                    if self.tree_selector and hasattr(self.tree_selector, "apply_selection"):
                        if self._normal_selection:
                            self.tree_selector.apply_selection(self._normal_selection)
                        elif hasattr(self.tree_selector, "deselect_all_items"):
                            self.tree_selector.deselect_all_items()
                except Exception:
                    pass

                self._refresh_mode_list()

                try:
                    def _fit_default():
                        if self.scene:
                            for v in self.scene.views():
                                br = self.scene.itemsBoundingRect().adjusted(-50, -50, 50, 50)
                                if not br.isNull():
                                    v.fitInView(br, Qt.KeepAspectRatio)
                    QTimer.singleShot(160, _fit_default)
                except Exception:
                    pass

                QMessageBox.information(self.parent(), "Mode Exited",
                                        f"✅ Successfully exited mode: {current_mode_name}")
            else:
                self._show_error("Failed to exit mode")
        else:
            QMessageBox.information(self.parent(), "No Mode", "No mode is currently active to exit.")
            
    def handle_mode_save(self):
        """Persist current tree selection as mode's modules, then save positions."""
        if not auth.is_logged_in():
            QMessageBox.warning(self.parent(), "Access denied", "Please log in first.")
            return

        if not self.current_mode:
            QMessageBox.information(self.parent(), "No Mode", "You're not inside a mode.")
            return
        if not self.mode_manager:
            self._show_error("Mode manager not available")
            return
        if not self.tree_selector or not hasattr(self.tree_selector, "get_checked_ids"):
            self._show_error("Tree selector not available")
            return

        selection = self.tree_selector.get_checked_ids()
        module_ids = selection.get('modules', []) or []
        if not module_ids:
            QMessageBox.warning(self.parent(), "Empty", "Select at least one module for this mode.")
            return

        # 1) upsert mode_modules (system admins apply now, others suggest)
        ok, msg = propose_mode_change("create", self.current_mode, module_ids)
        if not ok:
            self._show_error(msg)
            return

        # 2) save current positions to mode_positions (only when the mode is
        # actually in the DB — pending suggestions aren't approved yet)
        try:
            if auth.is_system():
                self.mode_manager.save_current_positions_to_mode(self.current_mode)
                QMessageBox.information(self.parent(), "Saved",
                                        f"Mode '{self.current_mode}' saved (modules + positions).")
            else:
                QMessageBox.information(self.parent(), "Submitted",
                                        f"Mode '{self.current_mode}' modules submitted for approval.\n"
                                        f"Save again after the system admin approves it to capture the position layout.")
            # refresh list (in case first time saved)
            self._refresh_mode_list()
        except Exception as e:
            self._show_error(f"Saving positions failed: {e}")

    def save_current_mode_positions(self):
        """Save current mode positions"""
        if self.mode_manager and self.current_mode:
            try:
                self.mode_manager.save_mode_positions()
                QMessageBox.information(self.parent(), "Positions Saved", 
                                      f"Positions saved for mode '{self.current_mode}'!")
            except Exception as e:
                self._show_error(f"Could not save positions: {e}")
        else:
            QMessageBox.warning(self.parent(), "No Active Mode", 
                              "No mode is currently active to save positions for.")
    
    def get_current_mode_status(self):
        """Get current mode status"""
        if self.mode_manager:
            current_mode = self.mode_manager.get_current_mode()
            all_modes = self.mode_manager.get_all_modes()
            
            status = f"Current Mode: {current_mode or 'None'}\n"
            status += f"Available Modes: {', '.join(all_modes) if all_modes else 'None'}\n"
            
            if current_mode:
                mode_modules = self.mode_manager.get_mode_modules(current_mode)
                status += f"Mode Modules: {mode_modules}"
            
            return status
        else:
            return "Mode manager not available"
    
    def _update_ui_state(self, current_mode):
        """Update UI state"""
        if self.mode_graphics:
            self.mode_graphics.update_button_states(current_mode)
            self.mode_graphics.clear_selections()
            
            # Update highlighting based on current mode
            if current_mode:
                self.mode_graphics.mode_list.highlight_current_mode(current_mode)
            else:
                self.mode_graphics.mode_list.clear_highlighting()
    
    def _refresh_mode_list(self):
        """Refresh mode list"""
        if self.mode_graphics:
            self.mode_graphics.refresh_modes()
    
    def _show_error(self, message):
        """Show error message"""
        QMessageBox.warning(self.parent(), "Error", message)
    
    def _show_success_message(self, mode_name):
        """Show success message for entering mode"""
        QMessageBox.information(self.parent(), "Mode Entered", 
                              f"✅ Successfully entered mode: {mode_name}\n\n"
                              f"You can now:\n"
                              f"• Move modules and connections\n"
                              f"• Click '💾 Save' to save positions\n"
                              f"• Click '🚪 Exit' to return to default view")


class ModeManager(QObject):
    """Main class for managing modes with theme support"""
    
    # Signals
    modeCreated = pyqtSignal(str)  # mode_name
    modeDeleted = pyqtSignal(str)  # mode_name
    modeEntered = pyqtSignal(str)  # mode_name
    modeExited = pyqtSignal()
    modeListUpdated = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.current_mode = None
        self.scene = None
    
    def set_scene(self, scene):
        """Set the graphics scene reference"""
        self.scene = scene
    
    def get_all_modes(self):
        """Get list of all mode names"""
        return get_all_modes()
    
    def create_mode(self, mode_name, module_ids):
        """Create/update a mode — admins apply now, others submit a suggestion."""
        success, _msg = propose_mode_change("create", mode_name, module_ids)
        if success:
            self.modeCreated.emit(mode_name)
            self.modeListUpdated.emit()
        return success
    
    def delete_mode(self, mode_name):
        """Delete a mode and all its data — admins apply now, others suggest."""
        success, _msg = propose_mode_change("delete", mode_name)
        if success:
            self.modeDeleted.emit(mode_name)
            self.modeListUpdated.emit()
        return success
    
    def get_mode_modules(self, mode_name):
        """Get list of module IDs in a mode"""
        return get_mode_modules(mode_name)
    
    def enter_mode(self, mode_name):
        """Enter a specific mode with correct operation order"""
        if mode_name not in self.get_all_modes():
            return False
        
        # Save current positions before changing scene state
        if self.scene:
            self.save_current_positions_to_mode("__default__")
        
        # Update scene to show mode items
        if self.scene:
            self.update_scene_for_mode(mode_name)
            
            # Delayed position loading to ensure scene is updated
            def delayed_position_load():
                self.load_mode_positions(mode_name)
                self.current_mode = mode_name
                self.modeEntered.emit(mode_name)

                try:
                    def _fit():
                        if self.scene:
                            for v in self.scene.views():
                                br = self.scene.itemsBoundingRect().adjusted(-50, -50, 50, 50)
                                if not br.isNull():
                                    v.fitInView(br, Qt.KeepAspectRatio)
                    QTimer.singleShot(10, _fit)
                except Exception:
                    pass
            
            QTimer.singleShot(100, delayed_position_load)
        else:
            self.current_mode = mode_name
            self.modeEntered.emit(mode_name)
        
        return True
    
    def exit_mode(self):
        """Exit current mode and return to default view"""
        if not self.current_mode:
            return False
        
        # Save current mode positions before changing scene
        if self.scene:
            self.save_current_positions_to_mode(self.current_mode)
        
        # Update scene to show all items
        if self.scene:
            self.update_scene_for_default()
            
            # Delayed position loading to ensure scene is updated
            def delayed_default_load():
                self.load_mode_positions("__default__")
                try:
                    def _fit():
                        if self.scene:
                            for v in self.scene.views():
                                br = self.scene.itemsBoundingRect().adjusted(-50, -50, 50, 50)
                                if not br.isNull():
                                    v.fitInView(br, Qt.KeepAspectRatio)
                    QTimer.singleShot(10, _fit)
                except Exception:
                    pass
            
            QTimer.singleShot(100, delayed_default_load)
        
        self.current_mode = None
        self.modeExited.emit()
        return True
    
    def save_current_positions_to_mode(self, mode_name):
        """Save current positions for the specified mode using database functions"""
        from database import get_connection, get_current_project_id
        
        if not self.scene:
            return
        
        current_project_id = get_current_project_id()
        if current_project_id is None:
            return
        
        module_positions = {}
        connector_positions = {}
        interface_positions = {}
        
        # Collect module positions
        if hasattr(self.scene, 'module_graphics_items') and self.scene.module_graphics_items:
            for mod_id, item in self.scene.module_graphics_items.items():
                if item.isVisible():
                    pos = item.pos()
                    module_positions[mod_id] = (pos.x(), pos.y())
        
        # Collect connector positions
        connector_types = ['BottomConnector', 'RightConnector', 'TopConnector', 'LeftConnector']
        scene_connectors = [item for item in self.scene.items() 
                        if type(item).__name__ in connector_types and item.isVisible()]
        
        if scene_connectors:
            try:
                with get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM connectors WHERE project_id = %s ORDER BY id", (current_project_id,))
                    db_connectors = [row[0] for row in cursor.fetchall()]
                    
                for i, scene_item in enumerate(scene_connectors):
                    if i < len(db_connectors):
                        connector_id = db_connectors[i]
                        pos = scene_item.pos()
                        rect = scene_item.boundingRect()
                        connector_positions[connector_id] = (pos.x(), pos.y(), 
                                                        rect.width(), rect.height())
            except Exception as e:
                print(f"Error collecting connector positions: {e}")
        
        # Collect interface positions
        scene_interfaces = [item for item in self.scene.items() 
                        if type(item).__name__ == 'InteractiveSegment' and item.isVisible()]
        
        if scene_interfaces:
            try:
                with get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM Interfaces WHERE project_id = %s ORDER BY id", (current_project_id,))
                    db_interfaces = [row[0] for row in cursor.fetchall()]
                    
                for i, scene_item in enumerate(scene_interfaces):
                    if i < len(db_interfaces):
                        interface_id = db_interfaces[i]
                        pos = scene_item.pos()
                        rotation = scene_item.rotation() if hasattr(scene_item, 'rotation') else 0
                        
                        # Handle cases where position is (0,0) - try to get line center
                        scene_x, scene_y = pos.x(), pos.y()
                        if scene_x == 0 and scene_y == 0:
                            if hasattr(scene_item, 'line'):
                                try:
                                    line = scene_item.line()
                                    if hasattr(line, 'p1') and hasattr(line, 'p2'):
                                        p1, p2 = line.p1(), line.p2()
                                        scene_x = (p1.x() + p2.x()) / 2
                                        scene_y = (p1.y() + p2.y()) / 2
                                except:
                                    pass
                        
                        interface_positions[interface_id] = (scene_x, scene_y, rotation)
            except Exception as e:
                print(f"Error collecting interface positions: {e}")
        
        # Save to database
        save_mode_positions(mode_name, module_positions, connector_positions, interface_positions)
            
    def load_mode_positions(self, mode_name):
        """Load positions for the specified mode using database functions"""
        from database import get_connection, get_current_project_id
        
        if not self.scene:
            return
        
        current_project_id = get_current_project_id()
        if current_project_id is None:
            return
        
        module_positions, connector_positions, interface_positions = get_mode_positions(mode_name)
        
        # Apply module positions
        if module_positions and hasattr(self.scene, 'module_graphics_items'):
            for module_id, (x, y) in module_positions.items():
                if module_id in self.scene.module_graphics_items:
                    item = self.scene.module_graphics_items[module_id]
                    if item.isVisible():
                        item.setPos(x, y)
        
        # Apply connector positions
        if connector_positions:
            connector_types = ['BottomConnector', 'RightConnector', 'TopConnector', 'LeftConnector']
            scene_connectors = [item for item in self.scene.items() 
                            if type(item).__name__ in connector_types and item.isVisible()]
            
            try:
                with get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM connectors WHERE project_id = %s ORDER BY id", (current_project_id,))
                    db_connectors = [row[0] for row in cursor.fetchall()]
                    
                for i, scene_item in enumerate(scene_connectors):
                    if i < len(db_connectors):
                        connector_id = db_connectors[i]
                        if connector_id in connector_positions:
                            x, y, width, height = connector_positions[connector_id]
                            scene_item.setPos(x, y)
            except Exception as e:
                print(f"Error applying connector positions: {e}")
        
        # Apply interface positions
        if interface_positions:
            scene_interfaces = [item for item in self.scene.items() 
                            if type(item).__name__ == 'InteractiveSegment' and item.isVisible()]
            
            try:
                with get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM Interfaces WHERE project_id = %s ORDER BY id", (current_project_id,))
                    db_interfaces = [row[0] for row in cursor.fetchall()]
                    
                for i, scene_item in enumerate(scene_interfaces):
                    if i < len(db_interfaces):
                        interface_id = db_interfaces[i]
                        if interface_id in interface_positions:
                            x, y, rotation = interface_positions[interface_id]
                            scene_item.setPos(x, y)
                            if hasattr(scene_item, 'setRotation') and rotation:
                                scene_item.setRotation(rotation)
            except Exception as e:
                print(f"Error applying interface positions: {e}")
        
        # Force scene update
        if hasattr(self.scene, 'update'):
            self.scene.update()
                
    def update_scene_for_mode(self, mode_name):
        """Update scene to show only modules belonging to the mode"""
        if not self.scene:
            return
        
        mode_modules = self.get_mode_modules(mode_name)
        
        if not hasattr(self.scene, 'update_display_from_selection'):
            return
        
        # Create selection dict with only mode modules
        selection = {
            'modules': mode_modules,
            'connectors': [],
            'pins': [],
            'interfaces': []
        }
        
        # Update the scene display
        self.scene.update_display_from_selection(selection)
        
        # Force a scene refresh
        if hasattr(self.scene, 'update'):
            self.scene.update()
    
    def update_scene_for_default(self):
        """Update scene to show all items (default view)"""
        if not self.scene:
            return
        
        if not hasattr(self.scene, 'update_display_from_selection'):
            return
        
        # Show all items by passing empty selection
        self.scene.update_display_from_selection({})
        
        # Force a scene refresh
        if hasattr(self.scene, 'update'):
            self.scene.update()
    
    def get_current_mode(self):
        """Get the name of the current mode, or None if not in a mode"""
        return self.current_mode
    
    def save_mode_positions(self):
        """Save current positions for the active mode"""
        if not self.current_mode:
            return False
        
        self.save_current_positions_to_mode(self.current_mode)
        return True
    
    def is_in_mode(self):
        """Check if currently in a mode"""
        return self.current_mode is not None