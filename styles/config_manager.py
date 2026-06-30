# -----------------------------------------------------------------------------
# config_manager.py - Central application configuration management
# -----------------------------------------------------------------------------

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from PyQt5.QtCore import QObject, pyqtSignal

from styles.theme_manager import ThemeType

class ConfigManager(QObject):
    """Application configuration manager"""
    
    # Configuration change signals
    config_changed = pyqtSignal(str, object)  # key, value
    theme_changed = pyqtSignal(str)  # theme_name
    
    def __init__(self, config_file: str = "app_config.json"):
        super().__init__()
        self.config_file = Path(config_file)
        self.config_data: Dict[str, Any] = {}
        self.load_config()
    
    def get_default_config(self) -> Dict[str, Any]:
        """Default configuration"""
        return {
            "ui": {
                "theme": ThemeType.DARK.value,
                "language": "fa",  # Persian
                "font_scale": 1.0,
                "window_geometry": {
                    "x": 100,
                    "y": 100, 
                    "width": 1300,
                    "height": 900
                },
                "splitter_sizes": [300, 1000],  # Tree view, Main content
                "toolbar_visible": True,
                "statusbar_visible": True,
                "auto_save": True,
                "auto_save_interval": 300  # seconds
            },
            "architecture": {
                "default_mass_unit": "kg",
                "default_power_unit": "W",
                "default_voltage_unit": "V",
                "default_current_unit": "A",
                "show_totals": True,
                "auto_expand_tree": False,
                "highlight_modified": True,
                "backup_on_save": True
            },
            "schematic": {
                "grid_size": 20,
                "snap_to_grid": True,
                "show_grid": True,
                "auto_layout": False,
                "connection_style": "curved",  # "straight", "curved", "orthogonal"
                "node_size": "medium",  # "small", "medium", "large"
                "show_labels": True,
                "show_statistics": True
            },
            "export": {
                "csv_delimiter": ",",
                "csv_encoding": "utf-8",
                "excel_format": "xlsx",
                "include_hierarchy": True,
                "export_path": "exports",
                "filename_timestamp": True
            },
            "database": {
                "auto_backup": True,
                "backup_interval": 24,  # hours
                "max_backups": 10,
                "vacuum_on_startup": False
            },
            "performance": {
                "max_undo_steps": 50,
                "cache_size": 100,  # MB
                "lazy_loading": True,
                "animation_duration": 200  # ms
            }
        }
    
    def load_config(self):
        """Load configuration from file"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.config_data = json.load(f)
                
                # Merge with default configuration for new keys
                default_config = self.get_default_config()
                self._merge_configs(default_config, self.config_data)
                self.config_data = default_config
                
            except (json.JSONDecodeError, Exception) as e:
                print(f"Error loading configuration: {e}")
                self.config_data = self.get_default_config()
        else:
            self.config_data = self.get_default_config()
        
        # Save configuration to ensure file exists
        self.save_config()
    
    def _merge_configs(self, default: dict, user: dict):
        """Merge user configuration with defaults"""
        for key, value in user.items():
            if key in default:
                if isinstance(value, dict) and isinstance(default[key], dict):
                    self._merge_configs(default[key], value)
                else:
                    default[key] = value
    
    def save_config(self):
        """Save configuration to file"""
        try:
            # Create directory if it doesn't exist
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config_data, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            print(f"Error saving configuration: {e}")
    
    def get(self, key_path: str, default=None):
        """Get configuration value using dot notation"""
        keys = key_path.split('.')
        value = self.config_data
        
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default
    
    def set(self, key_path: str, value: Any):
        """Set configuration value using dot notation"""
        keys = key_path.split('.')
        config = self.config_data
        
        # Navigate to the last level
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        
        # Set value
        config[keys[-1]] = value
        
        # Save and emit signal
        self.save_config()
        self.config_changed.emit(key_path, value)
        
        # Check for theme change
        if key_path == "ui.theme":
            self.theme_changed.emit(value)
    
    def get_ui_config(self) -> dict:
        """Get UI configuration"""
        return self.config_data.get("ui", {})
    
    def get_architecture_config(self) -> dict:
        """Get architecture configuration"""
        return self.config_data.get("architecture", {})
    
    def get_schematic_config(self) -> dict:
        """Get schematic configuration"""
        return self.config_data.get("schematic", {})
    
    def get_export_config(self) -> dict:
        """Get export configuration"""
        return self.config_data.get("export", {})
    
    def set_theme(self, theme: ThemeType):
        """Set theme"""
        self.set("ui.theme", theme.value)
    
    def get_theme(self) -> ThemeType:
        """Get current theme"""
        theme_str = self.get("ui.theme", ThemeType.DARK.value)
        try:
            return ThemeType(theme_str)
        except ValueError:
            return ThemeType.DARK
    
    def set_window_geometry(self, x: int, y: int, width: int, height: int):
        """Set window position and size"""
        self.set("ui.window_geometry.x", x)
        self.set("ui.window_geometry.y", y)
        self.set("ui.window_geometry.width", width)
        self.set("ui.window_geometry.height", height)
    
    def get_window_geometry(self) -> tuple:
        """Get window position and size"""
        geo = self.get_ui_config().get("window_geometry", {})
        return (
            geo.get("x", 100),
            geo.get("y", 100),
            geo.get("width", 1300),
            geo.get("height", 900)
        )
    
    def set_splitter_sizes(self, sizes: list):
        """Set splitter sizes"""
        self.set("ui.splitter_sizes", sizes)
    
    def get_splitter_sizes(self) -> list:
        """Get splitter sizes"""
        return self.get("ui.splitter_sizes", [300, 1000])
    
    def reset_to_defaults(self):
        """Reset to default configuration"""
        self.config_data = self.get_default_config()
        self.save_config()
        
        # Emit signals for all settings
        for section, values in self.config_data.items():
            if isinstance(values, dict):
                for key, value in values.items():
                    self.config_changed.emit(f"{section}.{key}", value)
    
    def export_config(self, filepath: str):
        """Export configuration to file"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(self.config_data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error exporting configuration: {e}")
            return False
    
    def import_config(self, filepath: str):
        """Import configuration from file"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                imported_config = json.load(f)
            
            # Merge with current configuration
            default_config = self.get_default_config()
            self._merge_configs(default_config, imported_config)
            self.config_data = default_config
            
            self.save_config()
            
            # Emit signals
            for section, values in self.config_data.items():
                if isinstance(values, dict):
                    for key, value in values.items():
                        self.config_changed.emit(f"{section}.{key}", value)
            
            return True
        except Exception as e:
            print(f"Error importing configuration: {e}")
            return False

# Singleton for easy use
config_manager = ConfigManager()

# Convenience functions
def get_config(key_path: str, default=None):
    """Get configuration"""
    return config_manager.get(key_path, default)

def set_config(key_path: str, value: Any):
    """Set configuration value"""
    config_manager.set(key_path, value)

def get_theme() -> ThemeType:
    """Get current theme"""
    return config_manager.get_theme()

def set_theme(theme: ThemeType):
    """Set theme"""
    config_manager.set_theme(theme)

# Usage examples:
# theme = get_theme()
# set_config("ui.font_scale", 1.2)
# window_size = get_config("ui.window_geometry.width", 1300)