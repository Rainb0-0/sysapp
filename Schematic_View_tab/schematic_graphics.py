# -----------------------------------------------------------------------------
# schematic_graphics.py - Clean version without debug output
# -----------------------------------------------------------------------------
import sys
import os
from PyQt5.QtWidgets import QGraphicsScene, QGraphicsView, QScrollArea, QVBoxLayout, QWidget
from PyQt5.QtGui import QBrush, QColor, QPainter
from PyQt5.QtCore import Qt, pyqtSignal, QRectF,QPointF

# Set up paths
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from database import get_connection, get_complete_layout
from Schematic_View_tab.shapes.module_graphics import ModuleGraphics
from Schematic_View_tab.shapes.smart_connection import SmartOrthogonalConnector
from Schematic_View_tab.shapes.connector_pin_graphics import build_pin_uid, ConnectorFactory
from Schematic_View_tab.integration_fixes import ConnectorMovementManager


# Import unified style system
from styles.style_manager import style_manager, register_widget, auto_style_widget
from styles.design_system import Colors, Typography, Spacing, BorderRadius
from styles.theme_manager import theme_manager, ThemeType

MARGIN_FOR_CONNECTION = 50

# ============================================================================
# CURRENT CALCULATION FUNCTIONS
# ============================================================================
def get_vcc_pins_with_connections():
    """Get all VCC pins that have active connections in the schema"""
    from database import get_connection, get_current_project_id
    
    current_project_id = get_current_project_id()
    if current_project_id is None:
        return []
    
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            
            query = """
    SELECT DISTINCT 
        p.id, p.name, p.current, p.connector_id, c.module_id, m.name as module_name
    FROM pins p
    JOIN connectors c ON p.connector_id = c.id
    JOIN modules m ON c.module_id = m.id
    JOIN Interfaces i ON (i.pin1_id = p.id OR i.pin2_id = p.id)
    WHERE p.project_id = %s
      AND (
        TRIM(UPPER(COALESCE(p.pin_type,''))) = 'VCC'
        OR UPPER(COALESCE(p.name,'')) LIKE '%VCC%'
        OR UPPER(COALESCE(p.name,'')) LIKE '%PWR%'
        OR UPPER(COALESCE(p.name,'')) LIKE '%POWER%'
        OR UPPER(COALESCE(p.description,'')) LIKE '%VCC%'
      )
    ORDER BY m.name, c.name, p.name
            """
            
            cursor.execute(query, (current_project_id,))
            vcc_pins = []
            
            for row in cursor.fetchall():
                pin_id, pin_name, current, connector_id, module_id, module_name = row
                vcc_pins.append({
                    'pin_id': pin_id,
                    'pin_name': pin_name,
                    'current': current or 0.0,
                    'connector_id': connector_id,
                    'module_id': module_id,
                    'module_name': module_name
                })
            
            return vcc_pins
            
    except Exception as e:
        return []


