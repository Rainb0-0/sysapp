# -----------------------------------------------------------------------------
# theme_manager.py - مدیریت تم‌های مختلف و تغییر آسان آن‌ها
# -----------------------------------------------------------------------------

from enum import Enum
from styles.design_system import Colors, Typography, Spacing, BorderRadius, Gradients, StyleTemplates

class ThemeType(Enum):
    DARK = "dark"
    LIGHT = "light"
    RED = "red"
    GREEN = "green"

class ThemeColors:
    """رنگ‌های مختلف برای تم‌های مختلف"""
    
    DARK_THEME = {
        "primary_dark": "#1e1e2d",
        "primary_medium": "#2b2e4a",
        "primary_light": "#3e4263",
        "text_primary": "#e0e5f2",
        "text_secondary": "#a4a9c7",
        "accent": "#4a90e2"
    }
    
    LIGHT_THEME = {
        "primary_dark": "#f8f9fa",
        "primary_medium": "#e9ecef",
        "primary_light": "#dee2e6",
        "text_primary": "#212529",
        "text_secondary": "#000000",
        "accent": "#007bff"
    }
    
    RED_THEME = {              
        "primary_dark": "#2d0a0a",  
        "primary_medium": "#4a1a1a", 
        "primary_light": "#6b2c2c",  
        "text_primary": "#f5e6e6",  
        "text_secondary": "#d4a8a8", 
        "accent": "#e74c3c"      
    }
    
    GREEN_THEME = {
        "primary_dark": "#0a1e0a",
        "primary_medium": "#1b3a1b",
        "primary_light": "#2d4a2d",
        "text_primary": "#e8f5e8",
        "text_secondary": "#b8d4b8",
        "accent": "#27ae60"
    }

class ThemeManager:
    """مدیر تم‌ها"""
    
    def __init__(self):
        self.current_theme = ThemeType.DARK
        self.theme_colors = ThemeColors.DARK_THEME
    
    def set_theme(self, theme_type: ThemeType):
        """تغییر تم فعلی"""
        self.current_theme = theme_type
        
        if theme_type == ThemeType.DARK:
            self.theme_colors = ThemeColors.DARK_THEME
        elif theme_type == ThemeType.LIGHT:
            self.theme_colors = ThemeColors.LIGHT_THEME
        elif theme_type == ThemeType.RED: 
                self.theme_colors = ThemeColors.RED_THEME 
        elif theme_type == ThemeType.GREEN:
            self.theme_colors = ThemeColors.GREEN_THEME
    
    def get_color(self, color_name: str) -> str:
        """دریافت رنگ از تم فعلی"""
        return self.theme_colors.get(color_name, "#ffffff")
    
    def get_gradient(self, gradient_type: str = "primary", direction: str = "x1:0, y1:0, x2:1, y2:1") -> str:
        """ایجاد گرادیان بر اساس تم فعلی"""
        if gradient_type == "primary":
            return f"""qlineargradient({direction},
                stop:0 {self.get_color('primary_light')},
                stop:1 {self.get_color('primary_medium')})"""
        elif gradient_type == "hover":
            return f"""qlineargradient({direction},
                stop:0 rgba(74, 144, 226, 70),
                stop:1 rgba(155, 89, 182, 60))"""
        elif gradient_type == "pressed":
            return f"""qlineargradient({direction},
                stop:0 rgba(74, 144, 226, 90),
                stop:1 rgba(155, 89, 182, 80))"""
    
    def get_button_style(self, size: str = "normal") -> str:
        """دریافت استایل دکمه بر اساس تم فعلی"""
        sizes = {
            "small": {"padding": "4px 8px", "font_size": Typography.SIZE_SMALL},
            "normal": {"padding": "6px 12px", "font_size": Typography.SIZE_MEDIUM},
            "large": {"padding": "8px 16px", "font_size": Typography.SIZE_LARGE}
        }
        
        current_size = sizes.get(size, sizes["normal"])
        
        return f"""
            QPushButton {{
                background: {self.get_gradient("primary")};
                border: 1px solid {self.get_color('accent')};
                border-radius: {BorderRadius.LARGE};
                color: {self.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {current_size['font_size']};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {current_size['padding']};
            }}
            QPushButton:hover {{
                background: {self.get_gradient("hover")};
                border: 1px solid rgba(74, 144, 226, 120);
                color: white;
            }}
            QPushButton:pressed {{
                background: {self.get_gradient("pressed")};
                border: 1px solid rgba(74, 144, 226, 120);
            }}
            QPushButton:disabled {{
                background: {self.get_color('primary_light')};
                color: {self.get_color('text_secondary')};
                border: 1px solid {self.get_color('primary_medium')};
            }}
        """
    
    def get_main_window_style(self) -> str:
        """استایل پنجره اصلی بر اساس تم"""
        return f"""
            QMainWindow {{
                background-color: {self.get_color('primary_dark')};
            }}
            QWidget {{
                background-color: {self.get_color('primary_dark')};
                color: {self.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_NORMAL};
            }}
            QTabWidget {{
                background-color: {self.get_color('primary_medium')};
                border: 1px solid {self.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}
            QTabBar::tab {{
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_NORMAL};
                font-weight: {Typography.WEIGHT_BOLD};
                color: {self.get_color('text_secondary')};
                background: {self.get_color('primary_light')};
                border: 1px solid {self.get_color('primary_medium')};
                border-bottom: none;
                border-radius: {BorderRadius.MEDIUM} {BorderRadius.MEDIUM} 0 0;
                padding: {Spacing.LG} {Spacing.XXL};
                margin-right: {Spacing.XS};
                width: 150px;
                min-width: 120px;
                max-width: 200px;
            }}
            QTabBar::tab:selected {{
                color: {self.get_color('text_primary')};
                background: {self.get_color('accent')};
                border-color: {self.get_color('accent')};
            }}
            QTabBar::tab:hover:!selected {{
                background: rgba(74, 144, 226, 30);
                color: {self.get_color('text_primary')};
            }}
        """

# سینگلتون برای استفاده آسان در کل برنامه
theme_manager = ThemeManager()

# توابع راحتی برای استفاده سریع
def get_current_theme() -> ThemeType:
    return theme_manager.current_theme

def set_theme(theme_type: ThemeType):
    theme_manager.set_theme(theme_type)

def get_button_style(size: str = "normal") -> str:
    return theme_manager.get_button_style(size)

def get_main_window_style() -> str:
    return theme_manager.get_main_window_style()

# مثال‌های استفاده:
# set_theme(ThemeType.BLUE)
# button_style = get_button_style("large")
# main_style = get_main_window_style()