# matrix_panel.py - مهاجرت یافته به سیستم استایل جدید

import os
import sys
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QDialog, QComboBox, QPushButton, QMessageBox, QGroupBox, 
    QFormLayout, QDialogButtonBox,QHeaderView, QHBoxLayout, QWidget as QW
)
from PyQt5.QtGui import QColor, QPixmap, QIcon, QFont
from PyQt5.QtCore import Qt, pyqtSignal

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import get_connection, get_current_project_id, add_interface_guarded
from auth_manager import auth

from Interface_Connectivity_tab.wiring_utils import (
    get_all_pins_with_full_numbered_name, PREDEFINED_COLORS, AddInterfaceDialog
)

# Import new style system
from styles.style_manager import (style_manager, register_widget, 
                                create_styled_button, auto_style_widget)
from styles.design_system import Colors, Typography, Spacing, BorderRadius
from styles.theme_manager import theme_manager

class ColorDisplayWidget(QWidget):
    def __init__(self, color_hex, color_name=None):
        super().__init__()
        self.color_hex = color_hex
        self.color_name = color_name or "Custom"
        self.setup_ui()
    
    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)
        
        # مربع رنگی
        color_square = QLabel()
        color_square.setFixedSize(30, 20)
        color_square.setStyleSheet(f"""
            QLabel {{
                background-color: {self.color_hex};
                border: 1px solid #333;
                border-radius: 3px;
            }}
        """)
        
        # نام رنگ
        name_label = QLabel(self.color_name)
        name_label.setStyleSheet(f"""
            QLabel {{
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_SMALL};
                background: transparent;
            }}
        """)
        
        layout.addWidget(color_square)
        layout.addWidget(name_label)
        layout.addStretch()

