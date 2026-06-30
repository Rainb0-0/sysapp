# -----------------------------------------------------------------------------
# style_manager.py - Central manager for applying styles to widgets
# -----------------------------------------------------------------------------
import sip

from PyQt5.QtWidgets import (QWidget, QPushButton, QTreeWidget, QListWidget,
                             QGroupBox, QComboBox, QLabel, QLineEdit, QTextEdit,
                             QTabWidget, QMainWindow)
from PyQt5.QtCore import QObject, pyqtSignal
from typing import Dict, List, Optional, Union

from styles.design_system import (Colors, Typography, Spacing, BorderRadius,
                          StyleTemplates, NodeStyles)
from styles.theme_manager import theme_manager, ThemeType

class StyleManager(QObject):
    """Central manager for applying styles"""

    # Signal to notify of theme changes
    theme_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.registered_widgets: Dict[str, List[QWidget]] = {}
        self.custom_styles: Dict[str, str] = {}

    def register_widget(self, widget: QWidget, style_type: str, widget_id: str = None):
        if style_type not in self.registered_widgets:
            self.registered_widgets[style_type] = []
        if widget_id:
            widget.setProperty("style_id", widget_id)

        def _on_destroyed(_=None, w=widget, st=style_type):
            self.unregister_widget(w, st)
        widget.destroyed.connect(_on_destroyed)

        self.registered_widgets[style_type].append(widget)
        self.apply_style_to_widget(widget, style_type)


    def apply_style_to_widget(self, widget: QWidget, style_type: str):
        """Applies a specific style to a widget"""
        style = self.get_style(style_type)
        if style:
            widget.setStyleSheet(style)

            # Apply menubar style if the widget is a QMainWindow
            if style_type == "main_window" and hasattr(widget, 'menuBar'):
                menu_style = self.get_style("menu_bar")
                if menu_style:
                    widget.menuBar().setStyleSheet(menu_style)

    def get_style(self, style_type: str) -> str:
        """Retrieves a style based on its type"""
        # Check for custom styles
        if style_type in self.custom_styles:
            return self.custom_styles[style_type]

        # Default styles
        style_map = {
            "button": theme_manager.get_button_style(),
            "button_small": theme_manager.get_button_style("small"),
            "button_large": theme_manager.get_button_style("large"),
            "tree_widget": self._get_tree_widget_style(),
            "list_widget": self._get_list_widget_style(),
            "group_box": self._get_group_box_style(),
            "combo_box": self._get_combo_box_style(),
            "line_edit": self._get_line_edit_style(),
            "text_edit": self._get_text_edit_style(),
            "label": self._get_label_style(),
            "main_window": self._get_main_window_style(),
            "menu_bar": self._get_menu_bar_style(),
        }

        return style_map.get(style_type, "")

    def _get_tree_widget_style(self) -> str:
        """Tree widget style for the current theme"""
        return f"""
            QTreeWidget {{
                background: {theme_manager.get_color('primary_dark')};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_MEDIUM};
                border: 1px solid {theme_manager.get_color('primary_light')};
                outline: none;
                selection-background-color: rgba(74, 144, 226, 40);
                show-decoration-selected: 1;
            }}
            QTreeWidget QHeaderView::section {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                color: white;
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {Spacing.MD};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.SMALL};
            }}
            QTreeWidget::item {{
                background: transparent;
                border: 1px solid rgba(74, 144, 226, 40);
                padding: {Spacing.LG} {Spacing.SM};
                margin: 1px 0px;
                border-radius: {BorderRadius.MEDIUM};
            }}
            QTreeWidget::item:hover {{
                background: rgba(74, 144, 226, 20);
                border: 1px solid rgba(74, 144, 226, 40);
                color: {theme_manager.get_color('text_primary')};
            }}
            QTreeWidget::item:selected {{
                background: rgba(74, 144, 226, 40);
                border: 1px solid rgba(74, 144, 226, 70);
                color: white;
                font-weight: {Typography.WEIGHT_BOLD};
            }}
        """

    def _get_list_widget_style(self) -> str:
        """List widget style"""
        return f"""
            QListWidget {{
                background: {theme_manager.get_color('primary_dark')};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.MEDIUM};
            }}
            QListWidget::item {{
                padding: {Spacing.MD};
                border-bottom: 1px solid {theme_manager.get_color('primary_medium')};
            }}
            QListWidget::item:hover {{
                background: rgba(74, 144, 226, 20);
            }}
            QListWidget::item:selected {{
                background: rgba(74, 144, 226, 40);
                color: white;
            }}
        """

    def _get_group_box_style(self) -> str:
        """Group box style"""
        return f"""
            QGroupBox {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:0, y2:1")};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.LARGE};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_XLARGE};
                font-weight: {Typography.WEIGHT_BOLD};
                color: {theme_manager.get_color('text_primary')};
                margin-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 3px;
                background: transparent;
            }}
        """

    def _get_combo_box_style(self) -> str:
        """Combo box style"""
        return f"""
            QComboBox {{
                background: {theme_manager.get_gradient("primary")};
                border: 1px solid {theme_manager.get_color('accent')};
                border-radius: {BorderRadius.LARGE};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE};
                padding: {Spacing.MD} {Spacing.XL};
            }}
            QComboBox:hover {{
                background: {theme_manager.get_gradient("hover")};
            }}
            QComboBox QAbstractItemView {{
                background: {theme_manager.get_color('primary_dark')};
                color: {theme_manager.get_color('text_primary')};
                selection-background-color: rgba(74, 144, 226, 70);
                border: 1px solid {theme_manager.get_color('primary_light')};
            }}
        """

    def _get_line_edit_style(self) -> str:
        """Line edit style"""
        return f"""
            QLineEdit {{
                background: {theme_manager.get_color('primary_medium')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.MEDIUM};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                padding: {Spacing.MD};
            }}
            QLineEdit:focus {{
                border: 2px solid {theme_manager.get_color('accent')};
            }}
        """

    def _get_text_edit_style(self) -> str:
        """Text edit style"""
        return f"""
            QTextEdit {{
                background: {theme_manager.get_color('primary_medium')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.MEDIUM};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                padding: {Spacing.MD};
            }}
            QTextEdit:focus {{
                border: 2px solid {theme_manager.get_color('accent')};
            }}
        """

    def _get_label_style(self) -> str:
        """Label style"""
        return f"""
            QLabel {{
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                background: transparent;
            }}
        """

    def _get_main_window_style(self) -> str:
        """Main window style"""
        return theme_manager.get_main_window_style()

    def _get_menu_bar_style(self) -> str:
        """Menu bar style"""
        return f"""
            QMenuBar {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_BOLD};
                border-bottom: 1px solid {theme_manager.get_color('primary_light')};
                padding: {Spacing.SM} {Spacing.MD};
            }}

            QMenuBar::item {{
                background: transparent;
                color: {theme_manager.get_color('text_primary')};
                padding: {Spacing.MD} {Spacing.LG};
                border-radius: {BorderRadius.MEDIUM};
                margin: 0 {Spacing.XS};
            }}

            QMenuBar::item:selected {{
                background: {theme_manager.get_color('accent')};
                color: white;
            }}

            QMenuBar::item:pressed {{
                background: rgba(74, 144, 226, 70);
            }}

            QMenu {{
                background: {theme_manager.get_color('primary_medium')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.MEDIUM};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                padding: {Spacing.SM};
            }}

            QMenu::item {{
                background: transparent;
                padding: {Spacing.MD} {Spacing.XL};
                border-radius: {BorderRadius.SMALL};
                margin: 1px 0;
            }}

            QMenu::item:selected {{
                background: {theme_manager.get_color('accent')};
                color: white;
            }}

            QMenu::item:disabled {{
                color: {theme_manager.get_color('text_secondary')};
                background: transparent;
            }}

            QMenu::separator {{
                height: 1px;
                background: {theme_manager.get_color('primary_light')};
                margin: {Spacing.SM} {Spacing.MD};
            }}

            QMenu::indicator {{
                width: 16px;
                height: 16px;
                margin-left: {Spacing.SM};
            }}

            QMenu::indicator:checked {{
                background: {theme_manager.get_color('accent')};
                border-radius: 8px;
                border: 2px solid white;
            }}

            QMenu::indicator:unchecked {{
                background: transparent;
                border: 1px solid {theme_manager.get_color('text_secondary')};
                border-radius: 8px;
            }}
        """

    def set_custom_style(self, style_name: str, style_content: str):
        """Defines a custom style"""
        self.custom_styles[style_name] = style_content

    def apply_theme(self, theme_type: ThemeType):
        """Applies a new theme to all registered widgets"""
        theme_manager.set_theme(theme_type)

        # Re-apply styles to all registered widgets
        for style_type, widgets in self.registered_widgets.items():
            for widget in widgets:
                if widget and not widget.isHidden():  # Check for widget existence
                    self.apply_style_to_widget(widget, style_type)

        self.theme_changed.emit(theme_type.value)

    def style_widget_by_type(self, widget: QWidget, auto_detect: bool = True):
        """Automatically styles a widget based on its type"""
        widget_type = type(widget).__name__

        type_map = {
            "QPushButton": "button",
            "QTreeWidget": "tree_widget",
            "QListWidget": "list_widget",
            "QGroupBox": "group_box",
            "QComboBox": "combo_box",
            "QLineEdit": "line_edit",
            "QTextEdit": "text_edit",
            "QLabel": "label",
            "QMainWindow": "main_window",
            "QMenuBar": "menu_bar",
        }

        style_type = type_map.get(widget_type)
        if style_type:
            self.apply_style_to_widget(widget, style_type)
            if auto_detect:
                self.register_widget(widget, style_type)

    def create_styled_widget(self, widget_class, style_type: str = None, **kwargs):
        """Creates a styled widget from scratch"""
        widget = widget_class(**kwargs)

        if not style_type:
            # Automatically detect style type
            self.style_widget_by_type(widget, auto_detect=True)
        else:
            self.register_widget(widget, style_type)

        return widget
    
    def unregister_widget(self, widget: QWidget, style_type: str = None):
        if style_type:
            lst = self.registered_widgets.get(style_type, [])
            if widget in lst:
                lst.remove(widget)
        else:
            for lst in self.registered_widgets.values():
                if widget in lst:
                    lst.remove(widget)

    def _iter_live_widgets(self, style_type: str):
        lst = self.registered_widgets.get(style_type, [])
        for w in list(lst):
            if w is None or sip.isdeleted(w):
                try: lst.remove(w)
                except ValueError: pass
                continue
            yield w

