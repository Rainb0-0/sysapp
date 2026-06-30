from PyQt5.QtWidgets import (
    QStyledItemDelegate,
    QLineEdit,
    QDoubleSpinBox,
    QSpinBox,
    QWidget,
    QVBoxLayout,
    QPushButton,
    QFileDialog,
    QComboBox as Combo,
)
from PyQt5.QtCore import Qt


class DoubleDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        w = QDoubleSpinBox(parent)
        w.setRange(-999999, 999999)
        return w

    def setEditorData(self, editor, index):
        editor.setValue(float(index.data() or 0))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.EditRole)


class IntDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        w = QSpinBox(parent)
        w.setRange(-999999, 999999)
        return w

    def setEditorData(self, editor, index):
        editor.setValue(int(index.data() or 0))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.EditRole)


class ColorDelegate(QStyledItemDelegate):
    def __init__(self, colors):
        super().__init__()
        self.colors = colors

    def createEditor(self, parent, option, index):
        c = Combo(parent)
        c.addItems(self.colors)
        return c

    def setEditorData(self, editor, index):
        val = index.data() or self.colors[0]
        pos = self.colors.index(val) if val in self.colors else 0
        editor.setCurrentIndex(pos)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)


class ImageButton(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.btn = QPushButton("Choose")
        layout.addWidget(self.btn)
        layout.setContentsMargins(0, 0, 0, 0)


class ImageDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = ImageButton(parent)
        editor.btn.clicked.connect(lambda _, e=editor, i=index: self.pick_image(e, i))
        editor.setFocus(Qt.MouseFocusReason)
        return editor

    def pick_image(self, editor, index):
        path, _ = QFileDialog.getOpenFileName(
            editor, "Select Image", "", "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        if path:
            model = index.model()
            model.setData(index, path, Qt.EditRole)
            self.commitData.emit(editor)
            model.dataChanged.emit(index, index)