class MatrixPanel(QWidget):
    interface_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.connector_data_cache = {}
        
        self.setObjectName("MatrixContainer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        
        # اعمال استایل پایه
        self.apply_main_panel_style()
        
        # اتصال به تغییر تم
        style_manager.theme_changed.connect(self.on_theme_changed)

        # --- UI Layout ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        # Header Widget
        self.create_header_widget(layout)

        # Matrix Table
        self.create_matrix_table(layout)

        self.matrix_table.cellDoubleClicked.connect(self.edit_matrix_cell)

        self.connector_ids_for_matrix = []
        self.connectors_for_matrix = []

        self.load_matrix_data()

    def apply_main_panel_style(self):
        """اعمال استایل به پنل اصلی"""
        style = f"""
            QWidget#MatrixContainer {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}
            
            QMessageBox {{
                background: {theme_manager.get_color('primary_dark')}; 
                color: {theme_manager.get_color('text_primary')};    
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
            }}

            QMessageBox QLabel {{
                color: {theme_manager.get_color('text_primary')};   
            }}

            QMessageBox QPushButton {{
                background: {theme_manager.get_gradient("primary")};
                border: 1px solid {theme_manager.get_color('accent')};
                border-radius: {BorderRadius.LARGE};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {Spacing.LG} {Spacing.XL};
            }}
                           
            QComboBox QAbstractItemView {{
                background: {theme_manager.get_color('primary_dark')};
                color: {theme_manager.get_color('text_primary')};  
                selection-background-color: rgba(74, 144, 226, 40);
                selection-color: #ffffff;
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.MEDIUM};
                padding: {Spacing.MD};
            }}

            QComboBox QAbstractItemView::item {{
                padding: {Spacing.LG} {Spacing.XL};
            }}

            QComboBox QAbstractItemView::item:hover {{
                background-color: rgba(74, 144, 226, 30);
                color: white;
            }}
        """
        self.setStyleSheet(style)

    def _ensure_project_selected(self):
        project_id = get_current_project_id()
        if project_id is None:
            QMessageBox.warning(self, "No Project Selected", 
                            "Please select or create a project first.")
            return False
        return True

    def create_header_widget(self, layout):
        """ایجاد ویجت هدر"""
        self.header_widget = QW()
        self.header_widget.setFixedHeight(40)
        
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(12, 0, 12, 0)
        
        title = QLabel("🔗 Wiring Matrix")
        title.setFont(QFont("Roboto Mono", 14, QFont.Bold))
        header_layout.addWidget(title)
        header_layout.addStretch()
        layout.addWidget(self.header_widget)
        
        # اعمال استایل هدر
        self.update_header_style()

    def update_header_style(self):
        """بروزرسانی استایل هدر"""
        header_style = f"""
            QWidget {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                border-radius: {BorderRadius.LARGE};
                border: 1px solid {theme_manager.get_color('primary_light')};
            }}
            QLabel {{
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_XLARGE};
                font-weight: {Typography.WEIGHT_BOLD};
                background: transparent;
                border: none;
            }}
        """
        self.header_widget.setStyleSheet(header_style)

    def create_matrix_table(self, layout):
        """ایجاد جدول ماتریس"""
        self.matrix_table = QTableWidget()
        self.matrix_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.matrix_table.setFont(QFont("Roboto Mono", 11))
        
        layout.addWidget(self.matrix_table)
        
        # اعمال استایل جدول
        self.update_matrix_table_style()

    def update_matrix_table_style(self):
        """بروزرسانی استایل جدول ماتریس"""
        table_style = f"""
            QTableWidget {{
                background: {theme_manager.get_color('primary_dark')};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_MEDIUM};
                border: 1px solid {theme_manager.get_color('primary_light')};
                outline: none;
                selection-background-color: rgba(74, 144, 226, 40);
                show-decoration-selected: 1;
                alternate-background-color: rgba(62, 66, 99, 20);
                gridline-color: rgba(44, 62, 80, 50);
            }}
            QTableWidget::item {{
                border: 1px solid rgba(74, 144, 226, 40);
                padding: {Spacing.LG} {Spacing.MD};
                margin: 2px 0px;
                border-radius: {BorderRadius.MEDIUM};
                min-height: 30px;
            }}
            QTableWidget::item:hover {{
                background: {theme_manager.get_gradient("hover", "x1:0, y1:0, x2:1, y2:1")};
                border: 1px solid rgba(74, 144, 226, 120);
                color: #ffffff;
            }}
            QTableWidget::item:selected {{
                background: {theme_manager.get_gradient("pressed", "x1:0, y1:0, x2:1, y2:1")};
                border: 1px solid rgba(74, 144, 226, 150);
                color: #ffffff;
                font-weight: {Typography.WEIGHT_BOLD};
            }}
            QHeaderView::section {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                color: white;
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {Spacing.LG};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.SMALL};
            }}
        """
        self.matrix_table.setStyleSheet(table_style)

    def on_theme_changed(self, theme_name):
        """هندل تغییر تم"""
        self.apply_main_panel_style()
        self.update_header_style()
        self.update_matrix_table_style()

    def load_matrix_data(self):
        """
        Load connector info and prepare matrix headers and size.
        """
        if not self._ensure_project_selected():
            self.matrix_table.setRowCount(0)
            self.matrix_table.setColumnCount(0)
            return
            
        project_id = get_current_project_id()
        
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT m.name, c.name, c.id
                    FROM connectors c
                    JOIN modules m ON c.module_id = m.id AND m.project_id = %s
                    WHERE c.project_id = %s
                    ORDER BY m.name, c.name
                """, (project_id, project_id))
                self.connectors_for_matrix = cursor.fetchall()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading connectors: {str(e)}")
            self.connectors_for_matrix = []

        self.connector_ids_for_matrix = [cid for _, _, cid in self.connectors_for_matrix]
        headers = [f"{m_name} - {c_name}" for m_name, c_name, _ in self.connectors_for_matrix]
        n = len(headers)
        self.matrix_table.setRowCount(n)
        self.matrix_table.setColumnCount(n)
        self.matrix_table.setHorizontalHeaderLabels(headers)
        self.matrix_table.setVerticalHeaderLabels(headers)
        self.refresh_matrix_display()

    def refresh_matrix_display(self):
        """
        Fill the matrix table with interface info between connectors.
        """
        if not self._ensure_project_selected():
            return
            
        project_id = get_current_project_id()
        
        try:
            all_pins_map = get_all_pins_with_full_numbered_name()
            
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT pin1_id, pin2_id, i.id,
                        (SELECT connector_id FROM pins WHERE id=pin1_id AND project_id=%s),
                        (SELECT connector_id FROM pins WHERE id=pin2_id AND project_id=%s),
                        i.color
                    FROM interfaces i
                    WHERE i.project_id = %s
                """, (project_id, project_id, project_id))
                all_ifaces = cursor.fetchall()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading interfaces: {str(e)}")
            return

        # Clear all cells
        for r in range(self.matrix_table.rowCount()):
            self.matrix_table.setRowHeight(r, 40)
            for c in range(self.matrix_table.columnCount()):
                item = self.matrix_table.item(r, c)
                if not item:
                    item = QTableWidgetItem()
                    self.matrix_table.setItem(r, c, item)
                item.setText("")
                item.setData(Qt.UserRole, [])
                item.setBackground(QColor(Qt.transparent))

        # Fill with interface lists and count
        for p1_id, p2_id, iface_id, c1_id, c2_id, color in all_ifaces:
            if c1_id is None or c2_id is None:
                continue  # Skip if connector lookup failed
                
            p1_full_name = all_pins_map.get(p1_id, "N/A")
            p2_full_name = all_pins_map.get(p2_id, "N/A")
            p1_name = p1_full_name.split(': ')[-1]
            p2_name = p2_full_name.split(': ')[-1]
            try:
                idx1 = self.connector_ids_for_matrix.index(c1_id)
                idx2 = self.connector_ids_for_matrix.index(c2_id)
                for r, c in [(idx1, idx2), (idx2, idx1)]:
                    item = self.matrix_table.item(r, c)
                    conn_list = item.data(Qt.UserRole) or []
                    entry = (p1_id, p1_name, p2_id, p2_name, iface_id, color or '#0000FF')
                    if entry not in conn_list:
                        conn_list.append(entry)
                    item.setData(Qt.UserRole, conn_list)
                    item.setText(f"{len(conn_list)} Interface(s)")
                    item.setFont(QFont("Roboto Mono", 11))
                    item.setTextAlignment(Qt.AlignCenter)
            except ValueError:
                continue
        self.matrix_table.resizeColumnsToContents()
        self.matrix_table.resizeRowsToContents()
        # تنظیم عرض ستون‌ها
        for col in range(self.matrix_table.columnCount()):
            self.matrix_table.setColumnWidth(col, 150)

    def edit_matrix_cell(self, row, column):
        """
        Show dialog for editing interfaces between two connectors.
        """
        if not auth.is_system():
            QMessageBox.warning(self, "Access denied", "Only 'system' can edit the wiring matrix.")
            return

        connector1_id = self.connector_ids_for_matrix[row]
        connector2_id = self.connector_ids_for_matrix[column]
        connector1_name = self.matrix_table.horizontalHeaderItem(row).text()
        connector2_name = self.matrix_table.horizontalHeaderItem(column).text()

        # Fetch pins for both connectors
        pins1 = self._get_pins_for_connector(connector1_id)
        pins2 = self._get_pins_for_connector(connector2_id)

        # Fetch existing interfaces for this cell
        item = self.matrix_table.item(row, column)
        existing_ifaces = item.data(Qt.UserRole) or []

        # Create dialog with new style system
        dialog = self.create_matrix_cell_dialog(
            connector1_name, connector2_name, 
            pins1, pins2, existing_ifaces
        )
        
        dialog.exec_()

    def create_matrix_cell_dialog(self, connector1_name, connector2_name, pins1, pins2, existing_ifaces):
        """ایجاد دیالوگ ویرایش سلول ماتریس با استایل جدید"""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Interfaces: {connector1_name} ↔ {connector2_name}")
        dialog.resize(800, 600)
        
        # اعمال استایل خودکار به دیالوگ
        auto_style_widget(dialog)
        
        # استایل سفارشی برای این دیالوگ
        dialog_style = f"""
            QDialog {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}
            QLabel {{
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE};
                font-weight: {Typography.WEIGHT_BOLD};
                color: {theme_manager.get_color('text_primary')};
                background: transparent;
            }}
            QComboBox {{
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                border: 1px solid rgba(74, 144, 226, 40);
                border-radius: {BorderRadius.MEDIUM};
                padding: {Spacing.MD};
                background: {theme_manager.get_gradient("primary")};
                color: {theme_manager.get_color('text_primary')};
            }}
            QGroupBox {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:0, y2:1")};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.LARGE};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE};
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
        dialog.setStyleSheet(dialog_style)
        
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)

        # --- Existing Interfaces ---
        existing_group = QGroupBox(f"Existing Interfaces ({len(existing_ifaces)})")
        existing_group.setFont(QFont("Roboto Mono", 13, QFont.Bold))
        existing_layout = QVBoxLayout(existing_group)

        # Table for existing interfaces
        iface_table = QTableWidget()
        iface_table.setColumnCount(5)
        iface_table.setHorizontalHeaderLabels(["Pin from Conn1", "Pin from Conn2", "Color", "Edit", "Delete"])
        
        # اعمال استایل جدول
        self.apply_interface_table_style(iface_table)
        
        iface_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        existing_layout.addWidget(iface_table)
        layout.addWidget(existing_group)
        is_sys = auth.is_system()

        # --- Refresh function for existing interfaces ---

        def refresh_cell_list():
            iface_table.setRowCount(len(existing_ifaces))
            for idx, (p1_id, p1_name, p2_id, p2_name, iface_id, color) in enumerate(existing_ifaces):
                iface_table.setRowHeight(idx, 60)
                iface_table.setItem(idx, 0, QTableWidgetItem(p1_name))
                iface_table.setItem(idx, 1, QTableWidgetItem(p2_name))
                
                # بهبود نمایش رنگ - ایجاد مربع رنگی بزرگتر با متن
                color_item = QTableWidgetItem()
                
                # ایجاد یک پیکسل‌مپ بزرگتر برای نمایش بهتر
                pixmap = QPixmap(32, 20)
                pixmap.fill(QColor(color))
                color_item.setIcon(QIcon(pixmap))
                
                # پیدا کردن نام رنگ از PREDEFINED_COLORS
                color_name = "Custom"
                for name, hex_code in PREDEFINED_COLORS:
                    if hex_code.upper() == color.upper():
                        color_name = name
                        break
                
                # نمایش نام رنگ در کنار آیکون
                color_item.setText(color_name)
                color_item.setTextAlignment(Qt.AlignCenter)
                
                iface_table.setItem(idx, 2, color_item)

                # ادامه کد برای دکمه‌های Edit و Delete
                edit_btn = create_styled_button("✏️ Edit", "small")
                edit_btn.setEnabled(is_sys)
                edit_btn.clicked.connect(lambda _, iid=iface_id: on_edit(iid))
                iface_table.setCellWidget(idx, 3, edit_btn)

                delete_btn = create_styled_button("🗑️ Delete", "small")
                delete_btn.setEnabled(is_sys)
                delete_btn.clicked.connect(lambda _, iid=iface_id: on_delete(iid))
                iface_table.setCellWidget(idx, 4, delete_btn)

            
            for col in range(iface_table.columnCount()):
                iface_table.setColumnWidth(col, 150)

        refresh_cell_list()

        # --- Delete handler ---
        def on_delete(iid):
            confirm = QMessageBox.question(dialog, "Confirm Delete", "Delete this interface?")
            if confirm == QMessageBox.Yes:
                if not self._ensure_project_selected():
                    return
                project_id = get_current_project_id()
                try:
                    with get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM interfaces WHERE id = %s AND project_id = %s", (iid, project_id))
                        conn.commit()
                        existing_ifaces[:] = [e for e in existing_ifaces if e[4] != iid]
                        self.load_matrix_data()
                        self.interface_changed.emit()
                        refresh_cell_list()
                except Exception as e:
                    QMessageBox.critical(dialog, "DB Error", f"Failed to delete: {e}")

        # --- Edit handler ---
        def on_edit(iid):
            self._edit_interface_in_dialog(iid, refresh_cell_list)

        # --- Add New Interface section ---
        add_new_group = QGroupBox("Add New Pin-to-Pin Interface")
        add_new_group.setFont(QFont("Roboto Mono", 13, QFont.Bold))
        add_new_layout = QFormLayout(add_new_group)
        add_new_layout.setLabelAlignment(Qt.AlignLeft)
        
        pin1_combo = QComboBox()
        pin1_combo.setFont(QFont("Roboto Mono", 11))
        for p_id, p_name_numbered in pins1:
            pin1_combo.addItem(p_name_numbered, p_id)
        add_new_layout.addRow(f"Pin from {connector1_name}:", pin1_combo)
        
        pin2_combo = QComboBox()
        pin2_combo.setFont(QFont("Roboto Mono", 11))
        for p_id, p_name_numbered in pins2:
            pin2_combo.addItem(p_name_numbered, p_id)
        add_new_layout.addRow(f"Pin from {connector2_name}:", pin2_combo)
        
        color_combo = QComboBox()
        color_combo.setFont(QFont("Roboto Mono", 11))
        for color_name, color_hex in PREDEFINED_COLORS:
            pixmap = QPixmap(16, 16)
            pixmap.fill(QColor(color_hex))
            icon = QIcon(pixmap)
            color_combo.addItem(icon, color_name)
        color_combo.setCurrentText("Blue")
        add_new_layout.addRow("Interface Color:", color_combo)

        # define handler BEFORE connecting
        def on_add():
            if not self._ensure_project_selected():
                return
            if not auth.is_system():
                QMessageBox.warning(dialog, "Access denied", "Only 'system' can add interfaces.")
                return

            project_id = get_current_project_id()

            p1_id = pin1_combo.currentData()
            p2_id = pin2_combo.currentData()
            color_index = color_combo.currentIndex()

            if not p1_id or not p2_id or p1_id == p2_id:
                QMessageBox.warning(dialog, "Error", "Select two different pins.")
                return

            color_hex = PREDEFINED_COLORS[color_index][1]

            # use DB-guarded helper (prevents duplicates both directions)
            ok, msg = add_interface_guarded(auth.user_id, p1_id, p2_id, color_hex, project_id)
            if not ok:
                # e.g., "Interface already exists."
                QMessageBox.information(dialog, "Info", msg)
                return

            # refresh UI once
            self.load_matrix_data()
            self.interface_changed.emit()
            dialog.accept()


        add_btn = create_styled_button("➕ Add This Pin Interface", "normal")
        add_btn.setEnabled(is_sys)
        add_btn.setFont(QFont("Roboto Mono", 11, QFont.Bold))
        add_new_layout.addRow(add_btn)

        if is_sys:
            add_btn.clicked.connect(on_add)
        else:
            add_btn.clicked.connect(lambda: QMessageBox.warning(dialog, "Access denied", "Only 'system' can add interfaces."))

        layout.addWidget(add_new_group)

        # --- Close Button ---
        close_btn = create_styled_button("❌ Close", "large")
        close_btn.clicked.connect(dialog.reject)

        add_btn.clicked.connect(on_add)
        return dialog

    def apply_interface_table_style(self, table):
        table_style = f"""
            QTableWidget {{
                background: {theme_manager.get_color('primary_dark')};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                font-weight: {Typography.WEIGHT_MEDIUM};
                border: 1px solid {theme_manager.get_color('primary_light')};
                outline: none;
                selection-background-color: rgba(74, 144, 226, 40);
                show-decoration-selected: 1;
                alternate-background-color: rgba(62, 66, 99, 20);
                gridline-color: rgba(44, 62, 80, 50);
            }}
            QTableWidget::item {{
                border: 1px solid rgba(74, 144, 226, 40);
                padding: {Spacing.LG} {Spacing.MD};
                margin: 2px 0px;
                border-radius: {BorderRadius.MEDIUM};
                min-height: 30px;
            }}
            QHeaderView::section {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                color: white;
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {Spacing.LG};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.SMALL};
            }}
        """
        table.setStyleSheet(table_style)

    def _edit_interface_in_dialog(self, iface_id, refresh_callback):
        """
        Edit interface in dialog and refresh the given callback after success.
        """
        if not self._ensure_project_selected():
            return
            
        project_id = get_current_project_id()
        
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT pin1_id, pin2_id, color FROM interfaces WHERE id = %s AND project_id = %s", 
                            (iface_id, project_id))
                data = cursor.fetchone()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading interface: {str(e)}")
            return
            
        if not data:
            QMessageBox.critical(self, "Error", "Interface not found.")
            return
        pin1_id, pin2_id, color = data

        dialog = AddInterfaceDialog(self)
        auto_style_widget(dialog)  # اعمال استایل خودکار

        # Load pin details for Side 1 (pin1_id)
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT s.id AS sub_id, s.name AS sub_name, m.id AS mod_id, m.name AS mod_name,
                        c.id AS con_id, c.name AS con_name, p.pin_number
                    FROM pins p
                    JOIN connectors c ON p.connector_id = c.id AND c.project_id = %s
                    JOIN modules m ON c.module_id = m.id AND m.project_id = %s
                    JOIN subsystems s ON m.subsystem_id = s.id AND s.project_id = %s
                    WHERE p.id = %s AND p.project_id = %s
                """, (project_id, project_id, project_id, pin1_id, project_id))
                pin1_data = cursor.fetchone()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading pin1 details: {str(e)}")
            return
            
        if pin1_data:
            sub_id, sub_name, mod_id, mod_name, con_id, con_name, pin_number = pin1_data
            index = dialog.side1_subsystem_combo.findData(sub_id)
            if index != -1:
                dialog.side1_subsystem_combo.setCurrentIndex(index)
            dialog.load_side1_modules()
            index = dialog.side1_module_combo.findData(mod_id)
            if index != -1:
                dialog.side1_module_combo.setCurrentIndex(index)
            dialog.load_side1_connectors()
            index = dialog.side1_connector_combo.findData(con_id)
            if index != -1:
                dialog.side1_connector_combo.setCurrentIndex(index)
            dialog.load_side1_pins()
            index = dialog.side1_pin_combo.findData(pin1_id)
            if index != -1:
                dialog.side1_pin_combo.setCurrentIndex(index)

        # Load pin details for Side 2 (pin2_id)
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT s.id AS sub_id, s.name AS sub_name, m.id AS mod_id, m.name AS mod_name,
                        c.id AS con_id, c.name AS con_name, p.pin_number
                    FROM pins p
                    JOIN connectors c ON p.connector_id = c.id AND c.project_id = %s
                    JOIN modules m ON c.module_id = m.id AND m.project_id = %s
                    JOIN subsystems s ON m.subsystem_id = s.id AND s.project_id = %s
                    WHERE p.id = %s AND p.project_id = %s
                """, (project_id, project_id, project_id, pin2_id, project_id))
                pin2_data = cursor.fetchone()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading pin2 details: {str(e)}")
            return
            
        if pin2_data:
            sub_id, sub_name, mod_id, mod_name, con_id, con_name, pin_number = pin2_data
            index = dialog.side2_subsystem_combo.findData(sub_id)
            if index != -1:
                dialog.side2_subsystem_combo.setCurrentIndex(index)
            dialog.load_side2_modules()
            index = dialog.side2_module_combo.findData(mod_id)
            if index != -1:
                dialog.side2_module_combo.setCurrentIndex(index)
            dialog.load_side2_connectors()
            index = dialog.side2_connector_combo.findData(con_id)
            if index != -1:
                dialog.side2_connector_combo.setCurrentIndex(index)
            dialog.load_side2_pins()
            index = dialog.side2_pin_combo.findData(pin2_id)
            if index != -1:
                dialog.side2_pin_combo.setCurrentIndex(index)

        # Set color
        for i in range(dialog.color_combo.count()):
            if PREDEFINED_COLORS[i][1] == color:
                dialog.color_combo.setCurrentIndex(i)
                break

        if dialog.exec_() == QDialog.Accepted:
            new_data = dialog.get_data()
            if new_data:
                new_p1, new_p2, new_color = new_data
                try:
                    with get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE interfaces SET pin1_id=%s, pin2_id=%s, color=%s WHERE id=%s AND project_id=%s",
                            (new_p1, new_p2, new_color, iface_id, project_id)
                        )
                        conn.commit()
                        QMessageBox.information(self, "Success", "Interface updated.")
                        self.load_matrix_data()
                        self.interface_changed.emit()
                        if refresh_callback:
                            refresh_callback()
                except Exception as e:
                    QMessageBox.critical(self, "DB Error", f"Failed to update: {e}")


    def _get_pins_for_connector(self, connector_id):
        """
        Retrieve and cache all pins for a given connector.
        """
        if not self._ensure_project_selected():
            return []
            
        project_id = get_current_project_id()
        
        if connector_id in self.connector_data_cache:
            return self.connector_data_cache[connector_id]
            
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, name, pin_number FROM pins WHERE connector_id = %s AND project_id = %s ORDER BY pin_number",
                    (connector_id, project_id)
                )
                pins = [(pin_id, f"Pin {pin_num or 'N/A'}: {pin_name}") for pin_id, pin_name, pin_num in cursor.fetchall()]
            self.connector_data_cache[connector_id] = pins
            return pins
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading pins: {str(e)}")
            return []