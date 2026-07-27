# Pin_Dialog.py - مهاجرت یافته به سیستم استایل جدید

import sys, os
from PyQt5.QtCore import (
    Qt,
    pyqtSignal,
    QTimer,
    QAbstractTableModel,
    QModelIndex,
    QVariant,
    QEvent,
)
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QTableView,
    QAbstractItemView,
    QSizePolicy,
    QPushButton,
    QWidget,
    QMessageBox,
    QHeaderView,
    QLineEdit,
    QStyledItemDelegate,
)
from PyQt5.QtGui import QFont, QDoubleValidator

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import get_connection, get_current_project_id, get_pin_subsystem_id
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
# Format / Power-type chooser
# -------------------------------------------------------------
class FormatTypeWidget(QWidget):
    formatChanged = pyqtSignal()
    powerTypeChanged = pyqtSignal()

    def __init__(self, initial_fmt="Data", initial_power="VCC", parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignCenter)

        self.cb_fmt = QComboBox()
        self.cb_fmt.addItems(["Data", "Power"])
        self.cb_fmt.setCurrentText(initial_fmt)
        self.cb_fmt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.cb_fmt)

        self.cb_power = QComboBox()
        self.cb_power.addItems(["VCC", "GND"])
        self.cb_power.setCurrentText(initial_power)
        self.cb_power.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.cb_power)

        self._update_visibility()
        self.cb_fmt.currentTextChanged.connect(self._on_fmt_changed)
        self.cb_power.currentTextChanged.connect(lambda _: self.powerTypeChanged.emit())

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
        self.cb_fmt.setStyleSheet(combo_style)
        self.cb_power.setStyleSheet(combo_style)

    def _on_fmt_changed(self, txt):
        self._update_visibility()
        self.formatChanged.emit()

    def _update_visibility(self):
        is_power = self.cb_fmt.currentText() == "Power"
        self.cb_power.setVisible(is_power)

    def get_format(self):
        return self.cb_fmt.currentText()

    def get_power_type(self):
        return self.cb_power.currentText()


# --- Model & Delegates for performance ---------------------------------


class PinTableModel(QAbstractTableModel):
    COL_ID = 0
    COL_NAME = 1
    COL_FORMAT = 2
    COL_VOLTAGE = 3
    COL_CURRENT = 4
    COL_SAVE = 5

    headers = ["ID", "Pin Name", "Type", "Voltage (V)", "Current (mA)", "Save"]

    def __init__(self, data=None, parent=None):
        super().__init__(parent)
        self._data = data or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return QVariant()

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsEnabled
        col = index.column()
        if col in (self.COL_NAME, self.COL_FORMAT, self.COL_VOLTAGE, self.COL_CURRENT):
            return Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable
        return Qt.ItemIsSelectable | Qt.ItemIsEnabled

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = index.row()
        col = index.column()
        item = self._data[row]
        if role == Qt.DisplayRole:
            if col == self.COL_ID:
                return str(item.get("id") or "")
            if col == self.COL_NAME:
                return item.get("name", "")
            if col == self.COL_FORMAT:
                fmt = item.get("format", "Data")
                power_type = item.get("power_type", "VCC")
                return fmt if fmt == "Data" else f"Power/{power_type}"
            if col == self.COL_VOLTAGE:
                return item.get("voltage", "")
            if col == self.COL_CURRENT:
                return item.get("current", "")
            if col == self.COL_SAVE:
                return ""
        if role == Qt.EditRole:
            if col == self.COL_ID:
                return str(item.get("id") or "")
            if col == self.COL_NAME:
                return item.get("name", "")
            if col == self.COL_FORMAT:
                return (item.get("format", "Data"), item.get("power_type", "VCC"))
            if col == self.COL_VOLTAGE:
                return item.get("voltage", "")
            if col == self.COL_CURRENT:
                return item.get("current", "")
            if col == self.COL_SAVE:
                return ""
        return QVariant()

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or role != Qt.EditRole:
            return False
        row = index.row()
        col = index.column()
        item = self._data[row]
        if col == self.COL_NAME:
            item["name"] = str(value)
        elif col == self.COL_FORMAT:
            if isinstance(value, (tuple, list)) and len(value) == 2:
                item["format"], item["power_type"] = str(value[0]), str(value[1])
            else:
                return False
        elif col == self.COL_VOLTAGE:
            item["voltage"] = str(value).strip()
        elif col == self.COL_CURRENT:
            item["current"] = str(value).strip()
        else:
            return False

        self.dataChanged.emit(index, index)
        return True

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
    clicked = pyqtSignal(int)

    def __init__(self, text, dirty_check, parent=None):
        super().__init__(parent)
        self.text = text
        self._dirty_check = dirty_check

    def paint(self, painter, option, index):
        from PyQt5.QtWidgets import QStyleOptionButton
        from PyQt5.QtWidgets import QStyle

        opt = QStyleOptionButton()
        opt.rect = option.rect.adjusted(4, 6, -4, -6)
        opt.text = self.text
        if self._dirty_check(index.row()):
            opt.state = QStyle.State_Enabled
        else:
            opt.state = QStyle.State_None
        QApplication.style().drawControl(QStyle.CE_PushButton, opt, painter)

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and option.rect.contains(
            event.pos()
        ):
            if self._dirty_check(index.row()):
                self.clicked.emit(index.row())
                return True
        return False


class FormatTypeDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        data = index.model().data(index, Qt.EditRole)
        initial_fmt, initial_power = (
            data if isinstance(data, (tuple, list)) else ("Data", "VCC")
        )
        return FormatTypeWidget(
            initial_fmt=initial_fmt, initial_power=initial_power, parent=parent
        )

    def setEditorData(self, editor, index):
        if not isinstance(editor, FormatTypeWidget):
            return
        data = index.model().data(index, Qt.EditRole)
        if isinstance(data, (tuple, list)) and len(data) == 2:
            editor.cb_fmt.setCurrentText(data[0])
            editor.cb_power.setCurrentText(data[1])

    def setModelData(self, editor, model, index):
        if not isinstance(editor, FormatTypeWidget):
            return
        model.setData(
            index, (editor.get_format(), editor.get_power_type()), Qt.EditRole
        )


class VoltageDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        validator = QDoubleValidator(parent)
        validator.setNotation(QDoubleValidator.StandardNotation)
        validator.setBottom(0.0)
        editor.setValidator(validator)
        return editor

    def setEditorData(self, editor, index):
        if not isinstance(editor, QLineEdit):
            return
        value = index.model().data(index, Qt.EditRole)
        editor.setText(str(value) if value is not None else "")

    def setModelData(self, editor, model, index):
        if not isinstance(editor, QLineEdit):
            return
        model.setData(index, editor.text().strip(), Qt.EditRole)


class CurrentDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        validator = QDoubleValidator(parent)
        validator.setNotation(QDoubleValidator.StandardNotation)
        validator.setBottom(0.0)
        editor.setValidator(validator)
        return editor

    def setEditorData(self, editor, index):
        if not isinstance(editor, QLineEdit):
            return
        value = index.model().data(index, Qt.EditRole)
        editor.setText(str(value) if value is not None else "")

    def setModelData(self, editor, model, index):
        if not isinstance(editor, QLineEdit):
            return
        model.setData(index, editor.text().strip(), Qt.EditRole)


