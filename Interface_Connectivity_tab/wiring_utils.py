# wiring_utils.py - مهاجرت یافته به سیستم استایل جدید

from PyQt5.QtGui import QPixmap, QIcon, QColor, QFont
from PyQt5.QtWidgets import (QStyledItemDelegate, QDialog, 
                             QFormLayout, QComboBox, QDialogButtonBox,
                             QMessageBox, QGroupBox, QLabel, QHBoxLayout, QVBoxLayout)
from PyQt5.QtCore import Qt

# Import database functions - CHANGED
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import get_connection, get_current_project_id  # اضافه شده

# Import new style system
from styles.style_manager import (style_manager, register_widget, 
                                create_styled_button, auto_style_widget)
from styles.design_system import Colors, Typography, Spacing, BorderRadius
from styles.theme_manager import theme_manager

PREDEFINED_COLORS = [
    ("Green", "#008000"),
    ("Blue", "#3838DA"),
    ("Red", "#FF0000"),
    ("Yellow", "#FFFF00"),
    ("Purple", "#800080"),
    ("Orange", "#FFA500"),
    ("Pink", "#FF69B4"),
    ("Cyan", "#00FFFF"),
    ("Gray", "#808080"),
    ("Black", "#000000")
]

def color_icon(hex_color):
    pixmap = QPixmap(18, 18)
    pixmap.fill(QColor(hex_color))
    return QIcon(pixmap)

def _ensure_project_selected():
    project_id = get_current_project_id()
    if project_id is None:
        return False
    return True

