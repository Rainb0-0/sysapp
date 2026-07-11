# -----------------------------------------------------------------------------
# connector_pin_graphics.py - Connector and Pin Graphics Components
# -----------------------------------------------------------------------------
import sys
from PyQt5.QtWidgets import (
    QApplication, QGraphicsScene, QGraphicsView, QDialog,
    QVBoxLayout, QListWidget, QPushButton, QHBoxLayout,
    QGraphicsPathItem, QGraphicsEllipseItem, QListWidgetItem, QGraphicsItem, QStyle
)
from PyQt5.QtGui import (
    QPainterPath, QBrush, QColor, QPen, QFont, QFontMetrics, QPainter, QIcon, QPixmap, QLinearGradient
)
from PyQt5.QtCore import QRectF, Qt, QPointF, QSize 

# Constants
PIN_FONT = QFont("Roboto Mono", 12, QFont.Bold)
SUP_FONT = QFont("Roboto Mono", 14, QFont.Bold)
PIN_COLOR = "#FFFFFF"
BODY_COLOR = "#F8913C"
SUP_COLOR = "#e0e5f2"
PIN_DIAMETER = 18
PIN_HEIGHT = 32
LABEL_HEIGHT = 32
CORNER_RADIUS = 14
LEFT_MARGIN = 10
MID_MARGIN = 7
RIGHT_MARGIN = 14
LABEL_MARGIN = 8

def build_pin_uid(module_id: str, connector_id: str, pin_label: str) -> str:
    """Generate a unique identifier for a pin"""
    return f"{module_id}/{connector_id}/{pin_label}"

class PinOrderDialog(QDialog):
    """Dialog for reordering pins via drag-and-drop"""
    
    def __init__(self, pin_names, connector_label="Connector", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Edit "{connector_label}" Pin Order')
        self.setFont(QFont("Roboto Mono", 12))
        self.pin_names = list(pin_names)
        self.setup_ui()

    def setup_ui(self):
        """Setup the user interface"""
        # Main list widget
        self.list_widget = QListWidget()
        self.list_widget.setFont(QFont("Roboto Mono", 12))
        self.list_widget.setDragDropMode(QListWidget.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.MoveAction)
        self._populate_items()

        # Buttons
        btn_ok = QPushButton("OK")
        btn_ok.setFont(QFont("Roboto Mono", 12, QFont.Bold))
        btn_ok.clicked.connect(self.accept)
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setFont(QFont("Roboto Mono", 12))
        btn_cancel.clicked.connect(self.reject)
        
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(btn_ok)
        button_layout.addWidget(btn_cancel)

        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self.list_widget)
        main_layout.addLayout(button_layout)

    def _make_handle_icon(self):
        """Create drag handle icon"""
        size = 24
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setFont(QFont("Roboto Mono", 14, QFont.Bold))
        painter.setPen(QColor("#888888"))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "⋮")
        painter.end()
        return QIcon(pixmap)

    def _populate_items(self):
        """Fill list widget with pin items"""
        self.list_widget.clear()
        handle_icon = self._make_handle_icon()

        for idx, pin in enumerate(self.pin_names):
            item = QListWidgetItem(pin)
            item.setFont(QFont("Roboto Mono", 12))
            item.setBackground(QBrush(QColor("#f0f0f0") if idx % 2 else QColor("#ffffff")))
            item.setIcon(handle_icon)
            item.setSizeHint(QSize(0, 32))
            self.list_widget.addItem(item)

    def get_new_order(self):
        """Get pin names in their new order after rearrangement"""
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

