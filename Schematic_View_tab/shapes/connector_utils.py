# -----------------------------------------------------------------------------
# connector_utils.py - Connector Layout and Management Utilities
# -----------------------------------------------------------------------------
from PyQt5.QtWidgets import QGraphicsItem
from PyQt5.QtCore import QTimer, QPointF
from Schematic_View_tab.shapes.connector_pin_graphics import ConnectorFactory, LEFT_MARGIN, MID_MARGIN, build_pin_uid

def suggest_module_size(connectors_info, min_width=130, min_height=90, spacing=16, padding=36):
    """
    UPDATED: Calculate optimal module size with better connector accommodation
    """
    width, height = min_width, min_height
    sides = ['top', 'right', 'bottom', 'left']
    
    if not connectors_info:
        return width, height
    
    # Create dummy connectors to test layout
    dummy_connectors = []
    for info in connectors_info:
        try:
            dummy = ConnectorFactory.create(0, 0, info['name'], info['pin_names'], 'top')
            dummy_connectors.append(dummy)
        except Exception:
            continue
    
    if not dummy_connectors:
        return width, height
    
    # Iterate until size stabilizes
    for iteration in range(6):  # Increased iterations
        # Distribute connectors
        auto_distribute_connectors(width, height, dummy_connectors, spacing)
        
        # Group by side
        by_side = {s: [] for s in sides}
        for conn in dummy_connectors:
            side = getattr(conn, 'side', 'top')
            by_side[side].append(conn)
        
        # Calculate required dimensions using the new function
        requirements = calculate_side_minimum_size(by_side, padding, spacing)
        required_width = requirements['width']
        required_height = requirements['height']
        
        # Add extra space for comfortable layout
        comfort_width = required_width + 20
        comfort_height = required_height + 20
        
        new_width = max(width, comfort_width)
        new_height = max(height, comfort_height)
        
        # Check for convergence
        if new_width == width and new_height == height:
            break
            
        width, height = new_width, new_height
    
    return width, height

