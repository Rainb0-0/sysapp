# Module_Dialog.py
import random
import sys, os
from PyQt5.QtCore import (
    Qt,
    pyqtSignal,
    QTimer,
    QAbstractTableModel,
    QModelIndex,
    QVariant,
    QEvent,
    QRect,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QTableView,
    QApplication,
    QSpinBox,
    QDoubleSpinBox,
    QWidget,
    QMessageBox,
    QHeaderView,
    QCheckBox,
    QFileDialog,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyle,
)
from PyQt5.QtGui import QFont, QColor, QPixmap, QPainter, QIcon

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import get_connection, get_current_project_id, get_module_subsystem_id
from access_control import guard_write, can_edit_subsystem
from auth_manager import auth
from suggestions import suggest_change
from util import profile

SUBMITTED_MSG = (
    "Change submitted for approval — it will apply once the system admin "
    "approves it."
)
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
    "Default": "#33A444",
    "Red": "#FF0000",
    "Blue": "#0000FF",
    "Yellow": "#FFFF00",
    "Purple": "#800080",
    "Orange": "#FFA500",
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

        # Apply styling to combo
        register_widget(self.combo, "combo_box", f"color_combo_{id(self)}")

        self._update_swatch()

    def current_color(self):
        name = self.combo.currentText()
        return COLOR_MAP.get(name, COLOR_MAP["Default"])

    def _update_swatch(self):
        color = QColor(self.current_color())
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(color)
        painter.drawRoundedRect(0, 0, 19, 19, 4, 4)
        painter.end()
        self.swatch.setPixmap(pixmap)


# --- Model & Delegates for performance ---------------------------------


class ModuleTableModel(QAbstractTableModel):
    COL_ID = 0
    COL_NAME = 1
    COL_MASS = 2
    COL_POWER = 3
    COL_MIN_TEMP = 4
    COL_MAX_TEMP = 5
    COL_NUM_CONN = 6
    COL_COLOR = 7
    COL_IMAGE = 8
    COL_SAVE = 9
    COL_REMOVE = 10

    headers = [
        "ID",
        "Module Name",
        "Mass (kg)",
        "Power (mW)",
        "Min Temp",
        "Max Temp",
        "Num Connectors",
        "Color",
        "Image",
        "Save",
        "Remove",
    ]

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
            if col == self.COL_MASS:
                return str(item.get("mass", ""))
            if col == self.COL_POWER:
                return str(item.get("power", ""))
            if col == self.COL_MIN_TEMP:
                return "" if item.get("min_temp") is None else str(item.get("min_temp", ""))
            if col == self.COL_MAX_TEMP:
                return "" if item.get("max_temp") is None else str(item.get("max_temp", ""))
            if col == self.COL_NUM_CONN:
                return item.get("num_connectors", 0)
            if col == self.COL_COLOR:
                return item.get("color", "")
            if col == self.COL_IMAGE:
                return item.get("photo", "")
            if col in (self.COL_SAVE, self.COL_REMOVE):
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
        if col in (
            self.COL_NAME,
            self.COL_MASS,
            self.COL_POWER,
            self.COL_MIN_TEMP,
            self.COL_MAX_TEMP,
            self.COL_NUM_CONN,
            self.COL_COLOR,
        ):
            return Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable
        # COL_IMAGE is handled by the painting delegate (click to browse),
        # so it must NOT be editable — a transient editor is what used to
        # crash when its child QFileDialog stole focus.
        return Qt.ItemIsSelectable | Qt.ItemIsEnabled

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid():
            return False
        row = index.row()
        col = index.column()
        if role == Qt.EditRole:
            if col == self.COL_NAME:
                self._data[row]["name"] = str(value)
            elif col == self.COL_MASS:
                try:
                    self._data[row]["mass"] = float(value)
                except Exception:
                    self._data[row]["mass"] = value
            elif col == self.COL_POWER:
                try:
                    self._data[row]["power"] = float(value)
                except Exception:
                    self._data[row]["power"] = value
            elif col == self.COL_MIN_TEMP:
                try:
                    self._data[row]["min_temp"] = float(value)
                except Exception:
                    self._data[row]["min_temp"] = value
            elif col == self.COL_MAX_TEMP:
                try:
                    self._data[row]["max_temp"] = float(value)
                except Exception:
                    self._data[row]["max_temp"] = value
            elif col == self.COL_NUM_CONN:
                self._data[row]["num_connectors"] = int(value)
            elif col == self.COL_COLOR:
                self._data[row]["color"] = str(value)
            elif col == self.COL_IMAGE:
                self._data[row]["photo"] = str(value)
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
    clicked = pyqtSignal(int)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text

    def paint(self, painter, option, index):
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
    def createEditor(self, parent, option, index):
        cur = index.model().data(index, Qt.EditRole)
        editor = ColorComboBox(cur if cur else "Default", parent)
        # Commit the picked color to the model immediately (when the user
        # selects from the drop-down) instead of relying on the view's
        # focus-out commit. A click on a button outside the table does not
        # move focus away from the editor, so the old color used to linger
        # in the model and the global Save/OK button saved the stale value.
        # The editor is deliberately left open after a pick so the user can
        # pick again without re-entering edit mode (which used to require
        # clicking the cell several times to reach the drop-down).
        editor.combo.activated.connect(lambda *_: self.commitData.emit(editor))
        # Open the drop-down as soon as the editor appears, so one click on
        # the cell is enough to reach the color list.
        QTimer.singleShot(0, editor.combo.showPopup)
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


