# wiring_utils.py - مهاجرت یافته به سیستم استایل جدید

from PyQt5.QtGui import QPixmap, QIcon, QColor, QFont
from PyQt5.QtWidgets import (QStyledItemDelegate, QDialog, 
                             QFormLayout, QComboBox, QDoubleSpinBox,
                             QMessageBox, QGroupBox, QLabel, QHBoxLayout, QVBoxLayout)
from PyQt5.QtCore import Qt

# Import database functions - CHANGED
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import (  # اضافه شده
    get_connection,
    get_current_project_id,
    classify_pin,
    pins_connectable_from_data,
)

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

def pin_type_label(pin_type, is_ground=False, value=None):
    """Short human-readable label for a pin's electrical type."""
    c = classify_pin(pin_type, is_ground, value)
    if c["class"] == "ground":
        return "GND"
    if c["class"] == "power":
        return f"{c['type']} {c['voltage']:g}V" if c["voltage"] else c["type"]
    if c["class"] == "untyped":
        return "no type"
    return c["type"]


def _pin_label(info):
    """Label for a pin combo item, including its electrical type."""
    num = info.get("pin_number")
    name = info.get("name")
    base = f"Pin {num}: {name}" if name else f"Pin {num}"
    type_txt = pin_type_label(info.get("pin_type"), info.get("is_ground"), info.get("value"))
    return f"{base} ({type_txt})"


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

        # Cached full pin lists (with type info) per side, used to filter
        # the opposite side's combo by the same-type wiring rule.
        self._side1_pins = []
        self._side2_pins = []

        # Main vertical layout for top content and bottom buttons
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # Top section: Side 1 and Side 2 side by side
        self.create_top_section(main_layout)

        # Color selection
        self.create_color_section(main_layout)

        # Connection current (the current lives on the connection itself)
        self.create_current_section(main_layout)

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

    def create_current_section(self, main_layout):
        """Connection current in mA — decided per connection, not per pin."""
        current_layout = QHBoxLayout()
        current_label = QLabel("⚡ Current (mA):")
        self.current_spin = QDoubleSpinBox()
        self.current_spin.setFont(QFont("Roboto Mono", 11))
        self.current_spin.setRange(0.0, 1000000.0)
        self.current_spin.setDecimals(1)
        self.current_spin.setValue(0.0)
        self.current_spin.setSuffix(" mA")

        current_layout.addWidget(current_label)
        current_layout.addWidget(self.current_spin)
        current_layout.addStretch()
        main_layout.addLayout(current_layout)

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

        # Same-type filtering between the two pin combos
        self.side1_pin_combo.currentIndexChanged.connect(self._on_side1_pin_changed)
        self.side2_pin_combo.currentIndexChanged.connect(self._on_side2_pin_changed)

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
    def _fill_pin_combo(self, combo, pin_infos, reference=None):
        """
        Populate a pin combo from cached pin info. When `reference` (a pin
        info dict) is given, only pins compatible with it are listed.
        """
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("Select Pin", None)
            for info in pin_infos:
                if reference is not None:
                    ok, _ = pins_connectable_from_data(reference, info)
                    if not ok:
                        continue
                combo.addItem(_pin_label(info), info["id"])
        finally:
            combo.blockSignals(False)

    def _pin_info(self, side, pin_id):
        """Return the cached pin info dict for a side, or None."""
        infos = self._side1_pins if side == "side1" else self._side2_pins
        for info in infos:
            if info["id"] == pin_id:
                return info
        return None

    def _on_side1_pin_changed(self, index):
        """A side-1 pin was picked: show only compatible side-2 pins."""
        ref = self._pin_info("side1", self.side1_pin_combo.currentData())
        self._fill_pin_combo(self.side2_pin_combo, self._side2_pins, reference=ref)

    def _on_side2_pin_changed(self, index):
        """A side-2 pin was picked: show only compatible side-1 pins."""
        ref = self._pin_info("side2", self.side2_pin_combo.currentData())
        self._fill_pin_combo(self.side1_pin_combo, self._side1_pins, reference=ref)

    def load_side1_pins(self):
        """Load pins based on selected Side 1 connector."""
        con_id = self.side1_connector_combo.currentData()
        if con_id is None or not _ensure_project_selected():
            return

        project_id = get_current_project_id()

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, pin_number, name, pin_type, is_ground, value "
                    "FROM pins WHERE connector_id = %s AND project_id = %s ORDER BY pin_number",
                    (con_id, project_id),
                )
                self._side1_pins = [
                    {
                        "id": pid,
                        "pin_number": num,
                        "name": pname,
                        "pin_type": ptype,
                        "is_ground": bool(isg) if isg is not None else False,
                        "value": val,
                    }
                    for pid, num, pname, ptype, isg, val in cur.fetchall()
                ]
        except Exception as e:
            print(f"Error loading pins: {e}")
            self._side1_pins = []

        # Restrict to pins compatible with the currently selected side-2 pin
        ref = self._pin_info("side2", self.side2_pin_combo.currentData())
        self._fill_pin_combo(self.side1_pin_combo, self._side1_pins, reference=ref)

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
        con_id = self.side2_connector_combo.currentData()
        if con_id is None or not _ensure_project_selected():
            return

        project_id = get_current_project_id()

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, pin_number, name, pin_type, is_ground, value "
                    "FROM pins WHERE connector_id = %s AND project_id = %s ORDER BY pin_number",
                    (con_id, project_id),
                )
                self._side2_pins = [
                    {
                        "id": pid,
                        "pin_number": num,
                        "name": pname,
                        "pin_type": ptype,
                        "is_ground": bool(isg) if isg is not None else False,
                        "value": val,
                    }
                    for pid, num, pname, ptype, isg, val in cur.fetchall()
                ]
        except Exception as e:
            print(f"Error loading pins: {e}")
            self._side2_pins = []

        # Restrict to pins compatible with the currently selected side-1 pin
        ref = self._pin_info("side1", self.side1_pin_combo.currentData())
        self._fill_pin_combo(self.side2_pin_combo, self._side2_pins, reference=ref)

    def get_data(self):
        """Return (pin1_id, pin2_id, hex_color, current_mA) or None on invalid."""
        p1 = self.side1_pin_combo.currentData()
        p2 = self.side2_pin_combo.currentData()
        if p1 is None or p2 is None or p1 == p2:
            QMessageBox.warning(self, "Selection Error", "Please select two different pins.")
            return None
        color = PREDEFINED_COLORS[self.color_combo.currentIndex()][1]
        current = self.current_spin.value()
        return p1, p2, color, current