def get_all_pins_with_full_numbered_name():
    pins_map = {}
    
    if not _ensure_project_selected():
        return pins_map
        
    project_id = get_current_project_id()
    
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.id, p.name, p.pin_number, c.name, m.name
                FROM pins p
                JOIN connectors c ON p.connector_id = c.id AND c.project_id = %s
                JOIN modules m ON c.module_id = m.id AND m.project_id = %s
                WHERE p.project_id = %s
                ORDER BY m.name, c.name, p.pin_number
            """, (project_id, project_id, project_id))
            
            for pin_id, name, num, conn_name, mod_name in cursor.fetchall():
                full = f"{mod_name} - {conn_name} - Pin {num or 'N/A'}: {name}"
                pins_map[pin_id] = full
    except Exception as e:
        print(f"Error loading pins: {e}")
        
    return pins_map

class CenteredIconDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        icon = index.data(Qt.DecorationRole)
        if icon:
            rect = option.rect
            size = min(rect.width(), rect.height()) - 8
            pix = icon.pixmap(size, size)
            x = rect.x() + (rect.width() - size) // 2
            y = rect.y() + (rect.height() - size) // 2
            painter.drawPixmap(x, y, pix)
        else:
            super().paint(painter, option, index)

class AddInterfaceDialog(QDialog):
    """Dialog for adding or editing an Interface with chained combo boxes."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add/Edit Interface")
        self.setMinimumWidth(600)
        
        self.setObjectName("AddInterfaceDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)
        
        self.apply_dialog_style()
        
        style_manager.theme_changed.connect(self.on_theme_changed)

        # Main vertical layout for top content and bottom buttons
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # Top section: Side 1 and Side 2 side by side
        self.create_top_section(main_layout)

        # Color selection
        self.create_color_section(main_layout)

        # Buttons (OK and Cancel)
        self.create_button_section(main_layout)

        # Load initial subsystems for both sides
        self.load_subsystems(self.side1_subsystem_combo)
        self.load_subsystems(self.side2_subsystem_combo)

        # Connect signals for chaining
        self.connect_signals()

    def apply_dialog_style(self):
        style = f"""
            QDialog#AddInterfaceDialog {{
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
            QComboBox::drop-down {{
                border: none;
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                height: 20px;
            }}
            QComboBox::drop-down:hover {{
                background: rgba(74, 144, 226, 30);
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

    def create_top_section(self, main_layout):
        """ایجاد بخش بالایی با دو طرف"""
        top_layout = QHBoxLayout()

        # Left section: Side 1 in a QGroupBox panel
        self.side1_group = QGroupBox("🔌 Side 1")
        self.side1_group.setFont(QFont("Roboto Mono", 13, QFont.Bold))
        side1_layout = QVBoxLayout(self.side1_group)

        self.side1_subsystem_combo = QComboBox()
        self.side1_module_combo = QComboBox()
        self.side1_connector_combo = QComboBox()
        self.side1_pin_combo = QComboBox()

        left_form = QFormLayout()
        left_form.setLabelAlignment(Qt.AlignLeft)
        left_form.addRow(QLabel("Subsystem:", self), self.side1_subsystem_combo)
        left_form.addRow(QLabel("Module:", self), self.side1_module_combo)
        left_form.addRow(QLabel("Connector:", self), self.side1_connector_combo)
        left_form.addRow(QLabel("Pin:", self), self.side1_pin_combo)
        side1_layout.addLayout(left_form)
        top_layout.addWidget(self.side1_group)

        # Right section: Side 2 in a QGroupBox panel
        self.side2_group = QGroupBox("🔌 Side 2")
        self.side2_group.setFont(QFont("Roboto Mono", 13, QFont.Bold))
        side2_layout = QVBoxLayout(self.side2_group)

        self.side2_subsystem_combo = QComboBox()
        self.side2_module_combo = QComboBox()
        self.side2_connector_combo = QComboBox()
        self.side2_pin_combo = QComboBox()

        right_form = QFormLayout()
        right_form.setLabelAlignment(Qt.AlignLeft)
        right_form.addRow(QLabel("Subsystem:", self), self.side2_subsystem_combo)
        right_form.addRow(QLabel("Module:", self), self.side2_module_combo)
        right_form.addRow(QLabel("Connector:", self), self.side2_connector_combo)
        right_form.addRow(QLabel("Pin:", self), self.side2_pin_combo)
        side2_layout.addLayout(right_form)
        top_layout.addWidget(self.side2_group)

        main_layout.addLayout(top_layout)

        # Set font for all combos
        font = QFont("Roboto Mono", 11)
        for widget in (self.side1_subsystem_combo, self.side1_module_combo, self.side1_connector_combo,
                      self.side1_pin_combo, self.side2_subsystem_combo, self.side2_module_combo,
                      self.side2_connector_combo, self.side2_pin_combo):
            widget.setFont(font)

    def create_color_section(self, main_layout):
        """ایجاد بخش انتخاب رنگ"""
        color_layout = QHBoxLayout()
        color_label = QLabel("🎨 Interface Color:")
        self.color_combo = QComboBox()
        self.color_combo.setFont(QFont("Roboto Mono", 11))
        
        for color_name, color_hex in PREDEFINED_COLORS:
            pixmap = QPixmap(20, 20)
            pixmap.fill(QColor(color_hex))
            icon = QIcon(pixmap)
            self.color_combo.addItem(icon, color_name)
        
        color_layout.addWidget(color_label)
        color_layout.addWidget(self.color_combo)
        color_layout.addStretch()
        main_layout.addLayout(color_layout)

    def create_button_section(self, main_layout):
        # دکمه‌های محلی: استایل بگیرند اما register نشوند
        ok_btn = create_styled_button("✅ OK", "large", register_global=False)
        cancel_btn = create_styled_button("❌ Cancel", "large", register_global=False)

        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        button_layout.addStretch()
        main_layout.addLayout(button_layout)


    def connect_signals(self):
        """اتصال سیگنال‌ها برای زنجیره‌ای شدن"""
        # Connect signals for chaining (Side 1)
        self.side1_subsystem_combo.currentIndexChanged.connect(self.load_side1_modules)
        self.side1_module_combo.currentIndexChanged.connect(self.load_side1_connectors)
        self.side1_connector_combo.currentIndexChanged.connect(self.load_side1_pins)

        # Connect signals for chaining (Side 2)
        self.side2_subsystem_combo.currentIndexChanged.connect(self.load_side2_modules)
        self.side2_module_combo.currentIndexChanged.connect(self.load_side2_connectors)
        self.side2_connector_combo.currentIndexChanged.connect(self.load_side2_pins)

    def on_theme_changed(self, theme_name):
        self.apply_dialog_style()

    def load_subsystems(self, combo):
        """Load all subsystems into the combo box."""
        combo.clear()
        combo.addItem("Select Subsystem", None)
        
        if not _ensure_project_selected():
            return
            
        project_id = get_current_project_id()
        
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, name FROM subsystems WHERE project_id = %s ORDER BY name", (project_id,))
                for sub_id, name in cur.fetchall():
                    combo.addItem(name, sub_id)
        except Exception as e:
            print(f"Error loading subsystems: {e}")

    def load_side1_modules(self):
        """Load modules based on selected Side 1 subsystem."""
        self.side1_module_combo.clear()
        self.side1_connector_combo.clear()
        self.side1_pin_combo.clear()
        
        sub_id = self.side1_subsystem_combo.currentData()
        if sub_id is None or not _ensure_project_selected():
            return
            
        project_id = get_current_project_id()
        
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, name FROM modules WHERE subsystem_id = %s AND project_id = %s ORDER BY name", (sub_id, project_id))
                self.side1_module_combo.addItem("Select Module", None)
                for mod_id, name in cur.fetchall():
                    self.side1_module_combo.addItem(name, mod_id)
        except Exception as e:
            print(f"Error loading modules: {e}")

    # 8. اصلاح متد load_side1_connectors
    def load_side1_connectors(self):
        """Load connectors based on selected Side 1 module."""
        self.side1_connector_combo.clear()
        self.side1_pin_combo.clear()
        
        mod_id = self.side1_module_combo.currentData()
        if mod_id is None or not _ensure_project_selected():
            return
            
        project_id = get_current_project_id()
        
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, name FROM connectors WHERE module_id = %s AND project_id = %s ORDER BY name", (mod_id, project_id))
                self.side1_connector_combo.addItem("Select Connector", None)
                for con_id, name in cur.fetchall():
                    self.side1_connector_combo.addItem(name, con_id)
        except Exception as e:
            print(f"Error loading connectors: {e}")

    # 9. اصلاح متد load_side1_pins
    def load_side1_pins(self):
        """Load pins based on selected Side 1 connector."""
        self.side1_pin_combo.clear()
        
        con_id = self.side1_connector_combo.currentData()
        if con_id is None or not _ensure_project_selected():
            return
            
        project_id = get_current_project_id()
        
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, pin_number, name FROM pins WHERE connector_id = %s AND project_id = %s ORDER BY pin_number", (con_id, project_id))
                self.side1_pin_combo.addItem("Select Pin", None)
                for pin_id, pin_number, pin_name in cur.fetchall():
                    display_text = f"Pin {pin_number}: {pin_name}" if pin_name else f"Pin {pin_number}"
                    self.side1_pin_combo.addItem(display_text, pin_id)
        except Exception as e:
            print(f"Error loading pins: {e}")

    # 10. اصلاح متد load_side2_modules
    def load_side2_modules(self):
        """Load modules based on selected Side 2 subsystem."""
        self.side2_module_combo.clear()
        self.side2_connector_combo.clear()
        self.side2_pin_combo.clear()
        
        sub_id = self.side2_subsystem_combo.currentData()
        if sub_id is None or not _ensure_project_selected():
            return
            
        project_id = get_current_project_id()
        
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, name FROM modules WHERE subsystem_id = %s AND project_id = %s ORDER BY name", (sub_id, project_id))
                self.side2_module_combo.addItem("Select Module", None)
                for mod_id, name in cur.fetchall():
                    self.side2_module_combo.addItem(name, mod_id)
        except Exception as e:
            print(f"Error loading modules: {e}")

    # 11. اصلاح متد load_side2_connectors
    def load_side2_connectors(self):
        """Load connectors based on selected Side 2 module."""
        self.side2_connector_combo.clear()
        self.side2_pin_combo.clear()
        
        mod_id = self.side2_module_combo.currentData()
        if mod_id is None or not _ensure_project_selected():
            return
            
        project_id = get_current_project_id()
        
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, name FROM connectors WHERE module_id = %s AND project_id = %s ORDER BY name", (mod_id, project_id))
                self.side2_connector_combo.addItem("Select Connector", None)
                for con_id, name in cur.fetchall():
                    self.side2_connector_combo.addItem(name, con_id)
        except Exception as e:
            print(f"Error loading connectors: {e}")

    # 12. اصلاح متد load_side2_pins
    def load_side2_pins(self):
        """Load pins based on selected Side 2 connector."""
        self.side2_pin_combo.clear()
        
        con_id = self.side2_connector_combo.currentData()
        if con_id is None or not _ensure_project_selected():
            return
            
        project_id = get_current_project_id()
        
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, pin_number, name FROM pins WHERE connector_id = %s AND project_id = %s ORDER BY pin_number", (con_id, project_id))
                self.side2_pin_combo.addItem("Select Pin", None)
                for pin_id, pin_number, pin_name in cur.fetchall():
                    display_text = f"Pin {pin_number}: {pin_name}" if pin_name else f"Pin {pin_number}"
                    self.side2_pin_combo.addItem(display_text, pin_id)
        except Exception as e:
            print(f"Error loading pins: {e}")

    def get_data(self):
        """Return (pin1_id, pin2_id, hex_color) or None on invalid."""
        p1 = self.side1_pin_combo.currentData()
        p2 = self.side2_pin_combo.currentData()
        if p1 is None or p2 is None or p1 == p2:
            QMessageBox.warning(self, "Selection Error", "Please select two different pins.")
            return None
        color = PREDEFINED_COLORS[self.color_combo.currentIndex()][1]
        return p1, p2, color