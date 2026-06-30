# Interfaces_list_panel.py 
import os
import sys
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QLabel, QTableWidget,
                             QTableWidgetItem, QPushButton, QHBoxLayout,
                             QMessageBox, QDialog, QFileDialog, QHeaderView, QSizePolicy)
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtCore import Qt, pyqtSignal, QTimer

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from auth_manager import auth
from database import (
    get_connection, get_current_project_id,
    add_interface_guarded, update_interface_guarded, delete_interface_guarded
)
from Interface_Connectivity_tab.wiring_utils import (AddInterfaceDialog, get_all_pins_with_full_numbered_name,
                           color_icon, PREDEFINED_COLORS, CenteredIconDelegate)

# Import new style system
from styles.style_manager import (style_manager, register_widget, 
                                create_styled_button, auto_style_widget)
from styles.design_system import Colors, Typography, Spacing, BorderRadius
from styles.theme_manager import theme_manager

class InterfacesListPanel(QWidget):
    interface_changed = pyqtSignal()
    interface_edited = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_app = parent
        
        self.setObjectName("InterfacesContainer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        
        # اعمال استایل پایه
        self.apply_main_panel_style()
        
        # اتصال به تغییر تم
        style_manager.theme_changed.connect(self.on_theme_changed)

        # --- Layout ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        # Header Widget
        self.create_header_widget(layout)

        # Table for all interfaces
        self.create_table_widget(layout)

        # Control buttons
        self.create_control_buttons(layout)

        # --- Connect signals ---
        self.connect_signals()

        self.load_interfaces()
        self.table.setItemDelegateForColumn(8, CenteredIconDelegate(self.table))

    def apply_main_panel_style(self):
        """اعمال استایل به پنل اصلی"""
        style = f"""
            QWidget#InterfacesContainer {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.LARGE};
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
        self.header_widget = QWidget()
        self.header_widget.setFixedHeight(40)
        
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(12, 0, 12, 0)
        
        label = QLabel("🌐 All Interfaces List")
        label.setFont(QFont("Roboto Mono", 14, QFont.Bold))
        header_layout.addWidget(label)
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

    def create_table_widget(self, layout):
        """ایجاد ویجت جدول"""
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "Subsystem 1", "Module 1", "Connector 1", "Pin 1",
            "Subsystem 2", "Module 2", "Connector 2", "Pin 2", "Connection Color"
        ])
        
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setMinimumSectionSize(100)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        
        layout.addWidget(self.table)
        
        # اعمال استایل جدول
        self.update_table_style()

    def update_table_style(self):
        """بروزرسانی استایل جدول"""
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
        self.table.setStyleSheet(table_style)

    def create_control_buttons(self, layout):
        """ایجاد دکمه‌های کنترل"""
        self.control_widget = QWidget()
        self.control_widget.setFixedHeight(60)
        
        buttons_layout = QHBoxLayout(self.control_widget)
        buttons_layout.setContentsMargins(10, 8, 10, 8)
        buttons_layout.setSpacing(10)

        # ایجاد دکمه‌ها با سیستم جدید
        self.add_btn = create_styled_button("➕ Add Interface", "normal")
        self.edit_btn = create_styled_button("✏️ Edit Selected", "normal")
        self.export_btn = create_styled_button("📊 Export CSV", "normal")
        self.delete_btn = create_styled_button("🗑️ Delete Selected", "normal")
        
        # تنظیم فونت
        btn_font = QFont("Roboto Mono", 11, QFont.Medium)
        for btn in (self.add_btn, self.edit_btn, self.export_btn, self.delete_btn):
            btn.setFont(btn_font)
        
        # چیدمان دکمه‌ها
        left_btns = QHBoxLayout()
        for btn in (self.add_btn, self.edit_btn, self.export_btn):
            left_btns.addWidget(btn)
        
        right_btns = QHBoxLayout()
        right_btns.addWidget(self.delete_btn)
        right_btns.addStretch(1)

        buttons_layout.addLayout(left_btns)
        buttons_layout.addStretch(1)
        buttons_layout.addLayout(right_btns)
        
        layout.addWidget(self.control_widget)
        
        # اعمال استایل کنترل ویجت
        self.update_control_widget_style()
        # apply access policy once and on auth changes
        self.apply_access_policy()
        auth.auth_changed.connect(self.apply_access_policy)


    def update_control_widget_style(self):
        """بروزرسانی استایل ویجت کنترل"""
        control_style = f"""
            QWidget {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:0, y2:1")};
                border-radius: {BorderRadius.LARGE};
                border: 1px solid {theme_manager.get_color('primary_light')};
            }}
        """
        self.control_widget.setStyleSheet(control_style)

    def connect_signals(self):
        """اتصال سیگنال‌ها"""
        self.add_btn.clicked.connect(self.handle_add)
        self.edit_btn.clicked.connect(self.handle_edit)
        self.export_btn.clicked.connect(self.handle_export_csv)
        self.delete_btn.clicked.connect(self.handle_delete)
        self.table.cellDoubleClicked.connect(self.handle_double_click)

    def on_theme_changed(self, theme_name):
        """هندل تغییر تم"""
        self.apply_main_panel_style()
        self.update_header_style()
        self.update_table_style()
        self.update_control_widget_style()

    def load_interfaces(self):
        if not self._ensure_project_selected():
            self.table.setRowCount(0)
            return
            
        project_id = get_current_project_id()
        self.table.setRowCount(0)
        
        try:
            all_pins_map = get_all_pins_with_full_numbered_name()
            
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 
                        s1.name, m1.name, c1.name, p1.id,
                        s2.name, m2.name, c2.name, p2.id,
                        con.id, con.color
                    FROM interfaces con
                    JOIN pins p1 ON con.pin1_id = p1.id AND p1.project_id = %s
                    JOIN connectors c1 ON p1.connector_id = c1.id AND c1.project_id = %s
                    JOIN modules m1 ON c1.module_id = m1.id AND m1.project_id = %s
                    LEFT JOIN subsystems s1 ON m1.subsystem_id = s1.id AND s1.project_id = %s
                    JOIN pins p2 ON con.pin2_id = p2.id AND p2.project_id = %s
                    JOIN connectors c2 ON p2.connector_id = c2.id AND c2.project_id = %s
                    JOIN modules m2 ON c2.module_id = m2.id AND m2.project_id = %s
                    LEFT JOIN subsystems s2 ON m2.subsystem_id = s2.id AND s2.project_id = %s
                    WHERE con.project_id = %s
                    ORDER BY s1.name, m1.name, c1.name
                """, (project_id, project_id, project_id, project_id, project_id, project_id, project_id, project_id, project_id))
                rows = cursor.fetchall()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading interfaces: {str(e)}")
            return

        self.table.setRowCount(len(rows))
        for row, (s1, m1, c1, p1, s2, m2, c2, p2, iface_id, color) in enumerate(rows):
            self.table.setRowHeight(row, 40)
            p1_display = " ".join(all_pins_map.get(p1, "Unknown Pin").split(' ')[-2:])
            p2_display = " ".join(all_pins_map.get(p2, "Unknown Pin").split(' ')[-2:])
            items = [
                QTableWidgetItem(s1 or ""),
                QTableWidgetItem(m1),
                QTableWidgetItem(c1),
                QTableWidgetItem(p1_display),
                QTableWidgetItem(s2 or ""),
                QTableWidgetItem(m2),
                QTableWidgetItem(c2),
                QTableWidgetItem(p2_display)
            ]
            for col, item in enumerate(items):
                item.setTextAlignment(Qt.AlignCenter)
                item.setFont(QFont("Roboto Mono", 11))
                self.table.setItem(row, col, item)

            color_item = QTableWidgetItem()
            color_item.setIcon(color_icon(color or "#bababa"))
            color_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 8, color_item)

            # Store iface_id in the first column as hidden data
            item = self.table.item(row, 0)
            if item:
                item.setData(Qt.UserRole, iface_id)
            else:
                item = QTableWidgetItem()
                item.setData(Qt.UserRole, iface_id)
                self.table.setItem(row, 0, item)
        
        for col in range(self.table.columnCount()):
            self.table.setColumnWidth(col, 150)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)

    def handle_add(self):
        if not self._ensure_project_selected():
            return
        if not auth.is_system():
            QMessageBox.warning(self, "Access denied", "Only 'system' can add interfaces.")
            return

        project_id = get_current_project_id()
        dialog = AddInterfaceDialog(self)
        auto_style_widget(dialog)

        if dialog.exec_() == QDialog.Accepted:
            data = dialog.get_data()
            if data:
                pin1_id, pin2_id, color = data
                ok, msg = add_interface_guarded(auth.user_id, pin1_id, pin2_id, color, project_id)
                if ok:
                    QMessageBox.information(self, "Success", msg)
                    self.load_interfaces()
                    self.interface_changed.emit()
                else:
                    QMessageBox.information(self, "Info", msg)
    def handle_edit(self):
        if not self._ensure_project_selected():
            return
        if not auth.is_system():
            QMessageBox.warning(self, "Access denied", "Only 'system' can edit interfaces.")
            return

        project_id = get_current_project_id()
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.warning(self, "Selection Error", "Please select an Interface to edit.")
            return
        row = selected[0].row()
        iface_id = self.table.item(row, 0).data(Qt.UserRole)

        # load current values (as before)
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT p1.id, p2.id, con.color
                    FROM interfaces con
                    JOIN pins p1 ON con.pin1_id = p1.id
                    JOIN pins p2 ON con.pin2_id = p2.id
                    WHERE con.id = %s AND con.project_id = %s
                """, (iface_id, project_id))
                data = cur.fetchone()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading interface data: {str(e)}")
            return
        if not data:
            QMessageBox.critical(self, "Error", "Could not find the selected interface in the database.")
            return
        pin1_id, pin2_id, color = data

        dialog = AddInterfaceDialog(self)
        auto_style_widget(dialog)

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
        # ... after pre-filling dialog selections and setting color ...

        if dialog.exec_() == QDialog.Accepted:
            new_data = dialog.get_data()
            if new_data:
                new_pin1_id, new_pin2_id, new_color = new_data
                ok, msg = update_interface_guarded(auth.user_id, iface_id, new_pin1_id, new_pin2_id, new_color, project_id)
                if ok:
                    QMessageBox.information(self, "Success", msg)
                    self.load_interfaces()
                    self.interface_changed.emit()
                else:
                    QMessageBox.warning(self, "Warning", msg)


    def handle_delete(self):
        if not self._ensure_project_selected():
            return
        if not auth.is_system():
            QMessageBox.warning(self, "Access denied", "Only 'system' can delete interfaces.")
            return

        project_id = get_current_project_id()
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.warning(self, "Selection Error", "Please select at least one Interface to delete.")
            return

        confirm = QMessageBox.question(
            self, 'Confirm Delete', f"Delete {len(selected)} selected interface(s)?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            with get_connection() as conn:
                for index in selected:
                    row = index.row()
                    iface_id = self.table.item(row, 0).data(Qt.UserRole)
                    delete_interface_guarded(auth.user_id, iface_id, project_id)
            QMessageBox.information(self, "Success", "Selected interfaces deleted.")
            self.load_interfaces()
            self.interface_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Failed to delete: {e}")

    def handle_export_csv(self):
        """
        Export all interfaces to a CSV file.
        """
        if not self._ensure_project_selected():
            return
            
        project_id = get_current_project_id()
        
        file_path, _ = QFileDialog.getSaveFileName(self, "Save CSV File", "", "CSV Files (*.csv)")
        if not file_path:
            return
        try:
            with open(file_path, 'w', newline='', encoding='utf-8') as csvfile:
                import csv
                writer = csv.writer(csvfile)
                writer.writerow([
                    "Subsystem 1", "Module 1", "Connector 1", "Pin 1",
                    "Subsystem 2", "Module 2", "Connector 2", "Pin 2", "Color"
                ])
                for row in range(self.table.rowCount()):
                    row_data = [self.table.item(row, col).text() if self.table.item(row, col) else ""
                                for col in range(self.table.columnCount())]
                    iface_id = self.table.item(row, 0).data(Qt.UserRole)
                    
                    with get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT color FROM interfaces WHERE id = %s AND project_id = %s", (iface_id, project_id))
                        result = cursor.fetchone()
                        color = result[0] if result else "#0000FF"
                    
                    color_name = next((name for name, hex_code in PREDEFINED_COLORS if hex_code == color), "Unknown")
                    row_data[-1] = color_name
                    writer.writerow(row_data)
            QMessageBox.information(self, "Success", "Interfaces exported to CSV successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export CSV: {e}")

    def handle_double_click(self, row, col):
        if not auth.is_system():
            QMessageBox.warning(self, "Access denied", "Only 'system' can edit interfaces.")
            return
        self.table.selectRow(row)
        self.handle_edit()

    def apply_access_policy(self):
        is_sys = auth.is_system()
        # Only system can add/edit/delete; export always enabled
        self.add_btn.setEnabled(is_sys)
        self.edit_btn.setEnabled(is_sys)
        self.delete_btn.setEnabled(is_sys)
        self.export_btn.setEnabled(True)