# Singleton for easy use
style_manager = StyleManager()

# Convenience functions
def register_widget(widget: QWidget, style_type: str, widget_id: str = None):
    """Registers a widget for automatic styling"""
    style_manager.register_widget(widget, style_type, widget_id)

def apply_theme(self, theme_type: ThemeType):
    theme_manager.set_theme(theme_type)
    for style_type in list(self.registered_widgets.keys()):
        for widget in self._iter_live_widgets(style_type):
            try:
                if not widget.isHidden():
                    self.apply_style_to_widget(widget, style_type)
            except (RuntimeError, ReferenceError):
                continue
    self.theme_changed.emit(theme_type.value)


def create_styled_button(text: str = "", size: str = "normal") -> QPushButton:
    """Creates a styled button"""
    button = QPushButton(text)
    style_type = f"button_{size}" if size != "normal" else "button"
    style_manager.register_widget(button, style_type)
    return button

def auto_style_widget(widget: QWidget):
    """Automatically styles a widget"""
    style_manager.style_widget_by_type(widget)

def create_styled_button(text: str = "", size: str = "normal", register_global: bool = True) -> QPushButton:
    button = QPushButton(text)
    style_type = f"button_{size}" if size != "normal" else "button"
    if register_global:
        style_manager.register_widget(button, style_type)
    else:
        style_manager.apply_style_to_widget(button, style_type)
    return button


# Example usage:
# button = create_styled_button("Click me", "large")
# register_widget(my_tree_widget, "tree_widget", "main_tree")
# apply_theme(ThemeType.BLUE)