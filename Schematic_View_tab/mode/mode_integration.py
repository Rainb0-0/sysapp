# -----------------------------------------------------------------------------
# mode_integration.py - Mode System Integration and Setup
# -----------------------------------------------------------------------------

from PyQt5.QtCore import QObject, pyqtSignal, QTimer
from PyQt5.QtWidgets import QMessageBox

from Schematic_View_tab.mode.mode_manager import ModeManager, ModeController
from Schematic_View_tab.mode.mode_ui import ModeGraphics


class ModeSystemIntegrator(QObject):
    """Complete system integrator for mode management with theme support"""
    
    systemReady = pyqtSignal()
    systemError = pyqtSignal(str)
    modeChanged = pyqtSignal(str)
    
    def __init__(self, schematic_tab, parent=None):
        super().__init__(parent)
        self.schematic_tab = schematic_tab
        self.is_integrated = False
        self.mode_manager = None
        self.mode_controller = None
        self.mode_graphics = None
    
    def integrate_complete_system(self):
        """Integrate the complete mode system"""
        try:
            self._verify_components()
            self._setup_mode_manager()
            self._setup_mode_controller()
            self._setup_mode_graphics()
            self._connect_all_signals()
            self._final_setup()
            
            self.is_integrated = True
            self.systemReady.emit()
            return True
            
        except Exception as e:
            error_msg = f"Mode system integration failed: {e}"
            self.systemError.emit(error_msg)
            return False
    
    def _verify_components(self):
        """Verify required components exist"""
        required_attrs = ['scene', 'view']
        for attr in required_attrs:
            if not hasattr(self.schematic_tab, attr):
                raise Exception(f"Missing required component: {attr}")
    
    def _setup_mode_manager(self):
        """Setup mode manager"""
        self.mode_manager = ModeManager()
        self.mode_manager.set_scene(self.schematic_tab.scene)
        self.schematic_tab.mode_manager = self.mode_manager
    
    def _setup_mode_controller(self):
        """Setup mode controller"""
        self.mode_controller = ModeController(self.schematic_tab)
        self.mode_controller.set_mode_manager(self.mode_manager)
        self.mode_controller.set_scene(self.schematic_tab.scene)
        self.schematic_tab.mode_controller = self.mode_controller
    
    def _setup_mode_graphics(self):
        """Setup mode graphics widget"""
        self.mode_graphics = ModeGraphics(self.schematic_tab.view)
        self.mode_graphics.setFixedSize(280, 400)  # Slightly taller for better proportion
        self.mode_graphics.hide()
        self.mode_graphics.set_mode_manager(self.mode_manager)
        self.schematic_tab.mode_graphics = self.mode_graphics
    
    def _connect_all_signals(self):
        """Connect all signals between components"""
        if self.mode_controller and self.mode_graphics:
            self.mode_controller.set_mode_graphics(self.mode_graphics)
            
            # Set tree selector reference if available
            if hasattr(self.schematic_tab, 'tree_selector'):
                self.mode_controller.set_tree_selector(self.schematic_tab.tree_selector)
            
            self.mode_controller.connect_signals()
            
            # Connect mode change signals
            if hasattr(self.mode_controller, 'modeEntered'):
                self.mode_controller.modeEntered.connect(self._on_mode_entered)
            if hasattr(self.mode_controller, 'modeExited'):
                self.mode_controller.modeExited.connect(self._on_mode_exited)
    
    def _final_setup(self):
        """Finalize setup"""
        if self.mode_graphics:
            self.mode_graphics.refresh_modes()
            self.mode_graphics.update_button_states(None)
        
        # apply access policy initially
        try:
            from auth_manager import auth
            auth.auth_changed.connect(self.mode_graphics.apply_access_policy)
            self.mode_graphics.apply_access_policy()
        except Exception:
            pass
        
        # Update position after UI is ready
        if hasattr(self.schematic_tab, 'update_mode_graphics_position'):
            QTimer.singleShot(100, self.schematic_tab.update_mode_graphics_position)
    
    def _on_mode_entered(self, mode_name):
        """Handle mode entered"""
        self.modeChanged.emit(mode_name)
    
    def _on_mode_exited(self):
        """Handle mode exited"""
        self.modeChanged.emit(None)
        
        # Refresh mode list to show any changes after exiting mode
        if self.mode_graphics:
            QTimer.singleShot(200, self.mode_graphics.refresh_modes)
    
    def get_integration_status(self):
        """Get integration status"""
        return {
            'integrated': self.is_integrated,
            'mode_manager': self.mode_manager is not None,
            'mode_controller': self.mode_controller is not None,
            'mode_graphics': self.mode_graphics is not None,
            'current_mode': getattr(self.mode_manager, 'current_mode', None) if self.mode_manager else None
        }
    
    def show_integration_status(self):
        """Show integration status dialog"""
        status = self.get_integration_status()
        status_text = "🔧 Mode System Integration Status:\n\n"
        
        for component, available in status.items():
            if component == 'current_mode':
                status_text += f"Current Mode: {available or 'None'}\n"
            else:
                icon = "✅" if available else "❌"
                status_text += f"{icon} {component.replace('_', ' ').title()}\n"
        
        QMessageBox.information(self.schematic_tab, "Integration Status", status_text)
    
    def test_system(self):
        """Test the integrated system"""
        status = self.get_integration_status()
        if not status['integrated']:
            return False
        
        tests_passed = 0
        total_tests = 0
        
        # Test mode manager
        if self.mode_manager:
            total_tests += 1
            try:
                modes = self.mode_manager.get_all_modes()
                tests_passed += 1
            except Exception:
                pass
        
        # Test mode controller
        if self.mode_controller:
            total_tests += 1
            try:
                status_text = self.mode_controller.get_current_mode_status()
                tests_passed += 1
            except Exception:
                pass
        
        # Test mode graphics
        if self.mode_graphics:
            total_tests += 1
            try:
                self.mode_graphics.refresh_modes()
                tests_passed += 1
            except Exception:
                pass
        
        return tests_passed == total_tests


# Integration functions
def integrate_mode_system(schematic_tab):
    """Integrate mode system into schematic tab"""
    integrator = ModeSystemIntegrator(schematic_tab)
    
    if integrator.integrate_complete_system():
        schematic_tab.mode_integrator = integrator
        
        # Test system after a short delay
        QTimer.singleShot(500, integrator.test_system)
        return integrator
    else:
        return None


def debug_mode_system(schematic_tab):
    """Debug mode system integration"""
    if hasattr(schematic_tab, 'mode_integrator'):
        integrator = schematic_tab.mode_integrator
        integrator.show_integration_status()
        integrator.test_system()
    else:
        # Show basic component status
        components = ['mode_manager', 'mode_controller', 'mode_graphics']
        status_msg = "Mode System Debug:\n\n"
        
        for comp in components:
            exists = hasattr(schematic_tab, comp)
            status_msg += f"{'✅' if exists else '❌'} {comp}: {'Found' if exists else 'Missing'}\n"
        
        QMessageBox.information(schematic_tab, "Mode System Debug", status_msg)


# Utility functions for backward compatibility
def get_mode_controller():
    """Get mode controller instance (utility function)"""
    return None


def get_mode_manager():
    """Get mode manager instance (utility function)"""
    return None


# Export main classes for external use
__all__ = [
    'ModeSystemIntegrator',
    'integrate_mode_system',
    'debug_mode_system',
    'get_mode_controller',
    'get_mode_manager'
]