class EditablePinOrderMixin:
    """Mixin that enables double-click to edit pin order"""
    
    def mouseDoubleClickEvent(self, event):
        """Handle double-click to edit pin order"""
        dialog = PinOrderDialog(self.pin_names, connector_label=self.name)
        if dialog.exec_():
            new_order = dialog.get_new_order()
            self._update_pin_order_in_database(new_order)
            self._refresh_connector_display()

    def _update_pin_order_in_database(self, new_order):
        """Update pin numbering in database according to new order"""
        from database import get_connection, get_current_project_id
        
        parent = self.parentItem()
        if not hasattr(parent, "db_id"):
            return
        
        current_project_id = get_current_project_id()
        if current_project_id is None:
            return
            
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                
                # Get connector ID
                cur.execute(
                    "SELECT id FROM connectors WHERE module_id = %s AND name = %s AND project_id = %s", 
                    (parent.db_id, self.name, current_project_id)
                )
                row = cur.fetchone()
                if not row:
                    return
                    
                connector_id = row[0]
                
                # Update each pin's number
                for index, pin_label in enumerate(new_order):
                    cur.execute(
                        "UPDATE pins SET pin_number = %s WHERE connector_id = %s AND name = %s AND project_id = %s",
                        (index, connector_id, pin_label, current_project_id)
                    )
                    
                conn.commit()
                
        except Exception as e:
            print(f"Error updating pin order in database: {e}")
            
    def _refresh_connector_display(self):
        """Refresh the connector display after pin reordering"""
        if hasattr(self.scene(), "refresh_connections_and_pins"):
            self.scene().refresh_connections_and_pins()

class PinItem(QGraphicsEllipseItem):
    """Graphics item representing a single pin"""
    
    def __init__(self, pin_id: str, radius: float = PIN_DIAMETER, parent=None):
        super().__init__(-radius/2, -radius/2, radius, radius, parent)
        self.pin_id = pin_id
        self.observers = []
        self.setBrush(QBrush(QColor(PIN_COLOR)))
        self.setPen(QPen(Qt.black, 1))
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def center(self):
        """Get pin center position in scene coordinates"""
        return self.mapToScene(self.rect().center())

    def itemChange(self, change, value):
        """Handle position changes to update connected paths"""
        if change == QGraphicsItem.ItemPositionHasChanged:
            for observer in self.observers:
                observer.update_path()
        return super().itemChange(change, value)

