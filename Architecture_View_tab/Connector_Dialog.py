# Connector_Dialog.py - مهاجرت یافته به سیستم استایل جدید

import sys, os
from PyQt5.QtCore import (
    Qt,
    pyqtSignal,
    QTimer,
    QAbstractTableModel,
    QModelIndex,
    QVariant,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QTableView,
    QPushButton,
    QWidget,
    QApplication,
    QMessageBox,
    QHeaderView,
    QCheckBox,
    QSpinBox,
    QStyledItemDelegate,
)
from PyQt5.QtGui import QColor, QPixmap, QPainter, QFont

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import get_connection, get_current_project_id, get_connector_subsystem_id
from access_control import guard_write, can_edit_subsystem
from auth_manager import auth

from Architecture_View_tab.table_navigation import (
    NavigableLineEdit,
    NavigableSpinBox,
    FocusEventFilter,
)

# Import new style system
from styles.style_manager import (
    style_manager,
    register_widget,
    create_styled_button,
    auto_style_widget,
)
from styles.design_system import Colors, Typography, Spacing, BorderRadius
from styles.theme_manager import theme_manager

# -------------------------------------------------------------
# Color-picker combo with swatch
# -------------------------------------------------------------
COLOR_MAP = {
    "Default": "#F8913C",
    "Red": "#FF0000",
    "Green": "#00FF00",
    "Blue": "#0000FF",
    "Yellow": "#FFFF00",
    "Purple": "#800080",
    "Gray": "#5D5A5A",
}


