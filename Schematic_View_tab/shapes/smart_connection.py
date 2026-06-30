# -----------------------------------------------------------------------------
# enhanced_smart_connector.py - FIXED VERSION with Better Movement Logic
# -----------------------------------------------------------------------------
from PyQt5.QtWidgets import QGraphicsLineItem, QGraphicsEllipseItem, QGraphicsItem
from PyQt5.QtGui import QPen, QColor, QCursor, QPixmap, QPainter
from PyQt5.QtCore import QPointF, QLineF, Qt, QRectF
import heapq, math, sip, json

# ------------------------------ FIXED CONSTANTS ------------------------------------
DEFAULT_COLOR = QColor(0, 140, 255)
DEFAULT_LINE_WIDTH = 3
DEFAULT_POINT_RADIUS = 5
DEFAULT_MARGIN = 20
DEFAULT_LEAD_LENGTH = 24
ORTHO_EPS = 1.5

# IMPROVED: Much higher thresholds for stable routing
MOVEMENT_THRESHOLD = 5.0  # pixels - minimum movement to trigger consideration
MANUAL_PERSISTENCE_THRESHOLD = 50.0  # pixels - much higher threshold for losing manual route
DRAG_START_THRESHOLD = 8.0  # pixels - minimum drag distance to enter manual mode

# Visual layers
Z_SEGMENTS = 80
Z_MARKERS = 90

SIDE_VECTORS = {
    'right': QPointF(+1, 0),
    'left':  QPointF(-1, 0),
    'top':   QPointF(0, -1),
    'bottom': QPointF(0, +1),
}

