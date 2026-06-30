from PyQt5.QtCore import Qt, QEvent, QObject,QTimer
from PyQt5.QtWidgets import QLineEdit, QSpinBox

class NavigableLineEdit(QLineEdit):
    def focusInEvent(self, event):
        super().focusInEvent(event)
        QTimer.singleShot(0, self.selectAll)  # Select all after focus (fixes issues with some Qt styles)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            event.ignore()  # Allow table to handle up/down for row navigation
            return

        cursor_pos = self.cursorPosition()
        text_length = len(self.text())

        if event.key() == Qt.Key_Right:
            if cursor_pos == text_length:
                event.ignore()  # At end: allow table to move to next cell
                return
        elif event.key() == Qt.Key_Left:
            if cursor_pos == 0:
                event.ignore()  # At start: allow table to move to previous cell
                return
        super().keyPressEvent(event)  # Handle normal cursor movement or other keys

class NavigableSpinBox(QSpinBox):
    def focusInEvent(self, event):
        super().focusInEvent(event)
        line_edit = self.lineEdit()
        if line_edit:
            QTimer.singleShot(0, line_edit.selectAll)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            event.ignore()  # Allow table to handle up/down for row navigation
            return

        line_edit = self.lineEdit()
        cursor_pos = line_edit.cursorPosition()
        text_length = len(line_edit.text())

        if event.key() == Qt.Key_Right:
            if cursor_pos == text_length:
                event.ignore()  # At end: allow table to move to next cell
                return
        elif event.key() == Qt.Key_Left:
            if cursor_pos == 0:
                event.ignore()  # At start: allow table to move to previous cell
                return

        super().keyPressEvent(event)  # Handle normal cursor movement or other key

class FocusEventFilter(QObject):
    """
    An event filter to highlight widget on focus.
    Install on each cell widget to get a blue border when focused.
    """
    def eventFilter(self, obj, event):
        if event.type() == QEvent.FocusIn:
            obj.setStyleSheet(obj.styleSheet() + "; border: 2px solid blue;")
        elif event.type() == QEvent.FocusOut:
            # Remove only the focus border, keep other styles
            style = obj.styleSheet().replace("; border: 2px solid blue;", "")
            obj.setStyleSheet(style)
        return False
