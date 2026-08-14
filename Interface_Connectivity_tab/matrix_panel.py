# matrix_panel.py - مهاجرت یافته به سیستم استایل جدید

import os
import sys
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QDialog, QComboBox, QPushButton, QMessageBox, QGroupBox, 
    QFormLayout, QDialogButtonBox,QHeaderView, QHBoxLayout, QWidget as QW,
    QDoubleSpinBox,
)
from PyQt5.QtGui import QColor, QPixmap, QIcon, QFont
from PyQt5.QtCore import Qt, pyqtSignal

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import (
    get_connection, get_current_project_id, pins_connectable_from_data,
)
from auth_manager import auth
from suggestions import propose_interface_change

from Interface_Connectivity_tab.wiring_utils import (
    get_all_pins_with_full_numbered_name, PREDEFINED_COLORS, AddInterfaceDialog,
    _pin_label,
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

        # Filter Bar (subsystem / module / connector)
        self.create_filter_widget(layout)

        # Matrix Table
        self.create_matrix_table(layout)

        self.matrix_table.cellDoubleClicked.connect(self.edit_matrix_cell)

        self.connector_ids_for_matrix = []
        self.connectors_for_matrix = []
        self.matrix_row_connector_ids = []
        self.matrix_col_connector_ids = []
        self.matrix_row_connectors = []
        self.matrix_col_connectors = []

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

    # ------------------------------------------------------------------
    # Filter bar: two cascading chains (Rows / Columns)
    # Each chain narrows Subsystem → Module → Connector with an "All …"
    # option at every level. The matrix always shows the relation between
    # the connectors selected on the ROWS axis and those on the COLUMNS axis.
    # ------------------------------------------------------------------
    def create_filter_widget(self, layout):
        """
        Two cascading filter chains — one for the matrix ROWS and one for
        the COLUMNS. Every level has an "All …" entry so a chain can stop at
        subsystem, module, or connector granularity.
        """
        self.filter_widget = QWidget()
        self.filter_widget.setObjectName("MatrixFilterBar")

        filter_layout = QVBoxLayout(self.filter_widget)
        filter_layout.setContentsMargins(12, 8, 12, 8)
        filter_layout.setSpacing(6)

        # ---- Rows chain: Subsystem → Module → Connector ----
        row_bar = QHBoxLayout()
        row_bar.setSpacing(8)
        row_label = QLabel("⬇ Rows:")
        row_label.setFont(QFont("Roboto Mono", 10, QFont.Bold))
        row_bar.addWidget(row_label)

        self.row_sub_combo = self._new_filter_combo("All subsystems")
        self.row_mod_combo = self._new_filter_combo("All modules")
        self.row_con_combo = self._new_filter_combo("All connectors")
        row_bar.addWidget(self.row_sub_combo)
        row_bar.addWidget(self.row_mod_combo)
        row_bar.addWidget(self.row_con_combo)
        row_bar.addStretch()
        filter_layout.addLayout(row_bar)

        # ---- Columns chain: Subsystem → Module → Connector ----
        col_bar = QHBoxLayout()
        col_bar.setSpacing(8)
        col_label = QLabel("➡ Columns:")
        col_label.setFont(QFont("Roboto Mono", 10, QFont.Bold))
        col_bar.addWidget(col_label)

        self.col_sub_combo = self._new_filter_combo("All subsystems")
        self.col_mod_combo = self._new_filter_combo("All modules")
        self.col_con_combo = self._new_filter_combo("All connectors")
        col_bar.addWidget(self.col_sub_combo)
        col_bar.addWidget(self.col_mod_combo)
        col_bar.addWidget(self.col_con_combo)

        self.clear_filter_btn = create_styled_button("✖ Clear", "small")
        self.clear_filter_btn.clicked.connect(self.clear_filter)
        col_bar.addWidget(self.clear_filter_btn)
        col_bar.addStretch()
        filter_layout.addLayout(col_bar)

        # Current scope summary
        self.scope_label = QLabel("")
        self.scope_label.setObjectName("MatrixScopeLabel")
        self.scope_label.setFont(QFont("Roboto Mono", 9))
        filter_layout.addWidget(self.scope_label)

        layout.addWidget(self.filter_widget)
        self.update_filter_style()

        # Seed the subsystem lists (the fillers block signals, so no handler
        # fires before self.matrix_table exists).
        for side in ("row", "col"):
            self._fill_subsystems(self._side_combos(side)[0])

        # Cascade: a subsystem pick repopulates modules, a module pick
        # repopulates connectors; every user pick reloads the matrix.
        self.row_sub_combo.currentIndexChanged.connect(lambda _: self._on_sub_changed("row"))
        self.row_mod_combo.currentIndexChanged.connect(lambda _: self._on_mod_changed("row"))
        self.row_con_combo.currentIndexChanged.connect(lambda _: self._on_con_changed("row"))
        self.col_sub_combo.currentIndexChanged.connect(lambda _: self._on_sub_changed("col"))
        self.col_mod_combo.currentIndexChanged.connect(lambda _: self._on_mod_changed("col"))
        self.col_con_combo.currentIndexChanged.connect(lambda _: self._on_con_changed("col"))

    def _new_filter_combo(self, placeholder):
        """Create a filter combo seeded with its 'All …' placeholder."""
        combo = QComboBox()
        combo.setFont(QFont("Roboto Mono", 10))
        combo.addItem(placeholder, None)
        return combo

    def _side_combos(self, side):
        """Return (subsystem, module, connector) combo for 'row' or 'col'."""
        if side == "row":
            return self.row_sub_combo, self.row_mod_combo, self.row_con_combo
        return self.col_sub_combo, self.col_mod_combo, self.col_con_combo

    def update_filter_style(self):
        """بروزرسانی استایل نوار فیلتر"""
        self.filter_widget.setStyleSheet(f"""
            QWidget#MatrixFilterBar {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                border-radius: {BorderRadius.LARGE};
                border: 1px solid {theme_manager.get_color('primary_light')};
            }}
            QLabel {{
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_SMALL};
                font-weight: {Typography.WEIGHT_BOLD};
                background: transparent;
                border: none;
            }}
            QLabel#MatrixScopeLabel {{
                color: {theme_manager.get_color('text_secondary')};
                font-size: {Typography.SIZE_SMALL};
                font-weight: {Typography.WEIGHT_NORMAL};
            }}
            QComboBox {{
                background: {theme_manager.get_color('primary_dark')};
                color: {theme_manager.get_color('text_primary')};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_MEDIUM};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.MEDIUM};
                padding: {Spacing.MD} {Spacing.LG};
                min-width: 150px;
            }}
            QComboBox:disabled {{
                color: rgba(128, 128, 128, 0.6);
            }}
            QComboBox QAbstractItemView {{
                background: {theme_manager.get_color('primary_dark')};
                color: {theme_manager.get_color('text_primary')};
                selection-background-color: rgba(74, 144, 226, 40);
                selection-color: #ffffff;
                border: 1px solid {theme_manager.get_color('primary_light')};
            }}
        """)

    def _fill_subsystems(self, combo, keep=None):
        """Fill a subsystem combo with 'All subsystems' + project subsystems."""
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("All subsystems", None)
            project_id = get_current_project_id()
            if project_id is None:
                return
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, name FROM subsystems WHERE project_id = %s ORDER BY name",
                    (project_id,),
                )
                for sub_id, name in cur.fetchall():
                    combo.addItem(name, sub_id)
            if keep is not None:
                idx = combo.findData(keep)
                if idx != -1:
                    combo.setCurrentIndex(idx)
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading subsystems: {str(e)}")
        finally:
            combo.blockSignals(False)

    def _fill_modules(self, combo, sub_id, keep=None):
        """Fill a module combo with 'All modules' + modules of sub_id (or all)."""
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("All modules", None)
            project_id = get_current_project_id()
            if project_id is None:
                return
            with get_connection() as conn:
                cur = conn.cursor()
                if sub_id is None:
                    cur.execute(
                        "SELECT id, name FROM modules WHERE project_id = %s ORDER BY name",
                        (project_id,),
                    )
                else:
                    cur.execute(
                        """SELECT id, name FROM modules
                           WHERE subsystem_id = %s AND project_id = %s ORDER BY name""",
                        (sub_id, project_id),
                    )
                for mod_id, name in cur.fetchall():
                    combo.addItem(name, mod_id)
            if keep is not None:
                idx = combo.findData(keep)
                if idx != -1:
                    combo.setCurrentIndex(idx)
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading modules: {str(e)}")
        finally:
            combo.blockSignals(False)

    def _fill_connectors(self, combo, mod_id, keep=None, sub_id=None):
        """
        Fill a connector combo with 'All connectors' + the connectors that
        belong to mod_id, or (when no module is chosen) to sub_id, or all
        project connectors when neither is given.
        """
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("All connectors", None)
            project_id = get_current_project_id()
            if project_id is None:
                return
            with get_connection() as conn:
                cur = conn.cursor()
                if mod_id is not None:
                    cur.execute(
                        """
                        SELECT c.id, c.name, m.name
                        FROM connectors c
                        JOIN modules m ON c.module_id = m.id AND m.project_id = %s
                        WHERE c.project_id = %s AND c.module_id = %s
                        ORDER BY m.name, c.name
                        """,
                        (project_id, project_id, mod_id),
                    )
                elif sub_id is not None:
                    cur.execute(
                        """
                        SELECT c.id, c.name, m.name
                        FROM connectors c
                        JOIN modules m ON c.module_id = m.id AND m.project_id = %s
                        WHERE c.project_id = %s AND m.subsystem_id = %s
                        ORDER BY m.name, c.name
                        """,
                        (project_id, project_id, sub_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT c.id, c.name, m.name
                        FROM connectors c
                        JOIN modules m ON c.module_id = m.id AND m.project_id = %s
                        WHERE c.project_id = %s
                        ORDER BY m.name, c.name
                        """,
                        (project_id, project_id),
                    )
                for cid, cname, mname in cur.fetchall():
                    combo.addItem(f"{mname} - {cname}", cid)
            if keep is not None:
                idx = combo.findData(keep)
                if idx != -1:
                    combo.setCurrentIndex(idx)
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading connectors: {str(e)}")
        finally:
            combo.blockSignals(False)

    def _side_scope(self, side):
        """
        Resolve the deepest selected level of a filter chain.
        Returns (level, id): ('all', None), ('subsystem', sid),
        ('module', mid) or ('connector', cid).
        """
        sub, mod, con = self._side_combos(side)
        cid = con.currentData()
        if cid is not None:
            return "connector", cid
        mid = mod.currentData()
        if mid is not None:
            return "module", mid
        sid = sub.currentData()
        if sid is not None:
            return "subsystem", sid
        return "all", None

    def _on_sub_changed(self, side):
        """
        Subsystem picked: repopulate the module + connector chains below it.
        Deeper selections are kept when they still belong to the new scope.
        """
        sub, mod, con = self._side_combos(side)
        old_mod_id = mod.currentData()
        old_con_id = con.currentData()
        sub_id = sub.currentData()

        self._fill_modules(mod, sub_id, keep=old_mod_id)
        new_mod_id = mod.currentData()
        # Keep the connector only if its module selection survived.
        keep_con = old_con_id if new_mod_id == old_mod_id else None
        self._fill_connectors(con, new_mod_id, keep=keep_con, sub_id=sub_id)
        self.load_matrix_data()

    def _on_mod_changed(self, side):
        """
        Module picked: repopulate the connector chain below it. The connector
        selection is kept when it still belongs to the new scope.
        """
        sub, mod, con = self._side_combos(side)
        old_con_id = con.currentData()
        mod_id = mod.currentData()

        self._fill_connectors(con, mod_id, keep=old_con_id, sub_id=sub.currentData())
        self.load_matrix_data()

    def _on_con_changed(self, side):
        """Connector picked: just reload the matrix."""
        self.load_matrix_data()

    def refresh_filter_options(self):
        """
        Re-populate both filter chains for the current project (called on tab
        switches / project changes) while preserving selections that still
        exist. Stale ids never leak across projects.
        """
        if not hasattr(self, "row_sub_combo"):
            return
        for side in ("row", "col"):
            sub, mod, con = self._side_combos(side)
            sub_id = sub.currentData()
            mod_id = mod.currentData()
            con_id = con.currentData()

            self._fill_subsystems(sub, keep=sub_id)
            new_sub_id = sub.currentData()

            # Only keep the module selection if its subsystem is unchanged.
            keep_mod = mod_id if new_sub_id == sub_id else None
            self._fill_modules(mod, new_sub_id, keep=keep_mod)
            new_mod_id = mod.currentData()

            # Only keep the connector selection if its module is unchanged.
            keep_con = con_id if new_mod_id == mod_id else None
            self._fill_connectors(con, new_mod_id, keep=keep_con, sub_id=new_sub_id)

    def clear_filter(self):
        """Reset both filter chains back to 'All' and reload the matrix."""
        for side in ("row", "col"):
            sub, mod, con = self._side_combos(side)
            for combo in (sub, mod, con):
                combo.blockSignals(True)
            sub.setCurrentIndex(0)
            mod.clear()
            mod.addItem("All modules", None)
            con.clear()
            con.addItem("All connectors", None)
            for combo in (sub, mod, con):
                combo.blockSignals(False)
        self.load_matrix_data()

    def _update_scope_label(self, n_rows, n_cols):
        """Update the summary label describing the current matrix scope."""
        row_desc = self._scope_description(*self._side_scope("row"))
        col_desc = self._scope_description(*self._side_scope("col"))
        self.scope_label.setText(
            f"Showing {n_rows} row connector(s) × {n_cols} column connector(s)   "
            f"(rows: {row_desc}  ·  columns: {col_desc})"
        )

    def _scope_description(self, level, sid):
        """Human-readable description of a filter scope."""
        if level == "all" or sid is None:
            return "all connectors"
        project_id = get_current_project_id()
        if project_id is None:
            return "..."
        table = {"subsystem": "subsystems", "module": "modules", "connector": "connectors"}[level]
        noun = {"subsystem": "subsystem", "module": "module", "connector": "connector"}[level]
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT name FROM {table} WHERE id = %s AND project_id = %s",
                    (sid, project_id),
                )
                row = cur.fetchone()
                if row:
                    return f"{noun} '{row[0]}'"
        except Exception:
            pass
        return "unknown"


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
        if hasattr(self, "filter_widget"):
            self.update_filter_style()
        self.update_matrix_table_style()

    def load_matrix_data(self):
        """
        Load connector info for the row & column filter scopes and prepare
        the matrix headers and size.

        The matrix always represents relations BETWEEN the connectors of the
        ROW scope and the connectors of the COLUMN scope — never inside a
        single connector.
        """
        if not self._ensure_project_selected():
            self.matrix_table.setRowCount(0)
            self.matrix_table.setColumnCount(0)
            return

        project_id = get_current_project_id()

        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                row_connectors = self._fetch_scope_connectors(
                    cursor, project_id, *self._side_scope("row")
                )
                col_connectors = self._fetch_scope_connectors(
                    cursor, project_id, *self._side_scope("col")
                )
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading connectors: {str(e)}")
            row_connectors = []
            col_connectors = []

        self.matrix_row_connectors = row_connectors
        self.matrix_col_connectors = col_connectors
        self.matrix_row_connector_ids = [cid for _, _, cid in row_connectors]
        self.matrix_col_connector_ids = [cid for _, _, cid in col_connectors]
        # Backwards-compatible aliases used by the refresh/edit paths
        self.connectors_for_matrix = row_connectors
        self.connector_ids_for_matrix = self.matrix_row_connector_ids

        row_headers = [f"{m} - {c}" for m, c, _ in row_connectors]
        col_headers = [f"{m} - {c}" for m, c, _ in col_connectors]
        self.matrix_table.setRowCount(len(row_headers))
        self.matrix_table.setColumnCount(len(col_headers))
        self.matrix_table.setVerticalHeaderLabels(row_headers)
        self.matrix_table.setHorizontalHeaderLabels(col_headers)

        self._update_scope_label(len(row_headers), len(col_headers))
        self.refresh_matrix_display()

    def _fetch_scope_connectors(self, cursor, project_id, level, sid):
        """Fetch (module_name, connector_name, connector_id) rows for a scope."""
        base = """
            SELECT DISTINCT m.name, c.name, c.id
            FROM connectors c
            JOIN modules m ON c.module_id = m.id AND m.project_id = %s
            WHERE c.project_id = %s
        """
        if level == "subsystem":
            cursor.execute(
                base + " AND m.subsystem_id = %s ORDER BY m.name, c.name",
                (project_id, project_id, sid),
            )
        elif level == "module":
            cursor.execute(
                base + " AND c.module_id = %s ORDER BY m.name, c.name",
                (project_id, project_id, sid),
            )
        elif level == "connector":
            cursor.execute(
                base + " AND c.id = %s ORDER BY m.name, c.name",
                (project_id, project_id, sid),
            )
        else:
            cursor.execute(base + " ORDER BY m.name, c.name", (project_id, project_id))
        return cursor.fetchall()

    def refresh_matrix_display(self):
        """
        Fill the matrix table with the interfaces that exist between the
        ROW-scope connectors and the COLUMN-scope connectors.
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
                        i.color, i.current
                    FROM interfaces i
                    WHERE i.project_id = %s
                """, (project_id, project_id, project_id))
                all_ifaces = cursor.fetchall()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading interfaces: {str(e)}")
            return

        row_index = {cid: i for i, (_, _, cid) in enumerate(self.matrix_row_connectors)}
        col_index = {cid: i for i, (_, _, cid) in enumerate(self.matrix_col_connectors)}

        n_rows = self.matrix_table.rowCount()
        n_cols = self.matrix_table.columnCount()

        # Clear all cells
        for r in range(n_rows):
            self.matrix_table.setRowHeight(r, 40)
            for c in range(n_cols):
                item = self.matrix_table.item(r, c)
                if not item:
                    item = QTableWidgetItem()
                    self.matrix_table.setItem(r, c, item)
                item.setText("")
                item.setData(Qt.UserRole, [])
                item.setBackground(QColor(Qt.transparent))

        # Fill cells: an interface between connector A (row) and connector B
        # (column) lands in cell (A, B) — and also in (B, A) when both
        # endpoints appear in both scopes (identical scopes keep the classic
        # symmetric view). Interfaces inside a single connector (c1 == c2)
        # are never shown.
        for p1_id, p2_id, iface_id, c1_id, c2_id, color, iface_cur in all_ifaces:
            if c1_id is None or c2_id is None or c1_id == c2_id:
                continue  # skip missing lookups and intra-connector relations

            p1_full_name = all_pins_map.get(p1_id, "N/A")
            p2_full_name = all_pins_map.get(p2_id, "N/A")
            p1_name = p1_full_name.split(': ')[-1]
            p2_name = p2_full_name.split(': ')[-1]
            entry = (p1_id, p1_name, p2_id, p2_name, iface_id, color or '#0000FF', float(iface_cur or 0.0))

            r1 = row_index.get(c1_id)
            c1c = col_index.get(c1_id)
            r2 = row_index.get(c2_id)
            c2c = col_index.get(c2_id)
            for r, c in ((r1, c2c), (r2, c1c)):
                if r is None or c is None:
                    continue
                item = self.matrix_table.item(r, c)
                conn_list = item.data(Qt.UserRole) or []
                if entry not in conn_list:
                    conn_list.append(entry)
                item.setData(Qt.UserRole, conn_list)
                item.setText(f"{len(conn_list)} Interface(s)")
                item.setFont(QFont("Roboto Mono", 11))
                item.setTextAlignment(Qt.AlignCenter)

        self.matrix_table.resizeColumnsToContents()
        self.matrix_table.resizeRowsToContents()
        # تنظیم عرض ستون‌ها
        for col in range(n_cols):
            self.matrix_table.setColumnWidth(col, 150)

    def edit_matrix_cell(self, row, column):
        """
        Show dialog for editing interfaces between two connectors.
        System admins edit the DB directly; other users' changes are
        recorded as suggestions for approval.
        """
        connector1_id = self.matrix_row_connector_ids[row]
        connector2_id = self.matrix_col_connector_ids[column]

        if connector1_id == connector2_id:
            QMessageBox.information(
                self, "Info",
                "Relations inside a single connector are not shown in the matrix.",
            )
            return

        connector1_name = self.matrix_table.verticalHeaderItem(row).text()
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
        iface_table.setColumnCount(6)
        iface_table.setHorizontalHeaderLabels(["Pin from Conn1", "Pin from Conn2", "Color", "Current (mA)", "Edit", "Delete"])
        
        # اعمال استایل جدول
        self.apply_interface_table_style(iface_table)
        
        iface_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        existing_layout.addWidget(iface_table)
        layout.addWidget(existing_group)

        # --- Refresh function for existing interfaces ---

        def refresh_cell_list():
            iface_table.setRowCount(len(existing_ifaces))
            for idx, (p1_id, p1_name, p2_id, p2_name, iface_id, color, cur_mA) in enumerate(existing_ifaces):
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

                # Connection current
                cur_item = QTableWidgetItem(f"{cur_mA or 0:g}")
                cur_item.setTextAlignment(Qt.AlignCenter)
                cur_item.setFont(QFont("Roboto Mono", 11))
                iface_table.setItem(idx, 3, cur_item)

                # ادامه کد برای دکمه‌های Edit و Delete
                edit_btn = create_styled_button("✏️ Edit", "small")
                edit_btn.clicked.connect(lambda _, iid=iface_id: on_edit(iid))
                iface_table.setCellWidget(idx, 4, edit_btn)

                delete_btn = create_styled_button("🗑️ Delete", "small")
                delete_btn.clicked.connect(lambda _, iid=iface_id: on_delete(iid))
                iface_table.setCellWidget(idx, 5, delete_btn)

            
            for col in range(iface_table.columnCount()):
                iface_table.setColumnWidth(col, 150)

        refresh_cell_list()

        # --- Delete handler ---
        def on_delete(iid):
            confirm = QMessageBox.question(dialog, "Confirm Delete", "Delete this interface?")
            if confirm == QMessageBox.Yes:
                if not self._ensure_project_selected():
                    return
                # System admins delete directly; others submit a suggestion.
                ok, msg = propose_interface_change("delete", None, None, iface_id=iid)
                if not ok:
                    QMessageBox.warning(dialog, "Cannot Delete", msg)
                    return
                QMessageBox.information(dialog, "Success", msg)
                existing_ifaces[:] = [e for e in existing_ifaces if e[4] != iid]
                self.load_matrix_data()
                self.interface_changed.emit()
                refresh_cell_list()

        # --- Edit handler ---
        def on_edit(iid):
            self._edit_interface_in_dialog(iid, refresh_cell_list)

        # --- Add New Interface section ---
        add_new_group = QGroupBox("Add New Pin-to-Pin Interface")
        add_new_group.setFont(QFont("Roboto Mono", 13, QFont.Bold))
        add_new_layout = QFormLayout(add_new_group)
        add_new_layout.setLabelAlignment(Qt.AlignLeft)
        
        # Pins now carry their electrical info: (pin_id, label, info_dict)
        pin1_combo = QComboBox()
        pin1_combo.setFont(QFont("Roboto Mono", 11))
        for p_id, p_name_numbered, _info in pins1:
            pin1_combo.addItem(p_name_numbered, p_id)
        add_new_layout.addRow(f"Pin from {connector1_name}:", pin1_combo)

        pin2_combo = QComboBox()
        pin2_combo.setFont(QFont("Roboto Mono", 11))
        for p_id, p_name_numbered, _info in pins2:
            pin2_combo.addItem(p_name_numbered, p_id)
        add_new_layout.addRow(f"Pin from {connector2_name}:", pin2_combo)

        # Same-type wiring rule: picking a pin on one side only lists
        # compatible pins on the other side.
        def _pin_info(pin_list, pin_id):
            for _pid, _pname, info in pin_list:
                if _pid == pin_id:
                    return info
            return None

        def _refill_pin_combo(combo, pin_list, reference):
            combo.blockSignals(True)
            try:
                combo.clear()
                for p_id, p_name_numbered, info in pin_list:
                    if reference is not None:
                        ok, _ = pins_connectable_from_data(reference, info)
                        if not ok:
                            continue
                    combo.addItem(p_name_numbered, p_id)
            finally:
                combo.blockSignals(False)

        def _on_pin1_changed(_idx):
            _refill_pin_combo(pin2_combo, pins2, _pin_info(pins1, pin1_combo.currentData()))

        def _on_pin2_changed(_idx):
            _refill_pin_combo(pin1_combo, pins1, _pin_info(pins2, pin2_combo.currentData()))

        pin1_combo.currentIndexChanged.connect(_on_pin1_changed)
        pin2_combo.currentIndexChanged.connect(_on_pin2_changed)
        _on_pin1_changed(0)  # initial state: filter pin2 by the first pin
        
        color_combo = QComboBox()
        color_combo.setFont(QFont("Roboto Mono", 11))
        for color_name, color_hex in PREDEFINED_COLORS:
            pixmap = QPixmap(16, 16)
            pixmap.fill(QColor(color_hex))
            icon = QIcon(pixmap)
            color_combo.addItem(icon, color_name)
        color_combo.setCurrentText("Blue")
        add_new_layout.addRow("Interface Color:", color_combo)

        # Connection current — decided on the connection itself, not the pin
        cur_spin = QDoubleSpinBox()
        cur_spin.setRange(0.0, 1000000.0)
        cur_spin.setDecimals(1)
        cur_spin.setValue(0.0)
        cur_spin.setSuffix(" mA")
        add_new_layout.addRow("Current (mA):", cur_spin)

        # define handler BEFORE connecting
        def on_add():
            if not self._ensure_project_selected():
                return

            p1_id = pin1_combo.currentData()
            p2_id = pin2_combo.currentData()
            color_index = color_combo.currentIndex()

            if not p1_id or not p2_id or p1_id == p2_id:
                QMessageBox.warning(dialog, "Error", "Select two different pins.")
                return

            color_hex = PREDEFINED_COLORS[color_index][1]

            # System admins write straight to the DB; everyone else's change
            # is recorded as a suggestion for the admin to approve.
            ok, msg = propose_interface_change(
                "create", p1_id, p2_id, color_hex, cur_spin.value()
            )
            if not ok:
                # e.g., "Interface already exists."
                QMessageBox.information(dialog, "Info", msg)
                return

            # Non-admins must know their change is only pending approval.
            if not auth.is_system():
                QMessageBox.information(dialog, "Submitted", msg)

            # refresh UI once
            self.load_matrix_data()
            self.interface_changed.emit()
            dialog.accept()


        add_btn = create_styled_button("➕ Add This Pin Interface", "normal")
        add_btn.setFont(QFont("Roboto Mono", 11, QFont.Bold))
        add_new_layout.addRow(add_btn)
        add_btn.clicked.connect(on_add)

        layout.addWidget(add_new_group)

        # --- Close Button ---
        close_btn = create_styled_button("❌ Close", "large")
        close_btn.clicked.connect(dialog.reject)
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
                cursor.execute("SELECT pin1_id, pin2_id, color, current FROM interfaces WHERE id = %s AND project_id = %s", 
                            (iface_id, project_id))
                data = cursor.fetchone()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading interface: {str(e)}")
            return
            
        if not data:
            QMessageBox.critical(self, "Error", "Interface not found.")
            return
        pin1_id, pin2_id, color, cur_mA = data

        dialog = AddInterfaceDialog(self)
        auto_style_widget(dialog)  # اعمال استایل خودکار
        dialog.current_spin.setValue(float(cur_mA or 0.0))

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
                new_p1, new_p2, new_color, new_current = new_data
                try:
                    ok, msg = propose_interface_change(
                        "update", new_p1, new_p2, new_color, new_current, iface_id=iface_id
                    )
                    if not ok:
                        QMessageBox.warning(self, "Cannot Update", msg)
                        return
                    QMessageBox.information(self, "Success", msg)
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
                    "SELECT id, name, pin_number, pin_type, is_ground, value "
                    "FROM pins WHERE connector_id = %s AND project_id = %s ORDER BY pin_number",
                    (connector_id, project_id)
                )
                pins = []
                for pin_id, pin_name, pin_num, ptype, isg, val in cursor.fetchall():
                    info = {
                        "id": pin_id,
                        "name": pin_name,
                        "pin_number": pin_num,
                        "pin_type": ptype,
                        "is_ground": bool(isg) if isg is not None else False,
                        "value": val,
                    }
                    pins.append((pin_id, _pin_label(info), info))
            self.connector_data_cache[connector_id] = pins
            return pins
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Error loading pins: {str(e)}")
            return []