def auto_distribute_connectors(module_width, module_height, connectors, spacing=10):
    """
    UPDATED: Better connector distribution with dynamic capacity calculation
    """
    if not connectors:
        return []
    
    # Calculate more accurate capacities based on actual connector sizes
    avg_width = sum(c.rect.width() for c in connectors) / len(connectors)
    avg_height = sum(c.rect.height() for c in connectors) / len(connectors)
    
    # Calculate capacities with some padding
    padding = 40
    top_capacity = max(1, int((module_width - padding) / (avg_width + spacing)))
    bottom_capacity = top_capacity
    left_capacity = max(1, int((module_height - padding) / (avg_height + spacing)))
    right_capacity = left_capacity
    
    sides = ['top', 'right', 'bottom', 'left']
    capacities = {
        'top': top_capacity,
        'right': right_capacity, 
        'bottom': bottom_capacity,
        'left': left_capacity
    }
    
    counts = {s: 0 for s in sides}
    assigned = []
    
    # Distribute connectors with load balancing
    for i, conn in enumerate(connectors):
        # Try to balance load across sides
        best_side = min(sides, key=lambda s: counts[s] / max(capacities[s], 1))
        
        conn.side = best_side
        assigned.append(conn)
        counts[best_side] += 1
        
        # If we've exceeded capacity on this side, reduce its priority
        if counts[best_side] >= capacities[best_side]:
            capacities[best_side] = max(1, capacities[best_side] // 2)
    
    return assigned

def layout_side(connectors, side, module_rect, spacing=14):
    """
    UPDATED: Layout connectors with better spacing and overflow handling
    """
    if not connectors:
        return
    
    # Calculate total space needed
    if side in ('left', 'right'):
        total_connector_size = sum(c.rect.height() for c in connectors)
        available_space = module_rect.height()
        
        # Calculate optimal spacing
        num_gaps = len(connectors) - 1
        if num_gaps > 0:
            remaining_space = available_space - total_connector_size
            optimal_spacing = max(spacing, remaining_space / num_gaps) if remaining_space > 0 else spacing
        else:
            optimal_spacing = spacing
        
        # Check if connectors fit with optimal spacing
        total_needed = total_connector_size + (optimal_spacing * num_gaps)
        if total_needed > available_space:
            # Use minimum spacing if optimal doesn't fit
            optimal_spacing = spacing
            
        # Calculate starting position
        max_width = max(c.rect.width() for c in connectors)
        if total_needed <= available_space:
            # Center the group
            start_y = module_rect.top() + (available_space - total_needed) / 2
        else:
            # Start from top and let it overflow (will trigger resize)
            start_y = module_rect.top()
        
        # Position connectors
        current_y = start_y
        for conn in connectors:
            if side == 'left':
                x = module_rect.left() - max_width - MID_MARGIN
            else:  # right
                x = module_rect.right() + MID_MARGIN
            
            # Center connector horizontally within its allocated width
            conn_x = x + (max_width - conn.rect.width()) / 2
            conn.setPos(conn_x, current_y)
            current_y += conn.rect.height() + optimal_spacing
            
    else:  # top or bottom
        total_connector_size = sum(c.rect.width() for c in connectors) 
        available_space = module_rect.width()
        
        # Calculate optimal spacing
        num_gaps = len(connectors) - 1
        if num_gaps > 0:
            remaining_space = available_space - total_connector_size
            optimal_spacing = max(spacing, remaining_space / num_gaps) if remaining_space > 0 else spacing
        else:
            optimal_spacing = spacing
            
        # Check if connectors fit
        total_needed = total_connector_size + (optimal_spacing * num_gaps)
        if total_needed > available_space:
            optimal_spacing = spacing
            
        # Calculate starting position
        max_height = max(c.rect.height() for c in connectors)
        if total_needed <= available_space:
            # Center the group
            start_x = module_rect.left() + (available_space - total_needed) / 2
        else:
            # Start from left and let it overflow
            start_x = module_rect.left()
            
        # Position connectors
        current_x = start_x
        for conn in connectors:
            if side == 'top':
                y = module_rect.top() - max_height - MID_MARGIN
            else:  # bottom
                y = module_rect.bottom() + MID_MARGIN
            
            # Center connector vertically within its allocated height
            conn_y = y + (max_height - conn.rect.height()) / 2
            conn.setPos(current_x, conn_y)
            current_x += conn.rect.width() + optimal_spacing

def enable_side_drag(connector, side, module_rect, margin=10, corner_offset=14, 
                    delay_ms=1000, edge_threshold=0.05):
    """
    Enable drag functionality for connectors with side switching
    
    Args:
        connector: Connector to enable dragging for
        side: Current side of the connector
        module_rect: Rectangle defining the module bounds
        margin: Margin from module edge
        corner_offset: Offset from corners
        delay_ms: Delay before side switching
        edge_threshold: Threshold for triggering side switch
    """
    def get_bounds():
        """Get movement bounds for current side"""
        width = connector.boundingRect().width()
        height = connector.boundingRect().height()
        if side in ('top', 'bottom'):
            return (module_rect.left() + corner_offset, module_rect.right() - width - corner_offset)
        else:
            return (module_rect.top() + corner_offset, module_rect.bottom() - height - corner_offset)

    def get_fixed_position(constrained_axis_value):
        """Get fixed position for connector based on side"""
        width = connector.boundingRect().width()
        height = connector.boundingRect().height()
        if side == 'top':
            return constrained_axis_value, module_rect.top() - height - margin
        elif side == 'bottom':
            return constrained_axis_value, module_rect.bottom() + margin
        elif side == 'left':
            return module_rect.left() - width - margin, constrained_axis_value
        else:
            return module_rect.right() + margin, constrained_axis_value

    def get_neighbor_side(at_max):
        """Get neighbor side for side switching"""
        transitions = {
            'top': ('right' if at_max else 'left'),
            'bottom': ('right' if at_max else 'left'),
            'left': ('bottom' if at_max else 'top'),
            'right': ('bottom' if at_max else 'top')
        }
        return transitions[side]

    # Initialize drag state
    connector.setFlag(QGraphicsItem.ItemIsMovable, False)
    connector._dragging = False
    connector._start_drag_pos = QPointF()
    connector._start_conn_pos = QPointF()
    
    # Setup side switch timer
    switch_timer = QTimer()
    switch_timer.setSingleShot(True)
    try:
        switch_timer.timeout.disconnect()
    except TypeError:
        pass

    def handle_press(event):
        """Handle mouse press to start dragging"""
        connector._dragging = True
        connector._start_drag_pos = event.pos()
        connector._start_conn_pos = connector.pos()
        switch_timer.stop()
        event.accept()

    def handle_release(event):
        """Handle mouse release to end dragging"""
        connector._dragging = False
        switch_timer.stop()
        connector.ungrabMouse()
        scene = connector.scene()
        if hasattr(scene, "refresh_connections_and_pins"):
            QTimer.singleShot(0, scene.refresh_connections_and_pins)
        event.accept()

    def handle_move(event):
        """Handle mouse move during dragging"""
        if not connector._dragging:
            return
            
        current_scene_pos = event.scenePos()
        target_conn_scene_pos = current_scene_pos - connector._start_drag_pos
        parent_item = connector.parentItem()
        if not parent_item:
            return
            
        target_conn_local_pos = parent_item.mapFromScene(target_conn_scene_pos)
        axis_pos = target_conn_local_pos.x() if side in ('top', 'bottom') else target_conn_local_pos.y()
        min_pos, max_pos = get_bounds()
        range_size = max_pos - min_pos
        position_fraction = (axis_pos - min_pos) / range_size if range_size else 0.5
        
        # Check if near edge for side switching
        near_edge = position_fraction < edge_threshold or position_fraction > 1 - edge_threshold
        if near_edge:
            at_max_edge = position_fraction > 0.5
            if not switch_timer.isActive():
                try:
                    switch_timer.timeout.disconnect()
                except TypeError:
                    pass
                switch_timer.timeout.connect(perform_side_switch)
                switch_timer.start(delay_ms)
        else:
            switch_timer.stop()
        
        # Constrain movement and update position
        constrained_pos = max(min_pos, min(axis_pos, max_pos))
        new_conn_x, new_conn_y = get_fixed_position(constrained_pos)
        connector.setPos(new_conn_x, new_conn_y)
        
        # آپدیت فوری کانکشن ها در حین حرکت
        scene = connector.scene()
        if hasattr(scene, '_connection_edges'):
            for edge in scene._connection_edges:
                if hasattr(edge, 'update_path'):
                    edge.update_path()
        
        event.accept()

    def perform_side_switch():
        """Perform side switching when at edge"""
        parent = connector.parentItem()
        if not parent:
            return
            
        current_side = connector.side
        at_max_edge = (connector.x() - get_bounds()[0]) / (get_bounds()[1] - get_bounds()[0]) > 0.5 \
                      if current_side in ('top', 'bottom') and (get_bounds()[1] - get_bounds()[0]) != 0 else \
                      (connector.y() - get_bounds()[0]) / (get_bounds()[1] - get_bounds()[0]) > 0.5 \
                      if current_side in ('left', 'right') and (get_bounds()[1] - get_bounds()[0]) != 0 else 0.5
        
        new_side = get_neighbor_side(at_max_edge)
        old_connector_local_pos = connector.pos()
        
        # Create new connector for the new side
        new_connector = ConnectorFactory.create(
            0, 0, connector.name, connector.pin_names, new_side,
            color=getattr(connector, '_body_color', connector.color)
        )
        new_connector.side = new_side
        new_connector.body_margin = connector.body_margin
        new_connector._body_color = getattr(connector, '_body_color', connector.color)
        
        # Update parent's connector list
        try:
            index = parent.connectors.index(connector)
            parent.connectors[index] = new_connector
        except ValueError:
            parent.connectors.append(new_connector)
        
        new_connector.setParentItem(parent)
        new_connector.addPinsToScene(parent.scene(), None)
        
        # Clean up old connector's pins from scene registry
        scene = parent.scene()
        if hasattr(scene, "_pin_registry"):
            pins_to_remove_uids = [
                pin_uid for pin_uid in list(scene._pin_registry.keys())
                if f"/{connector.name}/" in pin_uid and parent._name in pin_uid
            ]
            for pin_uid in pins_to_remove_uids:
                pin_item = scene._pin_registry.get(pin_uid)
                if pin_item and pin_item.scene():
                    scene.removeItem(pin_item)
                del scene._pin_registry[pin_uid]
        
        # Update parent's pin registry
        parent.pins = {}
        for conn_item in parent.connectors:
            for pin_idx, pin_name in enumerate(conn_item.pin_names):
                pin_center_local_to_connector = conn_item.get_pin_offset(pin_idx)
                pin_center_local_to_module = conn_item.pos() + pin_center_local_to_connector
                pin_uid = build_pin_uid(parent._name, conn_item.name, pin_name)
                parent.pins[pin_uid] = {"center_local": pin_center_local_to_module, "side": conn_item.side}
                if hasattr(scene, "_pin_registry"):
                    pin_item = conn_item.pin_items.get(pin_uid)
                    if pin_item:
                        scene._pin_registry[pin_uid] = pin_item
        
        # Calculate new position
        if new_side in ('top', 'bottom'):
            new_min_x, new_max_x = (module_rect.left() + corner_offset,
                                    module_rect.right() - new_connector.boundingRect().width() - corner_offset)
            old_pos_fraction = (old_connector_local_pos.x() - get_bounds()[0]) / (get_bounds()[1] - get_bounds()[0]) \
                if current_side in ('top', 'bottom') and (get_bounds()[1] - get_bounds()[0]) != 0 else \
                (old_connector_local_pos.y() - get_bounds()[0]) / (get_bounds()[1] - get_bounds()[0]) \
                if current_side in ('left', 'right') and (get_bounds()[1] - get_bounds()[0]) != 0 else 0.5
            target_x = new_min_x + (new_max_x - new_min_x) * old_pos_fraction
            target_x = max(new_min_x, min(target_x, new_max_x))
            target_y = (module_rect.top() - new_connector.boundingRect().height() - margin
                        if new_side == 'top' else module_rect.bottom() + margin)
        else:
            new_min_y, new_max_y = (module_rect.top() + corner_offset,
                                    module_rect.bottom() - new_connector.boundingRect().height() - corner_offset)
            old_pos_fraction = (old_connector_local_pos.x() - get_bounds()[0]) / (get_bounds()[1] - get_bounds()[0]) \
                if current_side in ('top', 'bottom') and (get_bounds()[1] - get_bounds()[0]) != 0 else \
                (old_connector_local_pos.y() - get_bounds()[0]) / (get_bounds()[1] - get_bounds()[0]) \
                if current_side in ('left', 'right') and (get_bounds()[1] - get_bounds()[0]) != 0 else 0.5
            target_y = new_min_y + (new_max_y - new_min_y) * old_pos_fraction
            target_y = max(new_min_y, min(target_y, new_max_y))
            target_x = (module_rect.left() - new_connector.boundingRect().width() - margin
                        if new_side == 'left' else module_rect.right() + margin)
        
        new_connector.setPos(target_x, target_y)
        
        # Remove old connector
        connector.setParentItem(None)
        if connector.scene():
            connector.scene().removeItem(connector)
        
        # Setup new connector for continued dragging
        new_connector._dragging = True
        new_connector.grabMouse()
        new_connector._start_drag_pos = QPointF(0, 0)
        new_connector._start_conn_pos = new_connector.pos()
        
        # Enable side drag for new connector
        enable_side_drag(new_connector, new_side, module_rect, margin=margin, 
                        corner_offset=corner_offset, delay_ms=delay_ms, edge_threshold=edge_threshold)
        
        # Refresh connections
        if hasattr(scene, "refresh_connections_and_pins"):
            scene.refresh_connections_and_pins()

    # Bind event handlers
    connector.mousePressEvent = handle_press
    connector.mouseMoveEvent = handle_move
    connector.mouseReleaseEvent = handle_release
    connector.setFlag(QGraphicsItem.ItemIsMovable, False)
    connector.setAcceptHoverEvents(True)

def get_connector_bounds(connector, module_rect, side):
    """
    Get the movement bounds for a connector on a specific side
    
    Args:
        connector: The connector object
        module_rect: Rectangle defining the module bounds
        side: Side of the module ('left', 'right', 'top', 'bottom')
    
    Returns:
        Tuple of (min_bound, max_bound)
    """
    width = connector.boundingRect().width()
    height = connector.boundingRect().height()
    corner_offset = 14
    
    if side in ('top', 'bottom'):
        return (module_rect.left() + corner_offset, 
                module_rect.right() - width - corner_offset)
    else:
        return (module_rect.top() + corner_offset, 
                module_rect.bottom() - height - corner_offset)

def calculate_connector_spacing(connectors, available_space, min_spacing=10):
    """
    Calculate optimal spacing between connectors
    
    Args:
        connectors: List of connectors
        available_space: Available space for layout
        min_spacing: Minimum spacing between connectors
    
    Returns:
        Optimal spacing value
    """
    if not connectors:
        return min_spacing
    
    total_connector_size = sum(c.rect.width() for c in connectors)
    remaining_space = available_space - total_connector_size
    num_gaps = len(connectors) - 1
    
    if num_gaps <= 0:
        return min_spacing
    
    calculated_spacing = remaining_space / num_gaps
    return max(min_spacing, calculated_spacing)

def validate_connector_layout(connectors, module_rect, margin=20):
    """
    UPDATED: Enhanced validation with minimum size requirements
    """
    if not connectors:
        return True
    
    # Group by side for validation
    by_side = {'top': [], 'right': [], 'bottom': [], 'left': []}
    for connector in connectors:
        side = getattr(connector, 'side', 'top')
        by_side[side].append(connector)
    
    # Check if module is large enough for each side
    requirements = calculate_side_minimum_size(by_side, margin * 2, 14)
    
    if (module_rect.width() < requirements['width'] or 
        module_rect.height() < requirements['height']):
        return False
    
    # Check individual connector positions
    for connector in connectors:
        connector_rect = connector.boundingRect()
        connector_pos = connector.pos()
        
        # Check if connector is reasonably positioned relative to module
        if (abs(connector_pos.x() - module_rect.center().x()) > module_rect.width() + connector_rect.width() or
            abs(connector_pos.y() - module_rect.center().y()) > module_rect.height() + connector_rect.height()):
            return False
    
    # Check for overlaps
    for i, conn1 in enumerate(connectors):
        for conn2 in connectors[i+1:]:
            rect1 = conn1.boundingRect().translated(conn1.pos())
            rect2 = conn2.boundingRect().translated(conn2.pos())
            if rect1.intersects(rect2):
                return False
    
    return True

def calculate_side_minimum_size(connectors_by_side, margin=36, spacing=14, title_height=28):
    """
    Calculate minimum width and height required for connectors on each side
    
    Args:
        connectors_by_side: Dict with keys 'top', 'right', 'bottom', 'left' and connector lists
        margin: Additional margin around connectors
        spacing: Spacing between connectors
        title_height: Height reserved for module title
        
    Returns:
        Dict with minimum dimensions: {'width': min_width, 'height': min_height}
    """
    min_width = 0
    min_height = title_height + 40  # Base height with title + some padding
    
    # Calculate width requirements from top/bottom connectors
    for side in ['top', 'bottom']:
        if connectors_by_side.get(side):
            connectors = connectors_by_side[side]
            total_width = sum(c.rect.width() for c in connectors)
            gaps_width = spacing * max(0, len(connectors) - 1)
            required_width = total_width + gaps_width + margin
            min_width = max(min_width, required_width)
    
    # Calculate height requirements from left/right connectors  
    for side in ['left', 'right']:
        if connectors_by_side.get(side):
            connectors = connectors_by_side[side]
            total_height = sum(c.rect.height() for c in connectors)
            gaps_height = spacing * max(0, len(connectors) - 1)
            required_height = total_height + gaps_height + margin + title_height
            min_height = max(min_height, required_height)
    
    # Ensure reasonable minimums
    min_width = max(min_width, 80)
    min_height = max(min_height, 60)
    
    return {'width': min_width, 'height': min_height}