# -------------------------------------------------------------
# Main Dialog
# -------------------------------------------------------------
class PinDialog(QDialog):
    """
    Editable table of all pins for a selected connector.
    Per-row save buttons, change tracking, and arrow-key navigation.
    """

    pins_updated = pyqtSignal()

    def __init__(self, pin_data=None, connector_id=None, parent=None):
        super().__init__(parent)

        self.resize(900, 650)
        self.setObjectName("PinDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)

        # اعمال استایل پایه
        self.apply_main_dialog_style()

        # اتصال به تغییر تم
        style_manager.theme_changed.connect(self.on_theme_changed)

        self._target_table_name = "pins"
        self._focus_filter = FocusEventFilter(self)
        self._changed_rows = set()

        # initial IDs
        self._initial_connector_id = connector_id
        self._initial_pin_id = pin_data.get("id") if pin_data else None
        self._initial_module_id = None
        self._initial_subsystem_id = None

        # find module & subsystem from connector
        if connector_id and self._ensure_project_selected():
            project_id = get_current_project_id()
            try:
                with get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT module_id FROM connectors WHERE id=%s AND project_id=%s",
                        (connector_id, project_id),
                    )
                    mod = cur.fetchone()
                    if mod:
                        self._initial_module_id = mod[0]
                        cur.execute(
                            "SELECT subsystem_id FROM modules WHERE id=%s AND project_id=%s",
                            (mod[0], project_id),
                        )
                        sub = cur.fetchone()
                        if sub:
                            self._initial_subsystem_id = sub[0]
            except Exception as e:
                QMessageBox.critical(
                    self, "Database Error", f"Error loading initial data: {str(e)}"
                )

        # --- UI Layout ---
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # 1) Subsystem / Module / Connector selectors
        self.create_selector_widgets(main_layout)

        # 2) Pins table
        self.create_table_widget(main_layout)

        # 3) OK button only (no Cancel)
        self.create_button_layout(main_layout)

        # --- Signals ---
        self.connect_signals()

        # Initial load
        self._populate_subsystems()
        QTimer.singleShot(0, self._deferred_initial_select)

    def apply_main_dialog_style(self):
        """اعمال استایل به دیالوگ اصلی"""
        style = f"""
            QDialog#PinDialog {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}

            QDialog#PinDialog QLabel {{
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
        """هندل تغییر تم"""
        self.apply_main_dialog_style()
        self.update_selector_styles()
        self.update_table_style()

    def create_selector_widgets(self, main_layout):
        """ایجاد ویجت‌های انتخابگر"""
        sel_layout = QHBoxLayout()

        # Subsystem
        subsystem_label = QLabel("Select Subsystem:")
        self.subsystem_combo = QComboBox()
        self.subsystem_combo.setFont(QFont("Roboto Mono", 11))
        sel_layout.addWidget(subsystem_label)
        sel_layout.addWidget(self.subsystem_combo)

        # Module
        module_label = QLabel("Select Module:")
        self.module_combo = QComboBox()
        self.module_combo.setFont(QFont("Roboto Mono", 11))
        sel_layout.addWidget(module_label)
        sel_layout.addWidget(self.module_combo)

        # Connector
        connector_label = QLabel("Select Connector:")
        self.connector_combo = QComboBox()
        self.connector_combo.setFont(QFont("Roboto Mono", 11))
        sel_layout.addWidget(connector_label)
        sel_layout.addWidget(self.connector_combo)

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
        self.connector_combo.setStyleSheet(combo_style)

    def create_table_widget(self, main_layout):
        """ایجاد ویجت جدول"""
        self.model = PinTableModel([])
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setColumnHidden(PinTableModel.COL_ID, True)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.table.verticalHeader().setDefaultAlignment(Qt.AlignCenter)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Stretch)
        hdr.setSectionResizeMode(PinTableModel.COL_SAVE, QHeaderView.Fixed)
        self.table.setColumnWidth(PinTableModel.COL_SAVE, 120)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.setWordWrap(False)

        self.table.setItemDelegateForColumn(
            PinTableModel.COL_FORMAT, FormatTypeDelegate(self)
        )
        self.table.setItemDelegateForColumn(
            PinTableModel.COL_VOLTAGE, VoltageDelegate(self)
        )
        self.table.setItemDelegateForColumn(
            PinTableModel.COL_CURRENT, CurrentDelegate(self)
        )
        self._save_delegate = ButtonDelegate("✓", self._is_row_dirty, self.table)
        self._save_delegate.clicked.connect(self._save_row)
        self.table.setItemDelegateForColumn(PinTableModel.COL_SAVE, self._save_delegate)

        main_layout.addWidget(self.table)
        self.update_table_style()
        self.model.dataChanged.connect(self._on_model_data_changed)

    def update_table_style(self):
        """بروزرسانی استایل جدول"""
        table_style = f"""
            QTableView {{
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
            
            QTableView::item {{
                border: 1px solid rgba(74, 144, 226, 40);
                padding: {Spacing.LG} {Spacing.MD};
                margin: 2px 0px;
                border-radius: {BorderRadius.MEDIUM};
                min-height: 30px;
            }}
            
            QTableView::item:hover {{
                background: {theme_manager.get_gradient("hover", "x1:0, y1:0, x2:1, y2:1")};
                border: 1px solid rgba(74, 144, 226, 120);
                color: #ffffff;
            }}
            
            QTableView::item:selected {{
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
        self.module_combo.currentIndexChanged.connect(self._populate_connectors)
        self.connector_combo.currentIndexChanged.connect(self._populate_table)

    def _delete_row(self, row):
        reply = QMessageBox.question(
            self,
            "Delete Pin",
            "Are you sure you want to delete this pin?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()
        item = self.model.item(row)
        if item and item.get("id"):
            try:
                with get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "DELETE FROM pins WHERE id=%s AND project_id=%s",
                        (item["id"], project_id),
                    )
                    conn.commit()
                self.pins_updated.emit()
            except Exception as e:
                QMessageBox.critical(
                    self, "Database Error", f"Error deleting pin: {str(e)}"
                )
                return

        self.model.removeRowItem(row)
        self._shift_dirty_rows_after_removal(row)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def _populate_subsystems(self):
        """Load subsystems and select initial one."""
        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()

        self.subsystem_combo.blockSignals(True)
        self.subsystem_combo.clear()

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id,name FROM subsystems WHERE project_id=%s ORDER BY name",
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

            if self._initial_subsystem_id is not None:
                idx = self.subsystem_combo.findData(self._initial_subsystem_id)
                if idx >= 0:
                    self.subsystem_combo.setCurrentIndex(idx)
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error loading subsystems: {str(e)}"
            )

        self.subsystem_combo.blockSignals(False)
        self._populate_modules()
        self.subsystem_combo.setEnabled(auth.is_system())

    def _populate_modules(self):
        """Load modules for current subsystem."""
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
                    "SELECT id,name FROM modules WHERE subsystem_id=%s AND project_id=%s ORDER BY name",
                    (sid, project_id),
                )
                for mid, name in cur.fetchall():
                    self.module_combo.addItem(name, mid)

            if self._initial_module_id is not None:
                idx = self.module_combo.findData(self._initial_module_id)
                if idx >= 0:
                    self.module_combo.setCurrentIndex(idx)
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error loading modules: {str(e)}"
            )

        self.module_combo.blockSignals(False)
        self._populate_connectors()

    def _populate_connectors(self):
        """Load connectors for current module."""
        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()

        self.connector_combo.blockSignals(True)
        self.connector_combo.clear()
        mid = self.module_combo.currentData()
        if mid is None:
            self.connector_combo.blockSignals(False)
            return

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id,name FROM connectors WHERE module_id=%s AND project_id=%s ORDER BY name",
                    (mid, project_id),
                )
                for cid, name in cur.fetchall():
                    self.connector_combo.addItem(name, cid)

            if self._initial_connector_id is not None:
                for i in range(self.connector_combo.count()):
                    if self.connector_combo.itemData(i) == self._initial_connector_id:
                        self.connector_combo.setCurrentIndex(i)
                        break
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error loading connectors: {str(e)}"
            )

        self.connector_combo.blockSignals(False)
        self._populate_table()

    def _populate_table(self):
        """Fill table with existing pins and blank rows up to capacity."""
        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()
        conn_id = self.connector_combo.currentData()
        if conn_id is None:
            return

        rows = []
        cap = 0
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT number_of_pins FROM connectors WHERE id=%s AND project_id=%s",
                    (conn_id, project_id),
                )
                result = cur.fetchone()
                cap = result[0] if result else 0

                cur.execute(
                    "SELECT id,name,pin_type,is_ground,value,current FROM pins WHERE connector_id=%s AND project_id=%s ORDER BY pin_number",
                    (conn_id, project_id),
                )
                for pid, name, ptype, is_gnd, val, curr in cur.fetchall():
                    fmt = "Power" if ptype == "Voltage" else ptype
                    power_t = "GND" if is_gnd else "VCC"
                    volt_str = (
                        f"{val:.2f}"
                        if val is not None
                        else ""
                    )
                    curr_str = (
                        f"{curr:.2f}"
                        if curr is not None
                        else ""
                    )
                    rows.append(
                        {
                            "id": pid,
                            "name": name or "",
                            "format": fmt,
                            "power_type": power_t,
                            "voltage": volt_str,
                            "current": curr_str,
                        }
                    )
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error loading pins: {str(e)}"
            )

        while len(rows) < cap:
            rows.append(
                {
                    "id": None,
                    "name": "",
                    "format": "Data",
                    "power_type": "VCC",
                    "voltage": "",
                    "current": "",
                }
            )

        self.model.blockSignals(True)
        self.model.setAll(rows)
        self.model.blockSignals(False)
        self._changed_rows.clear()

        for col in range(1, 5):
            self.table.setColumnWidth(col, 150)

    # ------------------------------------------------------------------
    # Row creation & change-tracking
    # ------------------------------------------------------------------
    def _append_row(self, pid, name, fmt, power_t, volt, curr):
        self.model.insertRowItem(
            {
                "id": pid,
                "name": name,
                "format": fmt,
                "power_type": power_t,
                "voltage": volt,
                "current": curr,
            }
        )

    def _mark_dirty(self, row):
        if row in self._changed_rows:
            return
        self._changed_rows.add(row)
        self.model.dataChanged.emit(
            self.model.index(row, PinTableModel.COL_SAVE),
            self.model.index(row, PinTableModel.COL_SAVE),
        )

    def _on_model_data_changed(self, topLeft, bottomRight, roles=None):
        row = topLeft.row()
        if row < 0:
            return

        changed_columns = range(topLeft.column(), bottomRight.column() + 1)
        if any(
            col
            in (
                PinTableModel.COL_NAME,
                PinTableModel.COL_FORMAT,
                PinTableModel.COL_VOLTAGE,
                PinTableModel.COL_CURRENT,
            )
            for col in changed_columns
        ):
            item = self.model.item(row)
            if item is None:
                return

            self._mark_dirty(row)

    def _is_row_dirty(self, row):
        return row in self._changed_rows

    # ------------------------------------------------------------------
    # Save logic
    # ------------------------------------------------------------------
    def _save_row(self, row, show_message=True):
        subsystem_id = getattr(self, "subsystem_id", None)
        if subsystem_id is None and hasattr(self, "pin_id"):
            subsystem_id = get_pin_subsystem_id(self.pin_id)
        perm_code = "pin.edit" if getattr(self, "is_edit", False) else "pin.create"
        if not guard_write(perm_code, subsystem_id, parent=self):
            return

        item = self.model.item(row)
        if not item:
            return

        name = item.get("name", "").strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Pin name cannot be empty.")
            return

        if not self._ensure_project_selected():
            return
        project_id = get_current_project_id()

        fmt = item.get("format", "Data")
        ptype = item.get("power_type", "VCC")
        volt_str = str(item.get("voltage", "")).strip()
        curr_str = str(item.get("current", "")).strip()

        try:
            voltage = float(volt_str) if volt_str else None
        except ValueError:
            QMessageBox.warning(self, "Validation", "Voltage must be a number.")
            return

        try:
            numeric_current = float(curr_str) if curr_str else None
        except ValueError:
            QMessageBox.warning(self, "Validation", "Current must be a number.")
            return

        conn_id = self.connector_combo.currentData()
        is_gnd = fmt == "Power" and ptype == "GND"
        # If a voltage value is entered, set pin_type to "Voltage" so it
        # displays correctly in the Schematic View edit dialog.
        # If grounded, always mark as "Voltage"/GND regardless of entered voltage.
        if is_gnd or (volt_str and voltage is not None and voltage > 0):
            pin_type = "Voltage"
        else:
            pin_type = fmt

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                if item.get("id"):
                    cur.execute(
                        "UPDATE pins "
                        "SET name=%s, pin_type=%s, is_ground=%s, value=%s, current=%s "
                        "WHERE id=%s AND project_id=%s",
                        (
                            name,
                            pin_type,
                            is_gnd,
                            voltage,
                            numeric_current,
                            item["id"],
                            project_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        SELECT COALESCE(MIN(pin_number + 1), 1) 
                        FROM pins 
                        WHERE connector_id = %s AND project_id = %s 
                        AND (pin_number + 1) NOT IN (
                            SELECT pin_number FROM pins 
                            WHERE connector_id = %s AND project_id = %s
                        )
                    """,
                        (conn_id, project_id, conn_id, project_id),
                    )
                    next_num = cur.fetchone()[0]
                    cur.execute(
                        "INSERT INTO pins "
                        "(connector_id, project_id, name, pin_number, pin_type, is_ground, value, current) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                        (
                            conn_id,
                            project_id,
                            name,
                            next_num,
                            pin_type,
                            is_gnd,
                            voltage,
                            numeric_current,
                        ),
                    )
                    item["id"] = cur.fetchone()[0]
                    self.model.dataChanged.emit(
                        self.model.index(row, PinTableModel.COL_ID),
                        self.model.index(row, PinTableModel.COL_ID),
                    )
                conn.commit()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error saving pin: {str(e)}")
            return

        if show_message:
            QMessageBox.information(self, "Saved", f"Pin '{name}' saved.")
        self.pins_updated.emit()
        self._changed_rows.discard(row)
        self.model.dataChanged.emit(
            self.model.index(row, PinTableModel.COL_SAVE),
            self.model.index(row, PinTableModel.COL_SAVE),
        )

    # ------------------------------------------------------------------
    # Initial highlight
    # ------------------------------------------------------------------
    def _deferred_initial_select(self):
        if self._initial_pin_id is None:
            return
        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            if item and item.get("id") == self._initial_pin_id:
                self.table.selectRow(row)
                self.table.scrollTo(self.model.index(row, 0))
                break

    # ------------------------------------------------------------------
    # OK handling
    # ------------------------------------------------------------------
    def _shift_dirty_rows_after_removal(self, removed_row):
        updated = set()
        for row in self._changed_rows:
            if row == removed_row:
                continue
            updated.add(row - 1 if row > removed_row else row)
        self._changed_rows = updated

    def _attempt_save_and_exit(self):
        # --- Access guard: pin delete ---
        subsystem_id = getattr(self, "subsystem_id", None)
        if subsystem_id is None and hasattr(self, "pin_id"):
            subsystem_id = get_pin_subsystem_id(self.pin_id)
        if not guard_write("pin.delete", subsystem_id, parent=self):
            return

        if not self._ensure_project_selected():
            return False

        project_id = get_current_project_id()
        rows_to_save = []
        empty_new_rows = []

        # Only iterate through changed rows (optimization: skip untouched rows)
        for row in sorted(self._changed_rows):
            item = self.model.item(row)
            if not item:
                continue
            name = item.get("name", "").strip()
            is_new = item.get("id") is None

            if not name:
                if is_new:
                    QMessageBox.warning(
                        self,
                        "Missing Name",
                        f"Row {row+1} has no name.\nPlease enter a name or delete the row.",
                    )
                    self.table.selectRow(row)
                    return False
                reply = QMessageBox.question(
                    self,
                    "Delete Empty Entry",
                    f"Row {row+1} has no name.\nDo you want to delete this item from the database?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply == QMessageBox.Yes:
                    db_id = item.get("id")
                    if db_id:
                        try:
                            with get_connection() as conn:
                                cur = conn.cursor()
                                table = self._target_table_name
                                cur.execute(
                                    f"DELETE FROM {table} WHERE id=%s AND project_id=%s",
                                    (db_id, project_id),
                                )
                                conn.commit()
                        except Exception as e:
                            QMessageBox.critical(
                                self, "Database Error", f"Error deleting pin: {str(e)}"
                            )
                    empty_new_rows.append(row)
                    continue
                self.table.selectRow(row)
                return False

            rows_to_save.append(row)

        for row in rows_to_save:
            self._save_row(row, show_message=False)

        if empty_new_rows:
            for row in sorted(empty_new_rows, reverse=True):
                self.model.removeRowItem(row)
                self._shift_dirty_rows_after_removal(row)

        if rows_to_save:
            QMessageBox.information(self, "Saved", "All valid changes have been saved.")
        return True

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