class SchematicGraphicsScene(QGraphicsScene):
    """
    Custom QGraphicsScene with integrated theme system for schematic drawings.
    """
    
    # Signal for theme changes
    theme_changed = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Initialize with current theme background
        self.apply_theme_background()
        
        # Scene state
        self._pin_registry = {}
        self._connection_edges = []
        self._last_selection = None
        self._fit_done = False
        
        # Track graphics items by their DB ID
        self.module_graphics_items = {}
        self.connector_graphics_items = {} 
        self.interface_graphics_items = {}
        
        # Labels for statistics (will be set by parent)
        self.total_mass_label = None
        self.total_power_label = None
        self.total_current_label = None
        
        self._movement_manager = ConnectorMovementManager(self)
        # Connect to theme changes
        style_manager.theme_changed.connect(self.on_theme_changed)

    def _collect_active_interface_ids(self):
        """Return interface IDs that are actually drawn in the scene."""
        ids = set()
        try:
            if hasattr(self, "interface_graphics_items") and self.interface_graphics_items:
                ids.update(self.interface_graphics_items.keys())
            # Fallback: scan known edges
            if not ids and hasattr(self, "_connection_edges"):
                for e in self._connection_edges:
                    if hasattr(e, "db_id") and e.db_id is not None:
                        ids.add(e.db_id)
            # Fallback: scan all items in the scene
            if not ids:
                for itm in getattr(self, "items", lambda: [])():
                    if hasattr(itm, "db_id") and itm.db_id is not None:
                        ids.add(itm.db_id)
            return ids
        except Exception:
            return set()

    def calculate_total_current(self):
        """Calculate total current ONLY from power/VCC-like pins whose connections are actually drawn in the scene."""
        from database import get_connection, get_current_project_id
        
        total_current = 0.0
        active_pins = 0
        
        current_project_id = get_current_project_id()
        if current_project_id is None:
            if getattr(self, "total_current_label", None):
                self.total_current_label.setText("Current: No Project")
            return 0.0, 0
        
        try:
            # Get active interface IDs from the scene
            active_interface_ids = self._collect_active_interface_ids()
            if not active_interface_ids:
                if getattr(self, "total_current_label", None):
                    self.total_current_label.setText("Current: 0.00 A")
                return 0.0, 0

            # Normalize displayed module ids
            displayed_module_ids = set(self.module_graphics_items.keys()) if hasattr(self, 'module_graphics_items') else set()
            norm_displayed = set()
            for x in displayed_module_ids:
                try:
                    norm_displayed.add(int(x))
                except Exception:
                    norm_displayed.add(x)
            displayed_module_ids = norm_displayed

            with get_connection() as conn:
                cursor = conn.cursor()

                # Main query for power-like pins
                power_pred = """
    (
        TRIM(UPPER(COALESCE(p.pin_type,''))) IN ('VCC','POWER','PWR','VDD','VOLTAGE')
        OR UPPER(COALESCE(p.name,'')) LIKE '%VCC%' OR UPPER(COALESCE(p.name,'')) LIKE '%VDD%' OR
        UPPER(COALESCE(p.name,'')) LIKE '%VIN%' OR UPPER(COALESCE(p.name,'')) LIKE '%VBAT%' OR
        UPPER(COALESCE(p.name,'')) LIKE '%V+%'  OR UPPER(COALESCE(p.name,'')) LIKE '%5V%'   OR
        UPPER(COALESCE(p.name,'')) LIKE '%3V3%' OR UPPER(COALESCE(p.name,'')) LIKE '%3.3V%' OR
        UPPER(COALESCE(p.name,'')) LIKE '%POWER%' OR UPPER(COALESCE(p.name,'')) LIKE '%PWR%'
        OR UPPER(COALESCE(c.name,'')) LIKE '%VCC%' OR UPPER(COALESCE(c.name,'')) LIKE '%VDD%' OR
        UPPER(COALESCE(c.name,'')) LIKE '%VIN%' OR UPPER(COALESCE(c.name,'')) LIKE '%VBAT%' OR
        UPPER(COALESCE(c.name,'')) LIKE '%V+%'  OR UPPER(COALESCE(c.name,'')) LIKE '%5V%'   OR
        UPPER(COALESCE(c.name,'')) LIKE '%3V3%' OR UPPER(COALESCE(c.name,'')) LIKE '%3.3V%' OR
        UPPER(COALESCE(c.name,'')) LIKE '%POWER%' OR UPPER(COALESCE(c.name,'')) LIKE '%PWR%'
        OR UPPER(COALESCE(p.description,'')) LIKE '%VCC%'
        OR COALESCE(p.current, 0.0) > 0.0
    )
    """

                placeholders = ",".join("%s" for _ in active_interface_ids)
                main_q = f"""
    SELECT DISTINCT 
        p.id, p.name, COALESCE(p.current, 0.0) as current, 
        c.module_id, m.name as module_name
    FROM pins p
    JOIN connectors c ON p.connector_id = c.id
    JOIN modules m ON c.module_id = m.id
    JOIN Interfaces i ON (i.pin1_id = p.id OR i.pin2_id = p.id)
    WHERE i.id IN ({placeholders})
    AND p.project_id = %s
    AND {power_pred}
    ORDER BY m.name, p.name
    """
                cursor.execute(main_q, tuple(active_interface_ids) + (current_project_id,))
                rows = cursor.fetchall()

                for pin_id, pin_name, current, module_id, module_name in rows:
                    # skip pins whose modules are not displayed
                    try:
                        mod_id_norm = int(module_id)
                    except Exception:
                        mod_id_norm = module_id
                    if displayed_module_ids and mod_id_norm not in displayed_module_ids:
                        continue

                    current_value = current or 0.0
                    total_current += current_value
                    active_pins += 1

                if getattr(self, "total_current_label", None):
                    self.total_current_label.setText(f"Current: {total_current:.2f} A")

                return total_current, active_pins

        except Exception:
            if getattr(self, "total_current_label", None):
                self.total_current_label.setText("Current: Error")
            return 0.0, 0
            
    def update_all_statistics(self):
        """Update all statistics: mass, display power, and current.
        
        Module power (from DB) is a standalone display value.
        The actual power is calculated as: sum of all pin powers (voltage * current)
        across all pins of displayed modules.
        """
        from database import get_connection, get_current_project_id
        
        current_project_id = get_current_project_id()
        if current_project_id is None:
            if self.total_mass_label:
                self.total_mass_label.setText("Mass: No Project")
            if self.total_power_label:
                self.total_power_label.setText("Power: No Project")
            if self.total_current_label:
                self.total_current_label.setText("Current: No Project")
            return 0.0, 0.0, 0.0
        
        try:
            # Calculate total mass (sum of module masses)
            total_mass = 0.0
            # Calculate display power (sum of module display powers)
            total_display_power = 0.0
            # Calculate actual power (sum of all pin voltages * currents)
            total_calculated_power = 0.0
            
            if hasattr(self, 'module_graphics_items'):
                with get_connection() as conn:
                    cursor = conn.cursor()
                    
                    for module_id in self.module_graphics_items.keys():
                        cursor.execute(
                            "SELECT mass, power FROM modules WHERE id = %s AND project_id = %s", 
                            (module_id, current_project_id)
                        )
                        result = cursor.fetchone()
                        if result:
                            mass, power = result
                            total_mass += mass or 0.0
                            total_display_power += power or 0.0
                        
                        # Calculate actual power from pin voltages * currents
                        cursor.execute(
                            "SELECT value, current FROM pins "
                            "WHERE connector_id IN (SELECT id FROM connectors WHERE module_id = %s) "
                            "AND project_id = %s",
                            (module_id, current_project_id)
                        )
                        for pin_val, pin_curr in cursor.fetchall():
                            v = pin_val or 0.0
                            c = pin_curr or 0.0
                            total_calculated_power += v * c
            
            # Calculate current (from VCC pins)
            total_current, active_pins = self.calculate_total_current()
            
            # Update all labels
            if self.total_mass_label:
                self.total_mass_label.setText(f"Mass: {total_mass:.2f} kg")
            
            if self.total_power_label:
                self.total_power_label.setText(f"Power: {total_calculated_power:.2f} W (display: {total_display_power:.2f})")
            
            if self.total_current_label:
                self.total_current_label.setText(f"Current: {total_current:.2f} A")
            
            return total_mass, total_calculated_power, total_current
            
        except Exception:
            return 0.0, 0.0, 0.0

    def apply_theme_background(self):
        """Apply theme-based background color"""
        bg_color = QColor(theme_manager.get_color('primary_dark'))
        self.setBackgroundBrush(QBrush(bg_color))

    def on_theme_changed(self, theme_name):
        """Handle theme change"""
        self.apply_theme_background()
        self.refresh_connection_colors()
        self.theme_changed.emit(theme_name)

    def refresh_connection_colors(self):
        """Refresh connection colors based on current theme"""
        accent_color = QColor(theme_manager.get_color('accent'))
        
        for edge in self._connection_edges:
            if hasattr(edge, 'update_color'):
                edge.update_color(accent_color)
            elif hasattr(edge, 'setPen'):
                pen = edge.pen()
                pen.setColor(accent_color)
                edge.setPen(pen)

    def apply_module_positions(self, positions):
        """
        Apply positions and sizes to modules from saved data.
        positions: Dict {module_id: (x, y, width, height)} or {module_id: (x, y)}
        """
        for mod_id, data in positions.items():
            if mod_id in self.module_graphics_items:
                item = self.module_graphics_items[mod_id]
                
                if len(data) == 4:  # (x, y, width, height)
                    x, y, width, height = data
                    item.setPos(x, y)
                    # FIXED: Update size properly - preserve original rect position
                    old_rect = item._rect
                    item._rect = QRectF(old_rect.x(), old_rect.y(), width, height)
                    item.updatePath()
                elif len(data) == 2:  # Legacy format (x, y)
                    x, y = data
                    item.setPos(x, y)
        
        self.update()  # Refresh scene
        
    def update_display_from_selection(self, selection_dict):
        """
        Updates the scene based on the selected modules, connectors, and pins.
        Now automatically loads saved positions.
        """
        from database import get_connection, get_current_project_id, get_complete_layout
        
        # Check if project is selected
        current_project_id = get_current_project_id()
        if current_project_id is None:
            # Clear scene and show no project message
            self.clear()
            self.module_graphics_items.clear()
            self.connector_graphics_items.clear()
            self.interface_graphics_items.clear()
            self._pin_registry = {}
            self._connection_edges = []
            if self.total_mass_label:
                self.total_mass_label.setText("Mass: No Project")
            if self.total_power_label:
                self.total_power_label.setText("Power: No Project")
            if self.total_current_label:
                self.total_current_label.setText("Current: No Project")
            return

        # Store current positions before clearing
        current_positions = {}
        for mod_id, item in self.module_graphics_items.items():
            current_positions[mod_id] = item.pos()

        # Clear scene and registries
        self.clear()
        self.module_graphics_items.clear()
        self.connector_graphics_items.clear()
        self.interface_graphics_items.clear()
        self._pin_registry = {}
        self._connection_edges = []
        self._fit_done = False
        self._last_selection = selection_dict

        module_ids = selection_dict.get('modules', [])
        connector_ids = set(selection_dict.get('connectors', []))
        pin_ids = set(selection_dict.get('pins', []))

        if not module_ids:
            if self.total_mass_label:
                self.total_mass_label.setText("Mass: 0.00 kg")
            if self.total_power_label:
                self.total_power_label.setText("Power: 0.00 W")
            if self.total_current_label:
                self.total_current_label.setText("Current: 0.00 A")
            return

        # Grid layout parameters (fallback if no saved positions)
        x_start, y_start = 80, 120
        spacing_x, spacing_y = 80, 120
        n_cols = 3

        mod_items_data = []
        total_mass = 0.0
        total_power = 0.0

        # First, get saved positions from database
        saved_module_positions = {}
        saved_connector_positions = {}
        saved_interface_positions = {}
        saved_interface_points = {}
        
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                
                # Get all connector IDs for selected modules
                if module_ids:
                    placeholders = ','.join('%s' for _ in module_ids)
                    cur.execute(f"SELECT id FROM connectors WHERE module_id IN ({placeholders}) AND project_id = %s", 
                            tuple(module_ids) + (current_project_id,))
                    all_connector_ids = [row[0] for row in cur.fetchall()]
                    
                    # Get all interface IDs for the pins that will be displayed
                    module_ids_double = tuple(module_ids) + tuple(module_ids)
                    cur.execute(f"""
                        SELECT DISTINCT i.id FROM Interfaces i
                        JOIN pins p1 ON i.pin1_id = p1.id
                        JOIN pins p2 ON i.pin2_id = p2.id
                        JOIN connectors c1 ON p1.connector_id = c1.id
                        JOIN connectors c2 ON p2.connector_id = c2.id
                        WHERE c1.module_id IN ({placeholders}) AND c2.module_id IN ({placeholders})
                        AND i.project_id = %s
                    """, module_ids_double + (current_project_id,))
                    all_interface_ids = [row[0] for row in cur.fetchall()]
                else:
                    all_connector_ids = []
                    all_interface_ids = []

                # Load saved positions from database
                if module_ids or all_connector_ids or all_interface_ids:
                    saved_module_positions, saved_connector_positions, saved_interface_positions, saved_interface_points = get_complete_layout(
                        module_ids, all_connector_ids, all_interface_ids
                    )
        except Exception as e:
            print(f"Warning: Could not load saved positions: {e}")

        # Create modules
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                
                for mod_id in module_ids:
                    cur.execute("""
                        SELECT name, photo, mass, power, color, pos_x, pos_y, width, height 
                        FROM modules 
                        WHERE id=%s AND project_id=%s
                    """, (mod_id, current_project_id))
                    
                    row_mod = cur.fetchone()
                    if not row_mod: 
                        continue

                    module_name, photo, mass, power, color, db_x, db_y, db_width, db_height = row_mod
                    total_mass += float(mass or 0.0)
                    total_power += float(power or 0.0)

                    cur.execute("""
                        SELECT id, name, color, side 
                        FROM connectors 
                        WHERE module_id=%s AND project_id=%s
                    """, (mod_id, current_project_id))
                    
                    connectors_info = []
                    for conn_id, conn_name, conn_color, saved_side in cur.fetchall():
                        if connector_ids and conn_id not in connector_ids: 
                            continue
                            
                        cur.execute("""
                            SELECT id, name 
                            FROM pins 
                            WHERE connector_id=%s AND project_id=%s 
                            ORDER BY pin_number
                        """, (conn_id, current_project_id))
                        
                        pins = cur.fetchall()
                        pin_names = [pn for _, pn in pins]
                        
                        default_color = color or theme_manager.get_color('accent')
                        
                        connectors_info.append({
                            'id': conn_id,
                            'name': conn_name, 
                            'pin_names': pin_names, 
                            'side': saved_side or 'top',  # Use saved side
                            'color': conn_color or default_color
                        })

                    # Create ModuleGraphics with saved size if available
                    final_width = 300   # Default
                    final_height = 200 

                    if mod_id in saved_module_positions:
                        saved_pos = saved_module_positions[mod_id]
                        if isinstance(saved_pos, tuple) and len(saved_pos) == 4:
                            _, _, saved_width, saved_height = saved_pos
                            final_width = saved_width
                            final_height = saved_height
                    # Priority 2: Database direct values
                    elif db_width and db_height:
                        final_width = db_width
                        final_height = db_height
                    
                    default_module_color = color or theme_manager.get_color('accent')
                    mod_item = ModuleGraphics(
                        x=0, y=0, width=final_width, height=final_height, radius=15,
                        name=module_name, image_path=photo,
                        connectors_info=connectors_info, color=default_module_color
                    )
                    
                    mod_item.db_id = mod_id
                    self.module_graphics_items[mod_id] = mod_item
                    mod_items_data.append({
                        "item": mod_item, "info": connectors_info, "id": mod_id,
                        "width": mod_item.boundingRect().width(),
                        "height": mod_item.boundingRect().height(),
                        "db_pos": (db_x, db_y)
                    })

        except Exception as e:
            print(f"Error creating modules: {e}")
            return

        # Position modules using priority: saved positions > current positions > database positions > grid
        row = col = 0
        x, y = x_start, y_start
        max_row_h = 0

        for data in mod_items_data:
            mod_item = data['item']
            mod_id = data['id']
            
            # Priority order for positioning:
            if mod_id in saved_module_positions:
                # 1. Saved positions from database (highest priority)
                saved_pos = saved_module_positions[mod_id]
                if isinstance(saved_pos, tuple) and len(saved_pos) >= 2:
                    saved_x, saved_y = saved_pos[:2]
                    mod_item.setPos(saved_x, saved_y)
                else:
                    # Handle legacy format (x, y) as values
                    saved_x, saved_y = saved_pos, saved_pos  # This shouldn't happen but just in case
                    mod_item.setPos(saved_x, saved_y)
            elif mod_id in current_positions:
                # 2. Current positions from before clearing
                mod_item.setPos(current_positions[mod_id])
            elif data['db_pos'][0] is not None and data['db_pos'][1] is not None:
                # 3. Database positions
                mod_item.setPos(data['db_pos'][0], data['db_pos'][1])
            else:
                # 4. Grid layout (fallback)
                w = data['width'] + 2 * MARGIN_FOR_CONNECTION
                h = data['height'] + 2 * MARGIN_FOR_CONNECTION
                mod_item.setPos(x + MARGIN_FOR_CONNECTION, y + MARGIN_FOR_CONNECTION)
                x += w + spacing_x
                max_row_h = max(max_row_h, h)
                col += 1
                if col >= n_cols:
                    col, x = 0, x_start
                    y += max_row_h + spacing_y
                    max_row_h = 0

            self.addItem(mod_item)
            mod_item.finalize(data['info'])

        # Update statistics
        self.update_all_statistics()

        # Apply saved connector positions INCLUDING sides
        if saved_connector_positions:
            for conn_id, item in self.connector_graphics_items.items():
                if conn_id in saved_connector_positions:
                    saved_x, saved_y, saved_width, saved_height, saved_side = saved_connector_positions[conn_id]
                    
                    # Apply saved side if different from current
                    if hasattr(item, 'side') and item.side != saved_side:
                        # Need to recreate connector with correct side
                        parent = item.parentItem()
                        if parent:
                            # Remove old connector from parent's list
                            if hasattr(parent, 'connectors') and item in parent.connectors:
                                parent.connectors.remove(item)
                            
                            # Create new connector with saved side
                            new_connector = ConnectorFactory.create(
                                0, 0, item.name, item.pin_names, saved_side,
                                color=getattr(item, 'color', '#F8913C')
                            )
                            new_connector.db_id = conn_id  # Keep same DB ID
                            new_connector.side = saved_side
                            new_connector.setParentItem(parent)
                            parent.connectors.append(new_connector)
                            
                            # Remove old connector
                            item.setParentItem(None)
                            if item.scene():
                                item.scene().removeItem(item)
                            
                            # Update registry
                            self.connector_graphics_items[conn_id] = new_connector
                            item = new_connector
                            
                            # Add pins to scene
                            item.addPinsToScene(self.scene, conn_id)
                    
                    # Apply saved position (relative to parent)
                    parent = item.parentItem()
                    if parent:
                        absolute_pos = parent.pos() + QPointF(saved_x, saved_y)
                        item.setPos(absolute_pos)
                    else:
                        item.setPos(saved_x, saved_y)

        # Fetch and create interfaces
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                
                cur.execute("""
                    SELECT I.id, p1.id, p1.name, c1.name, m1.name, p2.id, p2.name, c2.name, m2.name, I.color
                    FROM Interfaces I
                    JOIN pins p1 ON I.pin1_id = p1.id 
                    JOIN connectors c1 ON p1.connector_id = c1.id 
                    JOIN modules m1 ON c1.module_id = m1.id
                    JOIN pins p2 ON I.pin2_id = p2.id 
                    JOIN connectors c2 ON p2.connector_id = c2.id 
                    JOIN modules m2 ON c2.module_id = m2.id
                    WHERE I.project_id = %s
                """, (current_project_id,))
                
                rows = cur.fetchall()

        except Exception as e:
            print(f"Error fetching interfaces: {e}")
            rows = []

        connection_pairs = []
        default_connection_color = theme_manager.get_color('accent')
        
        for interface_id, p1_id, p1_name, c1_name, m1_name, p2_id, p2_name, c2_name, m2_name, color in rows:
            if p1_id in pin_ids and p2_id in pin_ids:
                uid1 = build_pin_uid(m1_name, c1_name, p1_name)
                uid2 = build_pin_uid(m2_name, c2_name, p2_name)
                connection_color = color or default_connection_color
                connection_pairs.append((uid1, uid2, connection_color, interface_id))

        # Create smart orthogonal connectors
        self._connection_edges = []
        obstacles = list(self.module_graphics_items.values())
        accent = QColor(theme_manager.get_color('accent'))

        for uid1, uid2, color_str, interface_id in connection_pairs:
            pin1 = self._pin_registry.get(uid1)
            pin2 = self._pin_registry.get(uid2)
            if pin1 and pin2:
                conn_edge = SmartOrthogonalConnector(
                    scene=self,
                    start_item=pin1,
                    end_item=pin2,
                    obstacles=list(self.module_graphics_items.values()),
                    lead=20,
                    margin=20,
                    line_width=3,
                    color=QColor(color_str) if color_str else accent
                )
                conn_edge.db_id = interface_id
                self.interface_graphics_items[interface_id] = conn_edge
                self._connection_edges.append(conn_edge)
                
                # Apply saved interface positions
                if interface_id in saved_interface_positions:
                    saved_x, saved_y, saved_rotation = saved_interface_positions[interface_id]
                    if hasattr(conn_edge, 'setPos'):
                        conn_edge.setPos(saved_x, saved_y)
                    if hasattr(conn_edge, 'setRotation'):
                        conn_edge.setRotation(saved_rotation)
                
                # Apply saved routing points
                if interface_id in saved_interface_points:
                    points = saved_interface_points[interface_id]
                    if points and hasattr(conn_edge, 'apply_routing_points'):
                        conn_edge._manual_override = True
                        conn_edge.apply_routing_points(points)
                    
        # Recalculate statistics after connections
        self.update_all_statistics()
        
        # Auto-fit scene
        scene_rect = self.itemsBoundingRect()
        if not scene_rect.isNull():
            scene_rect = scene_rect.adjusted(-50, -50, 50, 50)
            for view in self.views():
                view.fitInView(scene_rect, Qt.KeepAspectRatio)
        self._fit_done = True
        self.setSceneRect(-5000, -5000, 10000, 10000)

    def refresh_connections_and_pins(self):
        """Updates pin order and connections without changing module positions."""
        # Update connector pins' graphics and registry
        for item in self.items():
            if hasattr(item, "update_pins_after_reorder"):
                item.update_pins_after_reorder()

        # Remove old connection edges
        for edge in self._connection_edges:
            if hasattr(edge, 'update_path'):
                edge.update_path()
        self.update_all_statistics()

        self._connection_edges = []

        # Fetch new connections from DB
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    p1.id AS pin1_id, p1.name AS pin1_name, c1.name AS conn1_name, m1.name AS mod1_name,
                    p2.id AS pin2_id, p2.name AS pin2_name, c2.name AS conn2_name, m2.name AS mod2_name,
                    I.color AS conn_color
                FROM Interfaces I
                JOIN pins p1 ON I.pin1_id = p1.id
                JOIN connectors c1 ON p1.connector_id = c1.id
                JOIN modules m1 ON c1.module_id = m1.id
                JOIN pins p2 ON I.pin2_id = p2.id
                JOIN connectors c2 ON p2.connector_id = c2.id
                JOIN modules m2 ON c2.module_id = m2.id
            """)
            rows = cur.fetchall()

        connection_pairs = []
        default_color = theme_manager.get_color('accent')
        
        for row in rows:
            pin1_id, pin1_name, conn1_name, mod1_name, pin2_id, pin2_name, conn2_name, mod2_name, conn_color = row
            uid1 = build_pin_uid(mod1_name, conn1_name, pin1_name)
            uid2 = build_pin_uid(mod2_name, conn2_name, pin2_name)
            color_str = conn_color or default_color
            connection_pairs.append((uid1, uid2, color_str))

        obstacles = [item for item in self.items() if hasattr(item, "db_id")]
        for uid1, uid2, color_str in connection_pairs:
            pin1 = self._pin_registry.get(uid1)
            pin2 = self._pin_registry.get(uid2)
            if not pin1 or not pin2 or pin1.parentItem() is None or pin2.parentItem() is None:
                continue

            edge = SmartOrthogonalConnector(
                scene=self,
                start_item=pin1,
                end_item=pin2,
                obstacles=[item for item in self.items() if hasattr(item, "db_id") and hasattr(item, 'boundingRect')],
                lead=20,
                margin=20,
                line_width=3,
                color=QColor(color_str)
            )
            self._connection_edges.append(edge)
        
        # Recalculate statistics after refresh
        self.update_all_statistics()


class TransparentTreeSelector:
    """Tree selector with proper transparency and unified theme system"""
    
    def __init__(self, parent_view):
        self.parent_view = parent_view
        self.setup_tree_widget()
        style_manager.theme_changed.connect(self.apply_theme_styles)
        
    def setup_tree_widget(self):
        """Setup transparent tree widget with theme-based styling"""
        from Schematic_View_tab.schematic_tree_selector import SchematicTreeSelector
        
        self.tree_widget = SchematicTreeSelector(self.parent_view)
        self.tree_widget.setAttribute(Qt.WA_TranslucentBackground, True)
        self.tree_widget.setAutoFillBackground(False)
        self.apply_theme_styles()
        
        if hasattr(self.tree_widget, 'tree'):
            self.tree_widget.tree.setAttribute(Qt.WA_TranslucentBackground, True)
            self.tree_widget.tree.setAutoFillBackground(False)
        
        self.tree_widget.setFixedSize(280, 400)
        self.tree_widget.move(15, 15)
        self.tree_widget.hide()

    def apply_theme_styles(self, theme_name=None):
        """Apply theme-based styles to tree selector"""
        if not hasattr(self, 'tree_widget'):
            return
            
        bg_color = theme_manager.get_color('primary_medium')
        border_color = theme_manager.get_color('primary_light')
        text_color = theme_manager.get_color('text_primary')
        accent_color = theme_manager.get_color('accent')
        
        def hex_to_rgba(hex_color, alpha=25):
            color = QColor(hex_color)
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        
        tree_style = f"""
            SchematicTreeSelector {{
                background: {hex_to_rgba(bg_color, 25)};
                border: 1px solid {hex_to_rgba(border_color, 60)};
                border-radius: {BorderRadius.XLARGE};
                color: {text_color};
                font-family: {Typography.FONT_FAMILY};
            }}
            
            QTreeWidget {{
                background: transparent;
                color: {text_color};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_SMALL};
                border: none;
                outline: none;
                selection-background-color: {hex_to_rgba(accent_color, 40)};
            }}
            
            QTreeWidget::item {{
                background: {hex_to_rgba(bg_color, 10)};
                border: 1px solid {hex_to_rgba(border_color, 20)};
                border-radius: {BorderRadius.SMALL};
                padding: {Spacing.SM};
                margin: 1px;
            }}
            
            QTreeWidget::item:hover {{
                background: {hex_to_rgba(accent_color, 30)};
                border: 1px solid {hex_to_rgba(accent_color, 60)};
                color: white;
            }}
            
            QTreeWidget::item:selected {{
                background: {hex_to_rgba(accent_color, 50)};
                border: 1px solid {hex_to_rgba(accent_color, 80)};
                color: white;
                font-weight: {Typography.WEIGHT_BOLD};
            }}
            
            QCheckBox {{
                color: {text_color};
                font-family: {Typography.FONT_FAMILY};
                font-size: {Typography.SIZE_SMALL};
            }}
            
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 2px solid {border_color};
                border-radius: 3px;
                background: {hex_to_rgba(bg_color, 50)};
            }}
            
            QCheckBox::indicator:checked {{
                background: {accent_color};
                border: 2px solid {accent_color};
            }}
            
            QCheckBox::indicator:hover {{
                border: 2px solid {accent_color};
            }}
        """
        
        self.tree_widget.setStyleSheet(tree_style)
    
    def show(self):
        if hasattr(self, 'tree_widget'):
            self.tree_widget.show()
    
    def hide(self):
        if hasattr(self, 'tree_widget'):
            self.tree_widget.hide()
    
    def isVisible(self):
        if hasattr(self, 'tree_widget'):
            return self.tree_widget.isVisible()
        return False
    
    def refresh_tree(self):
        if hasattr(self, 'tree_widget') and hasattr(self.tree_widget, 'refresh_tree'):
            self.tree_widget.refresh_tree()
    
    def get_checked_ids(self):
        if hasattr(self, 'tree_widget') and hasattr(self.tree_widget, 'get_checked_ids'):
            return self.tree_widget.get_checked_ids()
        return {}
    
    @property
    def selectionChanged(self):
        if hasattr(self, 'tree_widget'):
            return self.tree_widget.selectionChanged
        return None
    
    def geometry(self):
        if hasattr(self, 'tree_widget'):
            return self.tree_widget.geometry()
        from PyQt5.QtCore import QRect
        return QRect(0, 0, 280, 400)
    
    def setGeometry(self, *args):
        if hasattr(self, 'tree_widget'):
            return self.tree_widget.setGeometry(*args)
    
    def move(self, *args):
        if hasattr(self, 'tree_widget'):
            return self.tree_widget.move(*args)
    
    def pos(self):
        if hasattr(self, 'tree_widget'):
            return self.tree_widget.pos()
        from PyQt5.QtCore import QPoint
        return QPoint(15, 15)
    
    def setPos(self, *args):
        if hasattr(self, 'tree_widget'):
            return self.tree_widget.move(*args)
    
    def size(self):
        if hasattr(self, 'tree_widget'):
            return self.tree_widget.size()
        from PyQt5.QtCore import QSize
        return QSize(280, 400)
    
    def setFixedSize(self, *args):
        if hasattr(self, 'tree_widget'):
            return self.tree_widget.setFixedSize(*args)
    
    def resize(self, *args):
        if hasattr(self, 'tree_widget'):
            return self.tree_widget.resize(*args)
    
    def __getattr__(self, name):
        if hasattr(self, 'tree_widget') and hasattr(self.tree_widget, name):
            return getattr(self.tree_widget, name)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")


class ZoomableGraphicsView(QGraphicsView):
    """
    QGraphicsView with proper transparent widget overlay support and theme integration.
    """
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        
        # Basic rendering setup
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.DontSavePainterState, True)
        self.setOptimizationFlag(QGraphicsView.DontAdjustForAntialiasing, True)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        
        # Enable transparency for the viewport
        self.viewport().setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        
        # Apply initial theme styles
        self.apply_theme_styles()
        
        # Interaction state
        self._is_panning = False
        self._last_pan_point = None
        self._user_zoomed = False
        self.viewport().setCursor(Qt.ArrowCursor)

        # Create transparent tree selector with theme support
        self.tree_selector = TransparentTreeSelector(self)

        # Connect signals
        if self.tree_selector.selectionChanged:
            self.tree_selector.selectionChanged.connect(self.scene().update_display_from_selection)
        
        # Connect to theme changes
        style_manager.theme_changed.connect(self.apply_theme_styles)

    def apply_theme_styles(self, theme_name=None):
        """Apply theme-based styles to the view"""
        view_style = f"""
            QGraphicsView {{
                border: none;
                background: transparent;
            }}
            QGraphicsView::viewport {{
                background: transparent;
            }}
        """
        self.setStyleSheet(view_style)

    def showEvent(self, event):
        """Auto-fit view when shown."""
        super().showEvent(event)
        br = self.scene().itemsBoundingRect().adjusted(-50, -50, 50, 50)
        if not br.isNull():
            self.fitInView(br, Qt.KeepAspectRatio)

    def wheelEvent(self, event):
        """Zoom in/out with mouse wheel."""
        zoom_factor = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        self.scale(zoom_factor, zoom_factor)
        self.scene()._user_zoomed = True

    def mousePressEvent(self, event):
        """Handle middle-click panning."""
        if event.button() == Qt.MiddleButton:
            self._is_panning = True
            self._last_pan_point = event.pos()
            self.viewport().setCursor(Qt.ClosedHandCursor)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle panning movement."""
        if self._is_panning and self._last_pan_point:
            delta = event.pos() - self._last_pan_point
            self._last_pan_point = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self.viewport().setCursor(Qt.ClosedHandCursor)
        else:
            self.viewport().setCursor(Qt.ArrowCursor)
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """End panning operation."""
        if event.button() == Qt.MiddleButton:
            self._is_panning = False
            self.viewport().setCursor(Qt.ArrowCursor)
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        """Handle keyboard navigation shortcuts."""
        pan_step = 50
        key_actions = {
            Qt.Key_Left: lambda: self.translate(pan_step, 0),
            Qt.Key_Right: lambda: self.translate(-pan_step, 0),
            Qt.Key_Up: lambda: self.translate(0, pan_step),
            Qt.Key_Down: lambda: self.translate(0, -pan_step),
            Qt.Key_Plus: lambda: self.scale(1.25, 1.25),
            Qt.Key_Minus: lambda: self.scale(1 / 1.25, 1 / 1.25),
        }

        if event.key() == Qt.Key_Space:
            scene_rect = self.scene().itemsBoundingRect()
            if not scene_rect.isNull():
                self.fitInView(scene_rect, Qt.KeepAspectRatio)
            event.accept()
        else:
            action = key_actions.get(event.key())
            if action:
                action()
            else:
                super().keyPressEvent(event)

    def resizeEvent(self, event):
        """Handle resize events to notify parent widget."""
        super().resizeEvent(event)
        parent_widget = self.parent()
        while parent_widget and not hasattr(parent_widget, 'update_mode_graphics_position'):
            parent_widget = parent_widget.parent()
        if parent_widget and hasattr(parent_widget, 'update_mode_graphics_position'):
            parent_widget.update_mode_graphics_position()

    def paintEvent(self, event):
        """Custom paint event to ensure transparency works"""
        painter = QPainter(self.viewport())
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(event.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.end()
        super().paintEvent(event)