class _BaseConnector(QGraphicsPathItem):
    """Base class for all connector types"""
    
    def __init__(self, x: float, y: float, name: str, pin_names: list[str], color=BODY_COLOR):
        super().__init__()
        self.name = name
        self.pin_names = pin_names or []
        self.pin_centers_local = []  # Local coordinates of pin centers
        self.pin_items = {}  # Mapping of pin UIDs to PinItem objects
        self.observers = []
        self.color = color
        
        # Create dynamic gradients based on user-selected color
        base_color = QColor(self.color)
        self._base_gradient = QLinearGradient(0, 0, 0, 0)  # Will be updated in subclasses
        self._base_gradient.setColorAt(0, base_color)
        self._base_gradient.setColorAt(1, base_color.darker(120))
        self._hover_gradient = QLinearGradient(0, 0, 0, 0)
        self._hover_gradient.setColorAt(0, base_color.lighter(140))
        self._hover_gradient.setColorAt(1, base_color)
        self._selected_gradient = QLinearGradient(0, 0, 0, 0)
        self._selected_gradient.setColorAt(0, base_color.lighter(160))
        self._selected_gradient.setColorAt(1, base_color.lighter(120))
        
        self._build_geometry()
        self.setPos(x, y)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)

    def _update_gradients(self, width, height):
        """Update gradient directions based on connector dimensions"""
        self._base_gradient.setFinalStop(0, height)
        self._hover_gradient.setFinalStop(width, height)
        self._selected_gradient.setFinalStop(width, height)

    def get_pin_offset(self, pin_idx: int) -> QPointF:
        """Get local position of pin center by index"""
        if 0 <= pin_idx < len(self.pin_centers_local):
            return self.pin_centers_local[pin_idx]
        return self.rect.center()

    def _paint_body(self, painter, option):
        """Paint the connector body with dynamic gradient"""
        if self.isSelected():
            painter.setBrush(QBrush(self._selected_gradient))
            painter.setPen(QPen(QColor(self.color).lighter(140), 2))
        elif option.state & QStyle.State_MouseOver:
            painter.setBrush(QBrush(self._hover_gradient))
            painter.setPen(QPen(QColor(self.color).lighter(120), 2))
        else:
            painter.setBrush(QBrush(self._base_gradient))
            painter.setPen(QPen(QColor(self.color).darker(150), 1))
        painter.drawPath(self.path())

    def _paint_sup_bar(self, painter, rect: QRectF):
        """Paint the connector title bar"""
        y_sup = rect.bottom() - LABEL_HEIGHT
        painter.setPen(QPen(QColor(SUP_COLOR), 2))
        painter.drawLine(QPointF(rect.left(), y_sup), QPointF(rect.right(), y_sup))
        painter.setFont(SUP_FONT)
        painter.setPen(QColor(SUP_COLOR))
        painter.drawText(QRectF(rect.left(), y_sup, rect.width(), LABEL_HEIGHT), 
                        Qt.AlignCenter, self.name)

    def _build_geometry(self):
        """Build connector geometry (implemented in subclasses)"""
        raise NotImplementedError

    def paint(self, painter, option, widget=None):
        """Main painting method"""
        painter.setRenderHint(QPainter.Antialiasing)
        self._paint_body(painter, option)
        self._draw_content(painter)

    def _draw_content(self, painter):
        """Draw connector-specific content (implemented in subclasses)"""
        raise NotImplementedError

    def boundingRect(self):
        """Return bounding rectangle with padding"""
        return self.rect.adjusted(-6, -6, 6, 6)

    def addPinsToScene(self, scene, connector_id):
        """FIXED: Add pin items to scene and register them with proper ordering"""
        # Clean up existing pins
        for child in list(self.childItems()):
            child.setParentItem(None)
            if scene is not None:
                scene.removeItem(child)
        
        # Clean up pin registry
        if hasattr(scene, "_pin_registry"):
            # Remove pins belonging to this connector
            to_remove = [k for k, v in scene._pin_registry.items() 
                        if v.parentItem() == self]
            for k in to_remove:
                del scene._pin_registry[k]
            
            # Remove orphaned pins
            orphans = [k for k, v in scene._pin_registry.items() 
                    if v.parentItem() is None]
            for k in orphans:
                del scene._pin_registry[k]
        
        self.pin_items.clear()

        # Add new pins with proper ordering
        if not hasattr(scene, "_pin_registry"):
            return
            
        module_item = self.parentItem()
        module_id = getattr(module_item, "_name", "module") 
        
        for idx, local_pos in enumerate(self.pin_centers_local):
            pin_label = self.pin_names[idx] if idx < len(self.pin_names) else str(idx)
            pin_uid = build_pin_uid(module_id, self.name, pin_label)

            pin = PinItem(pin_uid, parent=self)
            pin.setPos(local_pos)   
            pin.order_idx = idx
            
            if hasattr(self, 'side'):
                pin.side = self.side
            
            self.pin_items[pin_uid] = pin
            scene._pin_registry[pin_uid] = pin
            
    def update_pins_after_reorder(self):
        """Reload pin order from database and refresh display"""
        from database import get_connection, get_current_project_id
        
        parent = self.parentItem()
        if not hasattr(parent, "db_id"):
            return
        
        current_project_id = get_current_project_id()
        if current_project_id is None:
            return
            
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                
                # Get connector ID
                cur.execute(
                    "SELECT id FROM connectors WHERE module_id = %s AND name = %s AND project_id = %s",
                    (parent.db_id, self.name, current_project_id)
                )
                row = cur.fetchone()
                if not row:
                    return
                    
                connector_id = row[0]
                
                # Get updated pin order
                cur.execute(
                    "SELECT name FROM pins WHERE connector_id = %s AND project_id = %s ORDER BY pin_number",
                    (connector_id, current_project_id)
                )
                self.pin_names = [r[0] for r in cur.fetchall()]

            # Rebuild geometry
            self.prepareGeometryChange()
            self._build_geometry()
            self.update()
            self.addPinsToScene(self.scene(), connector_id=None)
            
        except Exception as e:
            print(f"Error updating pins after reorder: {e}")

