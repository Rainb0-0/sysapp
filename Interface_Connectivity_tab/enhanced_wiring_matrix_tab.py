# enhanced_wiring_matrix_tab.py - مهاجرت یافته به سیستم استایل جدید

import sys
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTabWidget
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from Interface_Connectivity_tab.matrix_panel import MatrixPanel
from Interface_Connectivity_tab.Interfaces_list_panel import InterfacesListPanel

# Import new style system
from styles.style_manager import (style_manager, register_widget, 
                                create_styled_button, auto_style_widget)
from styles.design_system import Colors, Typography, Spacing, BorderRadius
from styles.theme_manager import theme_manager

class EnhancedWiringMatrixTab(QWidget):
    """
    Main tab widget: contains two sub-tabs — the wiring matrix panel and the
    all-interfaces list panel. Handles synchronization and data refresh
    between the panels.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setObjectName("WiringMatrixContainer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        
        # اعمال استایل پایه
        self.apply_main_widget_style()
        
        # اتصال به تغییر تم
        style_manager.theme_changed.connect(self.on_theme_changed)
        
        self.setFont(QFont("Roboto Mono", 10))

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Sub-tabs: wiring matrix (top) and interfaces list (bottom)
        self.sub_tabs = QTabWidget()
        self.apply_tabs_style(self.sub_tabs)
        main_layout.addWidget(self.sub_tabs)

        # Create and add panels as tab pages
        self.matrix_panel = MatrixPanel(self)
        self.interfaces_list_panel = InterfacesListPanel(self)

        self.sub_tabs.addTab(self.matrix_panel, "🔗 Wiring Matrix")
        self.sub_tabs.addTab(self.interfaces_list_panel, "🌐 Interfaces List")

        # --- Connect panel signals to keep data in sync ---
        self.matrix_panel.interface_changed.connect(self.interfaces_list_panel.load_interfaces)
        self.interfaces_list_panel.interface_changed.connect(self.matrix_panel.load_matrix_data)

        self.parent_app = parent

    def apply_main_widget_style(self):
        """اعمال استایل به ویجت اصلی"""
        style = f"""
            QWidget#WiringMatrixContainer {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}
        """
        self.setStyleSheet(style)

    def apply_tabs_style(self, tabs):
        """اعمال استایل به تب‌های داخلی"""
        tabs.setStyleSheet(f"""
            QTabWidget {{
                border: none;
                background: transparent;
            }}
            QTabWidget::pane {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.MEDIUM};
                top: -1px;
            }}
            QTabBar::tab {{
                background: {theme_manager.get_color('primary_light')};
                color: {theme_manager.get_color('text_secondary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {Spacing.LG} {Spacing.XL};
                border: 1px solid {theme_manager.get_color('primary_medium')};
                border-bottom: none;
                border-radius: {BorderRadius.MEDIUM} {BorderRadius.MEDIUM} 0 0;
                margin-right: {Spacing.XS};
            }}
            QTabBar::tab:selected {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                color: {theme_manager.get_color('text_primary')};
            }}
            QTabBar::tab:hover:!selected {{
                background: rgba(74, 144, 226, 30);
                color: {theme_manager.get_color('text_primary')};
            }}
        """)

    def on_theme_changed(self, theme_name):
        """هندل تغییر تم"""
        self.apply_main_widget_style()
        # اعمال مجدد استایل تب‌های داخلی
        if hasattr(self, "sub_tabs"):
            self.apply_tabs_style(self.sub_tabs)

    def refresh_all(self):
        # Re-sync filter dropdowns with the current project first, so a
        # filter picked in a previous project never leaks stale ids across.
        if hasattr(self.matrix_panel, "refresh_filter_options"):
            self.matrix_panel.refresh_filter_options()
        self.matrix_panel.load_matrix_data()
        self.interfaces_list_panel.load_interfaces()