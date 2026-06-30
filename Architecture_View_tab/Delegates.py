from PyQt5.QtWidgets import QStyledItemDelegate, QSpinBox, QComboBox
from PyQt5.QtCore import Qt


# SpinBox Delegate
class SpinDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        spin = QSpinBox(parent)
        spin.setRange(0, 1000)
        return spin

    def setEditorData(self, editor, index):
        value = int(index.data())
        editor.setValue(value)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value())


# Color Combo Delegate
class ColorDelegate(QStyledItemDelegate):
    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.colors = colors

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(self.colors)
        return combo

    def setEditorData(self, editor, index):
        value = index.data()
        idx = editor.findText(value)
        editor.setCurrentIndex(idx if idx >= 0 else 0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText())