class ImagePreviewDelegate(QStyledItemDelegate):
    """
    Image column delegate: paints a thumbnail preview + filename and lets the
    user pick an image by clicking the cell (or clear it via the ✕ badge).

    The file dialog is parented to the *view* rather than to a transient
    delegate editor, so it can never be destroyed while it is open — this
    also fixes the previous crash where the editor (and the dialog as its
    child) was destroyed mid-`exec_()` as soon as the dialog stole focus.
    """

    _THUMB_W = 64
    _THUMB_H = 44

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thumb_cache = {}

    @staticmethod
    def _clear_rect_for(rect):
        return QRect(rect.right() - 18, rect.top() + (rect.height() - 16) // 2, 16, 16)

    def _thumb(self, path):
        """Return a scaled thumbnail for the given path (cached)."""
        pix = self._thumb_cache.get(path)
        if pix is None:
            raw = QPixmap(path)
            if raw.isNull():
                pix = QPixmap()  # invalid marker: file missing / not an image
            else:
                pix = raw.scaled(
                    self._THUMB_W,
                    self._THUMB_H,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            if len(self._thumb_cache) >= 200:  # keep the cache bounded (evict oldest)
                self._thumb_cache.pop(next(iter(self._thumb_cache)))
            self._thumb_cache[path] = pix
        return pix

    def _text_color(self, option):
        """Palette text colour that stays readable on the selected state."""
        if option.state & QStyle.State_Selected:
            return option.palette.highlightedText().color()
        return option.palette.text().color()

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect

        # NOTE: no explicit background fill — the view's stylesheet already
        # renders selection / hover / alternating row colours; painting over
        # them with palette brushes would break the theme.

        photo = (index.model().data(index, Qt.EditRole) or "").strip()

        if not photo:
            painter.setPen(option.palette.mid().color())
            painter.drawText(
                rect.adjusted(8, 0, -8, 0),
                Qt.AlignVCenter | Qt.AlignLeft,
                "No image — click to browse",
            )
            painter.restore()
            return

        pix = self._thumb(photo)
        if pix.isNull():
            painter.setPen(option.palette.mid().color())
            painter.drawText(
                rect.adjusted(8, 0, -8, 0),
                Qt.AlignVCenter | Qt.AlignLeft,
                "⚠️ image unavailable",
            )
            painter.restore()
            return

        # thumbnail, vertically centred
        th_rect = QRect(
            rect.left() + 6,
            rect.top() + max(0, (rect.height() - pix.height()) // 2),
            pix.width(),
            pix.height(),
        )
        painter.drawPixmap(th_rect, pix)

        # elided filename next to the thumbnail
        font = painter.font()
        font.setPointSize(max(8, font.pointSize() - 1))
        painter.setFont(font)
        text_rect = rect.adjusted(th_rect.width() + 16, 0, -24, 0)
        elided = painter.fontMetrics().elidedText(
            os.path.basename(photo), Qt.ElideMiddle, max(24, text_rect.width())
        )
        painter.setPen(self._text_color(option))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)

        # clear (✕) badge
        clear_rect = self._clear_rect_for(rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(200, 60, 60, 210))
        painter.drawRoundedRect(clear_rect, 3, 3)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(clear_rect, Qt.AlignCenter, "✕")
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (
            event.type() == QEvent.MouseButtonRelease
            and event.button() == Qt.LeftButton
            and index.column() == ModuleTableModel.COL_IMAGE
        ):
            # clicking the ✕ badge clears the image
            if self._clear_rect_for(option.rect).contains(event.pos()):
                model.setData(index, "", Qt.EditRole)
                return True
            # otherwise open the file picker, parented to the view so it
            # outlives any editor / focus changes while the dialog is open
            parent = option.widget
            path, _ = QFileDialog.getOpenFileName(
                parent,
                "Choose Module Image",
                "",
                "Images (*.png *.jpg *.jpeg *.bmp *.gif)",
            )
            if path:
                model.setData(index, path, Qt.EditRole)
            return True
        return super().editorEvent(event, model, option, index)


# numeric input delegates
class FloatDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QDoubleSpinBox(parent)
        editor.setRange(0, 1e9)
        editor.setDecimals(3)
        editor.setSingleStep(0.1)
        return editor

    def setEditorData(self, editor, index):
        val = index.model().data(index, Qt.EditRole)
        try:
            editor.setValue(float(val))
        except Exception:
            editor.setValue(0.0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.EditRole)


class TempFloatDelegate(QStyledItemDelegate):
    """Numeric editor for operating temperatures (may be negative)."""

    def createEditor(self, parent, option, index):
        editor = QDoubleSpinBox(parent)
        editor.setRange(-273.15, 9999.0)
        editor.setDecimals(3)
        editor.setSingleStep(1.0)
        return editor

    def setEditorData(self, editor, index):
        val = index.model().data(index, Qt.EditRole)
        try:
            editor.setValue(float(val))
        except Exception:
            editor.setValue(0.0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.EditRole)


def parse_temp_value(value):
    """
    Normalise a temperature cell value to a float, or None when unset.
    Raises ValueError for non-numeric input.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return float(s)


class IntDelegate(QStyledItemDelegate):
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


# -------------------------------------------------------------
# Main Dialog
# -------------------------------------------------------------
class ModuleDialog(QDialog):
    """
    Editable table of all modules under a selected subsystem.
    Per-row save buttons, change tracking, and keyboard navigation.
    """

    modules_updated = pyqtSignal()

    def __init__(self, subsystem_id=None, module_data=None, parent=None):
        super().__init__(parent)

        self.setObjectName("ModuleDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)

        # Apply base styling
        self.apply_dialog_style()

        # Connect to theme changes
        style_manager.theme_changed.connect(self.on_theme_changed)

        self._target_table_name = "modules"
        self._focus_filter = FocusEventFilter(self)
        self._changed_rows = set()

        # Received from ArchitectureViewTab
        self._initial_subsystem_id = subsystem_id
        self._initial_module_id = module_data.get("id") if module_data else None
        if module_data and subsystem_id is None:
            self._initial_subsystem_id = module_data.get("subsystem_id")

        self.setWindowTitle("Module Management")

        # Set larger size and make it responsive to screen
        self.setup_dialog_size()

        self.init_ui()

        # Initial load & deferred highlight
        self._populate_subsystems()
        QTimer.singleShot(0, self._deferred_initial_select)

    def apply_dialog_style(self):
        style = f"""
            QDialog#ModuleDialog {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}
            
            QDialog#ModuleDialog QLabel {{
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE}; 
                font-weight: {Typography.WEIGHT_BOLD};
                color: {theme_manager.get_color('text_primary')};
                background: transparent;
            }}
            
            QDialog#ModuleDialog QComboBox {{
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM}; 
                border: 1px solid rgba(74, 144, 226, 40);
                border-radius: {BorderRadius.MEDIUM};
                padding: {Spacing.MD};
                background: {theme_manager.get_gradient("primary")};
                color: {theme_manager.get_color('text_primary')};
            }}
            
            QDialog#ModuleDialog QComboBox::drop-down {{
                border: none;
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                height: 20px;
            }}
            
            QDialog#ModuleDialog QComboBox::drop-down:hover {{
                background: rgba(74, 144, 226, 30);
            }}
            
            QDialog#ModuleDialog QTableWidget {{
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
            
            QDialog#ModuleDialog QTableWidget::item {{
                border: 1px solid rgba(74, 144, 226, 40);
                padding: 10px {Spacing.MD};
                margin: 2px 0px;
                border-radius: {BorderRadius.MEDIUM};
                min-height: 30px;
            }}
            
            QDialog#ModuleDialog QTableWidget::item:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(74, 144, 226, 40), 
                    stop:1 rgba(155, 89, 182, 25));
                border: 1px solid rgba(74, 144, 226, 120);
                color: #ffffff;
            }}
            
            QDialog#ModuleDialog QTableWidget::item:selected {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(74, 144, 226, 70), 
                    stop:1 rgba(155, 89, 182, 40));
                border: 1px solid rgba(74, 144, 226, 150);
                color: #ffffff;
                font-weight: 600;
            }}
            
            QDialog#ModuleDialog QHeaderView::section {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                color: white;
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_LARGE};
                font-weight: {Typography.WEIGHT_BOLD};
                padding: {Spacing.LG}; 
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.SMALL};
            }}
            
            QDialog#ModuleDialog QSpinBox, QDialog#ModuleDialog NavigableLineEdit {{
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM}; 
                border: 1px solid rgba(74, 144, 226, 40);
                border-radius: {BorderRadius.MEDIUM};
                padding: {Spacing.MD}; 
                background: {theme_manager.get_gradient("primary")};
                color: {theme_manager.get_color('text_primary')};
            }}
            
            QDialog#ModuleDialog QSpinBox::up-button, QDialog#ModuleDialog QSpinBox::down-button {{
                border: none;
                background-color: transparent;
                width: 18px; 
                height: 18px;
            }}
            
            QDialog#ModuleDialog QSpinBox::up-button:hover, QDialog#ModuleDialog QSpinBox::down-button:hover {{
                background: rgba(74, 144, 226, 30);
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

    def on_theme_changed(self, theme_name):
        """هندل تغییر تم"""
        self.apply_dialog_style()
        self.update_table_styles()

    def update_table_styles(self):
        """به‌روزرسانی استایل جدول"""
        if hasattr(self, "table"):
            # Update vertical header style
            self.table.verticalHeader().setStyleSheet(f"""
                QHeaderView::section {{
                    background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:0, y2:1")};
                    border: 1px solid {theme_manager.get_color('primary_light')};
                    font-family: {Typography.FONT_FAMILY};
                    font-size: {Typography.SIZE_MEDIUM};
                    font-weight: {Typography.WEIGHT_BOLD};
                    color: {theme_manager.get_color('text_primary')};
                    padding: {Spacing.MD};
                }}
            """)

    def _ensure_project_selected(self):
        project_id = get_current_project_id()
        if project_id is None:
            QMessageBox.warning(
                self, "No Project Selected", "Please select or create a project first."
            )
            return False
        return True

    def init_ui(self):
        # --- Layout ---
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # Subsystem selector
        sel_layout = QHBoxLayout()

        # Label with new styling
        label = QLabel("Select Subsystem:")
        register_widget(label, "label", "subsystem_label")
        sel_layout.addWidget(label)

        # Combo box with new styling
        self.subsystem_combo = QComboBox()
        self.subsystem_combo.setFont(QFont("Roboto Mono", 11))
        register_widget(self.subsystem_combo, "combo_box", "subsystem_combo")
        sel_layout.addWidget(self.subsystem_combo)

        sel_layout.addStretch()

        # Add Module button with new styling
        self.add_btn = create_styled_button("➕ Add Module", "normal")
        self.add_btn.clicked.connect(self._add_empty_row)
        sel_layout.addWidget(self.add_btn)

        main_layout.addLayout(sel_layout)

        # Modules table
        self.create_table_widget(main_layout)

        # OK button only
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        ok_btn = create_styled_button("✅ OK", "large")
        ok_btn.clicked.connect(self._on_ok)
        btn_layout.addWidget(ok_btn)

        main_layout.addLayout(btn_layout)

        # Signals
        self.subsystem_combo.currentIndexChanged.connect(self._populate_table)

    def create_table_widget(self, layout):
        """Create a performant table using QTableView + model/delegates."""
        self.table = QTableView()
        self.model = ModuleTableModel([])
        self.table.setModel(self.model)

        # Configure headers
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.Fixed)
        hdr.setSectionResizeMode(7, QHeaderView.Fixed)
        hdr.setSectionResizeMode(8, QHeaderView.Fixed)
        hdr.setSectionResizeMode(9, QHeaderView.Fixed)
        hdr.setSectionResizeMode(10, QHeaderView.Fixed)

        self.table.setColumnHidden(0, True)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 110)
        self.table.setColumnWidth(5, 110)
        self.table.setColumnWidth(6, 120)
        self.table.setColumnWidth(7, 100)
        self.table.setColumnWidth(8, 200)
        self.table.setColumnWidth(9, 80)
        self.table.setColumnWidth(10, 80)
        hdr.setMinimumSectionSize(150)

        # Delegates
        self.table.setItemDelegateForColumn(
            ModuleTableModel.COL_COLOR, ColorDelegate(self)
        )
        self.table.setItemDelegateForColumn(
            ModuleTableModel.COL_IMAGE, ImagePreviewDelegate(self)
        )
        # numeric validators
        self.table.setItemDelegateForColumn(
            ModuleTableModel.COL_MASS, FloatDelegate(self)
        )
        self.table.setItemDelegateForColumn(
            ModuleTableModel.COL_POWER, FloatDelegate(self)
        )
        self.table.setItemDelegateForColumn(
            ModuleTableModel.COL_MIN_TEMP, TempFloatDelegate(self)
        )
        self.table.setItemDelegateForColumn(
            ModuleTableModel.COL_MAX_TEMP, TempFloatDelegate(self)
        )
        self.table.setItemDelegateForColumn(
            ModuleTableModel.COL_NUM_CONN, IntDelegate(self)
        )

        # Button delegates for Save and Remove
        self.save_delegate = ButtonDelegate("✓", self)
        self.remove_delegate = ButtonDelegate("✖", self)
        self.save_delegate.clicked.connect(self._on_save_clicked)
        self.remove_delegate.clicked.connect(self._on_remove_clicked)
        self.table.setItemDelegateForColumn(
            ModuleTableModel.COL_SAVE, self.save_delegate
        )
        self.table.setItemDelegateForColumn(
            ModuleTableModel.COL_REMOVE, self.remove_delegate
        )

        # Styling registration
        register_widget(self.table, "tree_widget", "modules_table")

        # When model data changes mark the row as changed
        self.model.dataChanged.connect(lambda tl, br: self._mark_row_changed(tl.row()))

        layout.addWidget(self.table)

    def _on_save_clicked(self, row):
        self._save_row(row)

    def _on_remove_clicked(self, row):
        self._delete_row(row)

    def _add_empty_row(self):
        """اضافه کردن سطر خالی"""
        self._append_row(None, "", 1.0, 2.0, 5, "Default", "")

    def _delete_row(self, row):
        # Access guard
        subsystem_id = getattr(self, "subsystem_id", None)
        if subsystem_id is None and hasattr(self, "module_id"):
            subsystem_id = get_module_subsystem_id(self.module_id)
        if not guard_write("module.delete", subsystem_id, parent=self):
            return

        reply = QMessageBox.question(
            self,
            "Delete Module",
            "Are you sure you want to delete this module and all its connectors & pins?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()
        item = self.model.item(row)
        mid = item.get("id") if item else None

        if mid:
            if not auth.is_system():
                # Non-admin: deletion becomes a suggestion awaiting approval.
                suggest_change("module", "delete", mid, subsystem_id,
                               {"id": mid},
                               f"Delete module '{item.get('name', '')}'")
                QMessageBox.information(self, "Submitted", SUBMITTED_MSG)
                self.model.removeRowItem(row)
                self._changed_rows = {
                    r if r < row else r - 1 for r in self._changed_rows if r != row
                }
                self.modules_updated.emit()
                return
            try:
                with get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT id FROM connectors WHERE module_id=%s AND project_id=%s",
                        (mid, project_id),
                    )
                    connector_ids = [r[0] for r in cur.fetchall()]
                    for connector_id in connector_ids:
                        cur.execute(
                            "DELETE FROM pins WHERE connector_id=%s AND project_id=%s",
                            (connector_id, project_id),
                        )
                    cur.execute(
                        "DELETE FROM connectors WHERE module_id=%s AND project_id=%s",
                        (mid, project_id),
                    )
                    cur.execute(
                        "DELETE FROM modules WHERE id=%s AND project_id=%s",
                        (mid, project_id),
                    )
                    conn.commit()
            except Exception as e:
                QMessageBox.critical(
                    self, "Database Error", f"Error deleting module: {str(e)}"
                )
                return

        # remove from model and update changed set
        self.model.removeRowItem(row)
        self._changed_rows = {
            r if r < row else r - 1 for r in self._changed_rows if r != row
        }
        self.modules_updated.emit()

    # ------------------------------------------------------------------
    #   Data Loading
    # ------------------------------------------------------------------
    def _populate_subsystems(self):
        """Load all subsystems and select initial one if given."""
        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()

        self.subsystem_combo.blockSignals(True)
        self.subsystem_combo.clear()

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id,name FROM subsystems WHERE project_id = %s ORDER BY name",
                    (project_id,),
                )
                rows = cur.fetchall()

            # keep only allowed subsystems for non-system users
            filtered = []
            for sid, name in rows:
                if auth.is_system() or can_edit_subsystem(sid):
                    filtered.append((sid, name))

            for sid, name in filtered:
                self.subsystem_combo.addItem(name, sid)
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error loading subsystems: {str(e)}"
            )
            return

        # select
        if self._initial_subsystem_id:
            idx = self.subsystem_combo.findData(self._initial_subsystem_id)
            if idx >= 0:
                self.subsystem_combo.setCurrentIndex(idx)

        self.subsystem_combo.blockSignals(False)
        self._populate_table()
        self.subsystem_combo.setEnabled(auth.is_system())

    def _populate_table(self):
        """Fill table with existing modules and blank row for new entry."""
        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()
        sid = self.subsystem_combo.currentData()
        if sid is None:
            return

        items = []
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id,name,mass,power,min_temp,max_temp,num_connectors,color,photo "
                    "FROM modules WHERE subsystem_id=%s AND project_id=%s ORDER BY id",
                    (sid, project_id),
                )
                rows = cur.fetchall()
                for mid, name, mass, power, min_temp, max_temp, num_conn, color, photo in rows:
                    items.append(
                        {
                            "id": mid,
                            "name": name,
                            "mass": mass,
                            "power": power,
                            "min_temp": min_temp,
                            "max_temp": max_temp,
                            "num_connectors": num_conn,
                            "color": color,
                            "photo": photo or "",
                        }
                    )
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error loading modules: {str(e)}"
            )
            return

        # set into model
        self.model.setAll(items)
        self._changed_rows.clear()

        # Apply table styling
        self.update_table_styles()

    def _on_cell_changed(self):
        # legacy: replaced by model.dataChanged connection
        return

    # ------------------------------------------------------------------
    #   Table Row Creation & Change Tracking
    # ------------------------------------------------------------------
    def _append_row(self, mid, name, mass, power, num_conn, color, photo,
                    min_temp=None, max_temp=None):
        # Add data row to model (no heavy widgets created)
        item = {
            "id": mid,
            "name": name,
            "mass": mass,
            "power": power,
            "min_temp": min_temp,
            "max_temp": max_temp,
            "num_connectors": num_conn,
            "color": color,
            "photo": photo or "",
        }
        self.model.insertRowItem(item)
        r = self.model.rowCount() - 1
        self.table.setRowHeight(r, 60)

    def _mark_row_changed(self, row):
        """Mark row as changed and enable its Save button."""
        if row in self._changed_rows:
            return
        # Track changed rows; visual update handled by delegates/paint
        self._changed_rows.add(row)
        # request repaint so delegates can reflect changed state if needed
        if hasattr(self, "table"):
            self.table.viewport().update()

    # ------------------------------------------------------------------
    #   Initial Row Highlight
    # ------------------------------------------------------------------
    def _deferred_initial_select(self):
        """Select row matching initial module_id after UI is ready."""
        if self._initial_module_id is None:
            return
        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            if item and item.get("id") == self._initial_module_id:
                self.table.selectRow(row)
                self.table.scrollTo(self.model.index(row, 0))
                break

    # ------------------------------------------------------------------
    #   Save Logic
    # ------------------------------------------------------------------
    @profile(top=10)
    def _save_row(self, row, show_message=True):

        subsystem_id = getattr(self, "subsystem_id", None)
        if subsystem_id is None and hasattr(self, "module_id"):
            subsystem_id = get_module_subsystem_id(self.module_id)
        perm_code = (
            "module.edit" if getattr(self, "is_edit", False) else "module.create"
        )
        if not guard_write(perm_code, subsystem_id, parent=self):
            return
        """ذخیره سطر"""
        item = self.model.item(row)
        if not item:
            return
        name = str(item.get("name", "")).strip()
        if not name:
            if show_message:
                QMessageBox.warning(self, "Validation", "Module name cannot be empty.")
            return

        if not self._ensure_project_selected():
            return

        project_id = get_current_project_id()
        sid = self.subsystem_combo.currentData()

        try:
            mass = float(item.get("mass", 0) or 0)
            power = float(item.get("power", 0) or 0)
            min_temp = parse_temp_value(item.get("min_temp"))
            max_temp = parse_temp_value(item.get("max_temp"))
        except ValueError:
            QMessageBox.warning(
                self, "Validation",
                "Mass, Power and operating temperatures must be numbers.",
            )
            return
        if min_temp is not None and max_temp is not None and min_temp > max_temp:
            QMessageBox.warning(
                self, "Validation",
                "Min operating temp cannot exceed max operating temp.",
            )
            return

        num_conn = int(item.get("num_connectors", 0) or 0)
        color = item.get("color", "")
        photo = item.get("photo", "")
        mid = item.get("id")
        is_existing = bool(mid)

        if not auth.is_system():
            # Non-admin: record the change as a suggestion instead of writing.
            fields = {
                "name": name, "mass": mass, "power": power,
                "min_temp": min_temp, "max_temp": max_temp,
                "num_connectors": num_conn, "color": color, "photo": photo,
            }
            if is_existing:
                suggest_change("module", "update", mid, subsystem_id,
                               {"id": mid, "fields": fields},
                               f"Edit module '{name}'")
            else:
                fields["subsystem_id"] = sid
                suggest_change("module", "create", None, subsystem_id,
                               fields, f"Add module '{name}'")
            if show_message:
                QMessageBox.information(self, "Submitted", SUBMITTED_MSG)
            self._changed_rows.discard(row)
            if hasattr(self, "table"):
                self.table.viewport().update()
            self.modules_updated.emit()
            return

        if is_existing:
            try:
                with get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT COUNT(*) FROM connectors WHERE module_id=%s AND project_id=%s",
                        (mid, project_id),
                    )
                    existing_count = cur.fetchone()[0]
            except Exception as e:
                QMessageBox.critical(
                    self, "Database Error", f"Error checking connectors: {str(e)}"
                )
                return

            if num_conn < existing_count:
                diff = existing_count - num_conn
                reply = QMessageBox.question(
                    self,
                    "Too Many Connectors",
                    f"This module already has {existing_count} connectors.\n"
                    f"To reduce the number to {num_conn}, you must delete {diff} of them.\n"
                    "Do you want to select which ones to delete?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    return
                else:
                    if not self._prompt_delete_subitems(mid, diff):
                        return

        try:
            with get_connection() as conn:
                cur = conn.cursor()

                if is_existing:
                    cur.execute(
                        "UPDATE modules SET name=%s, subsystem_id=%s, mass=%s, power=%s, min_temp=%s, max_temp=%s, num_connectors=%s, color=%s, photo=%s WHERE id=%s AND project_id=%s",
                        (
                            name,
                            sid,
                            mass,
                            power,
                            min_temp,
                            max_temp,
                            num_conn,
                            color,
                            photo,
                            mid,
                            project_id,
                        ),
                    )
                else:
                    cur.execute(
                        "INSERT INTO modules(name, subsystem_id, project_id, mass, power, min_temp, max_temp, num_connectors, color, photo) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                        (name, sid, project_id, mass, power, min_temp, max_temp, num_conn, color, photo),
                    )
                    new_mid = cur.fetchone()[0]
                    # set id back into model
                    self.model._data[row]["id"] = new_mid
                    id_idx = self.model.index(row, ModuleTableModel.COL_ID)
                    self.model.dataChanged.emit(id_idx, id_idx)

                conn.commit()
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error saving module: {str(e)}"
            )
            return

        if show_message:
            QMessageBox.information(self, "Saved", f"Module '{name}' saved.")
        self.modules_updated.emit()

        # mark as saved
        self._changed_rows.discard(row)
        if hasattr(self, "table"):
            self.table.viewport().update()

    def _batch_save(self, rows):
        """Save multiple changed rows in one database transaction."""
        if not rows:
            return True
        if not self._ensure_project_selected():
            return False

        if not auth.is_system():
            # Non-admin: every row becomes a suggestion (no DB writes).
            sid = self.subsystem_combo.currentData()
            subsystem_id = getattr(self, "subsystem_id", None) or sid
            suggested = 0
            for row in rows:
                item = self.model.item(row)
                if not item:
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                try:
                    mass = float(item.get("mass", 0) or 0)
                    power = float(item.get("power", 0) or 0)
                    min_temp = parse_temp_value(item.get("min_temp"))
                    max_temp = parse_temp_value(item.get("max_temp"))
                except Exception:
                    mass, power, min_temp, max_temp = 0.0, 0.0, None, None
                if min_temp is not None and max_temp is not None and min_temp > max_temp:
                    QMessageBox.warning(
                        self, "Validation",
                        "Min operating temp cannot exceed max operating temp.",
                    )
                    return False
                num_conn = int(item.get("num_connectors", 0) or 0)
                color = item.get("color", "")
                photo = item.get("photo", "")
                mid = item.get("id")
                fields = {"name": name, "mass": mass, "power": power,
                          "min_temp": min_temp, "max_temp": max_temp,
                          "num_connectors": num_conn, "color": color,
                          "photo": photo}
                if mid:
                    cid2 = suggest_change("module", "update", mid, subsystem_id,
                                          {"id": mid, "fields": fields},
                                          f"Edit module '{name}'")
                else:
                    fields["subsystem_id"] = sid
                    cid2 = suggest_change("module", "create", None, subsystem_id,
                                          fields, f"Add module '{name}'")
                if cid2 is not None:
                    suggested += 1
            QMessageBox.information(self, "Submitted", SUBMITTED_MSG)
            for row in rows:
                self._changed_rows.discard(row)
            if hasattr(self, "table"):
                self.table.viewport().update()
            self.modules_updated.emit()
            return True

        project_id = get_current_project_id()
        sid = self.subsystem_combo.currentData()
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                for row in rows:
                    item = self.model.item(row)
                    if not item:
                        continue
                    name = str(item.get("name", "")).strip()
                    if not name:
                        continue
                    try:
                        mass = float(item.get("mass", 0) or 0)
                        power = float(item.get("power", 0) or 0)
                        min_temp = parse_temp_value(item.get("min_temp"))
                        max_temp = parse_temp_value(item.get("max_temp"))
                    except Exception:
                        mass, power, min_temp, max_temp = 0.0, 0.0, None, None
                    if min_temp is not None and max_temp is not None and min_temp > max_temp:
                        QMessageBox.warning(
                            self, "Validation",
                            "Min operating temp cannot exceed max operating temp.",
                        )
                        return False
                    num_conn = int(item.get("num_connectors", 0) or 0)
                    color = item.get("color", "")
                    photo = item.get("photo", "")
                    mid = item.get("id")
                    if mid:
                        cur.execute(
                            "UPDATE modules SET name=%s, subsystem_id=%s, mass=%s, power=%s, min_temp=%s, max_temp=%s, num_connectors=%s, color=%s, photo=%s WHERE id=%s AND project_id=%s",
                            (
                                name,
                                sid,
                                mass,
                                power,
                                min_temp,
                                max_temp,
                                num_conn,
                                color,
                                photo,
                                mid,
                                project_id,
                            ),
                        )
                    else:
                        cur.execute(
                            "INSERT INTO modules(name, subsystem_id, project_id, mass, power, min_temp, max_temp, num_connectors, color, photo) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                            (
                                name,
                                sid,
                                project_id,
                                mass,
                                power,
                                min_temp,
                                max_temp,
                                num_conn,
                                color,
                                photo,
                            ),
                        )
                        new_mid = cur.fetchone()[0]
                        item["id"] = new_mid
                        id_idx = self.model.index(row, ModuleTableModel.COL_ID)
                        self.model.dataChanged.emit(id_idx, id_idx)
                conn.commit()
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error saving modules: {str(e)}"
            )
            return False
        for row in rows:
            self._changed_rows.discard(row)
        if hasattr(self, "table"):
            self.table.viewport().update()
        self.modules_updated.emit()
        return True

    def _prompt_delete_subitems(self, module_id, count_to_delete):
        """Prompt user to select connectors to delete"""
        if not self._ensure_project_selected():
            return False

        project_id = get_current_project_id()

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, name FROM connectors WHERE module_id=%s AND project_id=%s",
                    (module_id, project_id),
                )
                rows = cur.fetchall()
        except Exception as e:
            QMessageBox.critical(
                self, "Database Error", f"Error loading connectors: {str(e)}"
            )
            return False

        dialog = QDialog(self)
        dialog.setWindowTitle("Select Connectors to Delete")
        auto_style_widget(dialog)  # Apply automatic styling

        layout = QVBoxLayout(dialog)

        # Label with styling
        label = QLabel(f"Select {count_to_delete} connectors to delete:")
        register_widget(label, "label")
        layout.addWidget(label)

        checkboxes = []
        for cid, name in rows:
            cb = QCheckBox(f"{name} (ID: {cid})")
            cb.cid = cid
            register_widget(cb, "label")  # Use label styling for checkboxes
            layout.addWidget(cb)
            checkboxes.append(cb)

        # Button with new styling
        btn_ok = create_styled_button("🗑️ Delete", "normal")
        layout.addWidget(btn_ok)

        def on_confirm():
            selected = [cb.cid for cb in checkboxes if cb.isChecked()]
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
                    for cid in selected:
                        # حذف pins مربوط به این connector
                        cur.execute(
                            "DELETE FROM pins WHERE connector_id=%s AND project_id=%s",
                            (cid, project_id),
                        )
                        # حذف connector
                        cur.execute(
                            "DELETE FROM connectors WHERE id=%s AND project_id=%s",
                            (cid, project_id),
                        )
                    conn.commit()
                dialog.accept()
            except Exception as e:
                QMessageBox.critical(
                    dialog, "Database Error", f"Error deleting connectors: {str(e)}"
                )

        btn_ok.clicked.connect(on_confirm)

        def closeEvent(event):
            dialog.reject()

        dialog.closeEvent = closeEvent
        dialog.exec_()
        return dialog.result() == QDialog.Accepted

    # ------------------------------------------------------------------
    #   OK Handling
    # ------------------------------------------------------------------
    def _attempt_save_and_exit(self):
        """Attempt to save all changes and exit"""
        if not self._ensure_project_selected():
            return False

        # If a cell editor is still open, force the view to commit it into
        # the model before we snapshot rows. Clicking a button outside the
        # table does not reliably move focus away from the editor, so the
        # last edit (color, name, mass, …) would otherwise be silently lost.
        if self.table.state() == QAbstractItemView.EditingState:
            self.table.setFocus()
            QApplication.processEvents()

        project_id = get_current_project_id()
        rows_to_save = []
        empty_new_rows = []

        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            name = str(item.get("name", "")).strip() if item else ""
            is_new = not bool(item.get("id"))
            is_changed = row in self._changed_rows

            if is_new and not is_changed:
                empty_new_rows.append(row)
                continue

            if not name:
                if is_new:
                    QMessageBox.warning(
                        self,
                        "Missing Name",
                        f"Row {row+1} has no name.\nPlease enter a name or delete the row.",
                    )
                    self.table.selectRow(row)
                    self.table.edit(self.model.index(row, ModuleTableModel.COL_NAME))
                    return False
                else:
                    reply = QMessageBox.question(
                        self,
                        "Delete Empty Entry",
                        f"Row {row+1} has no name.\nDo you want to delete this module and all its connectors & pins?",
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    if reply == QMessageBox.Yes:
                        mid = item.get("id") if item else None
                        if mid:
                            if not auth.is_system():
                                # Non-admin: propose the deletion instead.
                                suggest_change(
                                    "module", "delete", mid, subsystem_id,
                                    {"id": mid},
                                    f"Delete module '{item.get('name', '')}'",
                                )
                                empty_new_rows.append(row)
                                self.modules_updated.emit()
                                continue
                            try:
                                with get_connection() as conn:
                                    cur = conn.cursor()
                                    cur.execute(
                                        "SELECT id FROM connectors WHERE module_id=%s AND project_id=%s",
                                        (mid, project_id),
                                    )
                                    connector_ids = [r[0] for r in cur.fetchall()]
                                    for cid in connector_ids:
                                        cur.execute(
                                            "DELETE FROM pins WHERE connector_id=%s AND project_id=%s",
                                            (cid, project_id),
                                        )
                                    cur.execute(
                                        "DELETE FROM connectors WHERE module_id=%s AND project_id=%s",
                                        (mid, project_id),
                                    )
                                    cur.execute(
                                        "DELETE FROM modules WHERE id=%s AND project_id=%s",
                                        (mid, project_id),
                                    )
                                    conn.commit()
                            except Exception as e:
                                QMessageBox.critical(
                                    self,
                                    "Database Error",
                                    f"Error deleting module: {str(e)}",
                                )
                        empty_new_rows.append(row)
                        self.modules_updated.emit()
                        continue
                    else:
                        self.table.selectRow(row)
                        self.table.edit(
                            self.model.index(row, ModuleTableModel.COL_NAME)
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
        """Handle OK button click"""
        if self._attempt_save_and_exit():
            self.accept()

    def closeEvent(self, event):
        """Handle dialog close event"""
        if self._changed_rows:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Save before closing?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                if not self._attempt_save_and_exit():
                    event.ignore()
                    return
        event.accept()

    def setup_dialog_size(self):
        """تنظیم سایز مناسب برای دیالوگ بر اساس صفحه نمایش"""
        # Get screen size
        screen = QApplication.desktop().screenGeometry()
        screen_width = screen.width()
        screen_height = screen.height()

        # Calculate dialog size (80% of screen size, but with minimum limits)
        min_width = 700
        min_height = 700

        dialog_width = max(min_width, int(screen_width * 0.8))
        dialog_height = max(min_height, int(screen_height * 0.75))

        # Set maximum limits to avoid too large windows
        max_width = min(1600, screen_width - 100)
        max_height = min(1000, screen_height - 100)

        dialog_width = min(dialog_width, max_width)
        dialog_height = min(dialog_height, max_height)

        self.resize(dialog_width, dialog_height)

        # Center the dialog on screen
        self.move(
            (screen_width - dialog_width) // 2, (screen_height - dialog_height) // 2
        )

        # Set minimum size to prevent shrinking too much
        self.setMinimumSize(min_width, min_height)

    def showEvent(self, event):
        """هنگام نمایش دیالوگ، ستون‌ها را تنظیم کن"""
        super().showEvent(event)
        # تنظیم مجدد ستون‌ها پس از نمایش
        # QTimer.singleShot(100, self.adjust_columns_after_show)

    def adjust_columns_after_show(self):
        """تنظیم نهایی ستون‌ها پس از نمایش کامل"""
        if hasattr(self, "table") and self.table.columnCount() > 0:
            # محاسبه عرض کل در دسترس
            available_width = self.table.viewport().width()

            # عرض ستون‌های ثابت
            fixed_width = 100 + 100 + 120 + 100 + 200 + 80 + 80  # مجموع ستون‌های ثابت

            # عرض باقی‌مانده برای ستون Module Name
            remaining_width = available_width - fixed_width

            if remaining_width > 80:  # حداقل عرض
                self.table.setColumnWidth(1, remaining_width)