def _make_double_arrow_cursor(color=QColor(255,255,255), size=22):
    """Create double arrow cursor for dragging"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(color)

    s = float(size)
    head = 0.32 * s
    t = 0.28 * s
    half = 0.5 * s

    # Horizontal bar
    p.drawRect(int(-half + head + half), int(-t/2 + half), int(s - 2*head), int(t))
    # Left arrow
    pl = [
        (-half + half, 0 + half),
        (-half + head + half, -t/2 + half),
        (-half + head + half, t/2 + half),
    ]
    p.drawPolygon(*[QPointF(x, y) for x, y in pl])
    # Right arrow  
    pr = [
        (+half + half, 0 + half),
        (+half - head + half, -t/2 + half),
        (+half - head + half, t/2 + half),
    ]
    p.drawPolygon(*[QPointF(x, y) for x, y in pr])
    p.end()
    return QCursor(pm, pm.width()//2, pm.height()//2)

# ============================== Grid Router (unchanged) ==================================
class _GridRouter:
    """Enhanced grid router with better obstacle handling"""
    def __init__(self, rects, margin):
        self.rects = [QRectF(r) for r in rects]
        self.margin = margin

    def _inside_any_rect(self, p: QPointF) -> bool:
        for r in self.rects:
            if r.contains(p):
                return True
        return False

    def _segment_hits_any_rect(self, a: QPointF, b: QPointF) -> bool:
        x1, y1, x2, y2 = a.x(), a.y(), b.x(), b.y()
        if abs(y1 - y2) <= 1e-9:  # horizontal
            y = y1
            if x1 > x2: x1, x2 = x2, x1
            for r in self.rects:
                if r.top() - 1e-9 <= y <= r.bottom() + 1e-9:
                    if not (x2 <= r.left() - 1e-9 or x1 >= r.right() + 1e-9):
                        return True
            return False
        elif abs(x1 - x2) <= 1e-9:  # vertical
            x = x1
            if y1 > y2: y1, y2 = y2, y1
            for r in self.rects:
                if r.left() - 1e-9 <= x <= r.right() + 1e-9:
                    if not (y2 <= r.top() - 1e-9 or y1 >= r.bottom() + 1e-9):
                        return True
            return False
        return True

    def _unique_sorted(self, vals):
        return sorted(set(round(v, 3) for v in vals))

    def _build_coords(self, start_pt: QPointF, end_pt: QPointF):
        xs = [start_pt.x(), end_pt.x()]
        ys = [start_pt.y(), end_pt.y()]
        for r in self.rects:
            xs.extend([r.left(), r.right()])
            ys.extend([r.top(), r.bottom()])
        return self._unique_sorted(xs), self._unique_sorted(ys)

    def _build_graph(self, xs, ys):
        def coord(ix, iy): return QPointF(xs[ix], ys[iy])

        nodes = []
        blocked = set()
        for ix in range(len(xs)):
            for iy in range(len(ys)):
                p = coord(ix, iy)
                if self._inside_any_rect(p):
                    blocked.add((ix, iy))
                else:
                    nodes.append((ix, iy))

        adj = {(ix, iy): [] for (ix, iy) in nodes}
        
        # Horizontal neighbors
        for iy in range(len(ys)):
            prev_ix = None
            for ix in range(len(xs)):
                if (ix, iy) in blocked:
                    prev_ix = None
                    continue
                if prev_ix is not None:
                    a = coord(prev_ix, iy)
                    b = coord(ix, iy)
                    if not self._segment_hits_any_rect(a, b):
                        w = abs(xs[ix] - xs[prev_ix])
                        adj[(prev_ix, iy)].append(((ix, iy), w))
                        adj[(ix, iy)].append(((prev_ix, iy), w))
                prev_ix = ix

        # Vertical neighbors
        for ix in range(len(xs)):
            prev_iy = None
            for iy in range(len(ys)):
                if (ix, iy) in blocked:
                    prev_iy = None
                    continue
                if prev_iy is not None:
                    a = coord(ix, prev_iy)
                    b = coord(ix, iy)
                    if not self._segment_hits_any_rect(a, b):
                        w = abs(ys[iy] - ys[prev_iy])
                        adj[(ix, prev_iy)].append(((ix, iy), w))
                        adj[(ix, iy)].append(((ix, prev_iy), w))
                prev_iy = iy
        return adj

    def _ensure_on_grid(self, xs, ys, p: QPointF):
        if round(p.x(), 3) not in set(xs):
            xs.append(round(p.x(), 3))
            xs.sort()
        if round(p.y(), 3) not in set(ys):
            ys.append(round(p.y(), 3))
            ys.sort()
        ix = xs.index(round(p.x(), 3))
        iy = ys.index(round(p.y(), 3))
        return ix, iy

    def _astar(self, adj, start_key, goal_key, xs, ys):
        def h(a, b):
            (ix, iy) = a
            (jx, jy) = b
            return abs(xs[ix]-xs[jx]) + abs(ys[iy]-ys[jy])

        def _dir(u, v):
            (ix, iy), (jx, jy) = u, v
            return 0 if iy == jy else 1

        TURN = 2.0

        g = {start_key: 0.0}
        f = {start_key: h(start_key, goal_key)}
        came = {}
        pq = [(f[start_key], start_key)]
        seen = set()

        while pq:
            _, u = heapq.heappop(pq)
            if u in seen:
                continue
            seen.add(u)
            if u == goal_key:
                path = [u]
                while u in came:
                    u = came[u]
                    path.append(u)
                return list(reversed(path))

            for v, w in adj.get(u, []):
                pen = 0.0
                if u in came:
                    prev = came[u]
                    if _dir(prev, u) != _dir(u, v):
                        pen = TURN
                ng = g[u] + float(w) + pen
                if ng < g.get(v, float('inf')):
                    g[v] = ng
                    f[v] = ng + h(v, goal_key)
                    came[v] = u
                    heapq.heappush(pq, (f[v], v))
        return None

    def route(self, start_pt: QPointF, end_pt: QPointF):
        xs, ys = self._build_coords(start_pt, end_pt)
        s_key = self._ensure_on_grid(xs, ys, start_pt)
        e_key = self._ensure_on_grid(xs, ys, end_pt)
        adj = self._build_graph(xs, ys)
        key_path = self._astar(adj, s_key, e_key, xs, ys)
        if not key_path:
            return None
        pts = [QPointF(xs[ix], ys[iy]) for (ix, iy) in key_path]
        
        # Compress collinear points
        out = [pts[0]]
        for i in range(1, len(pts)-1):
            a, b, c = out[-1], pts[i], pts[i+1]
            if (abs(a.x()-b.x()) <= 1e-6 and abs(b.x()-c.x()) <= 1e-6) or \
               (abs(a.y()-b.y()) <= 1e-6 and abs(b.y()-c.y()) <= 1e-6):
                continue
            out.append(b)
        out.append(pts[-1])
        return out

# ============================ FIXED Enhanced Segment =============================
class _EnhancedSegmentItem(QGraphicsLineItem):
    """FIXED: Enhanced draggable segment with proper drag threshold"""
    
    def __init__(self, connector, index, color: QColor, width: int):
        super().__init__()
        self.connector = connector
        self.index = index
        self._base_pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        self._hover_pen = QPen(color.lighter(140), width + 1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        self._selected_pen = QPen(color.lighter(160), width + 1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        self.setPen(self._base_pen)
        self.setZValue(Z_SEGMENTS)

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setFlag(QGraphicsItem.ItemIsFocusable, True)

        self.is_selected = False
        self.is_horizontal = False
        self.is_vertical = False
        self._press_scene_pos = None
        self._drag_started = False  # FIXED: Track if drag actually started
        self.original_points = None

    def set_color(self, color: QColor):
        self._base_pen.setColor(color)
        self._hover_pen.setColor(color.lighter(140))
        self._selected_pen.setColor(color.lighter(160))
        if not self.is_selected:
            self.setPen(self._base_pen)

    def hoverEnterEvent(self, e):
        if hasattr(self.connector, "_hover_cursor") and self.connector._hover_cursor:
            self.setCursor(self.connector._hover_cursor)
        self.setPen(self._hover_pen)
        super().hoverEnterEvent(e)

    def hoverLeaveEvent(self, e):
        self.unsetCursor()
        self.setPen(self._base_pen if not self.is_selected else self._selected_pen)
        super().hoverLeaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_scene_pos = e.scenePos()
            self._drag_started = False  # FIXED: Reset drag state
            i = self.index
            self.original_points = [QPointF(self.connector._points[i]), QPointF(self.connector._points[i+1])]
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press_scene_pos is not None:
            # FIXED: Only enter drag mode after significant movement
            movement = self.connector._distance(e.scenePos(), self._press_scene_pos)
            if not self._drag_started and movement > DRAG_START_THRESHOLD:
                self._drag_started = True
                self.setPen(self._selected_pen)
                self.is_selected = True
                self.connector._begin_segment_drag(self.index)
                
            if self._drag_started:
                delta = e.scenePos() - self._press_scene_pos
                if self.is_horizontal:
                    filtered = QPointF(0, delta.y())
                elif self.is_vertical:
                    filtered = QPointF(delta.x(), 0)
                else:
                    filtered = QPointF(0, 0)
                self.connector._drag_segment_to(self.index, filtered, self.original_points)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._press_scene_pos is not None:
            if self._drag_started:
                self.connector._end_segment_drag(self.index)
                self.is_selected = False
                self.setPen(self._base_pen)
            self._press_scene_pos = None
            self._drag_started = False  # FIXED: Reset drag state
            self.original_points = None
            e.accept()
            return
        super().mouseReleaseEvent(e)

# =================== FIXED Enhanced SmartOrthogonalConnector ===================
class SmartOrthogonalConnector:
    """
    FIXED: Enhanced orthogonal connector with intelligent movement handling
    """
    
    def __init__(self, scene, start_item, end_item,
                 obstacles=None, lead=DEFAULT_LEAD_LENGTH, margin=DEFAULT_MARGIN,
                 color=DEFAULT_COLOR, line_width=DEFAULT_LINE_WIDTH):

        self.scene = scene
        self.start_item = start_item
        self.end_item = end_item
        self.obstacles = list(obstacles or [])
        self.lead = int(lead)
        self.margin = int(margin)
        self.color = QColor(color)
        self.line_width = int(line_width)
        if self.margin < self.line_width + 8:
            self.margin = self.line_width + 8

        self.db_id = None
        self.read_only = False

        # FIXED: Enhanced state management
        self._segment_items = []
        self._points = []
        self._manual_points = []  # Stored manual routing points
        self._manual_override = False
        self._manual_edit_count = 0
        
        # IMPROVED: Better movement tracking
        self._last_anchor_start = None
        self._last_anchor_end = None
        self._stable_route_locked = False  # FIXED: Lock stable routes
        self._initial_auto_route_done = False  # FIXED: Track first route

        # Graphics
        self.start_marker = self._create_endpoint_marker()
        self.end_marker = self._create_endpoint_marker()
        self._hover_cursor = _make_double_arrow_cursor(self.color)

        # IMPROVED: Better metadata
        self._route_metadata = {
            'created_at': self._get_timestamp(),
            'last_manual_edit': None,
            'edit_count': 0,
            'stability_score': 1.0,
            'locked': False
        }

        self._register_observers()
        self.update_path()

    # ======================= FIXED Movement Logic ==========================
    def set_read_only(self, flag: bool):
        self.read_only = flag

    def _distance(self, a: QPointF, b: QPointF) -> float:
        """Calculate distance between two points"""
        dx = a.x() - b.x()
        dy = a.y() - b.y()
        return math.sqrt(dx*dx + dy*dy)

    def _should_preserve_manual_route(self, p_start: QPointF, p_end: QPointF) -> bool:
        """FIXED: Smarter preservation logic - only reroute if obstacles prevent current path"""
        if not self._manual_override or not self._manual_points:
            return False
            
        # FIXED: Always preserve if we have a locked stable route
        if self._stable_route_locked:
            return True
            
        # FIXED: Check if current path is still valid (not blocked by obstacles)
        if self._is_current_path_valid(p_start, p_end):
            return True
            
        # Only reroute if path is actually blocked
        return False

    def _is_current_path_valid(self, p_start: QPointF, p_end: QPointF) -> bool:
        """FIXED: Check if current path can be adjusted without hitting obstacles"""
        if not self._manual_points or len(self._manual_points) < 2:
            return False
            
        # Create test path with updated endpoints
        test_points = [QPointF(p) for p in self._manual_points]
        test_points[0] = p_start
        test_points[-1] = p_end
        
        # Adjust intermediate points to maintain orthogonal structure
        self._adjust_path_endpoints(test_points)
        
        # Check if adjusted path hits obstacles
        return not self._path_hits_obstacles(test_points)

    def _path_hits_obstacles(self, points) -> bool:
        """Check if a path hits any obstacles"""
        if len(points) < 2:
            return False
            
        inflated_rects = self._get_inflated_obstacle_rects()
        router = _GridRouter(inflated_rects, self.margin)
        
        for i in range(len(points) - 1):
            if router._segment_hits_any_rect(points[i], points[i+1]):
                return True
        return False

    def _adjust_path_endpoints(self, points):
        """FIXED: Smart endpoint adjustment that maintains route structure"""
        if len(points) < 4:
            return
            
        # Adjust first intermediate point to maintain perpendicular exit
        start_side = self._pin_side(self.start_item)
        if start_side in ('left', 'right'):
            points[1].setY(points[0].y())  # Keep horizontal exit
        else:
            points[1].setX(points[0].x())  # Keep vertical exit
            
        # Adjust last intermediate point to maintain perpendicular entry
        end_side = self._pin_side(self.end_item)
        if end_side in ('left', 'right'):
            points[-2].setY(points[-1].y())  # Keep horizontal entry
        else:
            points[-2].setX(points[-1].x())  # Keep vertical entry

    # ========================= FIXED Path Management =====================
    
    def update_path(self):
        """FIXED: Much smarter path update logic"""
        if self._endpoints_invalid():
            self.remove()
            return

        p_start = self.start_item.center()
        p_end = self.end_item.center()
        
        # Update markers immediately
        self._update_marker(self.start_marker, p_start)
        self._update_marker(self.end_marker, p_end)

        # FIXED: Different strategies based on route state
        if not self._initial_auto_route_done:
            # First time - create initial route
            self._full_reroute(p_start, p_end)
            self._initial_auto_route_done = True
            
        elif self._should_preserve_manual_route(p_start, p_end):
            # Preserve and adjust manual route
            self._preserve_and_adjust_route(p_start, p_end)
            
        else:
            # Only reroute if absolutely necessary
            self._smart_reroute(p_start, p_end)
            
        # Update anchors
        self._last_anchor_start = QPointF(p_start)
        self._last_anchor_end = QPointF(p_end)

    def _preserve_and_adjust_route(self, p_start: QPointF, p_end: QPointF):
        """FIXED: Preserve route structure but adjust for endpoint changes"""
        if not self._manual_points or len(self._manual_points) < 2:
            self._full_reroute(p_start, p_end)
            return
            
        # Use manual points as base
        self._points = [QPointF(p) for p in self._manual_points]
        
        # FIXED: Smart endpoint updating
        self._update_endpoints_intelligently(p_start, p_end)
        self._rebuild_segments_from_points()

    def _update_endpoints_intelligently(self, p_start: QPointF, p_end: QPointF):
        """FIXED: Update endpoints while maintaining route structure"""
        if len(self._points) < 2:
            return
            
        # Calculate movement vectors
        old_start = self._points[0]
        old_end = self._points[-1]
        start_delta = QPointF(p_start.x() - old_start.x(), p_start.y() - old_start.y())
        end_delta = QPointF(p_end.x() - old_end.x(), p_end.y() - old_end.y())
        
        # Update endpoints
        self._points[0] = p_start
        self._points[-1] = p_end
        
        if len(self._points) >= 4:
            # Adjust lead points to maintain perpendicular exits
            start_side = self._pin_side(self.start_item)
            end_side = self._pin_side(self.end_item)
            
            # Adjust first intermediate point
            if start_side in ('left', 'right'):
                # Maintain horizontal exit, adjust for vertical movement
                self._points[1].setY(p_start.y())
                self._points[1].setX(self._points[1].x() + start_delta.x())
            else:
                # Maintain vertical exit, adjust for horizontal movement  
                self._points[1].setX(p_start.x())
                self._points[1].setY(self._points[1].y() + start_delta.y())
                
            # Adjust last intermediate point  
            if end_side in ('left', 'right'):
                # Maintain horizontal entry
                self._points[-2].setY(p_end.y())
                self._points[-2].setX(self._points[-2].x() + end_delta.x())
            else:
                # Maintain vertical entry
                self._points[-2].setX(p_end.x())
                self._points[-2].setY(self._points[-2].y() + end_delta.y())

    def _smart_reroute(self, p_start: QPointF, p_end: QPointF):
        """FIXED: Only reroute if current path is blocked"""
        # Try to preserve current structure first
        if self._points and len(self._points) >= 2:
            test_points = [QPointF(p) for p in self._points]
            test_points[0] = p_start
            test_points[-1] = p_end
            self._adjust_path_endpoints(test_points)
            
            if not self._path_hits_obstacles(test_points):
                # Current structure works, use it
                self._points = test_points
                self._rebuild_segments_from_points()
                return
                
        # Only do full reroute if adjustment failed
        self._full_reroute(p_start, p_end)

    def _full_reroute(self, p_start: QPointF, p_end: QPointF):
        """Perform full automatic re-routing"""
        lead1 = self._extruded_lead_point(p_start, self.start_item)
        lead2 = self._extruded_lead_point(p_end, self.end_item)

        inflated_rects = self._get_inflated_obstacle_rects()
        router = _GridRouter(inflated_rects, self.margin)
        mid_path = router.route(lead1, lead2)

        if not mid_path or len(mid_path) < 2:
            mid_path = [lead1, QPointF(lead2.x(), lead1.y()), lead2]

        pts = [p_start, lead1] + mid_path[1:-1] + [lead2, p_end]
        self._points = pts
        self._snap_first_last_legs()
        self._rebuild_segments_from_points()

    # ========================= FIXED Manual Route Management ======================
    
    def apply_routing_points(self, points_list):
        """FIXED: Apply saved routing points with better validation"""
        if not points_list or len(points_list) < 2:
            return False
            
        try:
            # Convert to QPointF objects
            point_objects = []
            for pt in points_list:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    point_objects.append(QPointF(float(pt[0]), float(pt[1])))
                elif hasattr(pt, 'x') and hasattr(pt, 'y'):
                    point_objects.append(QPointF(float(pt.x()), float(pt.y())))
                else:
                    return False
                    
            # Store manual points
            self._manual_points = point_objects
            self._points = [QPointF(p) for p in point_objects]
            self._manual_override = True
            self._stable_route_locked = True  # FIXED: Lock this route
            
            # Update current endpoints
            current_start = self.start_item.center()
            current_end = self.end_item.center()
            self._points[0] = current_start
            self._points[-1] = current_end
            
            self._adjust_path_endpoints(self._points)
            self._rebuild_segments_from_points()
            
            print(f"Applied and locked {len(point_objects)} manual routing points")
            return True
                
        except Exception as e:
            print(f"Error applying routing points: {e}")
            return False

    def get_routing_points(self):
        """Get current routing points for saving"""
        if self._manual_override and self._manual_points:
            return [(p.x(), p.y()) for p in self._manual_points]
        elif self._points:
            return [(p.x(), p.y()) for p in self._points]
        else:
            return []

    def _force_manual_mode(self):
        """FIXED: Only force manual mode during actual drag operations"""
        if not self._manual_override:
            self._manual_points = [QPointF(p) for p in self._points]
            self._manual_override = True
            self._stable_route_locked = True
            self._manual_edit_count += 1
            self._route_metadata['edit_count'] = self._manual_edit_count
            self._route_metadata['last_manual_edit'] = self._get_timestamp()
            print(f"Entered manual mode with {len(self._points)} points")

    # ========================= FIXED Drag Handling =======================
    
    def _begin_segment_drag(self, idx: int):
        """FIXED: Only enter manual mode when drag actually starts"""
        if self.read_only:
            return
        self._force_manual_mode()  # Now only called during actual drag

    def _drag_segment_to(self, idx: int, delta: QPointF, original_points):
        """Enhanced segment dragging with collision detection"""
        if self.read_only:
            return
        if idx >= len(self._points) - 1:
            return
            
        p1_idx, p2_idx = idx, idx + 1
        if p1_idx == 0 or p2_idx == len(self._points) - 1:
            return  # Don't drag endpoint segments

        new_p1 = original_points[0] + delta
        new_p2 = original_points[1] + delta

        # Snap to axis
        if abs(new_p1.x() - new_p2.x()) <= abs(new_p1.y() - new_p2.y()):
            new_p2.setX(new_p1.x())  # Vertical segment
        else:
            new_p2.setY(new_p1.y())  # Horizontal segment

        # Check collision with more tolerance for manual edits
        if not self._check_drag_collision_tolerant(idx, new_p1, new_p2):
            return

        # Apply the drag
        self._points[p1_idx] = new_p1
        self._points[p2_idx] = new_p2
        self._snap_first_last_legs()
        
        # Update segments immediately
        for i, seg in enumerate(self._segment_items):
            if i < len(self._points) - 1:
                seg.setLine(QLineF(self._points[i], self._points[i+1]))

    def _check_drag_collision_tolerant(self, idx: int, new_p1: QPointF, new_p2: QPointF) -> bool:
        """FIXED: More tolerant collision checking for manual edits"""
        # Allow closer proximity to obstacles during manual editing
        tolerant_margin = max(5, self.margin * 0.5)
        inflated_rects = self._get_inflated_obstacle_rects()
        
        # Create more tolerant router for manual editing
        tolerant_router = _GridRouter(inflated_rects, tolerant_margin)
        
        # Check the dragged segment
        if tolerant_router._segment_hits_any_rect(new_p1, new_p2):
            return False
            
        return True

    def _end_segment_drag(self, idx: int):
        """End segment drag and lock the route"""
        # Update manual backup with current state
        if self.read_only:
            return
        self._manual_points = [QPointF(p) for p in self._points]
        self._stable_route_locked = True  # FIXED: Lock the route after manual edit
        self._route_metadata['last_manual_edit'] = self._get_timestamp()
        print(f"Manual edit completed. Route locked with {len(self._manual_points)} points")

    # ========================= Enhanced Save/Load ===========================
    
    def get_save_data(self):
        """Get comprehensive save data including lock state"""
        base_points = self.get_routing_points()
        
        return {
            'points': base_points,
            'manual_override': self._manual_override,
            'edit_count': self._manual_edit_count,
            'locked': self._stable_route_locked,  # FIXED: Include lock state
            'metadata': self._route_metadata.copy(),
            'pin_sides': {
                'start': self._pin_side(self.start_item),
                'end': self._pin_side(self.end_item)
            }
        }

    def apply_save_data(self, save_data):
        """FIXED: Apply save data with proper state restoration"""
        if not isinstance(save_data, dict):
            # Legacy format - just points
            return self.apply_routing_points(save_data)
            
        try:
            # Restore state
            self._manual_override = save_data.get('manual_override', False)
            self._manual_edit_count = save_data.get('edit_count', 0)
            self._stable_route_locked = save_data.get('locked', False)  # FIXED: Restore lock state
            self._route_metadata.update(save_data.get('metadata', {}))
            
            # Apply points
            points = save_data.get('points', [])
            if points:
                success = self.apply_routing_points(points)
                if success:
                    print(f"Restored route: {len(points)} points, manual={self._manual_override}, locked={self._stable_route_locked}")
                return success
            return False
            
        except Exception as e:
            print(f"Error applying save data: {e}")
            return False

    # ========================= Utility Methods (mostly unchanged) ===============================
    
    def _get_timestamp(self):
        """Get current timestamp"""
        from datetime import datetime
        return datetime.now().isoformat()
    
    def _endpoints_invalid(self) -> bool:
        try:
            if sip.isdeleted(self.start_item) or sip.isdeleted(self.end_item):
                return True
        except Exception:
            pass
        return (self.start_item.scene() is None or self.end_item.scene() is None)

    def _register_observers(self):
        for obj in [self.start_item, self.end_item] + self.obstacles:
            if hasattr(obj, "observers"):
                if self not in obj.observers:
                    obj.observers.append(self)

    def _create_endpoint_marker(self) -> QGraphicsEllipseItem:
        r = DEFAULT_POINT_RADIUS
        m = QGraphicsEllipseItem(-r, -r, 2*r, 2*r)
        m.setBrush(self.color)
        m.setPen(QPen(Qt.NoPen))
        m.setZValue(Z_MARKERS)
        if self.scene:
            self.scene.addItem(m)
        return m

    def _update_marker(self, marker: QGraphicsEllipseItem, center: QPointF):
        r = DEFAULT_POINT_RADIUS
        marker.setRect(center.x()-r, center.y()-r, 2*r, 2*r)

    def _pin_side(self, pin) -> str:
        side = getattr(pin, "side", None)
        if side in SIDE_VECTORS:
            return side
        parent = getattr(pin, "parentItem", lambda: None)()
        if parent:
            br = parent.mapToScene(parent.boundingRect()).boundingRect()
            p = pin.center()
            d = {
                'left': abs(p.x() - br.left()),
                'right': abs(p.x() - br.right()),
                'top': abs(p.y() - br.top()),
                'bottom': abs(p.y() - br.bottom()),
            }
            return min(d, key=d.get)
        return 'right'

    def _extruded_lead_point(self, p: QPointF, pin) -> QPointF:
        side = self._pin_side(pin)
        v = SIDE_VECTORS[side]
        length = max(self.lead, self.margin + 2)
        return QPointF(p.x() + v.x()*length, p.y() + v.y()*length)

    def _get_inflated_obstacle_rects(self):
        """Get obstacle rectangles with margin"""
        def root(item):
            p = item
            while hasattr(p, 'parentItem') and p.parentItem() is not None:
                p = p.parentItem()
            return p
        
        start_root = root(self.start_item)
        end_root = root(self.end_item)

        def _rect_of(item):
            try:
                r = item.mapToScene(item.boundingRect()).boundingRect()
            except Exception:
                r = item.sceneBoundingRect()
            return r.adjusted(-self.margin, -self.margin, self.margin, self.margin)

        inflated_rects = []
        if start_root:
            inflated_rects.append(_rect_of(start_root))
        if end_root and end_root is not start_root:
            inflated_rects.append(_rect_of(end_root))

        for obs in self.obstacles:
            rr = _rect_of(obs)
            if all(abs(rr.left()-x.left()) > 1e-6 or abs(rr.top()-x.top()) > 1e-6 or
                   abs(rr.right()-x.right()) > 1e-6 or abs(rr.bottom()-x.bottom()) > 1e-6
                   for x in inflated_rects):
                inflated_rects.append(rr)
        
        return inflated_rects

    def _snap_first_last_legs(self):
        """Snap first and last legs to be perpendicular to pins"""
        if len(self._points) < 2:
            return
            
        # Start stub
        s0, s1 = self._points[0], self._points[1]
        side_s = self._pin_side(self.start_item)
        if side_s in ('left', 'right'):
            self._points[1].setY(s0.y())
        else:
            self._points[1].setX(s0.x())

        # End stub
        if len(self._points) >= 2:
            eN, eN1 = self._points[-1], self._points[-2]
            side_e = self._pin_side(self.end_item)
            if side_e in ('left', 'right'):
                self._points[-2].setY(eN.y())
            else:
                self._points[-2].setX(eN.x())

    def _rebuild_segments_from_points(self):
        """Rebuild visual segments from current points"""
        # Remove old segments
        for s in self._segment_items:
            if s.scene():
                s.scene().removeItem(s)
        self._segment_items.clear()

        # Create new segments with enhanced dragging
        for i in range(len(self._points) - 1):
            a, b = QPointF(self._points[i]), QPointF(self._points[i+1])
            
            # Snap to orthogonal
            if abs(a.y() - b.y()) <= ORTHO_EPS:
                b.setY(a.y())
            elif abs(a.x() - b.x()) <= ORTHO_EPS:
                b.setX(a.x())
            else:
                if abs(a.y() - b.y()) < abs(a.x() - b.x()):
                    b.setY(a.y())
                else:
                    b.setX(a.x())
            self._points[i+1] = b

            seg = _EnhancedSegmentItem(self, i, self.color, self.line_width)
            seg.setLine(QLineF(a, b))
            seg.is_horizontal = abs(a.y() - b.y()) <= ORTHO_EPS
            seg.is_vertical = abs(a.x() - b.x()) <= ORTHO_EPS
            self.scene.addItem(seg)
            self._segment_items.append(seg)

    def remove(self):
        """Clean removal of all graphics items"""
        for seg in self._segment_items:
            if seg.scene():
                seg.scene().removeItem(seg)
        self._segment_items.clear()
        
        for m in (self.start_marker, self.end_marker):
            if m and m.scene():
                m.scene().removeItem(m)
        self.start_marker = self.end_marker = None
        
        # Unregister from observers
        for obj in [self.start_item, self.end_item] + self.obstacles:
            if hasattr(obj, "observers") and self in getattr(obj, "observers", []):
                try:
                    obj.observers.remove(self)
                except ValueError:
                    pass

    def clear_selection(self):
        """Clear selection state from all segments"""
        for seg in self._segment_items:
            seg.is_selected = False
            seg.setPen(seg._base_pen)

    def update_color(self, qcolor: QColor):
        """Update color of all components"""
        self.color = QColor(qcolor)
        for seg in self._segment_items:
            seg.set_color(self.color)
        if self.start_marker:
            self.start_marker.setBrush(self.color)
        if self.end_marker:
            self.end_marker.setBrush(self.color)
        self._hover_cursor = _make_double_arrow_cursor(self.color)

    def get_status_info(self):
        """Get detailed status information for debugging"""
        return {
            'manual_override': self._manual_override,
            'manual_edit_count': self._manual_edit_count,
            'points_count': len(self._points),
            'manual_points_count': len(self._manual_points),
            'locked': self._stable_route_locked,
            'initial_route_done': self._initial_auto_route_done,
            'last_edit': self._route_metadata.get('last_manual_edit', 'Never')
        }

# ========================= Integration Helper (Updated) ===============================

def upgrade_existing_connectors(scene):
    """Upgrade existing SmartOrthogonalConnector instances to FIXED enhanced version"""
    if not hasattr(scene, '_connection_edges'):
        return 0
        
    upgraded_count = 0
    new_edges = []
    
    for edge in scene._connection_edges:
        if hasattr(edge, '__class__') and 'SmartOrthogonalConnector' in edge.__class__.__name__:
            try:
                # Create FIXED enhanced version
                enhanced_edge = SmartOrthogonalConnector(
                    scene=scene,
                    start_item=edge.start_item,
                    end_item=edge.end_item,
                    obstacles=edge.obstacles,
                    lead=getattr(edge, 'lead', DEFAULT_LEAD_LENGTH),
                    margin=getattr(edge, 'margin', DEFAULT_MARGIN),
                    color=getattr(edge, 'color', DEFAULT_COLOR),
                    line_width=getattr(edge, 'line_width', DEFAULT_LINE_WIDTH)
                )
                
                # Copy db_id if it exists
                if hasattr(edge, 'db_id'):
                    enhanced_edge.db_id = edge.db_id
                
                # Try to preserve existing route if it was manually edited
                if hasattr(edge, '_points') and edge._points:
                    enhanced_edge.apply_routing_points([(p.x(), p.y()) for p in edge._points])
                
                # Remove old edge
                if hasattr(edge, 'remove'):
                    edge.remove()
                
                new_edges.append(enhanced_edge)
                upgraded_count += 1
                
            except Exception as e:
                print(f"Failed to upgrade connector: {e}")
                new_edges.append(edge)  # Keep original if upgrade fails
        else:
            new_edges.append(edge)  # Keep non-SmartOrthogonalConnector items
    
    scene._connection_edges = new_edges
    print(f"Upgraded {upgraded_count} connectors to FIXED enhanced version")
    return upgraded_count