class ColorComboBox(QWidget):
    """Combo-box that shows both the color name and a small colored square."""

    def __init__(self, initial="Default", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.swatch = QLabel()
        self.swatch.setFixedSize(20, 20)
        self.combo.addItems(COLOR_MAP.keys())
        if initial.startswith("#"):
            name = next((n for n, c in COLOR_MAP.items() if c == initial), "Default")
        else:
            name = initial
        self.combo.setCurrentText(name)
        self.combo.currentIndexChanged.connect(self._update_swatch)
        layout.addWidget(self.combo)
        layout.addWidget(self.swatch)
        self._update_swatch()

        # اتصال به تغییر تم
        style_manager.theme_changed.connect(self.apply_theme_style)
        self.apply_theme_style()

    def apply_theme_style(self):
        """اعمال استایل بر اساس تم فعلی"""
        combo_style = f"""
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
        self.combo.setStyleSheet(combo_style)

    def _update_swatch(self):
        hex_color = COLOR_MAP.get(self.combo.currentText(), "#C8C8FF")
        self.swatch.setStyleSheet(
            f"background-color:{hex_color};border:1px solid #bdc3c7;"
        )

    def current_color(self):
        name = self.combo.currentText()
        return COLOR_MAP.get(name, COLOR_MAP["Default"])


# --- Model & Delegates for performance ---------------------------------


class ConnectorTableModel(QAbstractTableModel):
    """Model for connectors table with deferred widget creation."""

    COL_ID = 0
    COL_NAME = 1
    COL_NUM_PINS = 2
    COL_COLOR = 3
    COL_SAVE = 4

    headers = ["ID", "Connector Name", "Num Pins", "Color", "Save"]

    def __init__(self, data=None, parent=None):
        super().__init__(parent)
        self._data = data or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = index.row()
        col = index.column()
        item = self._data[row]
        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == self.COL_ID:
                return str(item.get("id") or "")
            if col == self.COL_NAME:
                return item.get("name", "")
            if col == self.COL_NUM_PINS:
                return item.get("num_pins", 0)
            if col == self.COL_COLOR:
                return item.get("color", "")
            if col == self.COL_SAVE:
                return ""
        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return QVariant()

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsEnabled
        col = index.column()
        if col in (self.COL_NAME, self.COL_NUM_PINS, self.COL_COLOR):
            return Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable
        return Qt.ItemIsSelectable | Qt.ItemIsEnabled

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid():
            return False
        row = index.row()
        col = index.column()
        if role == Qt.EditRole:
            if col == self.COL_NAME:
                self._data[row]["name"] = str(value)
            elif col == self.COL_NUM_PINS:
                try:
                    self._data[row]["num_pins"] = int(value)
                except Exception:
                    self._data[row]["num_pins"] = value
            elif col == self.COL_COLOR:
                self._data[row]["color"] = str(value)
            else:
                return False
            self.dataChanged.emit(index, index)
            return True
        return False

    def insertRowItem(self, item):
        self.beginInsertRows(QModelIndex(), self.rowCount(), self.rowCount())
        self._data.append(item)
        self.endInsertRows()

    def removeRowItem(self, row):
        if 0 <= row < self.rowCount():
            self.beginRemoveRows(QModelIndex(), row, row)
            self._data.pop(row)
            self.endRemoveRows()

    def setAll(self, items):
        self.beginResetModel()
        self._data = items
        self.endResetModel()

    def item(self, row):
        if 0 <= row < len(self._data):
            return self._data[row]
        return None


class ButtonDelegate(QStyledItemDelegate):
    """Delegate for Save/Remove buttons."""

    clicked = pyqtSignal(int)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text

    def paint(self, painter, option, index):
        from PyQt5.QtWidgets import QStyleOptionButton
        from PyQt5.QtWidgets import QStyle

        opt = QStyleOptionButton()
        opt.rect = option.rect.adjusted(4, 6, -4, -6)
        opt.text = self.text
        opt.state = QStyle.State_Enabled
        QApplication.style().drawControl(QStyle.CE_PushButton, opt, painter)

    def editorEvent(self, event, model, option, index):
        if event.type() == event.MouseButtonRelease and option.rect.contains(
            event.pos()
        ):
            self.clicked.emit(index.row())
            return True
        return False


class ColorDelegate(QStyledItemDelegate):
    """Delegate for color picker."""

    def createEditor(self, parent, option, index):
        cur = index.model().data(index, Qt.EditRole)
        editor = ColorComboBox(cur if cur else "Default", parent)
        return editor

    def setEditorData(self, editor, index):
        val = index.model().data(index, Qt.EditRole)
        if isinstance(editor, ColorComboBox):
            if val and val.startswith("#"):
                name = next((n for n, c in COLOR_MAP.items() if c == val), "Default")
                editor.combo.setCurrentText(name)
            else:
                editor.combo.setCurrentText(val or "Default")

    def setModelData(self, editor, model, index):
        if isinstance(editor, ColorComboBox):
            model.setData(index, editor.current_color(), Qt.EditRole)


class IntDelegate(QStyledItemDelegate):
    """Delegate for integer input with validation."""

    def createEditor(self, parent, option, index):
        editor = QSpinBox(parent)
        editor.setRange(0, 1000000)
        return editor

    def setEditorData(self, editor, index):
        val = index.model().data(index, Qt.EditRole)
        try:
            editor.setValue(int(val))
        except Exception:
            editor.setValue(0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.EditRole)


class ConnectorDialog(QDialog):
    """
    Editable table of all connectors for a selected module.
    Per-row save buttons, change tracking, and keyboard navigation.
    """

    connectors_updated = pyqtSignal()

    def __init__(self, module_id=None, connector_data=None, parent=None):
        super().__init__(parent)

        self.setObjectName("ConnectorDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)

        # اعمال استایل پایه
        self.apply_main_dialog_style()

        # اتصال به تغییر تم
        style_manager.theme_changed.connect(self.on_theme_changed)

        self._target_table_name = "connectors"
        self._focus_filter = FocusEventFilter(self)
        self._changed_rows = set()

        # initial IDs from Architecture tab
        self._initial_module_id = module_id
        self._initial_connector_id = (
            connector_data.get("id") if connector_data else None
        )
        if connector_data and module_id is None:
            self._initial_module_id = connector_data.get("module_id")

        self.setWindowTitle("Connector Management")
        self.resize(950, 600)

        # --- Layout ---
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # subsystem & module selectors
        self.create_selector_widgets(main_layout)

        # connectors table
        self.create_table_widget(main_layout)

        # OK button (no Cancel)
        self.create_button_layout(main_layout)

        # signals
        self.connect_signals()

        # initial population & deferred select
        self._populate_subsystems()
        QTimer.singleShot(0, self._deferred_initial_select)

    def apply_main_dialog_style(self):
        """اعمال استایل به دیالوگ اصلی"""
        style = f"""
            QDialog#ConnectorDialog {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}
            
            QDialog#ConnectorDialog QLabel {{
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE}; 
                font-weight: {Typography.WEIGHT_BOLD};
                color: {theme_manager.get_color('text_primary')}; 
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
        """
        self.setStyleSheet(style)

    def _ensure_project_selected(self):
        project_id = get_current_project_id()
        if project_id is None:
            QMessageBox.warning(
                self, "No Project Selected", "Please select or create a project first."
            )
            return False
        return True

    def on_theme_changed(self, theme_name):
        self.apply_main_dialog_style()
        self.update_selector_styles()
        self.update_table_style()

    def create_selector_widgets(self, main_layout):
        sel_layout = QHBoxLayout()

        subsystem_label = QLabel("Select Subsystem:")
        self.subsystem_combo = QComboBox()

        module_label = QLabel("Select Parent Module:")
        self.module_combo = QComboBox()

        sel_layout.addWidget(subsystem_label)
        sel_layout.addWidget(self.subsystem_combo)
        sel_layout.addWidget(module_label)
        sel_layout.addWidget(self.module_combo)
        sel_layout.addStretch()

        main_layout.addLayout(sel_layout)

        # اعمال استایل
        self.update_selector_styles()

    def update_selector_styles(self):
        """بروزرسانی استایل انتخابگرها"""
        combo_style = f"""
            QComboBox {{
                background: {theme_manager.get_gradient("primary")};
                border: 1px solid {theme_manager.get_color('accent')};
                border-radius: {BorderRadius.LARGE};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {Spacing.MD} {Spacing.XL};
            }}
            QComboBox:hover {{
                background: {theme_manager.get_gradient("hover")};
                border: 1px solid rgba(74, 144, 226, 120);
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
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

        self.subsystem_combo.setStyleSheet(combo_style)
        self.module_combo.setStyleSheet(combo_style)

    def create_table_widget(self, main_layout):
        """Create a performant table using QTableView + model/delegates."""
        self.table = QTableView()
        self.model = ConnectorTableModel([])
        self.table.setModel(self.model)

        # Configure headers
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.Fixed)

        self.table.setColumnHidden(0, True)  # hide ID
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 120)
        self.table.setColumnWidth(4, 80)
        hdr.setMinimumSectionSize(150)

        # Delegates
        self.table.setItemDelegateForColumn(
            ConnectorTableModel.COL_COLOR, ColorDelegate(self)
        )
        self.table.setItemDelegateForColumn(
            ConnectorTableModel.COL_NUM_PINS, IntDelegate(self)
        )

        # Button delegate for Save
        self.save_delegate = ButtonDelegate("✓", self)
        self.save_delegate.clicked.connect(self._on_save_clicked)
        self.table.setItemDelegateForColumn(
            ConnectorTableModel.COL_SAVE, self.save_delegate
        )

        # Styling registration
        register_widget(self.table, "tree_widget", "connectors_table")

        # When model data changes mark the row as changed
        self.model.dataChanged.connect(
            lambda idx1, idx2: self._mark_row_changed(idx1.row())
        )

        main_layout.addWidget(self.table)

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
            
            QSpinBox, NavigableLineEdit {{
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM}; 
                border: 1px solid rgba(74, 144, 226, 40);
                border-radius: {BorderRadius.MEDIUM};
                padding: {Spacing.MD}; 
                background: {theme_manager.get_gradient("primary")};
                color: {theme_manager.get_color('text_primary')};
            }}
            
            QSpinBox::up-button, QSpinBox::down-button {{
                border: none;
                background-color: transparent;
                width: 18px;
                height: 18px; 
            }}
            
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
                background: rgba(74, 144, 226, 30);
            }}
        """
        self.table.setStyleSheet(table_style)

    def create_button_layout(self, main_layout):
        """ایجاد لایوت دکمه‌ها"""
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        # استفاده از سیستم استایل جدید برای دکمه
        ok_btn = create_styled_button("✅ OK", "large")
        ok_btn.clicked.connect(self._on_ok)
        btn_layout.addWidget(ok_btn)
        main_layout.addLayout(btn_layout)

    def connect_signals(self):
        """اتصال سیگنال‌ها"""
        self.subsystem_combo.currentIndexChanged.connect(self._populate_modules)
        self.module_combo.currentIndexChanged.connect(self._populate_table)

    # ------------------------------------------------------------------
    #   Data Loading
    # ------------------------------------------------------------------
    def _populate_subsystems(self):
        """Load all subsystems and select initial one if provided."""
        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()

        self.subsystem_combo.blockSignals(True)
        self.subsystem_combo.clear()

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, name FROM subsystems WHERE project_id = %s ORDER BY name",
                    (project_id,),
                )
                all_rows = cur.fetchall()

            rows = [
                (sid, name)
                for sid, name in all_rows
                if auth.is_system() or can_edit_subsystem(sid)
            ]
            for sid, name in rows:
                self.subsystem_combo.addItem(name, sid)

                # select initial subsystem
                if self._initial_module_id:
                    cur.execute(
                        "SELECT subsystem_id FROM modules WHERE id=%s AND project_id=%s",
                        (self._initial_module_id, project_id),
                    )
                    row = cur.fetchone()
                    if row:
                        idx = self.subsystem_combo.findData(row[0])
                        if idx >= 0:
                            self.subsystem_combo.setCurrentIndex(idx)
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error loading subsystems: {str(e)}"
            )
            return

        self.subsystem_combo.blockSignals(False)
        self._populate_modules()
        self.subsystem_combo.setEnabled(auth.is_system())

    def _populate_modules(self):
        """Load modules for selected subsystem; select initial module if given."""
        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()

        self.module_combo.blockSignals(True)
        self.module_combo.clear()
        sid = self.subsystem_combo.currentData()
        if sid is None:
            self.module_combo.blockSignals(False)
            return

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, name, num_connectors FROM modules WHERE subsystem_id=%s AND project_id=%s ORDER BY name",
                    (sid, project_id),
                )
                for mid, name, nc in cur.fetchall():
                    self.module_combo.addItem(
                        f"{name} ", {"id": mid, "num_connectors": nc}
                    )
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error loading modules: {str(e)}"
            )
            self.module_combo.blockSignals(False)
            return

        # select initial module
        if self._initial_module_id:
            for i in range(self.module_combo.count()):
                if self.module_combo.itemData(i)["id"] == self._initial_module_id:
                    self.module_combo.setCurrentIndex(i)
                    break
        self.module_combo.blockSignals(False)
        self._populate_table()

    def _populate_table(self):
        """Fill table with existing connectors and blank rows up to capacity."""
        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()

        self.model.blockSignals(True)
        data = self.module_combo.currentData()
        if not data:
            self.model.setAll([])
            self.model.blockSignals(False)
            return
        self.module_id = data["id"]
        capacity = data["num_connectors"] or 0

        rows_data = []
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, name, number_of_pins, color FROM connectors WHERE module_id=%s AND project_id=%s ORDER BY id",
                    (self.module_id, project_id),
                )
                for cid, name, pins, color in cur.fetchall():
                    rows_data.append(
                        {
                            "id": cid,
                            "name": name or "",
                            "num_pins": pins or 0,
                            "color": color or "Default",
                        }
                    )
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error loading connectors: {str(e)}"
            )

        # Fill up to capacity with empty rows
        while len(rows_data) < capacity:
            rows_data.append(
                {"id": None, "name": "", "num_pins": 0, "color": "Default"}
            )

        self.model.setAll(rows_data)
        self._changed_rows.clear()
        self.model.blockSignals(False)

    # ------------------------------------------------------------------
    #   Table Row Creation & Change Tracking
    # ------------------------------------------------------------------
    def _mark_row_changed(self, row):
        """Mark row as changed and enable the Save button for it."""
        if row in self._changed_rows:
            return
        self._changed_rows.add(row)

    def _on_save_clicked(self, row):
        """Handle Save button clicked for a row."""
        self._save_row(row)

    # ------------------------------------------------------------------
    #   Initial Row Highlight
    # ------------------------------------------------------------------
    def _deferred_initial_select(self):
        """After UI is built, select the row matching initial connector_id."""
        if self._initial_connector_id is None:
            return
        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            if item and item.get("id") == self._initial_connector_id:
                self.table.selectRow(row)
                self.table.scrollTo(
                    self.model.index(row, 0), QAbstractItemView.PositionAtCenter
                )
                break

    # ------------------------------------------------------------------
    #   Save Logic
    # ------------------------------------------------------------------
    def _save_row(self, row, show_message=True):
        # --- Access guard: connector create/edit ---
        subsystem_id = getattr(self, "subsystem_id", None)
        if subsystem_id is None and hasattr(self, "connector_id"):
            subsystem_id = get_connector_subsystem_id(self.connector_id)
        perm_code = (
            "connector.edit" if getattr(self, "is_edit", False) else "connector.create"
        )
        if not guard_write(perm_code, subsystem_id, parent=self):
            return

        item = self.model.item(row)
        if not item:
            return

        name = item.get("name", "").strip()
        if not name:
            if show_message:
                QMessageBox.warning(
                    self, "Validation", "Connector name cannot be empty."
                )
            return

        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()

        try:
            num_pins = int(item.get("num_pins", 0) or 0)
        except ValueError:
            QMessageBox.warning(
                self, "Validation", "Number of pins must be an integer."
            )
            return

        color = item.get("color", "Default")
        cid = item.get("id")
        is_existing = bool(cid)

        if is_existing:
            try:
                with get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT COUNT(*) FROM pins WHERE connector_id=%s AND project_id=%s",
                        (cid, project_id),
                    )
                    existing_count = cur.fetchone()[0]
            except Exception as e:
                QMessageBox.critical(
                    self, "Database Error", f"Error checking pins: {str(e)}"
                )
                return

            if num_pins < existing_count:
                diff = existing_count - num_pins
                reply = QMessageBox.question(
                    self,
                    "Too Many Pins",
                    f"This connector already has {existing_count} pins.\n"
                    f"You reduced the number to {num_pins}, so {diff} pins must be deleted.\n"
                    "Do you want to select which pins to delete?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    return
                else:
                    if not self._prompt_delete_pins(cid, diff):
                        return

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                if is_existing:
                    cur.execute(
                        "UPDATE connectors SET name=%s, number_of_pins=%s, color=%s WHERE id=%s AND project_id=%s",
                        (name, num_pins, color, cid, project_id),
                    )
                else:
                    cur.execute(
                        "INSERT INTO connectors(name, module_id, project_id, number_of_pins, color) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                        (name, self.module_id, project_id, num_pins, color),
                    )
                    new_cid = cur.fetchone()[0]
                    item["id"] = new_cid
                    id_idx = self.model.index(row, ConnectorTableModel.COL_ID)
                    self.model.dataChanged.emit(id_idx, id_idx)
                conn.commit()
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error saving connector: {str(e)}"
            )
            return

        if show_message:
            QMessageBox.information(self, "Saved", f"Connector '{name}' saved.")
        self.connectors_updated.emit()
        self._changed_rows.discard(row)

    def _batch_save(self, rows):
        """Save multiple changed rows in one database transaction."""
        if not rows:
            return True
        if not self._ensure_project_selected():
            return False
        project_id = get_current_project_id()
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                for row in rows:
                    item = self.model.item(row)
                    if not item:
                        continue
                    name = item.get("name", "").strip()
                    if not name:
                        continue
                    try:
                        num_pins = int(item.get("num_pins", 0) or 0)
                    except Exception:
                        num_pins = 0
                    color = item.get("color", "Default")
                    cid = item.get("id")

                    if cid:
                        cur.execute(
                            "UPDATE connectors SET name=%s, number_of_pins=%s, color=%s WHERE id=%s AND project_id=%s",
                            (name, num_pins, color, cid, project_id),
                        )
                    else:
                        cur.execute(
                            "INSERT INTO connectors(name, module_id, project_id, number_of_pins, color) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                            (name, self.module_id, project_id, num_pins, color),
                        )
                        new_cid = cur.fetchone()[0]
                        item["id"] = new_cid
                        id_idx = self.model.index(row, ConnectorTableModel.COL_ID)
                        self.model.dataChanged.emit(id_idx, id_idx)
                conn.commit()
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error saving connectors: {str(e)}"
            )
            return False
        for row in rows:
            self._changed_rows.discard(row)
        if hasattr(self, "table"):
            self.table.viewport().update()
        self.connectors_updated.emit()
        return True

    def _prompt_delete_pins(self, connector_id, count_to_delete):
        """Prompt user to select pins to delete when connector has too many."""
        if not self._ensure_project_selected():
            return False

        project_id = get_current_project_id()

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, name FROM pins WHERE connector_id=%s AND project_id=%s",
                    (connector_id, project_id),
                )
                pin_rows = cur.fetchall()
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error loading pins: {str(e)}"
            )
            return False

        dialog = QDialog(self)
        dialog.setWindowTitle("Select Pins to Delete")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Select {count_to_delete} pins to delete:"))

        checkboxes = []
        for pid, name in pin_rows:
            cb = QCheckBox(f"{name} (ID: {pid})")
            cb.pid = pid
            layout.addWidget(cb)
            checkboxes.append(cb)

        btn_ok = create_styled_button("🗑️ Delete", "normal")
        layout.addWidget(btn_ok)

        def on_confirm():
            selected = [cb.pid for cb in checkboxes if cb.isChecked()]
            if len(selected) != count_to_delete:
                QMessageBox.warning(
                    dialog,
                    "Selection Error",
                    f"You must select exactly {count_to_delete} items.",
                )
                return
            try:
                with get_connection() as conn:
                    cur = conn.cursor()
                    for pid in selected:
                        cur.execute(
                            "DELETE FROM pins WHERE id=%s AND project_id=%s",
                            (pid, project_id),
                        )
                    conn.commit()
                dialog.accept()
            except Exception as e:
                QMessageBox.critical(
                    dialog, "Database Error", f"Error deleting pins: {str(e)}"
                )

        btn_ok.clicked.connect(on_confirm)

        def closeEvent(event):
            dialog.reject()

        dialog.closeEvent = closeEvent

        # اعمال استایل به دیالوگ
        auto_style_widget(dialog)

        dialog.exec_()
        return dialog.result() == QDialog.Accepted

    def _attempt_save_and_exit(self):
        # --- Access guard: connector delete ---
        subsystem_id = getattr(self, "subsystem_id", None)
        if subsystem_id is None and hasattr(self, "connector_id"):
            subsystem_id = get_connector_subsystem_id(self.connector_id)
        if not guard_write("connector.delete", subsystem_id, parent=self):
            return

        if not self._ensure_project_selected():
            return False

        project_id = get_current_project_id()
        rows_to_save = []
        empty_new_rows = []

        # Only inspect rows that were edited, not the whole table.
        for row in sorted(self._changed_rows):
            item = self.model.item(row)
            if not item:
                continue

            name = item.get("name", "").strip()
            is_new = not bool(item.get("id"))

            if not name:
                if is_new:
                    QMessageBox.warning(
                        self,
                        "Missing Name",
                        f"Row {row+1} has no name.\nPlease enter a name or delete the row.",
                    )
                    self.table.selectRow(row)
                    self.table.edit(self.model.index(row, ConnectorTableModel.COL_NAME))
                    return False
                else:
                    reply = QMessageBox.question(
                        self,
                        "Delete Empty Entry",
                        f"Row {row+1} has no name.\nDo you want to delete this item and all its pins?",
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    if reply == QMessageBox.Yes:
                        cid = item.get("id") if item else None
                        if cid:
                            try:
                                with get_connection() as conn:
                                    cur = conn.cursor()
                                    cur.execute(
                                        "DELETE FROM pins WHERE connector_id=%s AND project_id=%s",
                                        (cid, project_id),
                                    )
                                    cur.execute(
                                        "DELETE FROM connectors WHERE id=%s AND project_id=%s",
                                        (cid, project_id),
                                    )
                                    conn.commit()
                            except Exception as e:
                                QMessageBox.critical(
                                    self,
                                    "Database Error",
                                    f"Error deleting connector: {str(e)}",
                                )
                        empty_new_rows.append(row)
                        self.connectors_updated.emit()
                        continue
                    else:
                        self.table.selectRow(row)
                        self.table.edit(
                            self.model.index(row, ConnectorTableModel.COL_NAME)
                        )
                        return False
            else:
                rows_to_save.append(row)

        # Save all valid rows in one transaction
        success = True
        if rows_to_save:
            success = self._batch_save(rows_to_save)

        # Remove empty rows
        for row in sorted(empty_new_rows, reverse=True):
            self.model.removeRowItem(row)

        if rows_to_save and success:
            QMessageBox.information(self, "Saved", "All valid changes have been saved.")
        return success

    def _on_ok(self):
        if self._attempt_save_and_exit():
            self.accept()

    def closeEvent(self, event):
        if self._changed_rows:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "Changes have been made. Do you want to save them before closing?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                if not self._attempt_save_and_exit():
                    event.ignore()
                    return
        event.accept()
