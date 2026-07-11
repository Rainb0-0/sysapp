# module_graphics.py - نسخه کامل fix شده

import sys
from PyQt5.QtWidgets import QGraphicsPathItem, QGraphicsItem, QStyle
from PyQt5.QtGui import QPainterPath, QPen, QBrush, QColor, QPixmap, QPainter, QFont, QLinearGradient, QGradient
from PyQt5.QtCore import QRectF, Qt, QPointF, QTimer, pyqtSignal

from Schematic_View_tab.shapes.connector_pin_graphics import ConnectorFactory, LEFT_MARGIN, MID_MARGIN, build_pin_uid
from Schematic_View_tab.shapes.connector_utils import suggest_module_size, auto_distribute_connectors, layout_side, enable_side_drag

# Constants
CONNECTION_MARGIN = 50
DEFAULT_MODULE_WIDTH = 130
DEFAULT_MODULE_HEIGHT = 90
DEFAULT_CORNER_RADIUS = 15
DEFAULT_COLOR = "#33A444"
TITLE_HEIGHT = 28
IMAGE_MARGIN = 8
HANDLE_SIZE = 8
RESIZE_MARGIN = 14

class ModuleGraphics(QGraphicsPathItem):
    """Custom graphics item representing a schematic module with connectors."""
    geometryChanged = pyqtSignal()

    def __init__(self, x=0, y=0, width=DEFAULT_MODULE_WIDTH, height=DEFAULT_MODULE_HEIGHT,
                radius=DEFAULT_CORNER_RADIUS, name="Module", image_path=None,
                color=DEFAULT_COLOR, connectors_info=None, parent=None):
        super().__init__(parent)
        
        # FIXED: Don't automatically apply suggest_module_size if explicit size given
        if connectors_info and (width == DEFAULT_MODULE_WIDTH and height == DEFAULT_MODULE_HEIGHT):
            # Only use suggested size if default values are used
            min_width, min_height = suggest_module_size(
                connectors_info, min_width=width, min_height=height, spacing=MID_MARGIN, padding=100
            )
            self._min_width = min_width
            self._min_height = min_height
            self._rect = QRectF(x, y, min_width, min_height)
        else:
            # Use explicit size provided (from database load)
            self._min_width = min(width, DEFAULT_MODULE_WIDTH)  # Allow smaller than default
            self._min_height = min(height, DEFAULT_MODULE_HEIGHT)  # Allow smaller than default
            self._rect = QRectF(x, y, width, height)
            
        self._radius = radius
        self._name = name
        self._image_path = image_path
        self._color = color
        self.connectors = []
        self.pins = {}
        self.observers = []
        self._pending_connectors_info = connectors_info
        self.setPen(QPen(QColor(self._color).darker(150), 1))
        self.setBrush(QBrush(QColor(self._color)))
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self._read_only = False
        self.setAcceptHoverEvents(True)
        self.handle_size = HANDLE_SIZE
        self.resizing = False
        self.resize_edge = None
        self.margin = RESIZE_MARGIN
        self._last_pos = None
        self.title_height = TITLE_HEIGHT
        self.img_margin = IMAGE_MARGIN
        self.border_offset = 30

        # Create dynamic gradients based on user-selected color
        base_color = QColor(self._color)
        darker_color = base_color.darker(120)
        lighter_color = base_color.lighter(120)
        self._base_gradient = QLinearGradient(0, 0, 0, self._rect.height())
        self._base_gradient.setColorAt(0, base_color)
        self._base_gradient.setColorAt(1, darker_color)
        self._hover_gradient = QLinearGradient(0, 0, self._rect.width(), self._rect.height())
        self._hover_gradient.setColorAt(0, base_color.lighter(140))
        self._hover_gradient.setColorAt(1, base_color)
        self._selected_gradient = QLinearGradient(0, 0, self._rect.width(), self._rect.height())
        self._selected_gradient.setColorAt(0, base_color.lighter(160))
        self._selected_gradient.setColorAt(1, base_color.lighter(120))

        self.updatePath()

    def set_read_only(self, flag: bool):
        self._read_only = flag
        self.setFlag(QGraphicsItem.ItemIsMovable, not flag)
        self.setFlag(QGraphicsItem.ItemIsSelectable, not flag)

    def finalize(self, connectors_info=None):
        if connectors_info is None:
            connectors_info = self._pending_connectors_info or []
        self._pending_connectors_info = None
        self._connectors_info = connectors_info
        self._add_connectors(connectors_info)
        self.update_connectors()

    def _add_connectors(self, connectors_info):
        """FIXED: Add connectors with proper attribute initialization"""
        # Ensure the connector 'id' is passed through to the temp_connectors list.
        temp_connectors = [
            {'id': info['id'], 'name': info['name'], 'pin_names': info['pin_names'], 
             'side': info.get('side', 'top'), 'color': info.get('color', "#F8913C")}
            for info in connectors_info
        ]

        dummy_connectors = [
            ConnectorFactory.create(0, 0, t['name'], t['pin_names'], t['side'])
            for t in temp_connectors
        ]
        auto_distribute_connectors(self._rect.width(), self._rect.height(), dummy_connectors, spacing=10)
        
        for i, temp_info in enumerate(temp_connectors):
            side = dummy_connectors[i].side if hasattr(dummy_connectors[i], 'side') else temp_info['side']
            connector = ConnectorFactory.create(0, 0, temp_info['name'], temp_info['pin_names'], side,
                                              color=temp_info['color'])
            
            # CRITICAL: تمام attributes رو اول set کن
            connector.db_id = temp_info['id']
            connector.pin_names = list(temp_info['pin_names'])
            connector.side = side
            connector._body_color = QColor(temp_info['color'])
            connector.body_margin = MID_MARGIN  # FIXED: این رو اول set کن
            
            # Add to scene and set parent
            self.scene().addItem(connector)
            connector.setParentItem(self)
            self.connectors.append(connector)
            
            # Calculate position
            width = connector.boundingRect().width()
            height = connector.boundingRect().height()
            if side == 'top':
                x = self._rect.left() + (self._rect.width() - width) / 2
                y = self._rect.top() - height - MID_MARGIN
            elif side == 'bottom':
                x = self._rect.left() + (self._rect.width() - width) / 2
                y = self._rect.bottom() + MID_MARGIN
            elif side == 'left':
                x = self._rect.left() - width - MID_MARGIN
                y = self._rect.top() + (self._rect.height() - height) / 2
            else:  # right
                x = self._rect.right() + MID_MARGIN
                y = self._rect.top() + (self._rect.height() - height) / 2
            
            connector.setPos(x, y)
            
            # Add pins to scene
            connector.addPinsToScene(self.scene(), None)
            
            # Update pin registry
            for pin_idx, pin_name in enumerate(connector.pin_names):
                pin_center_local_to_connector = connector.get_pin_offset(pin_idx)
                pin_center_local_to_module = connector.pos() + pin_center_local_to_connector
                pin_uid = build_pin_uid(self._name, connector.name, pin_name)
                
                self.pins[pin_uid] = {
                    "center_local": pin_center_local_to_module, 
                    "side": side,
                    "order_idx": pin_idx
                }
                
                if hasattr(self.scene(), "_pin_registry"):
                    pin_item = connector.pin_items.get(pin_uid)
                    if pin_item:
                        pin_item.order_idx = pin_idx
                        pin_item.side = side
                        self.scene()._pin_registry[pin_uid] = pin_item
        
        # Update connectors layout
        self.update_connectors()
        
        # Calculate and apply proper minimum size
        self.update_minimum_size()
        
        # Check if current size is smaller than minimum required
        min_width, min_height = self.calculate_minimum_size()
        current_width = self._rect.width()
        current_height = self._rect.height()
        
        # Expand if necessary to accommodate connectors
        if current_width < min_width or current_height < min_height:
            new_width = max(current_width, min_width)
            new_height = max(current_height, min_height)
            
            # Keep the module centered when expanding
            center = self._rect.center()
            self._rect = QRectF(
                center.x() - new_width/2,
                center.y() - new_height/2, 
                new_width,
                new_height
            )
            self.updatePath()
        
        # Enable side drag for all connectors after final sizing
        for connector in self.connectors:
            if not hasattr(connector, 'body_margin'):
                connector.body_margin = MID_MARGIN
            
            try:
                enable_side_drag(connector, connector.side, self._rect, 
                                margin=connector.body_margin,
                                corner_offset=self._radius)
            except Exception as e:
                print(f"Warning: Could not enable side drag for connector {connector.name}: {e}")

    def update_connectors(self, spacing=14):
        """UPDATED: Update connector positions with immediate observer notification"""
        by_side = {'top': [], 'right': [], 'bottom': [], 'left': []}
        for connector in self.connectors:
            side = getattr(connector, 'side', 'top')
            by_side[side].append(connector)
        
        for side, connectors in by_side.items():
            layout_side(connectors, side, self._rect, spacing)
        
        # Update minimum size constraints after layout
        self.update_minimum_size()
        
        # آپدیت pin registry
        if self.scene() and hasattr(self.scene(), "_pin_registry"):
            for connector in self.connectors:
                if not hasattr(connector, 'body_margin'):
                    connector.body_margin = MID_MARGIN
                
                for pin_idx, pin_name in enumerate(connector.pin_names):
                    pin_uid = build_pin_uid(self._name, connector.name, pin_name)
                    if pin_uid in self.pins:
                        pin_center_local_to_connector = connector.get_pin_offset(pin_idx)
                        self.pins[pin_uid]["center_local"] = connector.pos() + pin_center_local_to_connector
                        self.pins[pin_uid]["side"] = getattr(connector, 'side', 'top')

    def updatePath(self):
        """UPDATED: Update path and notify observers immediately"""
        path = QPainterPath()
        path.addRoundedRect(self._rect, self._radius, self._radius)
        self.setPath(path)
        
        # آپدیت کانکتورها
        self.update_connectors()
        
        # Update minimum size after path update
        self.update_minimum_size()
        
        # Enable side drag for connectors
        for connector in self.connectors:
            if not hasattr(connector, 'body_margin'):
                connector.body_margin = MID_MARGIN
            if not hasattr(connector, 'side'):
                connector.side = 'top'
            
            try:
                enable_side_drag(connector, connector.side, self._rect, 
                                margin=connector.body_margin,
                                corner_offset=self._radius)
            except Exception as e:
                print(f"Warning: Could not enable side drag for connector {connector.name}: {e}")
        
        # آپدیت فوری observers
        self._notify_observers_of_resize()

    def hoverMoveEvent(self, event):
        if getattr(self, "_read_only", False):
            self.setCursor(Qt.ArrowCursor)
            return

        edge = self._detect_edge(event.pos())
        cursor_map = {
            'l': Qt.SizeHorCursor, 'r': Qt.SizeHorCursor, 't': Qt.SizeVerCursor, 'b': Qt.SizeVerCursor,
            'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor, 'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor,
            None: Qt.ArrowCursor
        }
        self.setCursor(cursor_map.get(edge, Qt.ArrowCursor))
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        if getattr(self, "_read_only", False):
            self.setCursor(Qt.ArrowCursor)
            return

        edge = self._detect_edge(event.pos())
        if event.button() == Qt.LeftButton and edge:
            self.resizing = True
            self.resize_edge = edge
            self._last_pos = event.pos()
            cursor_map = {
                'l': Qt.SizeHorCursor, 'r': Qt.SizeHorCursor, 't': Qt.SizeVerCursor, 'b': Qt.SizeVerCursor,
                'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor, 'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor
            }
            self.setCursor(cursor_map.get(edge, Qt.SizeAllCursor))
            event.accept()
        else:
            self.setCursor(Qt.ClosedHandCursor)
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, "_read_only", False):
            self.setCursor(Qt.ArrowCursor)
            return

        """Enhanced mouse move event with real-time connection updates"""
        if self.resizing and self.resize_edge:
            diff = event.pos() - self._last_pos
            new_rect = QRectF(self._rect)
            
            # Calculate current minimum size based on connector layout
            actual_min_width, actual_min_height = self.calculate_minimum_size()
            
            # Apply resize constraints
            if 'l' in self.resize_edge:
                new_left = min(new_rect.right() - actual_min_width, new_rect.left() + diff.x())
                new_rect.setLeft(new_left)
            if 'r' in self.resize_edge:
                new_right = max(new_rect.left() + actual_min_width, new_rect.right() + diff.x())
                new_rect.setRight(new_right)
            if 't' in self.resize_edge:
                new_top = min(new_rect.bottom() - actual_min_height, new_rect.top() + diff.y())
                new_rect.setTop(new_top)
            if 'b' in self.resize_edge:
                new_bottom = max(new_rect.top() + actual_min_height, new_rect.bottom() + diff.y())
                new_rect.setBottom(new_bottom)
            
            # Only update if the new rect is valid
            if new_rect.width() >= actual_min_width and new_rect.height() >= actual_min_height:
                self._rect = new_rect
                self.updatePath()
                
                # آپدیت فوری کانکتورها
                self._update_connectors_during_resize()
                
                # آپدیت فوری connections - این خط مهمه
                self._notify_observers_of_resize()
                
                self._last_pos = event.pos()
                    
            event.accept()
        else:
            self.setCursor(Qt.ClosedHandCursor)
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, "_read_only", False):
            self.setCursor(Qt.ArrowCursor)
            return

        if self.resizing:
            self.resizing = False
            self.resize_edge = None
            
            # آپدیت نهایی فقط کانکتورها
            self.updatePath()
            self._update_connectors_during_resize()
            
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            
            # آپدیت کلی scene بعد از کمی تاخیر
            scene = self.scene()
            if hasattr(scene, "update_all_statistics"):
                QTimer.singleShot(50, scene.update_all_statistics)
                
        else:
            self.setCursor(Qt.ArrowCursor)
            super().mouseReleaseEvent(event)

    def _update_pin_registry(self):
        """آپدیت registry pin ها بعد از resize"""
        scene = self.scene()
        if not scene or not hasattr(scene, "_pin_registry"):
            return
            
        for connector in self.connectors:
            for pin_idx, pin_name in enumerate(connector.pin_names):
                pin_uid = build_pin_uid(self._name, connector.name, pin_name)
                
                # محاسبه موقعیت جدید pin نسبت به connector
                pin_center_local_to_connector = connector.get_pin_offset(pin_idx)
                pin_center_local_to_module = connector.pos() + pin_center_local_to_connector
                
                # آپدیت registry ماژول
                if pin_uid in self.pins:
                    self.pins[pin_uid]["center_local"] = pin_center_local_to_module
                
                # آپدیت scene registry - فقط موقعیت relative به connector
                if pin_uid in scene._pin_registry:
                    pin_item = scene._pin_registry[pin_uid]
                    if pin_item and hasattr(pin_item, 'setPos'):
                        # موقعیت pin باید relative به connector باشد نه scene
                        pin_item.setPos(pin_center_local_to_connector)

    def _detect_edge(self, pos):
        rect = self._rect
        margin = self.margin
        corner_margin = margin * 1.5
        on_left = abs(pos.x() - rect.left()) < margin
        on_right = abs(pos.x() - rect.right()) < margin
        on_top = abs(pos.y() - rect.top()) < margin
        on_bottom = abs(pos.y() - rect.bottom()) < margin
        if abs(pos.x()-rect.left()) < corner_margin and abs(pos.y()-rect.top()) < corner_margin:
            return 'tl'
        if abs(pos.x()-rect.right()) < corner_margin and abs(pos.y()-rect.top()) < corner_margin:
            return 'tr'
        if abs(pos.x()-rect.left()) < corner_margin and abs(pos.y()-rect.bottom()) < corner_margin:
            return 'bl'
        if abs(pos.x()-rect.right()) < corner_margin and abs(pos.y()-rect.bottom()) < corner_margin:
            return 'br'
        if on_left: return 'l'
        if on_right: return 'r'
        if on_top: return 't'
        if on_bottom: return 'b'
        return None

    def boundingRect(self):
        base_rect = super().boundingRect()
        return base_rect.adjusted(-CONNECTION_MARGIN, -CONNECTION_MARGIN, +CONNECTION_MARGIN, +CONNECTION_MARGIN)

    def paint(self, painter, option, widget=None):
        """Custom painting of module with rounded rect, title, and optional image."""
        painter.setRenderHint(QPainter.Antialiasing)
        base_color = QColor(self._color)
        if self.isSelected():
            painter.setBrush(QBrush(self._selected_gradient))
            painter.setPen(QPen(base_color.lighter(140), 2))
        elif option.state & QStyle.State_MouseOver:
            painter.setBrush(QBrush(self._hover_gradient))
            painter.setPen(QPen(base_color.lighter(120), 2))
        else:
            painter.setBrush(QBrush(self._base_gradient))
            painter.setPen(QPen(base_color.darker(150), 1))
        painter.drawPath(self.path())
        painter.setPen(QPen(QColor("#e0e5f2"), 2))
        title_y = self._rect.top() + self.title_height
        painter.drawLine(QPointF(self._rect.left() + 2, title_y), QPointF(self._rect.right() - 2, title_y))
        pixmap = None
        if self._image_path:
            if isinstance(self._image_path, str):
                pixmap = QPixmap(self._image_path)
            elif isinstance(self._image_path, QPixmap):
                pixmap = self._image_path
            elif isinstance(self._image_path, (bytes, bytearray)):
                pixmap = QPixmap()
                pixmap.loadFromData(self._image_path)
        if pixmap and not pixmap.isNull():
            image_rect = QRectF(
                self._rect.left() + self.img_margin,
                self._rect.top() + self.title_height + self.img_margin,
                self._rect.width() - 2 * self.img_margin,
                self._rect.height() - self.title_height - 2 * self.img_margin
            )
            scaled = pixmap.scaled(int(image_rect.width()), int(image_rect.height()),
                                  Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(int(image_rect.left() + (image_rect.width() - scaled.width()) / 2),
                              int(image_rect.top() + (image_rect.height() - scaled.height()) / 2), scaled)
        painter.setPen(QColor("#e0e5f2"))
        font = QFont("Roboto Mono", 14, QFont.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(self._rect.left(), self._rect.top(),
                               self._rect.width(), self.title_height), Qt.AlignCenter, self._name)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            # آپدیت observers فقط در صورت تغییر واقعی موقعیت
            return value
        elif change == QGraphicsItem.ItemPositionHasChanged:
            # آپدیت connections بعد از حرکت کامل
            for observer in getattr(self, "observers", []):
                if hasattr(observer, 'update_path'):
                    observer.update_path()
        return super().itemChange(change, value)
    
    def force_resize(self, width, height):
        """UPDATED: Force resize with real-time connection updates"""
        # Calculate minimum size based on current connectors
        min_width, min_height = self.calculate_minimum_size()
        
        # Ensure new size respects minimums
        final_width = max(width, min_width)
        final_height = max(height, min_height)
        
        # Apply new size
        self.prepareGeometryChange()
        self._rect.setWidth(final_width)
        self._rect.setHeight(final_height)
        
        # آپدیت کامل
        self.updatePath()
        self._update_connectors_during_resize()
        self._update_connections_during_resize()
        self._update_pin_registry()
        
        # Update minimum constraints
        self.update_minimum_size()

    
    def calculate_minimum_size(self, margin=36, spacing=14):
        """
        Calculate minimum size based on current connector layout on each side
        
        Args:
            margin: Additional margin around connectors 
            spacing: Spacing between connectors
        
        Returns:
            Tuple of (min_width, min_height)
        """
        if not self.connectors:
            return DEFAULT_MODULE_WIDTH, DEFAULT_MODULE_HEIGHT
        
        # Group connectors by side
        by_side = {'top': [], 'right': [], 'bottom': [], 'left': []}
        for connector in self.connectors:
            side = getattr(connector, 'side', 'top')
            by_side[side].append(connector)
        
        # Calculate required width for top/bottom connectors
        min_width = 0
        for side in ['top', 'bottom']:
            if by_side[side]:
                total_connector_width = sum(c.boundingRect().width() for c in by_side[side])
                gaps_width = spacing * max(0, len(by_side[side]) - 1)
                side_width = total_connector_width + gaps_width + margin
                min_width = max(min_width, side_width)
        
        # Calculate required height for left/right connectors  
        min_height = 0
        for side in ['left', 'right']:
            if by_side[side]:
                total_connector_height = sum(c.boundingRect().height() for c in by_side[side])
                gaps_height = spacing * max(0, len(by_side[side]) - 1)
                side_height = total_connector_height + gaps_height + margin
                min_height = max(min_height, side_height)
        
        # Add title height to minimum height
        min_height = max(min_height, TITLE_HEIGHT + 40)
        
        # Ensure minimums are reasonable
        min_width = max(min_width, 80)
        min_height = max(min_height, 60)
        
        return min_width, min_height

    def update_minimum_size(self):
        """Update the minimum size constraints based on current connector layout"""
        self._min_width, self._min_height = self.calculate_minimum_size()

    def _update_connectors_during_resize(self):
        """آپدیت موقعیت کانکتورها در حین resize"""
        if not self.connectors:
            return
            
        # گروه بندی کانکتورها بر اساس side
        by_side = {'top': [], 'right': [], 'bottom': [], 'left': []}
        for connector in self.connectors:
            side = getattr(connector, 'side', 'top')
            by_side[side].append(connector)
        
        # آپدیت موقعیت هر side
        for side, connectors in by_side.items():
            if connectors:
                self._reposition_connectors_for_side(connectors, side)
        
        # آپدیت pin registry محلی
        self._update_local_pin_registry()

    def _update_local_pin_registry(self):
        """آپدیت registry محلی pins بدون تغییر scene positions"""
        for connector in self.connectors:
            for pin_idx, pin_name in enumerate(connector.pin_names):
                pin_uid = build_pin_uid(self._name, connector.name, pin_name)
                
                # محاسبه موقعیت جدید pin نسبت به ماژول
                pin_center_local_to_connector = connector.get_pin_offset(pin_idx)
                pin_center_local_to_module = connector.pos() + pin_center_local_to_connector
                
                # آپدیت فقط registry ماژول
                if pin_uid in self.pins:
                    self.pins[pin_uid]["center_local"] = pin_center_local_to_module

    def _reposition_connectors_for_side(self, connectors, side):
        """تنظیم موقعیت کانکتورها برای یک side مشخص"""
        from Schematic_View_tab.shapes.connector_utils import layout_side
        layout_side(connectors, side, self._rect, spacing=14)

    def _update_connections_during_resize(self):
        pass  

    def _get_item_module_parent(self, item):
        """پیدا کردن ماژول والد یک item"""
        current = item
        while current:
            if hasattr(current, 'parentItem'):
                parent = current.parentItem()
                if parent and hasattr(parent, '_name') and parent == self:
                    return self
                current = parent
            else:
                break

    def _notify_observers_of_resize(self):
        """اطلاع رسانی به observers در حین resize"""
        for observer in getattr(self, "observers", []):
            if hasattr(observer, 'update_path'):
                observer.update_path()