class LeftConnector(EditablePinOrderMixin, _BaseConnector):
    """Left-side connector with vertical pin layout"""
    
    def __init__(self, x, y, name, pin_names, color=BODY_COLOR):
        super().__init__(x, y, name, pin_names, color)

    def _build_geometry(self):
        """Build left connector geometry"""
        draw_names = self.pin_names
        fm = QFontMetrics(PIN_FONT)
        
        # Calculate dimensions
        body_width = max(
            self._pin_text_width(draw_names) + LEFT_MARGIN + PIN_DIAMETER + MID_MARGIN + RIGHT_MARGIN,
            QFontMetrics(SUP_FONT).horizontalAdvance(self.name) + 24
        )
        body_height = len(draw_names) * PIN_HEIGHT + LABEL_HEIGHT
        
        self.rect = QRectF(0, 0, body_width, body_height)
        self._update_gradients(body_width, body_height)
        path = QPainterPath()
        path.addRoundedRect(self.rect, CORNER_RADIUS, CORNER_RADIUS)
        self.setPath(path)
        
        # Calculate pin positions
        self.pin_centers_local.clear()
        x_center = LEFT_MARGIN + PIN_DIAMETER / 2
        for i in range(len(draw_names)):
            self.pin_centers_local.append(QPointF(x_center, i * PIN_HEIGHT + PIN_HEIGHT/2))
            
        self._draw_names = draw_names

    def _pin_text_width(self, pin_names):
        """Calculate maximum pin label width"""
        fm = QFontMetrics(PIN_FONT)
        return max(fm.horizontalAdvance(t) for t in pin_names) if pin_names else 0

    def _draw_content(self, painter):
        """Draw left connector content"""
        x_sep = LEFT_MARGIN + PIN_DIAMETER + MID_MARGIN
        x_txt = x_sep + 4
        text_width = self.rect.width() - x_txt - RIGHT_MARGIN
        
        painter.setFont(PIN_FONT)
        painter.setPen(QColor(SUP_COLOR))
        
        for idx, text in enumerate(self._draw_names):
            y0 = self.rect.top() + idx * PIN_HEIGHT
            
            # Draw separator line
            painter.setPen(QPen(QColor(SUP_COLOR), 1))
            painter.drawLine(
                QPointF(self.rect.left() + x_sep, y0 + 7),
                QPointF(self.rect.left() + x_sep, y0 + PIN_HEIGHT - 7)
            )
            
            # Draw pin label
            painter.drawText(
                QRectF(self.rect.left() + x_txt, y0, text_width, PIN_HEIGHT),
                Qt.AlignCenter, text
            )
            
            # Draw horizontal separator between pins
            if idx < len(self._draw_names) - 1:
                painter.drawLine(
                    QPointF(self.rect.left() + 4, y0 + PIN_HEIGHT),
                    QPointF(self.rect.right() - 4, y0 + PIN_HEIGHT)
                )
                
        self._paint_sup_bar(painter, self.rect)

class RightConnector(EditablePinOrderMixin, _BaseConnector):
    """Right-side connector with vertical pin layout"""
    
    def __init__(self, x, y, name, pin_names, color=BODY_COLOR):
        super().__init__(x, y, name, pin_names, color)

    def _build_geometry(self):
        """Build right connector geometry"""
        draw_names = list(self.pin_names)
        
        body_width = max(
            self._pin_text_width(draw_names) + LEFT_MARGIN + PIN_DIAMETER + MID_MARGIN + RIGHT_MARGIN,
            QFontMetrics(SUP_FONT).horizontalAdvance(self.name) + 24
        )
        body_height = len(draw_names) * PIN_HEIGHT + LABEL_HEIGHT
        
        self.rect = QRectF(0, 0, body_width, body_height)
        self._update_gradients(body_width, body_height)
        path = QPainterPath()
        path.addRoundedRect(self.rect, CORNER_RADIUS, CORNER_RADIUS)
        self.setPath(path)
        
        self.pin_centers_local.clear()
        x_center = self.rect.width() - LEFT_MARGIN - PIN_DIAMETER/2
        for i in range(len(draw_names)):
            self.pin_centers_local.append(QPointF(x_center, i * PIN_HEIGHT + PIN_HEIGHT/2))
            
        self._draw_names = draw_names

    def _pin_text_width(self, pin_names):
        """Calculate maximum pin label width"""
        fm = QFontMetrics(PIN_FONT)
        return max(fm.horizontalAdvance(t) for t in pin_names) if pin_names else 0

    def _draw_content(self, painter):
        """Draw right connector content"""
        x_circle_left = self.rect.width() - (LEFT_MARGIN + PIN_DIAMETER)
        x_sep = x_circle_left - MID_MARGIN
        x_txt = RIGHT_MARGIN
        text_width = x_circle_left - MID_MARGIN - 4 - x_txt
        
        painter.setFont(PIN_FONT)
        painter.setPen(QColor(SUP_COLOR))
        
        for idx, text in enumerate(self._draw_names):
            y0 = idx * PIN_HEIGHT
            
            # Draw separator line
            painter.setPen(QPen(QColor(SUP_COLOR), 1))
            painter.drawLine(
                QPointF(x_sep, y0 + 7),
                QPointF(x_sep, y0 + PIN_HEIGHT - 7)
            )
            
            # Draw pin label
            painter.drawText(
                QRectF(x_txt, y0, text_width, PIN_HEIGHT),
                Qt.AlignCenter, text
            )
            
            # Draw horizontal separator
            if idx < len(self._draw_names) - 1:
                painter.drawLine(
                    QPointF(4, y0 + PIN_HEIGHT),
                    QPointF(self.rect.width() - 4, y0 + PIN_HEIGHT)
                )
                
        self._paint_sup_bar(painter, self.rect)

