# -----------------------------------------------------------------------------
# design_system.py - سیستم طراحی یکپارچه برای کل پروژه
# -----------------------------------------------------------------------------

class Colors:
    """رنگ‌های پایه سیستم"""
    # Primary Colors
    PRIMARY_DARK = "#1e1e2d"
    PRIMARY_MEDIUM = "#2b2e4a" 
    PRIMARY_LIGHT = "#3e4263"
    
    # Accent Colors
    ACCENT_BLUE = "#4a90e2"
    ACCENT_PURPLE = "#9b59b6"
    ACCENT_ORANGE = "#f39c12"
    ACCENT_GREEN = "#27ae60"
    ACCENT_RED = "#c0392b"
    
    # Text Colors
    TEXT_PRIMARY = "#e0e5f2"
    TEXT_SECONDARY = "#a4a9c7"
    TEXT_DISABLED = "#888888"
    
    # Border Colors
    BORDER_PRIMARY = "#4a4d6f"
    BORDER_FOCUS = "#6b70a1"
    BORDER_ACTIVE = "rgba(74, 144, 226, 120)"
    
    # Background Colors
    BG_TRANSPARENT = "transparent"
    BG_HOVER = "rgba(74, 144, 226, 20)"
    BG_SELECTED = "rgba(74, 144, 226, 40)"
    BG_PRESSED = "rgba(74, 144, 226, 70)"

class Typography:
    """تایپوگرافی سیستم"""
    FONT_FAMILY = "'Roboto Mono', Consolas, monospace"
    
    # Font Sizes
    SIZE_SMALL = "11px"
    SIZE_NORMAL = "13px"
    SIZE_MEDIUM = "14px"
    SIZE_LARGE = "15px"
    SIZE_XLARGE = "16px"
    
    # Font Weights
    WEIGHT_NORMAL = "normal"
    WEIGHT_MEDIUM = "500"
    WEIGHT_BOLD = "bold"

class Spacing:
    """فاصله‌گذاری سیستم"""
    XS = "2px"
    SM = "4px"
    MD = "6px"
    LG = "8px"
    XL = "12px"
    XXL = "15px"

class BorderRadius:
    """شعاع گردی سیستم"""
    SMALL = "4px"
    MEDIUM = "6px"
    LARGE = "8px"
    XLARGE = "12px"

class Gradients:
    """گرادیان‌های پایه سیستم"""
    
    @staticmethod
    def primary_gradient(direction="x1:0, y1:0, x2:1, y2:1"):
        return f"""qlineargradient({direction},
            stop:0 {Colors.PRIMARY_LIGHT},
            stop:1 {Colors.PRIMARY_MEDIUM})"""
    
    @staticmethod
    def hover_gradient(direction="x1:0, y1:0, x2:1, y2:1"):
        return f"""qlineargradient({direction},
            stop:0 rgba(74, 144, 226, 70),
            stop:1 rgba(155, 89, 182, 60))"""
    
    @staticmethod
    def pressed_gradient(direction="x1:0, y1:0, x2:1, y2:1"):
        return f"""qlineargradient({direction},
            stop:0 rgba(74, 144, 226, 90),
            stop:1 rgba(155, 89, 182, 80))"""

