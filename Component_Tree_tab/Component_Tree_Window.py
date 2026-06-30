import os
import sys
import json
import tempfile
import shutil
import base64
from PIL import Image
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QHBoxLayout, QLabel, QFrame,
    QFileDialog, QMessageBox, QProgressDialog, QInputDialog, QApplication
)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtCore import QObject, pyqtSignal,QSizeF, pyqtSlot, QUrl, QTimer, QMarginsF
from PyQt5.QtGui import QPixmap, QPainter, QPageSize, QPageLayout  # Added QPageLayout and QMarginsF
from PyQt5.QtPrintSupport import QPrinter
from PyQt5.QtCore import Qt

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import get_connection, get_current_project_id, get_current_project_name

# Import style system (maintaining original structure)
from styles.style_manager import (style_manager, register_widget, 
                                create_styled_button, auto_style_widget)
from styles.design_system import Colors, Typography, Spacing, BorderRadius, NodeStyles
from styles.theme_manager import theme_manager

class Node:
    def __init__(self, name, type_, id_, children=None, expanded=True):
        self.name = name
        self.type = type_
        self.id = id_
        self.children = children or []
        self.expanded = expanded  # Keep for initial state

    def to_dict(self):
        return {
            'name': self.name,
            'type': self.type,
            'id': self.id,
            'children': [child.to_dict() for child in self.children],
            'expanded': self.expanded,
        }

class ComponentTreeBridge(QObject):
    """Bridge between Python and JavaScript for tree data communication"""
    
    # Signals to JavaScript
    tree_data_ready = pyqtSignal(str)  # JSON tree data
    theme_changed = pyqtSignal(str)    # Theme update
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tree_data = {}
        self.load_tree_from_database()
        
        # Connect to theme changes
        style_manager.theme_changed.connect(self._on_theme_changed)
    
    def _ensure_project_selected(self):
        project_id = get_current_project_id()
        if project_id is None:
            return False
        return True
        
    def load_tree_from_database(self):
        """Load tree structure from database and convert to JavaScript-friendly format"""
        try:
            if not self._ensure_project_selected():
                self.tree_data = {
                    "name": "No Project Selected",
                    "type": "project", 
                    "id": "root",
                    "children": [],
                    "expanded": True
                }
                return
                
            project_id = get_current_project_id()
            
            # GET THE ACTUAL PROJECT NAME INSTEAD OF "Project Root"
            project_name = get_current_project_name() or "Project Root"
            
            with get_connection() as conn:
                cursor = conn.cursor()
                
                # Build tree using Node class
                root = Node(project_name, "project", "root")
                
                # Load subsystems
                cursor.execute("SELECT id, name FROM subsystems WHERE project_id = %s ORDER BY name", (project_id,))
                subsystems = cursor.fetchall()
                
                for ss_id, ss_name in subsystems:
                    subsystem = Node(ss_name, "subsystem", f"ss_{ss_id}", expanded=False)
                    
                    # Load modules for this subsystem
                    cursor.execute("SELECT id, name FROM modules WHERE subsystem_id=%s AND project_id=%s ORDER BY name", (ss_id, project_id))
                    modules = cursor.fetchall()
                    
                    for mod_id, mod_name in modules:
                        module = Node(mod_name, "module", f"mod_{mod_id}", expanded=False)
                        
                        # Load connectors for this module
                        cursor.execute("SELECT id, name FROM connectors WHERE module_id=%s AND project_id=%s ORDER BY name", (mod_id, project_id))
                        connectors = cursor.fetchall()
                        
                        for conn_id, conn_name in connectors:
                            connector = Node(conn_name, "connector", f"conn_{conn_id}", expanded=False)
                            
                            # Load pins for this connector
                            cursor.execute("SELECT id, name FROM pins WHERE connector_id=%s AND project_id=%s ORDER BY pin_number", (conn_id, project_id))
                            pins = cursor.fetchall()
                            
                            for pin_id, pin_name in pins:
                                pin = Node(pin_name, "pin", f"pin_{pin_id}", expanded=False)
                                connector.children.append(pin)
                                
                            module.children.append(connector)
                        subsystem.children.append(module)
                    root.children.append(subsystem)
                    
                self.tree_data = root.to_dict()
                
        except Exception as e:
            print(f"Error loading tree data: {e}")
            self.tree_data = {
                "name": "Error loading data", 
                "type": "error", 
                "children": [],
                "error_message": str(e)
            }

    @pyqtSlot()
    def get_tree_data(self):
        """Send tree data to JavaScript"""
        if not self._ensure_project_selected():
            empty_tree = {
                "name": "Please Select or Create a Project",
                "type": "project", 
                "id": "root",
                "children": [],
                "expanded": True
            }
            self.tree_data_ready.emit(json.dumps(empty_tree))
        else:
            self.tree_data_ready.emit(json.dumps(self.tree_data))

        
    @pyqtSlot()
    def refresh_tree_data(self):
        """Refresh tree data from database"""
        self.load_tree_from_database()
        self.tree_data_ready.emit(json.dumps(self.tree_data))

    def refresh_with_project_check(self):
        if not self._ensure_project_selected():
            self.tree_data = {
                "name": "Please Select a Project",
                "type": "project", 
                "id": "root",
                "children": [],
                "expanded": True
            }
        else:
            self.load_tree_from_database()
        self.tree_data_ready.emit(json.dumps(self.tree_data))
        
    def _on_theme_changed(self, theme_name):
        """Handle theme changes and notify JavaScript"""
        theme_colors = {
            'primary_dark': theme_manager.get_color('primary_dark'),
            'primary_light': theme_manager.get_color('primary_light'), 
            'accent': theme_manager.get_color('accent'),
            'text_primary': theme_manager.get_color('text_primary')
        }
        self.theme_changed.emit(json.dumps(theme_colors))


