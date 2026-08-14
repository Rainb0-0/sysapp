# suggestions_tab.py
"""
Reviews tab: the system admin's queue of change suggestions.

Non-admin users see their OWN pending suggestions here (status, ability to
withdraw). The system admin sees every suggestion for the current project
and can approve (apply to the DB), reject, or jump to the affected item.

Emits:
    pending_count_changed(int)  -- total pending count (for tab badges)
    changes_applied()           -- after approve/reject/withdraw (refresh tabs)
    jump_requested(str, int)    -- (entity_type, id_or_temp_id) switch tab
"""

from datetime import datetime

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QDialog,
    QTextEdit,
    QPushButton,
    QFrame,
    QInputDialog,
)

from auth_manager import auth
from styles.style_manager import style_manager, create_styled_button
from styles.theme_manager import theme_manager
from styles.design_system import BorderRadius, Spacing, Typography

import suggestions as suggestions_mod


STATUS_STYLE = {
    "pending": ("⏳ Pending", "#ffd76e"),
    "approved": ("✅ Approved", "#27ae60"),
    "rejected": ("🚫 Rejected", "#e74c3c"),
    "stale": ("⚠️ Stale", "#f39c12"),
}


def _fmt_dt(value):
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


class PayloadDialog(QDialog):
    """Read-only dialog showing the full JSON payload of a suggestion."""

    def __init__(self, change: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Suggestion #{change['id']} — {change['summary']}")
        self.resize(560, 420)
        layout = QVBoxLayout(self)

        import json as _json

        text = _json.dumps(change.get("payload") or {}, indent=2, default=str)
        view = QTextEdit()
        view.setPlainText(text)
        view.setReadOnly(True)
        view.setStyleSheet(
            "QTextEdit { background: #14141f; color: #9fe0a8;"
            " font-family: 'Roboto Mono', Consolas, monospace;"
            " border: 1px solid #333; border-radius: 8px; }"
        )
        layout.addWidget(view)

        close_btn = create_styled_button("Close", "normal")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class SuggestionsTab(QWidget):
    pending_count_changed = pyqtSignal(int)
    changes_applied = pyqtSignal()
    jump_requested = pyqtSignal(str, int)  # (entity_type, id_or_temp_id)

    STATUS_FILTERS = [
        ("pending", "⏳ Pending"),
        ("approved", "✅ Approved"),
        ("rejected", "🚫 Rejected"),
        ("stale", "⚠️ Stale"),
        ("all", "All"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SuggestionsTab")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._current_filter = "pending"
        self._rows = []  # dicts from suggestions.list_changes

        self._build_ui()
        self._apply_style()
        auth.auth_changed.connect(self._on_auth_changed)
        self.refresh()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ---- header ----
        header = QFrame()
        header.setObjectName("SuggestionsHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 8, 16, 8)

        title = QLabel("📥 Change Suggestions")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {theme_manager.get_color('text_primary')};"
        )
        h.addWidget(title)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            f"font-size: 12px; color: {theme_manager.get_color('text_secondary')};"
        )
        h.addWidget(self.count_label)
        h.addStretch()

        h.addWidget(QLabel("Show:"))
        self.filter_combo = QComboBox()
        for value, label in self.STATUS_FILTERS:
            self.filter_combo.addItem(label, value)
        self.filter_combo.currentIndexChanged.connect(
            lambda _: self._set_filter(self.filter_combo.currentData())
        )
        h.addWidget(self.filter_combo)

        self.refresh_btn = create_styled_button("🔄 Refresh", "small")
        self.refresh_btn.clicked.connect(self.refresh)
        h.addWidget(self.refresh_btn)

        layout.addWidget(header)

        # ---- table ----
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Status", "ID", "Type", "Action", "Summary", "Suggested by", "When"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(1, 48)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 70)
        self.table.setColumnWidth(6, 130)
        layout.addWidget(self.table, 1)

        # ---- actions ----
        actions = QHBoxLayout()
        self.approve_btn = create_styled_button("✅ Approve", "normal")
        self.reject_btn = create_styled_button("🚫 Reject…", "normal")
        self.details_btn = create_styled_button("📄 Details", "small")
        self.withdraw_btn = create_styled_button("🗑️ Withdraw (my suggestion)", "small")
        self.jump_btn = create_styled_button("📍 Jump to item", "small")
        self.approve_btn.clicked.connect(self._approve_selected)
        self.reject_btn.clicked.connect(self._reject_selected)
        self.details_btn.clicked.connect(self._show_details)
        self.withdraw_btn.clicked.connect(self._withdraw_selected)
        self.jump_btn.clicked.connect(self._jump_selected)
        for b in (self.approve_btn, self.reject_btn, self.details_btn,
                  self.withdraw_btn, self.jump_btn):
            actions.addWidget(b)
        actions.addStretch()
        layout.addLayout(actions)

        self.apply_access_policy()

    def _apply_style(self):
        self.setStyleSheet(f"""
            QWidget#SuggestionsTab {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}
            QFrame#SuggestionsHeader {{
                background: {theme_manager.get_gradient('primary', 'x1:0, y1:0, x2:1, y2:0')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.LARGE};
            }}
            QComboBox {{
                background: {theme_manager.get_color('primary_dark')};
                color: {theme_manager.get_color('text_primary')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.MEDIUM};
                padding: 4px 12px;
                min-width: 130px;
            }}
            QTableWidget {{
                background: {theme_manager.get_color('primary_dark')};
                color: {theme_manager.get_color('text_primary')};
                gridline-color: rgba(128,128,128,0.15);
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.LARGE};
                font-size: 12px;
            }}
            QTableWidget::item {{ padding: 4px 8px; }}
            QHeaderView::section {{
                background: {theme_manager.get_color('primary_medium')};
                color: {theme_manager.get_color('text_primary')};
                border: none;
                padding: 6px 8px;
                font-weight: 600;
            }}
        """)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def _on_auth_changed(self):
        self.apply_access_policy()
        self.refresh()

    def apply_access_policy(self):
        """Only the system admin can approve/reject."""
        is_system = auth.is_logged_in() and auth.is_system()
        if hasattr(self, "approve_btn"):
            self.approve_btn.setEnabled(is_system)
            self.reject_btn.setEnabled(is_system)
        if hasattr(self, "withdraw_btn"):
            # everyone can withdraw their own pending suggestions
            self.withdraw_btn.setEnabled(auth.is_logged_in())

    def _set_filter(self, value):
        self._current_filter = value or "pending"
        self.refresh()

    def refresh(self):
        """Reload the queue (project-scoped; own-only for non-admins)."""
        try:
            statuses = None if self._current_filter == "all" else [self._current_filter]
            is_system = auth.is_logged_in() and auth.is_system()
            self._rows = suggestions_mod.list_changes(
                statuses=statuses,
                user_id=None if is_system else auth.user_id,
            )
            self._populate_table()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load suggestions:\n{e}")
            self._rows = []
            self._populate_table()

        # Emit badge count regardless of filter
        try:
            counts = suggestions_mod.pending_counts()
            self.pending_count_changed.emit(int(counts.get("pending", 0)))
        except Exception:
            self.pending_count_changed.emit(0)

    def _populate_table(self):
        rows = self._rows
        self.table.setRowCount(len(rows))
        for i, ch in enumerate(rows):
            label, color = STATUS_STYLE.get(ch["status"], (ch["status"], "#888"))
            status_item = QTableWidgetItem(label)
            status_item.setForeground(Qt.white)
            status_item.setBackground(
                __import__("PyQt5.QtGui", fromlist=["QColor"]).QColor(color + "55")
            )
            id_item = QTableWidgetItem(str(ch["id"]))
            type_item = QTableWidgetItem(
                suggestions_mod.ENTITY_LABELS.get(ch["entity_type"], ch["entity_type"])
            )
            action_item = QTableWidgetItem(
                suggestions_mod.ACTION_LABELS.get(ch["action"], ch["action"])
            )
            summary_item = QTableWidgetItem(ch["summary"])
            who_item = QTableWidgetItem(ch.get("username") or f"user#{ch['user_id']}")
            when_item = QTableWidgetItem(_fmt_dt(ch["created_at"]))

            self.table.setItem(i, 0, status_item)
            self.table.setItem(i, 1, id_item)
            self.table.setItem(i, 2, type_item)
            self.table.setItem(i, 3, action_item)
            self.table.setItem(i, 4, summary_item)
            self.table.setItem(i, 5, who_item)
            self.table.setItem(i, 6, when_item)

            for col in range(7):
                item = self.table.item(i, col)
                if item and col in (1, 2, 3, 5, 6):
                    item.setTextAlignment(Qt.AlignCenter)

        if len(rows) == 1:
            self.table.selectRow(0)

        self.count_label.setText(f"{len(rows)} shown")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _selected_change(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "No selection", "Select a suggestion first.")
            return None
        return self._rows[row]

    def _approve_selected(self):
        ch = self._selected_change()
        if not ch:
            return
        if not (auth.is_logged_in() and auth.is_system()):
            QMessageBox.warning(self, "Access denied", "Only the system admin can approve changes.")
            return
        reply = QMessageBox.question(
            self,
            "Approve change",
            f"Apply this change to the database?\n\n{ch['summary']}",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            ok, msg = suggestions_mod.approve_change(ch["id"], auth.user_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to approve:\n{e}")
            return
        if ok:
            QMessageBox.information(self, "Approved", f"Change applied to the database.\n{msg}")
        else:
            QMessageBox.warning(
                self, "Not applied",
                f"The change could not be applied and was marked as stale:\n\n{msg}",
            )
        self.refresh()
        self.changes_applied.emit()

    def _reject_selected(self):
        ch = self._selected_change()
        if not ch:
            return
        if not (auth.is_logged_in() and auth.is_system()):
            QMessageBox.warning(self, "Access denied", "Only the system admin can reject changes.")
            return
        note, ok = QInputDialog.getMultiLineText(
            self, "Reject change", "Reason (optional):", ""
        )
        if not ok:
            return
        try:
            suggestions_mod.reject_change(ch["id"], auth.user_id, note.strip())
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to reject:\n{e}")
            return
        QMessageBox.information(self, "Rejected", "The change was rejected and will not be applied.")
        self.refresh()
        self.changes_applied.emit()

    def _withdraw_selected(self):
        ch = self._selected_change()
        if not ch:
            return
        if ch["status"] != "pending":
            QMessageBox.information(self, "Withdraw", "Only pending suggestions can be withdrawn.")
            return
        if ch["user_id"] != auth.user_id and not auth.is_system():
            QMessageBox.warning(self, "Access denied", "You can only withdraw your own suggestions.")
            return
        reply = QMessageBox.question(
            self, "Withdraw",
            f"Remove this pending suggestion?\n\n{ch['summary']}",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            suggestions_mod.withdraw_change(ch["id"])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to withdraw:\n{e}")
            return
        self.refresh()
        self.changes_applied.emit()

    def _show_details(self):
        ch = self._selected_change()
        if not ch:
            return
        PayloadDialog(ch, self).exec_()

    def _jump_selected(self):
        ch = self._selected_change()
        if not ch:
            return
        etype = ch["entity_type"]
        eid = ch["entity_id"] if ch["entity_id"] is not None else ch["temp_id"]
        self.jump_requested.emit(etype, eid)