class StyleTemplates:
    """قالب‌های استایل پایه"""
    
    @staticmethod
    def button_style(size="normal"):
        """استایل دکمه با اندازه‌های مختلف"""
        sizes = {
            "small": {"padding": "4px 8px", "font_size": Typography.SIZE_SMALL},
            "normal": {"padding": "6px 12px", "font_size": Typography.SIZE_MEDIUM},
            "large": {"padding": "8px 16px", "font_size": Typography.SIZE_LARGE}
        }
        
        current_size = sizes.get(size, sizes["normal"])
        
        return f"""
            QPushButton {{
                background: {Gradients.primary_gradient()};
                border: 1px solid {Colors.BORDER_FOCUS};
                border-radius: {BorderRadius.LARGE};
                color: {Colors.TEXT_PRIMARY};
                font-family: {Typography.FONT_FAMILY};
                font-size: {current_size['font_size']};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {current_size['padding']};
            }}
            QPushButton:hover {{
                background: {Gradients.hover_gradient()};
                border: 1px solid {Colors.BORDER_ACTIVE};
                color: white;
            }}
            QPushButton:pressed {{
                background: {Gradients.pressed_gradient()};
                border: 1px solid {Colors.BORDER_ACTIVE};
            }}
            QPushButton:disabled {{
                background: {Colors.PRIMARY_LIGHT};
                color: {Colors.TEXT_DISABLED};
                border: 1px solid {Colors.BORDER_PRIMARY};
            }}
        """
    
    @staticmethod
    def tree_widget_style():
        """استایل ویجت درختی"""
        return f"""
            QTreeWidget {{
                background: {Colors.PRIMARY_DARK};
                color: {Colors.TEXT_PRIMARY};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_MEDIUM};
                border: 1px solid {Colors.BORDER_PRIMARY};
                outline: none;
                selection-background-color: {Colors.BG_SELECTED};
                show-decoration-selected: 1;
                alternate-background-color: rgba(62, 66, 99, 20);
            }}
            
            QTreeWidget QHeaderView::section {{
                background: {Gradients.primary_gradient("x1:0, y1:0, x2:1, y2:0")};
                color: white;
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {Spacing.MD};
                border: 1px solid {Colors.BORDER_PRIMARY};
                border-radius: {BorderRadius.SMALL};
            }}
            
            QTreeWidget::item {{
                background: {Colors.BG_TRANSPARENT};
                border: 1px solid {Colors.BG_SELECTED};
                padding: {Spacing.LG} {Spacing.SM};
                margin: 1px 0px;
                border-radius: {BorderRadius.MEDIUM};
            }}
            
            QTreeWidget::item:hover {{
                background: {Colors.BG_HOVER};
                border: 1px solid {Colors.BG_SELECTED};
                color: {Colors.TEXT_PRIMARY};
            }}
            
            QTreeWidget::item:selected {{
                background: {Colors.BG_SELECTED};
                border: 1px solid {Colors.BG_PRESSED};
                color: white;
                font-weight: {Typography.WEIGHT_BOLD};
            }}
        """
    
    @staticmethod
    def list_widget_style():
        """استایل ویجت لیستی"""
        return f"""
            QListWidget {{
                background: rgba(30, 30, 45, 15);
                color: {Colors.TEXT_PRIMARY};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_MEDIUM};
                border: none;
                outline: none;
                selection-background-color: {Colors.BG_SELECTED};
                show-decoration-selected: 1;
                alternate-background-color: {Colors.BG_TRANSPARENT};
            }}
            
            QListWidget::item {{
                background: rgba(255, 255, 255, 5);
                border: 1px solid rgba(74, 144, 226, 8);
                padding: {Spacing.MD} {Spacing.SM};
                margin: 1px 0px;
                border-radius: {BorderRadius.MEDIUM};
                min-height: 18px;
            }}
            
            QListWidget::item:hover {{
                background: {Colors.BG_HOVER};
                border: 1px solid {Colors.BG_SELECTED};
                color: {Colors.TEXT_PRIMARY};
            }}
            
            QListWidget::item:selected {{
                background: rgba(74, 144, 226, 35);
                border: 1px solid {Colors.BG_PRESSED};
                color: {Colors.TEXT_PRIMARY};
                font-weight: {Typography.WEIGHT_BOLD};
            }}
        """
    
    @staticmethod
    def group_box_style():
        """استایل گروه‌باکس"""
        return f"""
            QGroupBox {{
                background: {Gradients.primary_gradient("x1:0, y1:0, x2:0, y2:1")};
                border: 1px solid {Colors.BORDER_PRIMARY};
                border-radius: {BorderRadius.LARGE};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_XLARGE};
                font-weight: {Typography.WEIGHT_BOLD};
                color: {Colors.TEXT_PRIMARY};
                margin-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 3px;
                background: {Colors.BG_TRANSPARENT};
            }}
        """
    
    @staticmethod
    def combo_box_style():
        """استایل کمبوباکس"""
        return f"""
            QComboBox {{
                background: {Gradients.primary_gradient()};
                border: 1px solid {Colors.BORDER_FOCUS};
                border-radius: {BorderRadius.LARGE};
                color: {Colors.TEXT_PRIMARY};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {Spacing.MD} {Spacing.XL};
            }}
            QComboBox:hover {{
                background: {Gradients.hover_gradient()};
                border: 1px solid {Colors.BORDER_ACTIVE};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background: {Colors.PRIMARY_DARK};
                color: {Colors.TEXT_PRIMARY};
                selection-background-color: {Colors.BG_PRESSED};
                border: 1px solid {Colors.BORDER_PRIMARY};
                border-radius: {BorderRadius.SMALL};
            }}
        """

class NodeStyles:
    """استایل‌های نودها"""
    
    NODE_CONFIGS = {
        'project': {
            'width': 200, 
            'height': 50, 
            'color': Colors.ACCENT_BLUE, 
            'font_size': Typography.SIZE_MEDIUM, 
            'icon': '🗗️'
        },
        'subsystem': {
            'width': 180, 
            'height': 40, 
            'color': Colors.ACCENT_PURPLE, 
            'font_size': Typography.SIZE_NORMAL, 
            'icon': '🏢'
        },
        'module': {
            'width': 160, 
            'height': 35, 
            'color': Colors.ACCENT_ORANGE, 
            'font_size': Typography.SIZE_SMALL, 
            'icon': '⚙️'
        },
        'connector': {
            'width': 140, 
            'height': 30, 
            'color': Colors.ACCENT_GREEN, 
            'font_size': "10px", 
            'icon': '🔌'
        },
        'pin': {
            'width': 120, 
            'height': 25, 
            'color': Colors.ACCENT_RED, 
            'font_size': "9px", 
            'icon': '📍'
        }
    }

# راحتی استفاده - متغیرهای مستقیم برای استایل‌های رایج
BUTTON_STYLE = StyleTemplates.button_style()
BUTTON_STYLE_SMALL = StyleTemplates.button_style("small")  
BUTTON_STYLE_LARGE = StyleTemplates.button_style("large")
TREE_WIDGET_STYLE = StyleTemplates.tree_widget_style()
LIST_WIDGET_STYLE = StyleTemplates.list_widget_style()
GROUP_BOX_STYLE = StyleTemplates.group_box_style()
COMBO_BOX_STYLE = StyleTemplates.combo_box_style()