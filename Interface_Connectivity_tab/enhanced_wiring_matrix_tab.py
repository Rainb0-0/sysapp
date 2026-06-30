# enhanced_wiring_matrix_tab.py - مهاجرت یافته به سیستم استایل جدید

import sys
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QSplitter
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
    Main tab widget: contains matrix panel (top) and interfaces list panel (bottom).
    Handles synchronization and data refresh between panels.
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

        # ایجاد splitter با استایل جدید
        splitter = QSplitter()
        splitter.setOrientation(Qt.Vertical)
        self.apply_splitter_style(splitter)
        main_layout.addWidget(splitter)

        # Create and add panels
        self.matrix_panel = MatrixPanel(self)
        self.interfaces_list_panel = InterfacesListPanel(self)

        splitter.addWidget(self.matrix_panel)
        splitter.addWidget(self.interfaces_list_panel)
        splitter.setSizes([int(self.height() * 0.6), int(self.height() * 0.4)])

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

    def apply_splitter_style(self, splitter):
        """اعمال استایل به splitter"""
        splitter_style = f"""
            QSplitter::handle:vertical {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                height: 10px;
                border-radius: {BorderRadius.SMALL};
                margin: 4px 0;
                border: 1px solid {theme_manager.get_color('primary_light')};
            }}
            QSplitter::handle:vertical:hover {{
                background: {theme_manager.get_gradient("hover", "x1:0, y1:0, x2:1, y2:0")};
                border: 1px solid {theme_manager.get_color('accent')};
            }}
            QSplitter::handle:vertical:pressed {{
                background: {theme_manager.get_gradient("pressed", "x1:0, y1:0, x2:1, y2:0")};
                border: 1px solid rgba(74, 144, 226, 150);
            }}
        """
        splitter.setStyleSheet(splitter_style)

    def on_theme_changed(self, theme_name):
        """هندل تغییر تم"""
        self.apply_main_widget_style()
        # اعمال مجدد استایل splitter
        for child in self.findChildren(QSplitter):
            self.apply_splitter_style(child)

    def refresh_all(self):
        self.matrix_panel.load_matrix_data()
        self.interfaces_list_panel.load_interfaces()