class TopConnector(EditablePinOrderMixin, _BaseConnector):
    """Top-side connector with horizontal pin layout"""
    
    def __init__(self, x, y, name, pin_names, color=BODY_COLOR):
        super().__init__(x, y, name, pin_names, color)

    def _build_geometry(self):
        """Build top connector geometry"""
        self._draw_names = list(self.pin_names)
        fm = QFontMetrics(PIN_FONT)
        
        # Calculate cell widths
        self.cell_w = [
            max(PIN_DIAMETER + LEFT_MARGIN + RIGHT_MARGIN,
                fm.horizontalAdvance(label) + LABEL_MARGIN * 2)
            for label in self._draw_names
        ]
        
        base_width = sum(self.cell_w)
        title_width = QFontMetrics(SUP_FONT).horizontalAdvance(self.name) + 24
        total_width = max(base_width, title_width)
        self.extra = max(0, total_width - base_width)
        total_height = LABEL_HEIGHT + PIN_HEIGHT + MID_MARGIN + PIN_DIAMETER + LEFT_MARGIN
        
        self.rect = QRectF(0, 0, total_width, total_height)
        self._update_gradients(total_width, total_height)
        path = QPainterPath()
        path.addRoundedRect(self.rect, CORNER_RADIUS, CORNER_RADIUS)
        self.setPath(path)
        
        # Calculate pin positions
        self.pin_centers_local.clear()
        left = self.extra / 2
        y_center = 10 + PIN_DIAMETER / 2
        
        for width in self.cell_w:
            self.pin_centers_local.append(QPointF(left + width / 2, y_center))
            left += width

    def _draw_content(self, painter):
        """Draw top connector content"""
        block_left = self.extra / 2
        self._paint_sup_bar(painter, self.rect)
        
        circle_y = 10
        separator_y = circle_y + PIN_DIAMETER + 7
        label_y = separator_y
        
        painter.setFont(PIN_FONT)
        
        for idx, (width, text) in enumerate(zip(self.cell_w, self._draw_names)):
            # Draw separator line
            pen_width = 2 if text.upper() == 'PWR' else 1
            painter.setPen(QPen(QColor(SUP_COLOR), pen_width))
            painter.drawLine(
                QPointF(block_left + 4, separator_y),
                QPointF(block_left + width - 4, separator_y)
            )
            
            # Draw pin label
            painter.setPen(QColor(SUP_COLOR))
            painter.drawText(
                QRectF(block_left, label_y, width, PIN_HEIGHT),
                Qt.AlignCenter, text
            )
            
            # Draw vertical separator
            if idx < len(self.cell_w) - 1:
                painter.setPen(QPen(QColor(SUP_COLOR), 1))
                painter.drawLine(
                    QPointF(block_left + width, circle_y),
                    QPointF(block_left + width, self.rect.bottom() - LABEL_HEIGHT)
                )
            
            block_left += width