class ComponentTreeTab(QWidget):
    """Enhanced Component Tree Tab using JavaScript + D3.js - Fixed Version"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ComponentTreeTab")
        self.setAttribute(Qt.WA_StyledBackground, True)
        
        # Set d3.js path before any UI setup
        self.d3_js_path = os.path.join(os.path.dirname(__file__), 'd3.min.js')
        
        # Initialize bridge for Python-JS communication
        self.bridge = ComponentTreeBridge()
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        
        # Apply styling
        self.apply_main_style()
        style_manager.theme_changed.connect(self.on_theme_changed)
        
        self.setup_ui()
        self.setup_connections()
        
        # Load the tree after UI is ready
        QTimer.singleShot(500, self._initialize_tree)

    def create_temp_directory_with_assets(self):
        temp_dir = tempfile.mkdtemp()
        
        if os.path.exists(self.d3_js_path):
            shutil.copy2(self.d3_js_path, os.path.join(temp_dir, 'd3.min.js'))
        else:
            raise FileNotFoundError(f"فایل d3.min.js در مسیر {self.d3_js_path} پیدا نشد")
            
        return temp_dir
    
    def check_d3_availability(self):
        """Check if D3.js file exists"""
        if not os.path.exists(self.d3_js_path):
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                "Error",
                f"d3.min.js file not found at:\n{self.d3_js_path}\n\n"
                "Please place d3.min.js file next to the Python file."
            )
            return False
        return True

    def apply_main_style(self):
        """Apply main component styling"""
        style = f"""
            QWidget#ComponentTreeTab {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}
        """
        self.setStyleSheet(style)

    def on_theme_changed(self, theme_name):
        """Handle theme changes"""
        self.apply_main_style()
        self.update_toolbar_style()
        self.update_container_style()

    def setup_ui(self):
        """Setup user interface"""
        # Check D3.js availability before proceeding
        if not self.check_d3_availability():
            return
            
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Create toolbar
        self.create_toolbar(main_layout)

        # Create web view container
        tree_container = self.create_tree_container()
        main_layout.addWidget(tree_container)

    def create_toolbar(self, main_layout):
        """Create toolbar with controls and export buttons"""
        self.toolbar = QFrame()
        self.toolbar.setFixedHeight(50)
        self.update_toolbar_style()
        
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(12, 0, 12, 0)
        toolbar_layout.setSpacing(8)
        
        # Title - Clean name
        title = QLabel("Component Tree")
        title.setFont(title.font())
        title.setStyleSheet("font-weight: bold; font-size: 16px;")
        toolbar_layout.addWidget(title)
        
        toolbar_layout.addSpacing(30)

        # Tree control buttons - All in toolbar
        self.toggle_expand_btn = create_styled_button("Expand All", "normal")
        self.refresh_btn = create_styled_button("Refresh", "normal")
        self.fit_btn = create_styled_button("Fit View", "normal")
        
        # Add tooltips
        self.toggle_expand_btn.setToolTip("Toggle expand/collapse\nShortcut: E/C")
        self.refresh_btn.setToolTip("Refresh tree from database\nShortcut: R")
        self.fit_btn.setToolTip("Fit tree to view\nShortcut: Space")
        
        toolbar_layout.addWidget(self.toggle_expand_btn)
        toolbar_layout.addWidget(self.refresh_btn)
        toolbar_layout.addWidget(self.fit_btn)
        
        toolbar_layout.addSpacing(30)
        
        # Export buttons
        self.export_pdf_btn = create_styled_button("PDF", "normal")
        self.export_png_btn = create_styled_button("PNG", "normal")
        
        self.export_pdf_btn.setToolTip("Export tree to PDF\nVector quality\nPerfect for documentation")
        self.export_png_btn.setToolTip("Export tree to PNG\nHigh resolution bitmap\nGreat for presentations")
        
        toolbar_layout.addWidget(self.export_pdf_btn)
        toolbar_layout.addWidget(self.export_png_btn)
        
        toolbar_layout.addStretch()
        main_layout.addWidget(self.toolbar)

    def update_toolbar_style(self):
        """Update toolbar styling - Theme integrated"""
        toolbar_style = f"""
            QFrame {{
                background: {theme_manager.get_gradient("primary", "x1:0, y1:0, x2:1, y2:0")};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.LARGE};
            }}
            QLabel {{
                color: {theme_manager.get_color('text_primary')};
                background: transparent;
                border: none;
            }}
        """
        if hasattr(self, 'toolbar'):
            self.toolbar.setStyleSheet(toolbar_style)

    def create_tree_container(self):
        """Create web view container for JavaScript tree"""
        self.container = QFrame()
        self.update_container_style()
        
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(8, 8, 8, 8)
        
        # Create web engine view
        self.web_view = QWebEngineView()
        self.web_view.page().setWebChannel(self.channel)
        
        # Enable keyboard focus
        self.web_view.setFocusPolicy(Qt.StrongFocus)

        self.page_ready = False
        self.toggle_expand_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.fit_btn.setEnabled(False)

        self.web_view.loadFinished.connect(self._on_page_loaded)
        
        # Load the HTML content
        self.load_tree_html()
        
        layout.addWidget(self.web_view)
        return self.container

    def update_container_style(self):
        """Update container styling - Theme integrated"""
        container_style = f"""
            QFrame {{
                background: {theme_manager.get_color('primary_dark')};
                border: 1px solid {theme_manager.get_color('primary_light')};
                border-radius: {BorderRadius.XLARGE};
            }}
        """
        if hasattr(self, 'container'):
            self.container.setStyleSheet(container_style)

    def load_tree_html(self):
        """بارگیری محتوای HTML - تغییر یافته"""
        html_content = self.get_tree_html_template()
        
        # ایجاد پوشه موقت با اصل‌ها
        self.temp_dir = self.create_temp_directory_with_assets()
        
        # ایجاد فایل HTML در پوشه موقت
        html_path = os.path.join(self.temp_dir, 'tree.html')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        # بارگیری فایل HTML
        self.web_view.load(QUrl.fromLocalFile(html_path))
        self.temp_html_file = html_path

    def get_tree_html_template(self):
        """Generate HTML template with FIXED spacing, expand button, and text positioning"""
        # Get theme colors for JavaScript
        primary_dark = theme_manager.get_color('primary_dark')
        primary_light = theme_manager.get_color('primary_light')
        accent = theme_manager.get_color('accent')
        
        return f'''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Component Tree</title>
        <script src="./d3.min.js"></script>
        <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
        <style>
            body {{
                    margin: 0;
                    padding: 0;
                    background: {primary_dark};
                    font-family: 'Segoe UI', sans-serif;
                    overflow: hidden;  // NEW: Hide scrollbars for body
            }}
            
            #tree-container {{
                width: 100%;
                height: calc(100vh - 16px);
                background: {primary_dark};
                border-radius: 12px;
                border: 1px solid {primary_light};
                overflow: hidden;  // NEW: Hide scrollbars for container
            }}
            
            svg {{
                overflow: hidden;  // NEW: Hide scrollbars for SVG
            }}
            
            /* NEW: Hide scrollbars cross-browser */
            body::-webkit-scrollbar, #tree-container::-webkit-scrollbar, svg::-webkit-scrollbar {{
                display: none;
            }}
            body, #tree-container, svg {{
                -ms-overflow-style: none;  /* IE and Edge */
                scrollbar-width: none;  /* Firefox */
            }}
            
            .node {{
                cursor: pointer;
                transition: all 0.3s ease;
            }}
            
            .node rect {{
                stroke: #fff;
                stroke-width: 1px;
                transition: all 0.3s ease;
                rx: 15;
                ry: 15;
            }}
            
            /* متن در وسط سلول قرار گیرد */
            .node text {{
                font: 12px 'Segoe UI', sans-serif;
                font-weight: 600;
                text-anchor: middle;
                dominant-baseline: central;
                pointer-events: none;
                fill: white;
                alignment-baseline: central;
            }}
            
            .node.project rect {{ fill: #3498db; }}
            .node.subsystem rect {{ fill: #9b59b6; }}
            .node.module rect {{ fill: #e67e22; }}
            .node.connector rect {{ fill: #27ae60; }}
            .node.pin rect {{ fill: #e74c3c; }}
            
            .node:hover rect {{
                stroke-width: 2px;
                filter: brightness(1.1);
            }}
            
            .link {{
                fill: none;
                stroke: {primary_light};
                stroke-width: 1.5px;
                stroke-opacity: 0.7;
                transition: all 0.3s ease;
            }}
            
            .link:hover {{
                stroke-width: 2px;
                stroke-opacity: 1;
            }}
            
            .zoom-info {{
                position: absolute;
                bottom: 10px;
                left: 10px;
                color: white;
                font-size: 12px;
                background: rgba(0,0,0,0.7);
                padding: 5px 10px;
                border-radius: 5px;
                border: 1px solid {primary_light};
            }}
        </style>
    </head>
    <body>
        <div id="tree-container"></div>
        <div class="zoom-info" id="zoom-info">Zoom: 100%</div>

        <script>
            let bridge = null;
            let treeData = null;
            let svg, g, root;
            let zoom;
            let currentZoom = 1;
            let isCurrentlyExpanded = false;
            let hasInitialFit = false;
                        
            // UNIFORM SPACING CONSTANTS
            const MIN_HORIZONTAL_SPACING = 280;  // فاصله افقی
            const NODE_HEIGHT = 40;              // ارتفاع سلول
            const MIN_VERTICAL_SPACING = 60;     // حداقل فاصله عمودی
            
            // Initialize when page loads
            document.addEventListener('DOMContentLoaded', function() {{
                initializeWebChannel();
                setupD3Tree();
                setupKeyboardShortcuts();
            }});
            
            function initializeWebChannel() {{
                new QWebChannel(qt.webChannelTransport, function(channel) {{
                    bridge = channel.objects.bridge;
                    
                    // Listen for tree data
                    bridge.tree_data_ready.connect(function(jsonData) {{
                        try {{
                            treeData = JSON.parse(jsonData);
                            updateTree();
                        }} catch(e) {{
                            console.error('Failed to parse tree data:', e);
                        }}
                    }});
                    
                    // Listen for theme changes
                    bridge.theme_changed.connect(function(themeData) {{
                        try {{
                            const colors = JSON.parse(themeData);
                            updateThemeColors(colors);
                        }} catch(e) {{
                            console.error('Failed to parse theme data:', e);
                        }}
                    }});
                    
                    // Request initial data
                    setTimeout(() => bridge.get_tree_data(), 100);
                }});
            }}
            
            function setupKeyboardShortcuts() {{
                document.addEventListener('keydown', function(event) {{
                    switch(event.key.toLowerCase()) {{
                        case 'e':
                            expandAll();
                            event.preventDefault();
                            break;
                        case 'c':
                            collapseAll();
                            event.preventDefault();
                            break;
                        case 'r':
                            refreshTree();
                            event.preventDefault();
                            break;
                        case ' ':
                            fitView();
                            event.preventDefault();
                            break;
                    }}
                }});
            }}
            
            function setupD3Tree() {{
                const container = d3.select("#tree-container");
                const containerRect = container.node().getBoundingClientRect();
                
                svg = container.append("svg")
                    .attr("width", "100%")
                    .attr("height", "100%")
                    .style("background", "transparent");
                
                // Setup zoom behavior
                zoom = d3.zoom()
                    .scaleExtent([0.05, 8])
                    .on("zoom", function(event) {{
                        g.attr("transform", event.transform);
                        currentZoom = event.transform.k;
                        updateZoomInfo();
                    }});
                
                svg.call(zoom);
                g = svg.append("g");
            }}

            
            function updateTree() {{
                if (!treeData) return;
                
                console.log('Updating tree with data:', treeData);
                
                g.selectAll("*").remove();
                
                root = d3.hierarchy(treeData);
                
                root.x0 = 100;
                root.y0 = 100;
                
                isCurrentlyExpanded = false;
                
                // Collapse nodes initially (except root)
                if (root.children) {{
                    root.children.forEach(function(child) {{
                        if (!child.data.expanded) {{
                            collapseNode(child);
                        }}
                    }});
                }}
                
                // NEW: Compute layout dynamically
                layoutTree(root, 0, 0, MIN_VERTICAL_SPACING, MIN_HORIZONTAL_SPACING);
                
                update(root);
                
                if (!hasInitialFit) {{
                    setTimeout(() => {{
                        fitView();
                        hasInitialFit = true;
                    }}, 500);
                }}
            }}

            // NEW: Translated layout function from Python (recursive, computes x/y based on visible children)
            function layoutTree(node, x, y, minDy = 60, deltaX = 280) {{
                node.data.x = x;
                node.data.y = y;
                if (!node.children || node.children.length === 0) {{
                    return;
                }}

                const numChildren = node.children.length;

                // Compute base offsets, sorted from highest to lowest (top to bottom)
                let baseOffsets = [];
                if (numChildren % 2 === 1) {{
                    const mid = Math.floor(numChildren / 2);
                    baseOffsets = Array.from({{length: numChildren}}, (_, i) => i - mid);
                    baseOffsets.sort((a, b) => b - a);  // Top to bottom
                }} else {{
                    for (let i = 0; i < numChildren / 2; i++) {{
                        baseOffsets.push(i + 0.5);
                        baseOffsets.push(-(i + 0.5));
                    }}
                    baseOffsets.sort((a, b) => b - a);  // Top to bottom
                }}

                // Recursively layout children with temporary y=0
                node.children.forEach(child => {{
                    layoutTree(child, x + deltaX, 0, minDy, deltaX);
                }});

                // Get relative min and max y for each child's subtree
                const relMins = [];
                const relMaxs = [];
                node.children.forEach(child => {{
                    const [cMin, cMax] = getSubtreeMinMax(child);
                    relMins.push(cMin);
                    relMaxs.push(cMax);
                }});

                // Compute the required scale to ensure minDy between adjacent subtrees
                let scale = minDy;
                for (let i = 0; i < numChildren - 1; i++) {{
                    const relMinI = relMins[i];
                    const relMaxIp1 = relMaxs[i + 1];
                    const diffOffset = baseOffsets[i] - baseOffsets[i + 1];
                    const required = minDy - (relMinI - relMaxIp1);
                    if (required > 0) {{
                        const requiredScale = required / diffOffset;
                        scale = Math.max(scale, requiredScale);
                    }}
                }}

                // Set actual y for each child and shift their subtrees
                node.children.forEach((child, idx) => {{
                    const childY = node.data.y + baseOffsets[idx] * scale;
                    shiftSubtree(child, childY - child.data.y);
                }});
            }}

            // NEW: Helper to get min/max y in subtree
            function getSubtreeMinMax(node) {{
                let minY = node.data.y;
                let maxY = node.data.y;
                if (node.children) {{
                    node.children.forEach(child => {{
                        const [cMin, cMax] = getSubtreeMinMax(child);
                        minY = Math.min(minY, cMin);
                        maxY = Math.max(maxY, cMax);
                    }});
                }}
                return [minY, maxY];
            }}

            // NEW: Helper to shift y of subtree
            function shiftSubtree(node, deltaY) {{
                node.data.y += deltaY;
                if (node.children) {{
                    node.children.forEach(child => {{
                        shiftSubtree(child, deltaY);
                    }});
                }}
            }}

            function update(source) {{
                console.log('Running update function');
                
                if (!source) {{
                    console.error('Source is null or undefined');
                    return;
                }}
                
                // مقداردهی اولیه امن
                source.x0 = source.x0 || 0;
                source.y0 = source.y0 || 0;
                
                // Get visible nodes and links
                const nodes = root.descendants();
                const links = root.descendants().slice(1);
                
                console.log('Nodes:', nodes.length, 'Links:', links.length);
                
                // Assign pre-computed positions (from layoutTree)
                nodes.forEach(d => {{
                    d.x = d.data.x;
                    d.y = d.data.y;
                }});
                
                // Update nodes
                const node = g.selectAll('g.node')
                    .data(nodes, function(d) {{ return d.id || (d.id = ++nodeId); }});
                
                const nodeEnter = node.enter().append('g')
                    .attr('class', function(d) {{
                        return 'node ' + d.data.type; 
                    }})
                    .attr("transform", function(d) {{
                        const x = isNaN(source.x0) ? 0 : source.x0;
                        const y = isNaN(source.y0) ? 0 : source.y0;
                        return "translate(" + x + "," + y + ")"; 
                    }})
                    .on('click', function(event, d) {{
                        click(event, d);
                    }});
                
                // Add rectangles with FIXED sizing
                nodeEnter.append('rect')
                    .attr('width', function(d) {{
                        return Math.max(140, d.data.name.length * 9 + 50); // عرض بهتر
                    }})
                    .attr('height', NODE_HEIGHT)
                    .attr('x', function(d) {{
                        return -(Math.max(140, d.data.name.length * 9 + 50)) / 2;
                    }})
                    .attr('y', -NODE_HEIGHT/2) // وسط کردن vertical
                    .style('opacity', 0);
                
                // Add text labels - متن در وسط سلول
                nodeEnter.append('text')
                    .attr("x", 0)
                    .attr("y", 0)
                    .attr("dy", "0.35em")
                    .attr("text-anchor", "middle")
                    .style("dominant-baseline", "central")
                    .text(function(d) {{
                        return getNodeIcon(d.data.type) + " " + d.data.name; 
                    }})
                    .style('opacity', 0);
                
                // Merge and transition
                const nodeUpdate = nodeEnter.merge(node);
                
                nodeUpdate.transition()
                    .duration(600)
                    .attr("transform", function(d) {{ 
                        const x = isNaN(d.x) ? 0 : d.x;
                        const y = isNaN(d.y) ? 0 : d.y;
                        return "translate(" + x + "," + y + ")"; 
                    }});
                
                nodeUpdate.select('rect')
                    .transition()
                    .duration(600)
                    .style('opacity', 1);
                
                nodeUpdate.select('text')
                    .transition()
                    .duration(600)
                    .style('opacity', 1);
                
                // Remove exiting nodes
                const nodeExit = node.exit().transition()
                    .duration(600)
                    .attr("transform", function(d) {{
                        const x = isNaN(source.x) ? 0 : source.x;
                        const y = isNaN(source.y) ? 0 : source.y;
                        return "translate(" + x + "," + y + ")"; 
                    }})
                    .remove();
                
                nodeExit.select('rect').style('opacity', 0);
                nodeExit.select('text').style('opacity', 0);
                
                // Update links
                const link = g.selectAll('path.link')
                    .data(links, function(d) {{ return d.id; }});
                
                const linkEnter = link.enter().insert('path', "g")
                    .attr("class", "link")
                    .attr('d', function(d) {{
                        const o = {{
                            x: isNaN(source.x0) ? 0 : source.x0, 
                            y: isNaN(source.y0) ? 0 : source.y0
                        }};
                        return diagonalHorizontal(o, o);
                    }});
                
                const linkUpdate = linkEnter.merge(link);
                
                linkUpdate.transition()
                    .duration(600)
                    .attr('d', function(d) {{ 
                        return diagonalHorizontal(d, d.parent);
                    }});
                
                link.exit().transition()
                    .duration(600)
                    .attr('d', function(d) {{
                        const o = {{
                            x: isNaN(source.x) ? 0 : source.x, 
                            y: isNaN(source.y) ? 0 : source.y
                        }};
                        return diagonalHorizontal(o, o);
                    }})
                    .remove();
                
                // Store old positions
                nodes.forEach(function(d) {{
                    d.x0 = d.x;
                    d.y0 = d.y;
                }});
            }}

            function diagonalHorizontal(s, d) {{
                // SAFE diagonal path with coordinate validation
                const sx = isNaN(s.x) ? 0 : s.x;
                const sy = isNaN(s.y) ? 0 : s.y;
                const dx = isNaN(d.x) ? 0 : d.x;
                const dy = isNaN(d.y) ? 0 : d.y;
                
                // Horizontal tree diagonal path
                return `M ${{sx}} ${{sy}}
                        C ${{(sx + dx) / 2}} ${{sy}},
                        ${{(sx + dx) / 2}} ${{dy}},
                        ${{dx}} ${{dy}}`;
            }}

            function click(event, d) {{
                console.log('Node clicked:', d.data.name);
                
                if (d.children) {{
                    d._children = d.children;
                    d.children = null;
                }} else if (d._children) {{
                    d.children = d._children;
                    d._children = null;
                }}
                // NEW: Re-layout after change
                layoutTree(root, 0, 0, MIN_VERTICAL_SPACING, MIN_HORIZONTAL_SPACING);
                update(d);
            }}
            
            function collapseNode(d) {{
                if (d.children) {{
                    d._children = d.children;
                    d._children.forEach(collapseNode);
                    d.children = null;
                }}
            }}
            
            function expandNode(d) {{
                if (d._children) {{
                    d.children = d._children;
                    d.children.forEach(expandNode);
                    d._children = null;
                }}
            }}
            
            function getNodeIcon(type) {{
                const icons = {{
                    'project': '🗃️',
                    'subsystem': '📦', 
                    'module': '⚙️',
                    'connector': '🔌',
                    'pin': '📍'
                }};
                return icons[type] || '⚪';
            }}
            
            function expandAll() {{
                console.log('Expand all called');
                if (!root) {{
                    console.log('Root not found for expand');
                    return;
                }}
                
                expandNodeRecursively(root);
                isCurrentlyExpanded = true;
                
                // NEW: Re-layout after expand
                layoutTree(root, 0, 0, MIN_VERTICAL_SPACING, MIN_HORIZONTAL_SPACING);
                update(root);
                nodes.forEach(function(d){{ d.x0 = d.x; d.y0 = d.y; }});
                // فیت ملایم بعد از چینش
                requestAnimationFrame(() => fitView());
                setTimeout(() => {{
                    fitView();
                }}, 300);
            }}

            function expandNodeRecursively(d) {{
                if (d._children) {{
                    d.children = d._children;
                    d._children = null;
                }}
                
                if (d.children) {{
                    d.children.forEach(expandNodeRecursively);
                }}
            }}

            function collapseAll() {{
                console.log('Collapse all called');
                if (!root || !root.children) {{
                    console.log('Root or root.children not found for collapse');
                    return;
                }}
                
                root.children.forEach(collapseNode);
                isCurrentlyExpanded = false;
                
                // NEW: Re-layout after collapse
                layoutTree(root, 0, 0, MIN_VERTICAL_SPACING, MIN_HORIZONTAL_SPACING);
                update(root);
                nodes.forEach(function(d){{ d.x0 = d.x; d.y0 = d.y; }});
                // فیت ملایم بعد از چینش
                requestAnimationFrame(() => fitView());
            }}

            function anyCollapsed(d) {{
                if (!d) return false;
                if (d._children && d._children.length) return true;
                const kids = d.children || [];
                for (const c of kids) {{
                    if (anyCollapsed(c)) return true;
                }}
                return false;
            }}

            function toggleExpandCollapse() {{
                if (!root) return 'unknown';
                
                // Check current state more reliably
                const hasCollapsedNodes = anyCollapsed(root);
                
                if (hasCollapsedNodes || !isCurrentlyExpanded) {{
                    // There are collapsed nodes, so expand all
                    expandAll();
                    return 'expanded';
                }} else {{
                    // All are expanded, so collapse
                    collapseAll();
                    return 'collapsed';
                }}
            }}
            
            function fitView() {{
                if (!g.node()) return;
                try {{
                    // صبر تا رندر به‌روز شود
                    requestAnimationFrame(() => {{
                    const parent = svg.node().getBoundingClientRect();
                    const width  = parent.width;
                    const height = parent.height;
                    if (width <= 0 || height <= 0) return;

                    // جعبهٔ واقعی کل محتوا (گره‌ها + لینک‌ها)
                    const bbox = g.node().getBBox();
                    if (!isFinite(bbox.width) || !isFinite(bbox.height) ||
                        bbox.width <= 0 || bbox.height <= 0) {{
                        return;
                    }}

                    // پدینگ مناسب (نسبتی از اندازهٔ نما)
                    const padX = Math.max(32, width  * 0.06);
                    const padY = Math.max(24, height * 0.06);

                    const scaleX = (width  - 2 * padX) / bbox.width;
                    const scaleY = (height - 2 * padY) / bbox.height;
                    let scale = Math.min(scaleX, scaleY);

                    // هم‌راستا با scaleExtent
                    const minK = 0.05, maxK = 8;
                    scale = Math.max(minK, Math.min(maxK, scale));

                    // مرکز کردن محتوا
                    const tx = (width  / 2) - scale * (bbox.x + bbox.width  / 2);
                    const ty = (height / 2) - scale * (bbox.y + bbox.height / 2);

                    svg.transition()
                        .duration(650)
                        .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
                    }});
                }} catch (e) {{
                    console.error('Error in fitView:', e);
                }}
            }}

            function debounce(fn, ms){{ let t; return (...a)=>{{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }}; }}
            window.addEventListener('resize', debounce(() => fitView(), 150));

            
            function refreshTree() {{
                console.log('Refresh tree called');
                if (bridge) {{
                    bridge.refresh_tree_data();
                }}
            }}
            
            function updateZoomInfo() {{
                const zoomPercent = Math.round(currentZoom * 100);
                document.getElementById('zoom-info').textContent = 'Zoom: ' + zoomPercent + '%';
            }}
            
            function updateThemeColors(colors) {{
                document.body.style.background = colors.primary_dark;
                document.getElementById('tree-container').style.background = colors.primary_dark;
                document.getElementById('tree-container').style.borderColor = colors.primary_light;
                
                // Update link colors
                g.selectAll('.link').style('stroke', colors.primary_light);
            }}
            
            // Global node ID counter
            let nodeId = 0;
            
            // Expose functions to Python
            window.expandAll = expandAll;
            window.collapseAll = collapseAll;
            window.toggleExpandCollapse = toggleExpandCollapse;
            window.fitView = fitView;
            window.refreshTree = refreshTree;
            
            let nodeWidth = 200;  // Increased to 200 for longer labels
            let nodeHeight = 40;
            
            // Dynamic adjustment based on node depth and children
            function adjustNodeSize(d) {{
                let width = nodeWidth;
                let height = nodeHeight;
                if (d.children && d.children.length > 0) {{
                    height += (d.children.length - 1) * 10;  // Extra height per child
                }}
                if (d.depth > 2) {{
                    width += 20;  // Extra width for deeper nodes
                }}
                return [width, height];
            }}
            
            // Initialize WebChannel and handle tree data
            new QWebChannel(qt.webChannelTransport, function(channel) {{
                bridge = channel.objects.bridge;
                bridge.tree_data_ready.connect(function(data) {{
                    try {{
                        const parsedData = JSON.parse(data);
                        if (!parsedData) throw new Error('Invalid tree data');
                        
                        // Update the tree
                        treeData = parsedData;
                        updateTree();
                        fitView();  // Ensure fit on initial load and refresh
                        
                    }} catch (e) {{
                        console.error('Failed to parse tree data:', e);
                    }}
                }});
                bridge.theme_changed.connect(function(data) {{
                    updateThemeColors(JSON.parse(data));
                }});
                bridge.get_tree_data();  // Initial data request
            }});
        </script>
    </body>
    </html>
            '''


    def setup_connections(self):
        """Setup signal connections - Fixed function calls"""
        self.toggle_expand_btn.clicked.connect(lambda: QTimer.singleShot(100, self.toggle_expand_collapse))
        self.refresh_btn.clicked.connect(self.refresh_tree)
        self.fit_btn.clicked.connect(lambda: QTimer.singleShot(100, self.fit_view))
        
        # Export buttons
        self.export_pdf_btn.clicked.connect(self.export_to_pdf)
        self.export_png_btn.clicked.connect(self.export_to_png)

    def _initialize_tree(self):
        """Initialize tree after web view is loaded - Fixed timing"""
        def delayed_init():
            try:
                self.bridge.get_tree_data()
                # Add delay to ensure data is processed and fitView can be called
                QTimer.singleShot(100, self.fit_view)
            except Exception as e:
                print(f"Error initializing tree: {e}")
                    
        QTimer.singleShot(1000, delayed_init)

    def toggle_expand_collapse(self):
        """Toggle expand/collapse all nodes and sync button text - FIXED"""
        try:
            code = """
                (function(){
                    if (typeof toggleExpandCollapse !== 'undefined') {
                        const result = toggleExpandCollapse();
                        console.log('Toggle result:', result);
                        return result;
                    } else {{
                        console.log('toggleExpandCollapse function not found');
                        return 'unknown';
                    }}
                })();
            """
            self.web_view.page().runJavaScript(code, self._update_toggle_label)
        except Exception as e:
            print(f"Error in toggle_expand_collapse: {e}")

    def _update_toggle_label(self, state):
        """Update toggle button label based on current state"""
        try:
            if hasattr(self, 'toggle_expand_btn'):
                if state == 'expanded':
                    self.toggle_expand_btn.setText("Collapse All")
                elif state == 'collapsed':
                    self.toggle_expand_btn.setText("Expand All")
                else:
                    self.toggle_expand_btn.setText("Toggle")
                print(f"Button label updated to: {self.toggle_expand_btn.text()}")
        except Exception as e:
            print(f"Error updating toggle label: {e}")

    # Fixed function names to match JavaScript calls
    def expand_all(self):
        """Expand all nodes - Fixed with better error handling"""
        try:
            code = """
                if(typeof expandAll !== 'undefined') {
                    expandAll();
                    console.log('Expand all executed');
                } else {
                    console.log('expandAll function not found');
                }
            """
            self.web_view.page().runJavaScript(code)
        except Exception as e:
            print(f"Error in expand_all: {e}")

    def collapse_all(self):
        """Collapse all nodes - Fixed with better error handling"""
        try:
            code = """
                if(typeof collapseAll !== 'undefined') {
                    collapseAll();
                    console.log('Collapse all executed');
                } else {
                    console.log('collapseAll function not found');
                }
            """
            self.web_view.page().runJavaScript(code)
        except Exception as e:
            print(f"Error in collapse_all: {e}")

    def refresh_tree(self):
        """Refresh tree data"""
        self.bridge.refresh_tree_data()

    def fit_view(self):
        """Fit tree to view - Fixed with error handling"""
        try:
            code = """
                if(typeof fitView !== 'undefined') {
                    fitView();
                    console.log('Fit view executed');
                } else {
                    console.log('fitView function not found');
                }
            """
            self.web_view.page().runJavaScript(code)
        except Exception as e:
            print(f"Error in fit_view: {e}")

    # Keyboard event handling
    def keyPressEvent(self, event):
        """Handle keyboard shortcuts"""
        key = event.key()
        if key == Qt.Key_Space:
            self.fit_view()
            event.accept()
        elif key == Qt.Key_E:
            self.expand_all()
            event.accept()
        elif key == Qt.Key_C:
            self.collapse_all()
            event.accept()
        elif key == Qt.Key_R:
            self.refresh_tree()
            event.accept()
        else:
            super().keyPressEvent(event)
            
    def export_to_png(self):
        """Export current view to high-quality PNG (no fit, exact current view)"""
        try:
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Component Tree to PNG",
                f"component_tree_{self._get_timestamp()}.png",
                "PNG Files (*.png);;All Files (*)"
            )
            if not file_path:
                return

            progress = QProgressDialog("Creating PNG...", "Cancel", 0, 100, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            progress.setValue(30)

            # Capture EXACT current view at 4x resolution
            pixmap = self.web_view.grab()
            
            progress.setValue(60)
            
            # Scale up for high quality (4x)
            high_res_pixmap = pixmap.scaled(
                pixmap.width() * 4,
                pixmap.height() * 4,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            
            progress.setValue(80)
            
            # Save with maximum quality
            if not high_res_pixmap.save(file_path, "PNG", 100):
                progress.close()
                QMessageBox.critical(self, "PNG Export Error", "Failed to save PNG file.")
                return
            
            progress.setValue(100)
            progress.close()
            
            file_size = os.path.getsize(file_path) / (1024 * 1024)
            
            QMessageBox.information(
                self,
                "PNG Export Successful",
                f"PNG saved successfully!\n\n"
                f"File: {os.path.basename(file_path)}\n"
                f"Resolution: {high_res_pixmap.width()} x {high_res_pixmap.height()} pixels\n"
                f"Size: {file_size:.2f} MB\n\n"
                f"High quality 4x scaled image."
            )
            
        except Exception as e:
            if 'progress' in locals():
                progress.close()
            QMessageBox.critical(self, "PNG Export Error", f"Failed to export: {str(e)}")


    def export_to_pdf(self):
        """Export current view to PDF"""
        try:
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Component Tree to PDF",
                f"component_tree_{self._get_timestamp()}.pdf",
                "PDF Files (*.pdf);;All Files (*)"
            )
            if not file_path:
                return

            progress = QProgressDialog("Creating PDF...", "Cancel", 0, 100, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            progress.setValue(20)

            # Capture current view
            pixmap = self.web_view.grab()
            
            progress.setValue(40)
            
            # Create printer - FIXED constructor
            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(file_path)
            
            # Set page orientation based on aspect ratio
            img_width = pixmap.width()
            img_height = pixmap.height()
            
            if img_width > img_height:
                printer.setPageOrientation(QPageLayout.Landscape)
            else:
                printer.setPageOrientation(QPageLayout.Portrait)
            
            printer.setPageSize(QPageSize(QPageSize.A4))
            
            progress.setValue(60)
            
            # Create painter
            painter = QPainter()
            if not painter.begin(printer):
                progress.close()
                QMessageBox.critical(self, "PDF Error", "Failed to create PDF.")
                return
            
            # Get page size in pixels
            page_rect = printer.pageRect(QPrinter.DevicePixel)
            
            # Scale image to fit page
            scaled_pixmap = pixmap.scaled(
                int(page_rect.width() * 0.95),
                int(page_rect.height() * 0.95),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            
            progress.setValue(80)
            
            # Center image on page
            x = (page_rect.width() - scaled_pixmap.width()) / 2
            y = (page_rect.height() - scaled_pixmap.height()) / 2
            
            painter.drawPixmap(int(x), int(y), scaled_pixmap)
            painter.end()
            
            progress.setValue(100)
            progress.close()
            
            file_size = os.path.getsize(file_path) / (1024 * 1024)
            
            QMessageBox.information(
                self,
                "PDF Export Successful",
                f"PDF created successfully!\n\n"
                f"File: {os.path.basename(file_path)}\n"
                f"Size: {file_size:.2f} MB"
            )
            
        except Exception as e:
            if 'progress' in locals():
                progress.close()
            QMessageBox.critical(self, "PDF Export Error", f"Failed to export: {str(e)}\n\nDetails: {type(e).__name__}")  
                    
    def _capture_png(self, file_path, progress):
        """Capture PNG from web view"""
        try:
            progress.setValue(50)
            
            # Capture the web view content at higher resolution (scale up)
            original_size = self.web_view.size()
            self.web_view.resize(original_size.width() * 2, original_size.height() * 2)  # 2x scale for higher DPI
            QTimer.singleShot(200, lambda: self._perform_grab(file_path, progress, original_size))  # Delay for resize
            
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "PNG Capture Error", f"Failed to capture PNG: {str(e)}")

    def _perform_grab(self, file_path, progress, original_size):
        """Helper to perform the grab after resize"""
        try:
            pixmap = self.web_view.grab()
            self.web_view.resize(original_size)  # Restore size
            
            progress.setValue(80)
            
            # Save with high quality
            if not pixmap.save(file_path, "PNG", 100):
                progress.close()
                QMessageBox.critical(self, "Export Error", "Failed to save PNG file.")
                return
            
            progress.setValue(100)
            progress.close()
            
            # Show success message
            file_size = os.path.getsize(file_path) / (1024 * 1024)
            pixmap_size = pixmap.size()
            
            QMessageBox.information(
                self,
                "PNG Export Successful",
                f"Component Tree PNG Export Complete!\n\n"
                f"File: {os.path.basename(file_path)}\n"
                f"Dimensions: {pixmap_size.width()} x {pixmap_size.height()} pixels\n"
                f"Source: D3.js Interactive Layout\n"
                f"Size: {file_size:.1f} MB\n\n"
                f"Enhanced Features:\n"
                f"  • Perfect D3.js tree algorithm\n"
                f"  • Zero node overlapping\n"
                f"  • Professional spacing\n"
                f"  • Interactive capture"
            )
            
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "PNG Capture Error", f"Failed to capture PNG: {str(e)}")
            
    def _get_timestamp(self):
        """Get timestamp for filename"""
        from datetime import datetime
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def showEvent(self, event):
        """Handle show event"""
        super().showEvent(event)

    def closeEvent(self, event):
        if hasattr(self, 'temp_html_file'):
            try:
                os.unlink(self.temp_html_file)
            except:
                pass
        
        if hasattr(self, 'temp_dir'):
            try:
                shutil.rmtree(self.temp_dir)
            except:
                pass
                
        super().closeEvent(event)

    def _on_page_loaded(self, ok: bool):
        """Handle page load completion"""
        self.page_ready = bool(ok)
        for btn in [getattr(self, 'toggle_expand_btn', None), 
                    getattr(self, 'refresh_btn', None),
                    getattr(self, 'fit_btn', None)]:
            if btn is not None:
                btn.setEnabled(self.page_ready)
        
        print(f"Page loaded: {ok}, buttons enabled: {self.page_ready}")


# Factory function for backward compatibility
def create_modern_component_tree_tab():
    return ComponentTreeTab()


class ComponentTreeWindow(QWidget):
    """Component Tree Window with JavaScript Integration"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Component Tree - Fixed Version")
        self.setGeometry(100, 100, 1400, 900)
        
        self.apply_window_style()
        style_manager.theme_changed.connect(self.on_theme_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        tree_tab = ComponentTreeTab()
        layout.addWidget(tree_tab)

    def apply_window_style(self):
        """Apply window styling"""
        style = f"""
            QWidget {{
                background: {theme_manager.get_color('primary_dark')};
            }}
        """
        self.setStyleSheet(style)

    def on_theme_changed(self, theme_name):
        """Handle theme changes"""
        self.apply_window_style()


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication, QStyleFactory
    import sys
    from database import init_db

    init_db()
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create('Fusion'))
    
    # Check if QWebEngineView is available
    try:
        from PyQt5.QtWebEngineWidgets import QWebEngineView
        print("QWebEngineView is available!")
    except ImportError:
        print("QWebEngineView not available. Install with: pip install PyQtWebEngine")
        sys.exit(1)
    
    window = ComponentTreeWindow()
    window.show()
    sys.exit(app.exec_())