class BottomConnector(EditablePinOrderMixin, _BaseConnector):
    """Bottom-side connector with horizontal pin layout"""
    
    def __init__(self, x, y, name, pin_names, color=BODY_COLOR):
        super().__init__(x, y, name, pin_names, color)

    def _build_geometry(self):
        """Build bottom connector geometry"""
        draw_names = self.pin_names
        fm = QFontMetrics(PIN_FONT)
        
        # Calculate cell widths
        self.cell_w = [
            max(PIN_DIAMETER + LEFT_MARGIN + RIGHT_MARGIN,
                fm.horizontalAdvance(label) + LABEL_MARGIN * 2)
            for label in draw_names
        ]
        
        base_width = sum(self.cell_w)
        title_width = QFontMetrics(SUP_FONT).horizontalAdvance(self.name) + 24
        total_width = max(base_width, title_width)
        self.extra = max(0, total_width - base_width)
        total_height = LABEL_HEIGHT + PIN_HEIGHT + MID_MARGIN + PIN_DIAMETER + 8
        
        self.rect = QRectF(0, 0, total_width, total_height)
        self._update_gradients(total_width, total_height)
        path = QPainterPath()
        path.addRoundedRect(self.rect, CORNER_RADIUS, CORNER_RADIUS)
        self.setPath(path)
        
        # Calculate pin positions
        self.pin_centers_local.clear()
        left = self.extra / 2
        y_center = LABEL_HEIGHT + PIN_HEIGHT + 6 + PIN_DIAMETER / 2
        
        for width in self.cell_w:
            self.pin_centers_local.append(QPointF(left + width / 2, y_center))
            left += width
            
        self._draw_names = draw_names

    def _draw_content(self, painter):
        """Draw bottom connector content"""
        # Draw title bar separator
        painter.setPen(QPen(QColor(SUP_COLOR), 2))
        painter.drawLine(
            QPointF(self.rect.left(), LABEL_HEIGHT),
            QPointF(self.rect.right(), LABEL_HEIGHT)
        )
        
        # Draw title
        painter.setFont(SUP_FONT)
        painter.setPen(QColor(SUP_COLOR))
        painter.drawText(
            QRectF(self.rect.left(), 0, self.rect.width(), LABEL_HEIGHT),
            Qt.AlignCenter, self.name
        )
        
        # Draw pin labels and separators
        x = self.extra / 2
        y_label = LABEL_HEIGHT
        
        painter.setFont(PIN_FONT)
        painter.setPen(QColor(SUP_COLOR))
        
        for width, label in zip(self.cell_w, self._draw_names):
            # Draw pin label
            painter.drawText(
                QRectF(x, y_label, width, PIN_HEIGHT),
                Qt.AlignCenter, label
            )
            x += width
        
        # Draw horizontal separators
        separator_y = y_label + PIN_HEIGHT - 4
        x = self.extra / 2
        painter.setPen(QPen(QColor(SUP_COLOR), 1))
        
        for width in self.cell_w:
            painter.drawLine(
                QPointF(x + 4, separator_y),
                QPointF(x + width - 4, separator_y)
            )
            x += width
        
        # Draw vertical separators
        x = self.extra / 2
        painter.setPen(QPen(QColor(SUP_COLOR), 1))
        
        for width in self.cell_w[:-1]:
            x += width
            painter.drawLine(
                QPointF(x, y_label),
                QPointF(x, self.rect.bottom() - LABEL_HEIGHT + 25)
            )

class ConnectorFactory:
    """Factory for creating different types of connectors"""
    
    @staticmethod
    def create(x, y, name, pin_names, side: str, color=BODY_COLOR):
        """Create a connector of the specified type"""
        side = side.lower()
        if side == "left":
            return LeftConnector(x, y, name, pin_names, color)
        if side == "right":
            return RightConnector(x, y, name, pin_names, color)
        if side == "top":
            return TopConnector(x, y, name, pin_names, color)
        if side == "bottom":
            return BottomConnector(x, y, name, pin_names, color)
        raise ValueError(f"Unknown side '{side}'")

# Demo/Test
if __name__ == "__main__":
    app = QApplication(sys.argv)
    scene = QGraphicsScene()
    view = QGraphicsView(scene)
    view.resize(820, 560)
    
    test_pins = ["PWR", "GND", "DATA_IN", "DATA_OUT", "CLK"]
    
    # Create connectors for demonstration
    connectors = [
        ("left1", LeftConnector(40, 180, "INPUT", test_pins)),
        ("right1", RightConnector(560, 180, "OUTPUT", test_pins)),
        ("top1", TopConnector(210, 50, "CONTROL", test_pins)),
        ("bottom1", BottomConnector(210, 340, "POWER", test_pins))
    ]
    
    # Add connectors to scene
    for cid, connector in connectors:
        scene.addItem(connector)
        connector.addPinsToScene(scene, cid)
    
    view.show()
    sys.exit(app.exec_())