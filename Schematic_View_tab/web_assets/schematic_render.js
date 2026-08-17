// Schematic_View_tab/web_assets/schematic_render.js
//
// SVG-based schematic renderer. Renders modules + connectors (all 4 sides:
// left/right/top/bottom) + pins + interfaces, with pan/zoom, module drag
// (auto-saved), live orthogonal re-routing during drag (schematic_routing.js),
// pin-to-pin drag to create new connections, click-to-select + Delete key,
// right-click context menus (module/connector/pin: add/rename/delete), and
// double-click on a connector to reorder its pins via the native
// PinOrderDialog (round-trips through the bridge since that's a real Qt
// modal dialog).
//
// NOT in this version yet: multi-select, undo/redo, manual per-segment
// wire dragging (SmartOrthogonalConnector's _EnhancedSegmentItem), and
// ConnectorMovementManager's movement-permission rules.

let bridge = null;
let svg, g, zoom;
let currentZoom = 1;
let sceneData = { modules: [], connectors: [], interfaces: [] };

// Multi-selection sets (rubber-band & shift-click)
let selectedModuleIds = new Set();
let selectedInterfaceIds = new Set();
let selectionRect = null;      // { startX, startY, currentX, currentY } – active rubber band
let isSpaceDown = false;       // for space+drag pan (Photoshop-style)
let batchDragState = null;     // { startPositions: {modId: {x, y}} } during batch drag
let isSelecting = false;       // rubber-band selection in progress; disables d3-zoom pan
let _selectionDragCompleted = false;  // guard to prevent click handler from clearing just-made selection
let connectDrag = null; // { fromPinId, tempPath }
let toastCount = 0;

// True when the signed-in user is NOT the system admin: the schematic view
// is read-only for everyone else (subsystem admins included). Set from the
// `readonly` field of every scene payload the bridge sends.
let IS_READONLY = false;

// Cached power lookup tables — rebuilt once per render() call to avoid
// O(connectors × modules × interfaces) work repeated for every module.
let _powerLookupCache = null;

const MODULE_MIN_WIDTH = 120;
const MODULE_MIN_HEIGHT = 60;
const PIN_RADIUS = 7;      // larger hitbox for easier pin-to-pin dragging
// const PIN_HOVER_RADIUS = 11;
const CONNECTOR_STUB = 8;  // distance from module edge to connector tip (diamond)
const PIN_EDGE_OFFSET = 24;  // distance from module edge to pin body midpoint

// Corner offset to avoid placing connectors too close to corners
const CORNER_OFFSET = 0.08;
const CONNECTOR_MARGIN = CONNECTOR_STUB; // alias (kept for backward compat, use CONNECTOR_STUB or PIN_EDGE_OFFSET directly)
const PIN_HALF_STEP = 14;  // half-step between adjacent pins (full step = 28px) — increase to space out pin labels more
const CONNECTOR_GAP = 12;  // minimum gap between connector edges on same side
const CONNECTOR_BBOX_PAD = 8; // extra padding around connector bounding box
const EDGE_MARGIN = 8; // min distance from edge corners for connector placement

// GRID_SIZE and snapToGrid are defined in schematic_routing.js (loaded first)

// Real DB side values are 'left' | 'right' | 'top' | 'bottom' (default 'top').
// normal = direction the stub sticks out; tangent = direction pins spread along the stub.
const SIDE_AXIS = {
    left: { normal: { x: -1, y: 0 }, tangent: { x: 0, y: 1 } },
    right: { normal: { x: 1, y: 0 }, tangent: { x: 0, y: 1 } },
    top: { normal: { x: 0, y: -1 }, tangent: { x: 1, y: 0 } },
    bottom: { normal: { x: 0, y: 1 }, tangent: { x: 1, y: 0 } },
};

function hexToRgba(hex, alpha) {
    if (!hex || hex.length < 7) return 'rgba(39, 174, 96, ' + alpha + ')';
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    if (isNaN(r) || isNaN(g) || isNaN(b)) return 'rgba(39, 174, 96, ' + alpha + ')';
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
}

function normalizeSide(side) {
    return SIDE_AXIS[side] ? side : 'top';
}

// Point on a module's boundary, in module-local coordinates, at fraction
// t (0..1) along that edge.
function edgePoint(module, side, t) {
    const w = Math.max(MODULE_MIN_WIDTH, module.width);
    const h = Math.max(MODULE_MIN_HEIGHT, module.height);
    switch (normalizeSide(side)) {
        case 'right': return { x: w, y: t * h };
        case 'top': return { x: t * w, y: 0 };
        case 'bottom': return { x: t * w, y: h };
        case 'left':
        default: return { x: 0, y: t * h };
    }
}

// Where a connector's stub line should start (on the module boundary),
// derived from its already-known tip position (c.x, c.y) + side. Because
// left/right and top/bottom are both axis-aligned, one coordinate of the
// tip is always "the edge distance" and can just be replaced with 0/w/h.
function connectorEdgeAnchor(module, c) {
    const w = Math.max(MODULE_MIN_WIDTH, module.width);
    const h = Math.max(MODULE_MIN_HEIGHT, module.height);
    switch (normalizeSide(c.side)) {
        case 'right': return { x: w, y: c.y };
        case 'top': return { x: c.x, y: 0 };
        case 'bottom': return { x: c.x, y: h };
        case 'left':
        default: return { x: 0, y: c.y };
    }
}

document.addEventListener('DOMContentLoaded', function () {
    setupScene();
    initializeWebChannel();
    setupKeyboardShortcuts();
    setupPanModifiers();
});

// ---------------------------------------------------------------------
// WebChannel wiring (same contract as Component_Tree_Window.py)
// ---------------------------------------------------------------------
function initializeWebChannel() {
    new QWebChannel(qt.webChannelTransport, function (channel) {
        bridge = channel.objects.bridge;

        bridge.scene_data_ready.connect(function (jsonData) {
            try {
                sceneData = JSON.parse(jsonData);
                IS_READONLY = !!(sceneData.readonly);
                render(sceneData);
            } catch (e) {
                console.error('Failed to parse scene data:', e);
            }
        });

        bridge.theme_changed.connect(function (themeJson) {
            try {
                updateThemeColors(JSON.parse(themeJson));
            } catch (e) {
                console.error('Failed to parse theme data:', e);
            }
        });

        bridge.save_finished.connect(function (success, message) {
            console.log('[schematic] save_finished:', success, message);
            showToast(message, success ? 'success' : 'error');
        });

        setTimeout(function () {
            bridge.get_scene_data();
        }, 100);

        // Safety timeout: hide loading overlay if data never arrives
        setTimeout(function () {
            hideLoading();
        }, 10000);
    });
}

function updateThemeColors(colors) {
    const root = document.documentElement.style;
    if (colors.primary_dark) root.setProperty('--primary-dark', colors.primary_dark);
    if (colors.primary_light) root.setProperty('--primary-light', colors.primary_light);
    if (colors.accent) root.setProperty('--accent', colors.accent);
    if (colors.text_primary) root.setProperty('--text-primary', colors.text_primary);
}

// ---------------------------------------------------------------------
// Scene / zoom setup
// ---------------------------------------------------------------------
function setupScene() {
    svg = d3.select('#schematic-svg');

    zoom = d3.zoom()
        .scaleExtent([0.1, 6])
        .filter(function (event) {
            // Allow standard zoom/pan interactions (scroll wheel, middle mouse, ctrl/meta)
            if (event.type === 'wheel' || event.type === 'dblclick' ||
                event.type === 'mousedown' && (event.button === 1 || event.ctrlKey || event.metaKey)) {
                return true;
            }
            // Space+drag = pan (Photoshop/Inkscape style)
            if (event.type === 'mousedown' && isSpaceDown) {
                return true;
            }
            // Rubber-band selection in progress — no panning
            if (isSelecting) return false;
            // Never start pan on right-click (button 2) — it interferes with
            // context menu events on Windows (QWebEngine) where D3 zoom would
            // otherwise swallow subsequent clicks on the custom context menu.
            if (event.type === 'mousedown' && event.button === 2) {
                return false;
            }
            // Don't start pan on elements that have their own drag behavior
            var target = event.target;
            if (target) {
                var cls = target.getAttribute && target.getAttribute('class');
                if (cls && typeof cls === 'string') {
                    if (cls.indexOf('interface-path') >= 0 ||
                        cls.indexOf('interface-drag-handle') >= 0 ||
                        cls.indexOf('pin-circle') >= 0 ||
                        cls.indexOf('connector-hitbox') >= 0 ||
                        cls.indexOf('connector-drag-handle') >= 0 ||
                        cls.indexOf('resize-handle') >= 0 ||
                        cls.indexOf('connector-interactive') >= 0) {
                        return false;
                    }
                }
            }
            // Plain left-click on blank canvas = start rubber-band selection, not pan
            if (event.type === 'mousedown' && event.button === 0 && !event.ctrlKey && !event.metaKey) {
                return false;
            }
            return true;
        })
        .on('zoom', function (event) {
            g.attr('transform', event.transform);
            currentZoom = event.transform.k;
            updateZoomInfo();
        });

    svg.call(zoom);
    g = svg.append('g').attr('class', 'scene-root');
}

function updateZoomInfo() {
    const el = document.getElementById('zoom-info');
    if (el) el.textContent = 'Zoom: ' + Math.round(currentZoom * 100) + '%';
}

function setupPanModifiers() {
    document.addEventListener('keydown', function (event) {
        if (event.key === ' ' && event.target.tagName !== 'INPUT') {
            isSpaceDown = true;
            document.body.style.cursor = 'grab';
            event.preventDefault();
        }
    });
    document.addEventListener('keyup', function (event) {
        if (event.key === ' ') {
            isSpaceDown = false;
            document.body.style.cursor = '';
        }
    });
}

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', function (event) {
        if (event.target.tagName === 'INPUT') return;
        if (event.key === ' ' && !isSpaceDown) {
            // First press of space goes to pan mode (handled by setupPanModifiers).
            // If space is already down (from pan modifier), don't fit view.
            // This allows space+drag for pan without also triggering fitView.
            return;
        } else if (event.key === 'Delete' || event.key === 'Backspace') {
            handleDeleteKey();
            event.preventDefault();
        } else if (event.key === 'Escape') {
            clearSelection();
            cancelConnectDrag();
        } else if (event.key.toLowerCase() === 'a' && (event.ctrlKey || event.metaKey)) {
            // Ctrl+A / Cmd+A = select all
            selectAll();
            event.preventDefault();
        }
    });
    document.addEventListener('keyup', function (event) {
        if (event.key === ' ') {
            isSpaceDown = false;
            document.body.style.cursor = '';
        }
    });

    // Left-click on empty canvas starts rubber-band selection
    svg.on('mousedown.selection', function (event) {
        if (event.button !== 0) return;          // left button only
        if (isSpaceDown) return;                 // space+drag = pan
        // Only start selection if clicking directly on the SVG (not on a module/connector/etc)
        if (event.target !== svg.node()) return;
        event.stopPropagation();
        var pt = d3.pointer(event, g.node());
        startSelectionRect(pt[0], pt[1]);
    });

    svg.on('click', function (event) {
        // If a rubber-band selection drag just completed, don't clear (finishSelectionRect
        // already handled it).  This flag is set by finishSelectionRect.
        if (_selectionDragCompleted) {
            _selectionDragCompleted = false;
            return;
        }
        // Clicking empty canvas (not a module/pin/interface) clears selection.
        if (event.target === svg.node()) {
            clearSelection();
        }
    });

    // Right-click on empty canvas shows 'Add Module' (system admin only)
    svg.on('contextmenu', function (event) {
        if (event.target !== svg.node() && !event.target.classList.contains('scene-root')) return;
        event.preventDefault();
        event.stopPropagation();
        if (IS_READONLY) {
            showToast('The schematic view is read-only for your account.', 'error');
            return;
        }
        showContextMenu(event, [
            { icon: '\u2795', label: 'Add Module', action: function () { showModuleDialog(); } },
        ]);
    });
}

function handleDeleteKey() {
    if (IS_READONLY) return;
    // Delete all selected interfaces first
    if (selectedInterfaceIds.size > 0) {
        var ids = Array.from(selectedInterfaceIds);
        if (bridge && confirm('Delete ' + ids.length + ' selected connection(s)?')) {
            ids.forEach(function (ifaceId) {
                bridge.delete_interface(ifaceId);
            });
        }
        clearSelection();
        return;
    }
    // Delete all selected modules
    if (selectedModuleIds.size > 0) {
        var ids = Array.from(selectedModuleIds);
        if (bridge && confirm('Delete ' + ids.length + ' selected module(s) and all their connections?')) {
            ids.forEach(function (modId) {
                bridge.delete_module(modId);
            });
        }
        clearSelection();
    }
}

function clearSelection() {
    selectedModuleIds.clear();
    selectedInterfaceIds.clear();
    removeSelectionRect();
    selectionRect = null;
    g.selectAll('.module-box').classed('selected', false);
    g.selectAll('.interface-path').classed('selected', false);
}

// ---------------------------------------------------------------------
// Rubber-band / rectangle selection
// ---------------------------------------------------------------------
function startSelectionRect(x, y) {
    isSelecting = true;  // prevent d3-zoom from panning
    removeSelectionRect();
    selectionRect = { startX: x, startY: y, currentX: x, currentY: y };
    // Draw initial rect
    drawSelectionRect();
    svg.on('mousemove.selection', function (event) {
        if (!selectionRect) return;
        var pt = d3.pointer(event, g.node());
        selectionRect.currentX = pt[0];
        selectionRect.currentY = pt[1];
        drawSelectionRect();
    });
    svg.on('mouseup.selection', function () {
        finishSelectionRect();
        svg.on('mousemove.selection', null);
        svg.on('mouseup.selection', null);
        isSelecting = false;
    });
    // Also cancel on mouseleave
    svg.on('mouseleave.selection', function () {
        finishSelectionRect();
        svg.on('mousemove.selection', null);
        svg.on('mouseup.selection', null);
        svg.on('mouseleave.selection', null);
        isSelecting = false;
    });
}

function drawSelectionRect() {
    removeSelectionRect();
    if (!selectionRect) return;
    var x = Math.min(selectionRect.startX, selectionRect.currentX);
    var y = Math.min(selectionRect.startY, selectionRect.currentY);
    var w = Math.abs(selectionRect.currentX - selectionRect.startX);
    var h = Math.abs(selectionRect.currentY - selectionRect.startY);
    if (w < 2 || h < 2) return; // too small to draw
    g.append('rect')
        .attr('class', 'selection-rect')
        .attr('x', x).attr('y', y)
        .attr('width', w).attr('height', h)
        .attr('fill', 'rgba(91, 141, 239, 0.12)')
        .attr('stroke', '#5b8def')
        .attr('stroke-width', 1.5)
        .attr('stroke-dasharray', '5,3')
        .attr('pointer-events', 'none');
}

function removeSelectionRect() {
    g.selectAll('.selection-rect').remove();
}

function finishSelectionRect() {
    if (!selectionRect) return;
    var x = Math.min(selectionRect.startX, selectionRect.currentX);
    var y = Math.min(selectionRect.startY, selectionRect.currentY);
    var w = Math.abs(selectionRect.currentX - selectionRect.startX);
    var h = Math.abs(selectionRect.currentY - selectionRect.startY);
    removeSelectionRect();

    // If the rect is tiny, treat as a click (clear selection)
    if (w < 4 && h < 4) {
        clearSelection();
        selectionRect = null;
        return;
    }

    // Find items inside the rectangle
    var newModIds = new Set();
    var newIfaceIds = new Set();

    // Check each module — intersect with selection rect
    sceneData.modules.forEach(function (m) {
        // Module bounding box in scene coords
        var mx = m.x, my = m.y, mw = Math.max(m.width || MODULE_MIN_WIDTH, MODULE_MIN_WIDTH), mh = Math.max(m.height || MODULE_MIN_HEIGHT, MODULE_MIN_HEIGHT);
        // Check if module rect overlaps selection rect
        if (mx < x + w && mx + mw > x && my < y + h && my + mh > y) {
            newModIds.add(m.id);
        }
    });

    // Check each interface — points inside rect
    sceneData.interfaces.forEach(function (iface) {
        if (!iface.points) return;
        for (var k = 0; k < iface.points.length; k++) {
            var pt = iface.points[k];
            var px = Array.isArray(pt) ? pt[0] : pt.x;
            var py = Array.isArray(pt) ? pt[1] : pt.y;
            if (px >= x && px <= x + w && py >= y && py <= y + h) {
                newIfaceIds.add(iface.id);
                break; // one point inside is enough
            }
        }
    });

    // Update selection (no shift = replace; but shift is not available on SVG click, so we replace)
    selectedModuleIds = newModIds;
    selectedInterfaceIds = newIfaceIds;

    // Update visual selection state
    g.selectAll('.module-box').classed('selected', function (d) { return selectedModuleIds.has(d.id); });
    g.selectAll('.interface-path').classed('selected', function () {
        var id = Number(d3.select(this).attr('data-interface-id'));
        return selectedInterfaceIds.has(id);
    });

    _selectionDragCompleted = true;
    selectionRect = null;
}

function selectAll() {
    selectedModuleIds = new Set(sceneData.modules.map(function (m) { return m.id; }));
    selectedInterfaceIds = new Set(sceneData.interfaces.map(function (iface) { return iface.id; }));
    g.selectAll('.module-box').classed('selected', function (d) { return selectedModuleIds.has(d.id); });
    g.selectAll('.interface-path').classed('selected', function () {
        var id = Number(d3.select(this).attr('data-interface-id'));
        return selectedInterfaceIds.has(id);
    });
}

// ---------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------
function render(scene) {
    g.selectAll('*').remove();

    // Clear cached power lookup so it gets rebuilt fresh for this scene
    _powerLookupCache = null;

    // Normalize module dimensions once to prevent NaN from null/undefined DB values.
    // Use Number() coercion to catch truthy-but-non-numeric values (e.g. strings).
    scene.modules.forEach(function (m) {
        var nw = Number(m.width);
        m.width = (!isNaN(nw) && nw > 0) ? Math.max(nw, MODULE_MIN_WIDTH) : MODULE_MIN_WIDTH;
        var nh = Number(m.height);
        m.height = (!isNaN(nh) && nh > 0) ? Math.max(nh, MODULE_MIN_HEIGHT) : MODULE_MIN_HEIGHT;
        if (typeof m.x !== 'number' || isNaN(m.x)) m.x = 0;
        if (typeof m.y !== 'number' || isNaN(m.y)) m.y = 0;
    });
    // Also normalize connector positions — null from DB becomes a default
    scene.connectors.forEach(function (c) {
        if (typeof c.x !== 'number' || isNaN(c.x)) c.x = 0;
        if (typeof c.y !== 'number' || isNaN(c.y)) c.y = 0;
    });

    // Only run autoSizeModules fully on the very first render (from data load),
    // not on subsequent renders (e.g. tab switch back). The module dimensions
    // saved to DB by resize or creation are authoritative. On subsequent
    // renders autoSizeModules still runs but skips all modules because they
    // have _sized = true (set below).
    if (!render.hasAutoSizedOnce) {
        render.hasAutoSizedOnce = true;
    }
    autoSizeModules(scene);

    // Mark all modules as explicitly sized so resizeBehavior.on('end')
    // doesn't need to set this — we track it via hasAutoSizedOnce.
    scene.modules.forEach(function (m) {
        m._sized = true;
    });

    assignFallbackConnectorPositions(scene);

    // Second-pass safety net: catch any connector that still has NaN after
    // assignFallbackConnectorPositions (e.g. orphaned connectors whose module
    // was filtered out). These would otherwise produce SVG NaN errors.
    scene.connectors.forEach(function (c) {
        if (typeof c.x !== 'number' || isNaN(c.x) || typeof c.y !== 'number' || isNaN(c.y)) {
            if (c.module_id != null) {
                // Try to find the module and put the connector at a default position
                var m = scene.modules.find(function (m) { return String(m.id) === String(c.module_id); });
                if (m) {
                    var normSide = normalizeSide(c.side);
                    var ep = edgePoint(m, normSide, 0.5);
                    var nml = SIDE_AXIS[normSide].normal;
                    c.x = ep.x + nml.x * CONNECTOR_STUB;
                    c.y = ep.y + nml.y * CONNECTOR_STUB;
                }
            }
        }
        if (typeof c.x !== 'number' || isNaN(c.x)) c.x = 0;
        if (typeof c.y !== 'number' || isNaN(c.y)) c.y = 0;
    });

    const pinLookup = buildPinLookup(scene);

    // Draw subsystem halos next (behind modules and wires)
    renderSubsystemHalos(scene);

    renderInterfaces(scene, pinLookup, null); // null = recompute all routes on full render
    renderModules(scene);

    // Hide loading overlay once we have rendered content
    hideLoading();

    // Update the power HUD with the current scene's total connected power
    updatePowerHud(scene);

    updateReadOnlyBadge(IS_READONLY);

    if (!render.hasFitOnce) {
        setTimeout(() => { fitView(); render.hasFitOnce = true; }, 300);
        // Show the rectangle-select hint briefly on first render
        showSelectionHint();
    }
}
render.hasFitOnce = false;

// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// Subsystem halos — draw a colored bounding box behind modules grouped
// by subsystem_id, with a label indicating the subsystem name.
// Also encompasses interface (wire) paths so wires don't poke out of the halo.
// ---------------------------------------------------------------------

// Build a lookup: pin_id -> subsystem_id (or 0 for ungrouped)
function _buildPinToSubsystem(scene) {
    var pinModMap = {};
    scene.connectors.forEach(function (c) {
        var mod = scene.modules.find(function (m) { return String(m.id) === String(c.module_id); });
        if (!mod) return;
        var ssId = mod.subsystem_id || 0;
        c.pins.forEach(function (p) { pinModMap[p.id] = ssId; });
    });
    return pinModMap;
}

// Extend (minX,minY,maxX,maxY) to encompass all points of interfaces whose
// pins belong to the given subsystem.
// pinModMap is a pre-built pin_id -> subsystem_id lookup (built once per frame).
function _extendBoundsWithInterfaces(scene, ssId, bounds, pinModMap) {
    scene.interfaces.forEach(function (iface) {
        var aSs = pinModMap[iface.from_pin];
        var bSs = pinModMap[iface.to_pin];
        // Include interface if either pin belongs to this subsystem
        if (aSs !== ssId && bSs !== ssId) return;
        if (!iface.points || !iface.points.length) return;
        iface.points.forEach(function (pt) {
            var x = pt[0] !== undefined ? pt[0] : pt.x;
            var y = pt[1] !== undefined ? pt[1] : pt.y;
            if (x < bounds.minX) bounds.minX = x;
            if (y < bounds.minY) bounds.minY = y;
            if (x > bounds.maxX) bounds.maxX = x;
            if (y > bounds.maxY) bounds.maxY = y;
        });
    });
}

// Compute the enlarged halo bounding box for a subsystem, including wiring.
// pinModMap is a pre-built pin_id -> subsystem_id lookup.
function _computeHaloBounds(scene, group, pinModMap) {
    var padding = 24;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    
    // Start from module positions
    group.modules.forEach(function (m) {
        var right = m.x + m.width;
        var bottom = m.y + m.height;
        if (m.x < minX) minX = m.x;
        if (m.y < minY) minY = m.y;
        if (right > maxX) maxX = right;
        if (bottom > maxY) maxY = bottom;
    });
    
    // Extend with interface wiring — find which ssId this group represents
    var ssId = group.modules.length > 0 ? (group.modules[0].subsystem_id || 0) : 0;
    
    var bounds = { minX: minX, minY: minY, maxX: maxX, maxY: maxY };
    _extendBoundsWithInterfaces(scene, ssId, bounds, pinModMap);
    
    bounds.minX -= padding;
    bounds.minY -= padding;
    bounds.maxX += padding;
    bounds.maxY += padding;
    
    return bounds;
}

function renderSubsystemHalos(scene) {
    if (!scene.subsystems || !scene.subsystems.length) return;
    if (!scene.modules || !scene.modules.length) return;

    // Group modules by subsystem_id
    const groups = {};
    scene.modules.forEach(function (m) {
        const ssId = m.subsystem_id || 0;
        if (!groups[ssId]) {
            groups[ssId] = { modules: [], name: 'Ungrouped', color: 'rgba(128,128,128,0.06)', haloColor: 'rgba(128,128,128,0.06)' };
        }
        groups[ssId].modules.push(m);
    });

    // Match subsystem names and colors
    scene.subsystems.forEach(function (ss) {
        if (groups[ss.id]) {
            groups[ss.id].name = ss.name;
            groups[ss.id].color = ss.color;
            groups[ss.id].haloColor = ss.halo_color;
        }
    });

    // Build pin->subsystem lookup ONCE for all groups (performance)
    var pinModMap = _buildPinToSubsystem(scene);
    // SVG paints later siblings on top, and some paths (module drag end,
    // connector side-change) remove + re-render this group, which would
    // otherwise append it on top of modules/wires. `.lower()` re-inserts it
    // as the first child of `g` so halos always sit beneath everything.
    const haloGroup = g.append('g').attr('class', 'subsystem-halos').lower();

    Object.keys(groups).forEach(function (ssId) {
        const group = groups[ssId];
        // Show halo even for single-module subsystems
        if (group.modules.length < 1) return;

        // Compute bounds including wiring
        var bounds = _computeHaloBounds(scene, group, pinModMap);
        var boxW = bounds.maxX - bounds.minX;
        var boxH = bounds.maxY - bounds.minY;

        // Background bounding box with dashed border — store data-subsystem-id for in-place updates
        haloGroup.append('rect')
            .attr('class', 'halo-bg')
            .attr('data-subsystem-id', ssId)
            .attr('x', bounds.minX)
            .attr('y', bounds.minY)
            .attr('width', boxW)
            .attr('height', boxH)
            .attr('rx', 12)
            .attr('ry', 12)
            .attr('fill', group.haloColor)
            .attr('stroke', group.color || '#555')
            .attr('stroke-width', 1)
            .attr('stroke-dasharray', '6,3')
            .attr('opacity', 0.85)
            .attr('pointer-events', 'none');

        // Subsystem label at top-left
        const labelX = bounds.minX + 10;
        const labelY = bounds.minY + 16;
        const textLength = group.name.length * 8 + 20;

        haloGroup.append('rect')
            .attr('class', 'halo-label-bg')
            .attr('data-subsystem-id', ssId)
            .attr('x', labelX - 4)
            .attr('y', labelY - 11)
            .attr('width', Math.max(textLength, 30))
            .attr('height', 18)
            .attr('rx', 4)
            .attr('ry', 4)
            .attr('fill', group.color || '#555')
            .attr('opacity', 0.7);

        haloGroup.append('text')
            .attr('class', 'halo-label')
            .attr('data-subsystem-id', ssId)
            .attr('x', labelX + 2)
            .attr('y', labelY)
            .attr('fill', '#ffffff')
            .attr('font-size', 11)
            .attr('font-weight', '600')
            .attr('font-family', '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif')
            .text(group.name);
    });
}

// Update subsystem halos in-place during drag — no DOM remove/recreate.
// Also updates for wiring positions.
function updateSubsystemHalosInPlace(scene) {
    if (!scene.subsystems || !scene.subsystems.length) return;
    if (!scene.modules || !scene.modules.length) return;

    const groups = {};
    scene.modules.forEach(function (m) {
        const ssId = m.subsystem_id || 0;
        if (!groups[ssId]) {
            groups[ssId] = { modules: [], name: 'Ungrouped', color: 'rgba(128,128,128,0.06)', haloColor: 'rgba(128,128,128,0.06)' };
        }
        groups[ssId].modules.push(m);
    });

    scene.subsystems.forEach(function (ss) {
        if (groups[ss.id]) {
            groups[ss.id].name = ss.name;
            groups[ss.id].color = ss.color;
            groups[ss.id].haloColor = ss.halo_color;
        }
    });

    // Build pin->subsystem lookup ONCE for all groups (performance)
    var pinModMap = _buildPinToSubsystem(scene);

    Object.keys(groups).forEach(function (ssId) {
        const group = groups[ssId];
        // Show halo even for single-module subsystems
        if (group.modules.length < 1) return;

        // Compute bounds including wiring
        var bounds = _computeHaloBounds(scene, group, pinModMap);
        var boxW = bounds.maxX - bounds.minX;
        var boxH = bounds.maxY - bounds.minY;

        // Update background rect in-place
        g.select('.halo-bg[data-subsystem-id="' + ssId + '"]')
            .attr('x', bounds.minX).attr('y', bounds.minY)
            .attr('width', Math.max(boxW, 10)).attr('height', Math.max(boxH, 10));

        // Update label background rect in-place
        g.select('.halo-label-bg[data-subsystem-id="' + ssId + '"]')
            .attr('x', bounds.minX + 6)
            .attr('y', bounds.minY + 5);

        // Update label text position
        g.select('.halo-label[data-subsystem-id="' + ssId + '"]')
            .attr('x', bounds.minX + 12)
            .attr('y', bounds.minY + 16);
    });
}



// Compute the full extent a connector needs along the module edge.
// For a connector with N pins, the pin spread is (N-1)*PIN_HALF_STEP*2
// centered on the connector's center along the edge.
function connectorEdgeExtent(c, side) {
    const count = c.pins.length;
    const pinSpan = Math.max(0, (count - 1)) * PIN_HALF_STEP + PIN_HALF_STEP;
    // Full extent = 2 * pinSpan (pins spread equally in both directions)
    return pinSpan * 2 + CONNECTOR_BBOX_PAD * 2;
}

// Before distributing connectors, auto-grow each module's width/height so
// all of its connectors fit without overlapping or being squished.
// Preserves saved dimensions — only expands if connectors need more room.
function autoSizeModules(scene) {
    // Group connectors by module_id
    var byModule = {};
    scene.connectors.forEach(function (c) {
        var key = String(c.module_id);
        (byModule[key] = byModule[key] || []).push(c);
    });

    Object.keys(byModule).forEach(function (modId) {
        var module = scene.modules.find(function (m) { return String(m.id) === modId; });
        if (!module) return;

        // Skip auto-sizing if module was explicitly sized (saved to DB).
        // A module is considered "explicitly sized" if its loaded dimensions
        // differ from the Python create_module defaults (160x100).
        // This prevents autoSizeModules from resizing back on tab switch.
        if (module._sized) return;

        var conns = byModule[modId];
        // Start from the EXISTING module dimensions (which may have been
        // loaded from DB or set by user resize), NOT from constants.
        // This ensures saved/user-set dimensions are never discarded.
        var minW = module.width;
        var minH = module.height;

        // Group connectors by side for this module
        var top = [], bottom = [], left = [], right = [];
        conns.forEach(function (c) {
            var side = normalizeSide(c.side);
            if (side === 'top') top.push(c);
            else if (side === 'bottom') bottom.push(c);
            else if (side === 'left') left.push(c);
            else right.push(c);
        });

        // Compute minimum width needed for top/bottom connectors
        [top, bottom].forEach(function (group) {
            if (group.length < 2) return; // single connector always fits
            var totalExtents = group.reduce(function (s, c) {
                return s + connectorEdgeExtent(c, 'top');
            }, 0);
            var totalGaps = (group.length - 1) * CONNECTOR_GAP;
            var needed = totalExtents + totalGaps + EDGE_MARGIN * 2;
            minW = Math.max(minW, needed);
        });

        // Compute minimum height needed for left/right connectors
        [left, right].forEach(function (group) {
            if (group.length < 2) return;
            var totalExtents = group.reduce(function (s, c) {
                return s + connectorEdgeExtent(c, 'left');
            }, 0);
            var totalGaps = (group.length - 1) * CONNECTOR_GAP;
            var needed = totalExtents + totalGaps + EDGE_MARGIN * 2;
            minH = Math.max(minH, needed);
        });

        // Apply: expands if needed, but never shrinks below existing/saved dimensions.
        // Ensure minimum constants are respected too.
        module.width = Math.max(minW, MODULE_MIN_WIDTH);
        module.height = Math.max(minH, MODULE_MIN_HEIGHT);
    });
}

// Distribute connectors along a module edge so they never overlap.
// ALL connectors are always redistributed — saved positions are cleared
// when they would cause overlap (e.g. zero/zero from DB or stale after
// pin add/remove). This ensures proper spacing at all times.
function assignFallbackConnectorPositions(scene) {
    const bySideModule = {};
    scene.connectors.forEach(function (c) {
        const key = c.module_id + '|' + normalizeSide(c.side);
        (bySideModule[key] = bySideModule[key] || []).push(c);
    });

    Object.keys(bySideModule).forEach(function (key) {
        const [modId, side] = key.split('|');
        const module = scene.modules.find(m => String(m.id) === modId);
        if (!module) return;

        const group = bySideModule[key];
        const w = Math.max(MODULE_MIN_WIDTH, module.width);
        const h = Math.max(MODULE_MIN_HEIGHT, module.height);
        const edgeSize = (side === 'top' || side === 'bottom') ? w : h;
        const edgeMargin = EDGE_MARGIN;

        // Always redistribute connectors evenly for auto-distancing
        const entries = group.map(function (c) {
            const extent = connectorEdgeExtent(c, side);
            return { c: c, extent: extent };
        });

        if (entries.length === 1) {
            // Single connector: center it on the edge
            const e = entries[0];
            const t = 0.5;
            const edge = edgePoint(module, side, t);
            const normal = SIDE_AXIS[side].normal;
            e.c.x = edge.x + normal.x * CONNECTOR_STUB;
            e.c.y = edge.y + normal.y * CONNECTOR_STUB;
            return;
        }

        // Multi-connector: distribute evenly
        const totalExtents = entries.reduce(function (s, e) { return s + e.extent; }, 0);
        const totalGaps = (entries.length - 1) * CONNECTOR_GAP;
        const totalNeeded = totalExtents + totalGaps + edgeMargin * 2;

        // If we need more space than the edge, shrink gap proportionally
        let actualGap = CONNECTOR_GAP;
        if (totalNeeded > edgeSize) {
            const availableForGaps = Math.max(0, edgeSize - totalExtents - edgeMargin * 2);
            actualGap = entries.length > 1 ? availableForGaps / (entries.length - 1) : 0;
        }

        // Compute starting position: center the whole group if total fits,
        // otherwise start at margin
        const actualTotal = totalExtents + actualGap * (entries.length - 1) + edgeMargin * 2;
        let startOffset = (edgeSize - actualTotal) / 2 + edgeMargin;
        if (startOffset < edgeMargin) startOffset = edgeMargin;

        let cursor = startOffset;
        entries.forEach(function (entry) {
            // Center of this connector along the edge
            const center = cursor + entry.extent / 2;

            const t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, center / edgeSize));
            const edge = edgePoint(module, side, t);
            const normal = SIDE_AXIS[side].normal;
            entry.c.x = edge.x + normal.x * CONNECTOR_STUB;
            entry.c.y = edge.y + normal.y * CONNECTOR_STUB;

            cursor += entry.extent + actualGap;
        });
    });
}// Compute the midpoint of a connector body (where pins sit), in module-local coords.
// For a connector on side S, the body midpoint is halfway between the module edge
// and the stub tip, along the normal direction.
function connectorBodyMidpoint(module, c) {
    const side = normalizeSide(c.side);
    const normal = SIDE_AXIS[side].normal;
    const edge = connectorEdgeAnchor(module, c);
    // Midpoint is at PIN_EDGE_OFFSET from edge along the normal
    return {
        x: edge.x + normal.x * PIN_EDGE_OFFSET,
        y: edge.y + normal.y * PIN_EDGE_OFFSET,
    };
}

function buildPinLookup(scene) {
    // pin_id -> absolute {x, y, side} in scene coordinates, used to draw interfaces.
    // Pins sit at the connector body midpoint, spread along the tangent direction.
    const lookup = {};
    scene.connectors.forEach(function (c) {
        const module = scene.modules.find(m => String(m.id) === String(c.module_id));
        if (!module) return;
        const side = normalizeSide(c.side);
        const tangent = SIDE_AXIS[side].tangent;
        const count = c.pins.length;
        const mid = connectorBodyMidpoint(module, c);
        c.pins.forEach(function (p, i) {
            const offset = -(count - 1) * PIN_HALF_STEP + i * (PIN_HALF_STEP * 2);
            lookup[p.id] = {
                x: module.x + mid.x + tangent.x * offset,
                y: module.y + mid.y + tangent.y * offset,
                side: side,
            };
        });
    });
    return lookup;
}

// Build a Set of interface IDs where either connected pin belongs to the
// "Power" subsystem. These wires should be visually highlighted (glow/gold).
// Uses the cached _powerLookupCache.
function _getPoweredInterfaceIds(scene) {
    var lookup = _buildPowerLookup(scene);
    if (lookup === null) return new Set();

    var pinToSubsystem = lookup.pinToSubsystem;
    var powerSubsystemId = lookup.powerSubsystemId;
    var powered = new Set();

    for (var ii = 0; ii < scene.interfaces.length; ii++) {
        var iface = scene.interfaces[ii];
        if (pinToSubsystem[iface.from_pin] === powerSubsystemId ||
            pinToSubsystem[iface.to_pin] === powerSubsystemId) {
            powered.add(iface.id);
        }
    }
    return powered;
}

// Build reusable lookup tables for power calculations.
// Caches result in _powerLookupCache so subsequent calls within the same
// render cycle reuse it (avoids O(connectors × modules × interfaces) per module).
// Caller MUST clear _powerLookupCache = null before a new render cycle.
// Returns { pinToSubsystem, pinInterfaces, powerSubsystemId } or null if no Power subsystem.
function _buildPowerLookup(scene) {
    if (_powerLookupCache) return _powerLookupCache;

    // 1. Find "Power" subsystem by name (case-insensitive)
    var powerSubsystemId = null;
    for (var si = 0; si < scene.subsystems.length; si++) {
        if (scene.subsystems[si].name.toLowerCase() === 'power') {
            powerSubsystemId = scene.subsystems[si].id;
            break;
        }
    }
    if (powerSubsystemId === null) {
        _powerLookupCache = null;
        return null;
    }

    // 2. Build module_id -> subsystem_id lookup ONCE (avoids inner loop over modules per connector)
    var modSubsystemMap = {};
    for (var mi = 0; mi < scene.modules.length; mi++) {
        var mod = scene.modules[mi];
        modSubsystemMap[mod.id] = mod.subsystem_id || 0;
    }

    // 3. Build pin_id -> subsystem_id (connector -> module -> subsystem)
    var pinToSubsystem = {};
    for (var ci = 0; ci < scene.connectors.length; ci++) {
        var c = scene.connectors[ci];
        var ssId = modSubsystemMap[c.module_id];
        if (ssId === undefined) ssId = 0;
        for (var pi = 0; pi < c.pins.length; pi++) {
            pinToSubsystem[c.pins[pi].id] = ssId;
        }
    }

    // 4. Build interface adjacency: pin_id -> [connected_pin_ids]
    var pinInterfaces = {};
    for (var ii = 0; ii < scene.interfaces.length; ii++) {
        var iface = scene.interfaces[ii];
        var fromP = iface.from_pin;
        var toP = iface.to_pin;
        if (!pinInterfaces[fromP]) pinInterfaces[fromP] = [];
        pinInterfaces[fromP].push(toP);
        if (!pinInterfaces[toP]) pinInterfaces[toP] = [];
        pinInterfaces[toP].push(fromP);
    }

    _powerLookupCache = { pinToSubsystem: pinToSubsystem, pinInterfaces: pinInterfaces, powerSubsystemId: powerSubsystemId };
    return _powerLookupCache;
}

// Compute connected power — voltage × current — where the current now lives
// on the CONNECTION (interfaces.current), not on the pins. Only interfaces
// whose voltage pin belongs to the "Power" subsystem contribute, matching
// the pre-existing HUD behavior. Voltage is in V, current in mA → mW.
function _computeConnectedPower(scene, modId) {
    var lookup = _buildPowerLookup(scene);
    if (lookup === null) return 0;

    var pinToSubsystem = lookup.pinToSubsystem;
    var powerSubsystemId = lookup.powerSubsystemId;

    var pinMod = {};
    var voltByPin = {};
    scene.connectors.forEach(function (c) {
        (c.pins || []).forEach(function (p) {
            pinMod[p.id] = c.module_id;
            voltByPin[p.id] = Number(p.voltage) || 0;
        });
    });

    var total = 0.0;
    scene.interfaces.forEach(function (iface) {
        var cur = Number(iface.current) || 0;
        if (cur <= 0) return;

        var aIsPower = pinToSubsystem[iface.from_pin] === powerSubsystemId;
        var bIsPower = pinToSubsystem[iface.to_pin] === powerSubsystemId;
        if (!aIsPower && !bIsPower) return;

        var v = aIsPower ? (voltByPin[iface.from_pin] || 0) : (voltByPin[iface.to_pin] || 0);
        if (v <= 0) return;

        var loadPin = aIsPower ? iface.to_pin : iface.from_pin;
        if (modId != null && String(pinMod[loadPin]) !== String(modId)) return;
        total += v * cur;
    });
    return total;
}

// Per-module connected pin power (used in the module info labels).
function computeModulePinPower(modId, scene) {
    return _computeConnectedPower(scene, modId);
}

// Total connected pin power across ALL modules currently in the scene.
function computeTotalScenePower(scene) {
    return _computeConnectedPower(scene, null);
}

// Update the power HUD in the top-right corner.
function updatePowerHud(scene) {
    var el = document.getElementById('power-hud');
    if (!el) return;
    var total = computeTotalScenePower(scene);
    el.textContent = '⚡ ' + total.toFixed(1) + ' mW';
}

// Show/hide the read-only badge in the top-left corner and neutralise
// the edit-cursor affordances (move / crosshair) in read-only mode.
function updateReadOnlyBadge(readonly) {
    var el = document.getElementById('readonly-badge');
    if (el) el.style.display = readonly ? 'block' : 'none';
    document.body.classList.toggle('readonly', readonly);
}

function renderModules(scene) {
    const moduleSel = g.selectAll('.module-box')
        .data(scene.modules, d => d.id)
        .enter()
        .append('g')
        .attr('class', 'module-box')
        .attr('transform', d => `translate(${d.x}, ${d.y})`)
        .call(dragBehavior())
        .on('click', function (event, d) {
            event.stopPropagation();
            if (event.shiftKey) {
                // Shift+click: toggle this module in/out of the selection
                if (selectedModuleIds.has(d.id)) {
                    selectedModuleIds.delete(d.id);
                } else {
                    selectedModuleIds.add(d.id);
                }
                selectedInterfaceIds.clear();
            } else {
                // Normal click: select just this module
                selectedModuleIds = new Set([d.id]);
                selectedInterfaceIds.clear();
            }
            g.selectAll('.module-box').classed('selected', dd => selectedModuleIds.has(dd.id));
            g.selectAll('.interface-path').classed('selected', false);
        })
        .on('contextmenu', function (event, d) {
            event.preventDefault();
            event.stopPropagation();
            showModuleContextMenu(event, d);
        });

    // Pending markers: edits/creates awaiting system-admin approval are
    // dashed amber; pending deletions are ghosted red.
    moduleSel
        .classed('pending', function (d) { return d.pending && d.pending !== 'delete'; })
        .classed('pending-delete', function (d) { return d.pending_delete || d.pending === 'delete'; });

    var modRect = moduleSel.append('rect')
        .attr('class', 'module-rect')
        .attr('width', d => Math.max(MODULE_MIN_WIDTH, d.width))
        .attr('height', d => Math.max(MODULE_MIN_HEIGHT, d.height))
        .attr('rx', 6).attr('ry', 6)
        .attr('fill', d => d.color || '#e67e22');

    // "⏳ pending" badge for proposed changes
    moduleSel.filter(function (d) { return d.pending && d.pending !== 'delete'; })
        .append('text')
        .attr('class', 'pending-badge')
        .attr('x', d => Math.max(MODULE_MIN_WIDTH, d.width) - 6)
        .attr('y', 4)
        .attr('text-anchor', 'end')
        .attr('font-size', 9)
        .attr('font-weight', '700')
        .attr('fill', '#ffd76e')
        .text('⏳ pending');

    modRect.append('title')
        .text(d => {
            var info = d.name || 'Unnamed';
            if (d.mass != null) info += ' | Mass: ' + d.mass;
            if (d.power != null) info += ' | Power: ' + d.power;
            var pinPower = computeModulePinPower(d.id, scene);
            if (pinPower > 0) info += ' | Pin Power: ' + pinPower.toFixed(1) + 'mW';
            if (d.subsystem_name) info += ' | Subsystem: ' + d.subsystem_name;
            return info;
        });

    moduleSel.append('text')
        .attr('class', 'module-label')
        .attr('x', d => Math.max(MODULE_MIN_WIDTH, d.width) / 2)
        .attr('y', d => Math.max(MODULE_MIN_HEIGHT, d.height) / 2 - 6)
        .text(d => d.name);

    // Power info label: standalone module power (P) and connected pin power
    moduleSel.append('text')
        .attr('class', 'module-power-label')
        .attr('x', d => Math.max(MODULE_MIN_WIDTH, d.width) / 2)
        .attr('y', d => Math.max(MODULE_MIN_HEIGHT, d.height) / 2 + 12)
        .attr('text-anchor', 'middle')
        .attr('fill', getComputedStyle(document.documentElement).getPropertyValue('--text-primary') || 'rgba(255,255,255,0.7)')
        .attr('font-size', 10)
        .attr('font-weight', '400')
        .attr('font-family', '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif')
        .text(d => {
            var displayP = (d.power != null ? Number(d.power) : 0);
            var pinP = computeModulePinPower(d.id, scene);
            return 'P: ' + displayP.toFixed(1) + 'mW  ⚡' + pinP.toFixed(1) + 'mW';
        });

    // Resize handles (shown on hover via CSS)
    const handleSize = 14;
    const hh = handleSize / 2; // half-handle for centering
    const handleDefs = [
        // corners
        { cls: 'resize-nw', x: -hh, y: -hh, cursor: 'nwse-resize', dir: 'nw' },
        { cls: 'resize-ne', x: d => Math.max(MODULE_MIN_WIDTH, d.width) - hh, y: -hh, cursor: 'nesw-resize', dir: 'ne' },
        { cls: 'resize-sw', x: -hh, y: d => Math.max(MODULE_MIN_HEIGHT, d.height) - hh, cursor: 'nesw-resize', dir: 'sw' },
        { cls: 'resize-se', x: d => Math.max(MODULE_MIN_WIDTH, d.width) - hh, y: d => Math.max(MODULE_MIN_HEIGHT, d.height) - hh, cursor: 'nwse-resize', dir: 'se' },
        // edge midpoints
        { cls: 'resize-n', x: d => Math.max(MODULE_MIN_WIDTH, d.width) / 2 - hh, y: -hh, cursor: 'ns-resize', dir: 'n' },
        { cls: 'resize-s', x: d => Math.max(MODULE_MIN_WIDTH, d.width) / 2 - hh, y: d => Math.max(MODULE_MIN_HEIGHT, d.height) - hh, cursor: 'ns-resize', dir: 's' },
        { cls: 'resize-w', x: -hh, y: d => Math.max(MODULE_MIN_HEIGHT, d.height) / 2 - hh, cursor: 'ew-resize', dir: 'w' },
        { cls: 'resize-e', x: d => Math.max(MODULE_MIN_WIDTH, d.width) - hh, y: d => Math.max(MODULE_MIN_HEIGHT, d.height) / 2 - hh, cursor: 'ew-resize', dir: 'e' },
    ];

    handleDefs.forEach(function (def) {
        moduleSel.append('rect')
            .attr('class', 'resize-handle ' + def.cls)
            .attr('width', handleSize)
            .attr('height', handleSize)
            .attr('x', def.x)
            .attr('y', def.y)
            .attr('fill', 'transparent')
            .attr('stroke', 'var(--accent)')
            .attr('stroke-width', 1.5)
            .attr('rx', 3).attr('ry', 3)
            .attr('cursor', def.cursor)
            .attr('opacity', 0)
            .attr('pointer-events', 'all')
            .call(resizeBehavior(def.dir));
    });

    // Connectors + pins, drawn relative to their parent module group.
    scene.connectors.forEach(function (c) {
        const parent = g.selectAll('.module-box').filter(d => String(d.id) === String(c.module_id));
        if (parent.empty()) return;
        const module = parent.datum();
        const side = normalizeSide(c.side);
        const tangent = SIDE_AXIS[side].tangent;
        const count = c.pins.length;
        const edge = connectorEdgeAnchor(module, c);

        // Use tree-view colors: orange for module fill, green for connector strokes, red for pins
        const connectorColor = c.color || '#27ae60';   // tree view connector color, fallback to green

        // Connector interactive group — contains visible line, handle, hitbox
        var connPendingCls = (c.pending || c.pending_delete)
            ? (c.pending === 'delete' || c.pending_delete ? ' pending-delete' : ' pending')
            : '';
        var connGroup = parent.append('g').attr('class', 'connector-interactive' + connPendingCls);

        // --- Connector bounding box (visible background rect) ---
        const bbox = connectorBBox(module, c);
        connGroup.append('rect')
            .attr('class', 'connector-bbox')
            .attr('data-connector-id', c.id)
            .attr('x', bbox.x).attr('y', bbox.y)
            .attr('width', bbox.w).attr('height', bbox.h)
            .attr('rx', 6).attr('ry', 6)
            .attr('fill', hexToRgba(connectorColor, '0.08'))
            .attr('stroke', hexToRgba(connectorColor, '0.25'))
            .attr('stroke-width', 1.5)
            .attr('pointer-events', 'none');

        // Visible connector line from module edge OUT to the stub tip
        connGroup.append('line')
            .attr('class', 'connector-line')
            .attr('data-connector-id', c.id)
            .attr('x1', edge.x).attr('y1', edge.y)
            .attr('x2', c.x).attr('y2', c.y)
            .attr('stroke', connectorColor)
            .attr('stroke-width', 2);

        // Visible drag handle at the stub tip — diamond shape to distinguish from pin circles
        const handleSize = 6;
        var connHandle = connGroup.append('path')
            .attr('class', 'connector-drag-handle')
            .attr('data-connector-id', c.id)
            .attr('d', `M${c.x},${c.y - handleSize} L${c.x + handleSize},${c.y} L${c.x},${c.y + handleSize} L${c.x - handleSize},${c.y} Z`)
            .attr('fill', connectorColor)
            .attr('stroke', connectorColor)
            .attr('stroke-width', 1.5);
        connHandle.append('title').text(c.name + ' | Side: ' + c.side + ' | Pins: ' + (c.pins ? c.pins.length : 0));

        // Connector name label near the drag handle
        const isHorizSide = (side === 'top' || side === 'bottom');
        const labelOffset = isHorizSide ? { x: 10, y: -6 } :
            (side === 'left' ? { x: -8, y: -6 } : { x: 8, y: -6 });
        connGroup.append('text')
            .attr('class', 'connector-label')
            .attr('data-connector-id', c.id)
            .attr('x', c.x + labelOffset.x)
            .attr('y', c.y + labelOffset.y)
            .attr('text-anchor', isHorizSide ? 'start' : (side === 'left' ? 'end' : 'start'))
            .attr('dominant-baseline', 'auto')
            .attr('fill', connectorColor)
            .attr('font-size', 9)
            .attr('font-weight', '600')
            .attr('font-family', '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif')
            .attr('pointer-events', 'none')
            .text(c.name);

        // Invisible wider hitbox ON TOP for easy connector interaction (covers bounding box area)
        connGroup.append('rect')
            .attr('class', 'connector-hitbox')
            .attr('data-connector-id', c.id)
            .attr('x', bbox.x).attr('y', bbox.y)
            .attr('width', bbox.w).attr('height', bbox.h)
            .attr('fill', 'transparent')
            .attr('pointer-events', 'all')
            .attr('cursor', 'pointer');

        // Enable drag-to-reposition along the module edge
        enableConnectorSideDrag(connGroup, c, module);

        // Context menu on dblclick (via hitbox)
        connGroup.on('dblclick', function (event) {
            event.stopPropagation();
            openPinOrderDialog(c);
        });
        connGroup.on('contextmenu', function (event) {
            event.preventDefault();
            event.stopPropagation();
            showConnectorContextMenu(event, c);
        });

        c.pins.forEach(function (p, i) {
            const offset = -(count - 1) * PIN_HALF_STEP + i * (PIN_HALF_STEP * 2);
            // Pins sit at the connector BODY MIDPOINT, spread along the tangent
            const mid = connectorBodyMidpoint(module, c);
            const px = mid.x + tangent.x * offset;
            const py = mid.y + tangent.y * offset;

            // Distinct symbols per electrical class (data / VCC / GND).
            // The group keeps the `pin-circle` class so highlight / cursor /
            // read-only behavior driven by that class keeps working.
            const pcls = pinClassOf(p);
            var pinPendingCls = (p.pending || p.pending_delete)
                ? (p.pending === 'delete' || p.pending_delete ? ' pending-delete' : ' pending')
                : '';
            var pinG = parent.append('g')
                .attr('class', 'pin-circle pin-' + pcls.cls + pinPendingCls)
                .attr('data-pin-id', p.id)
                .attr('transform', 'translate(' + px + ',' + py + ')');
            appendPinSymbol(pinG, pcls);
            // Invisible hit area so grabbing the exact symbol is easy
            pinG.append('circle').attr('class', 'pin-hit').attr('r', PIN_RADIUS);
            pinG.append('title').text(p.name + ' | Connector: ' + c.name);
            pinG.on('mousedown', function (event) {
                    event.stopPropagation();
                    if (event.button !== 0) return;  // left button only (right-click = context menu)
                    if (p.pending || p.pending_delete) {
                        showToast('This pin is pending approval', 'error');
                        return;
                    }
                    startConnectDrag(p.id, px, py, parent);
                })
                .on('mouseup', function (event) {
                    event.stopPropagation();
                    finishConnectDrag(p.id);
                })
                .on('contextmenu', function (event) {
                    event.preventDefault();
                    event.stopPropagation();
                    showPinContextMenu(event, p, c);
                });

            const isVertical = (side === 'top' || side === 'bottom');
            const labelDx = isVertical ? 0 : (side === 'left' ? -PIN_RADIUS - 3 : PIN_RADIUS + 3);
            const labelDy = isVertical ? (side === 'top' ? -PIN_RADIUS - 3 : PIN_RADIUS + 11) : 0;
            const anchor = isVertical ? 'middle' : (side === 'left' ? 'end' : 'start');

            parent.append('text')
                .attr('class', 'pin-label')
                .attr('data-pin-id', p.id)
                .attr('x', px + labelDx)
                .attr('y', py + labelDy)
                .attr('text-anchor', anchor)
                .attr('dominant-baseline', 'middle')
                .text(p.name);

            // Data pins also show a small caption with their concrete type
            // (UART, I2C, …) on the side facing the module edge. The generic
            // "Data" type is skipped to avoid noise.
            if (pcls.cls === 'data' && String(p.pin_type || '').trim().toUpperCase() !== 'DATA') {
                var tDx = 0, tDy = 0, tAnchor = 'middle';
                if (side === 'right') { tDx = -PIN_RADIUS - 4; tAnchor = 'end'; }
                else if (side === 'left') { tDx = PIN_RADIUS + 4; tAnchor = 'start'; }
                else if (side === 'top') { tDy = PIN_RADIUS + 4; }
                else { tDy = -PIN_RADIUS - 4; }
                parent.append('text')
                    .attr('class', 'pin-type-label')
                    .attr('data-pin-id', p.id)
                    .attr('x', px + tDx)
                    .attr('y', py + tDy)
                    .attr('text-anchor', tAnchor)
                    .attr('dominant-baseline', 'middle')
                    .text(p.pin_type);
            }
        });
    });
}

// ---------------------------------------------------------------------
// Connector bounding box computation
// ---------------------------------------------------------------------
// Compute the bounding box for a connector's pins in module-local coords.
// Returns {x, y, w, h} for a rect that encloses all pins plus the stub.
function connectorBBox(module, c) {
    const side = normalizeSide(c.side);
    const tangent = SIDE_AXIS[side].tangent;
    const normal = SIDE_AXIS[side].normal;
    const count = c.pins.length;
    const w = Math.max(MODULE_MIN_WIDTH, module.width);
    const h = Math.max(MODULE_MIN_HEIGHT, module.height);

    // The body midpoint (where pins sit) in module-local coords
    const mid = connectorBodyMidpoint(module, c);
    // The edge anchor
    const edge = connectorEdgeAnchor(module, c);

    let minX, minY, maxX, maxY;
    if (count === 0) {
        // No pins: bbox spans from edge to stub tip
        minX = Math.min(edge.x, c.x) - CONNECTOR_BBOX_PAD;
        minY = Math.min(edge.y, c.y) - CONNECTOR_BBOX_PAD;
        maxX = Math.max(edge.x, c.x) + CONNECTOR_BBOX_PAD;
        maxY = Math.max(edge.y, c.y) + CONNECTOR_BBOX_PAD;
    } else {
        const pinSpread = (count - 1) * PIN_HALF_STEP;
        // Pin positions along the tangent at the body midpoint
        const firstPx = mid.x + tangent.x * (-pinSpread);
        const firstPy = mid.y + tangent.y * (-pinSpread);
        const lastPx = mid.x + tangent.x * pinSpread;
        const lastPy = mid.y + tangent.y * pinSpread;
        // Bounding box covers edge + body + pins + tip
        minX = Math.min(edge.x, firstPx, lastPx, c.x) - CONNECTOR_BBOX_PAD;
        minY = Math.min(edge.y, firstPy, lastPy, c.y) - CONNECTOR_BBOX_PAD;
        maxX = Math.max(edge.x, firstPx, lastPx, c.x) + CONNECTOR_BBOX_PAD;
        maxY = Math.max(edge.y, firstPy, lastPy, c.y) + CONNECTOR_BBOX_PAD;
    }

    return {
        x: minX,
        y: minY,
        w: maxX - minX,
        h: maxY - minY,
    };
}

// ---------------------------------------------------------------------
// Same-type wiring rule (mirrors database.pins_connectable_from_data)
// ---------------------------------------------------------------------
var GROUND_ALIASES = ['GND', 'GROUND', 'VSS', 'AGND', 'DGND'];
var POWER_ALIASES = ['VCC', 'VDD', 'POWER', 'PWR', 'VOLTAGE'];

function pinClassOf(p) {
    var t = String((p && p.pin_type) || '').trim().toUpperCase();
    if (p && (p.is_ground || GROUND_ALIASES.indexOf(t) >= 0)) {
        return { cls: 'ground', type: t || 'GND', voltage: 0 };
    }
    if (POWER_ALIASES.indexOf(t) >= 0) {
        return { cls: 'power', type: t, voltage: Number(p.voltage) || 0 };
    }
    if (!t) return { cls: 'untyped', type: '' };
    return { cls: 'data', type: t };
}

// Draw the electrical-class symbol inside a pin group (centered on 0,0):
//   data   → rounded square
//   power  → upward triangle (VCC)
//   ground → classic three-bar ground symbol
function appendPinSymbol(g, pcls) {
    if (pcls.cls === 'ground') {
        g.append('line').attr('class', 'pin-ground-line').attr('x1', 0).attr('y1', -3).attr('x2', 0).attr('y2', 4);
        g.append('line').attr('class', 'pin-ground-line').attr('x1', -3).attr('y1', 4).attr('x2', 3).attr('y2', 4);
        g.append('line').attr('class', 'pin-ground-line').attr('x1', -5).attr('y1', 7).attr('x2', 5).attr('y2', 7);
        g.append('line').attr('class', 'pin-ground-line').attr('x1', -7).attr('y1', 10).attr('x2', 7).attr('y2', 10);
        return;
    }
    if (pcls.cls === 'power') {
        g.append('path').attr('class', 'pin-power-shape').attr('d', 'M0,-6 L5,3 L-5,3 Z');
        return;
    }
    g.append('rect').attr('class', 'pin-data-shape')
        .attr('x', -4.5).attr('y', -4.5).attr('width', 9).attr('height', 9).attr('rx', 1.5);
}

// Same-type wiring rule. NOTE: keep in sync with database.classify_pin /
// database.pins_connectable_from_data — the DB is the source of truth and
// re-validates on every create/update; this copy only drives the UI
// (highlight + friendly drop rejection).
function pinsCompatible(a, b) {
    if (!a || !b) return { ok: false, reason: 'Unknown pin.' };
    var ca = pinClassOf(a), cb = pinClassOf(b);
    if (ca.cls === 'untyped' || cb.cls === 'untyped') {
        return { ok: false, reason: 'Set a pin type (GND, VCC/voltage or a data type) before connecting.' };
    }
    if (ca.cls === 'ground' || cb.cls === 'ground') {
        if (ca.cls === 'ground' && cb.cls === 'ground') return { ok: true, reason: '' };
        return { ok: false, reason: 'Ground pins can only connect to other ground pins.' };
    }
    if (ca.cls === 'power' || cb.cls === 'power') {
        if (ca.cls !== 'power' || cb.cls !== 'power') {
            return { ok: false, reason: 'Voltage (VCC) pins can only connect to other voltage pins.' };
        }
        if (ca.voltage && cb.voltage && Math.abs(ca.voltage - cb.voltage) > 1e-6) {
            return { ok: false, reason: 'Voltage pins must have the same voltage (' + ca.voltage + 'V ≠ ' + cb.voltage + 'V).' };
        }
        return { ok: true, reason: '' };
    }
    if (ca.type !== cb.type) {
        return { ok: false, reason: "'" + ca.type + "' pins can only connect to other '" + ca.type + "' pins (cannot connect '" + ca.type + "' to '" + cb.type + "')." };
    }
    return { ok: true, reason: '' };
}

function findPinById(pinId) {
    var target = Number(pinId);
    for (var ci = 0; ci < sceneData.connectors.length; ci++) {
        var pins = sceneData.connectors[ci].pins || [];
        for (var pi = 0; pi < pins.length; pi++) {
            if (Number(pins[pi].id) === target) return pins[pi];
        }
    }
    return null;
}

// Highlight the pins the user may drop on while a connect drag is active.
function highlightCompatiblePins(fromPinId) {
    var fromPin = findPinById(fromPinId);
    g.selectAll('.pin-circle').classed('pin-compatible', function () {
        var pid = Number(d3.select(this).attr('data-pin-id'));
        if (pid === Number(fromPinId)) return false;
        return pinsCompatible(fromPin, findPinById(pid)).ok;
    });
}

// ---------------------------------------------------------------------
// Pin-to-pin drag to create a new connection
// ---------------------------------------------------------------------
function startConnectDrag(pinId, localX, localY, moduleSelection) {
    if (IS_READONLY) return;
    cancelConnectDrag();

    const d = moduleSelection.datum();
    const startAbs = { x: d.x + localX, y: d.y + localY };

    const tempPath = g.append('path')
        .attr('class', 'interface-path temp-drag')
        .attr('d', `M${startAbs.x},${startAbs.y} L${startAbs.x},${startAbs.y}`);

    connectDrag = { fromPinId: pinId, fromPoint: startAbs, tempPath: tempPath };
    highlightCompatiblePins(pinId);

    // Lock cursor to crosshair during connect drag so it does not flicker
    // between grab (SVG default) and crosshair (pin-circle) when hovering
    // over target pins.
    document.body.classList.add('dragging-connection');

    svg.on('mousemove.connect', function (event) {
        if (!connectDrag) return;
        const [mx, my] = d3.pointer(event, g.node());
        connectDrag.tempPath.attr('d', `M${connectDrag.fromPoint.x},${connectDrag.fromPoint.y} L${mx},${my}`);
    });
    svg.on('mouseup.connect', function () {
        // Mouse released over empty canvas (not a pin) -- cancel.
        cancelConnectDrag();
    });
}

function finishConnectDrag(toPinId) {
    if (!connectDrag) return;
    const fromPinId = connectDrag.fromPinId;
    const fromPoint = connectDrag.fromPoint;
    cancelConnectDrag();

    if (!bridge || toPinId === fromPinId) return;

    // Same-type wiring rule: only drop on a compatible pin.
    var compat = pinsCompatible(findPinById(fromPinId), findPinById(toPinId));
    if (!compat.ok) {
        showToast(compat.reason || 'Pins are not compatible.', 'error');
        return;
    }

    // The current is decided on the connection itself — ask for it now.
    // If the user cancels, no connection is created.
    promptConnectionCurrent(0, function (cur) {
        const newId = bridge.create_interface(fromPinId, toPinId, '', cur);
        if (newId > 0) {
            // Immediately render the new interface so the user sees it right away
            const pinLookup = buildPinLookup(sceneData);
            const a = pinLookup[fromPinId];
            const b = pinLookup[toPinId];
            if (a && b) {
                const obstacleRects = sceneData.modules.map(function (m) {
                    return { x: m.x, y: m.y, width: m.width, height: m.height };
                });
                const points = computeRoutePoints(a, b, obstacleRects);
                sceneData.interfaces.push({
                    id: newId,
                    from_pin: fromPinId,
                    to_pin: toPinId,
                    color: '',
                    current: cur,
                    points: points.slice(),
                });
                // Re-render just the interface layer (no full scene rebuild)
                g.select('.interfaces-layer').remove();
                renderInterfaces(sceneData, pinLookup, new Set()); // don't reroute anything, use cached points
            }
        }
        // Refresh from DB in background to ensure routing persistence is consistent
        setTimeout(function () { bridge.get_scene_data(); }, 50);
    });
}

// Modal that asks for the connection current (mA) before a wire is created.
// onConfirm receives the numeric mA value; canceling the dialog aborts.
function promptConnectionCurrent(defaultValue, onConfirm) {
    removeModal();

    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    var dialog = document.createElement('div');
    dialog.className = 'modal-dialog';

    var title = document.createElement('h3');
    title.textContent = 'Set Connection Current';
    dialog.appendChild(title);

    var hint = document.createElement('p');
    hint.className = 'modal-hint';
    hint.textContent = 'The current flows through this connection. Pins no longer carry their own current.';
    dialog.appendChild(hint);

    var field = createModalField(dialog, 'Current (mA)');
    var input = document.createElement('input');
    input.type = 'number';
    input.min = '0';
    input.step = '0.1';
    input.value = (defaultValue != null && defaultValue > 0) ? defaultValue : '';
    input.placeholder = 'e.g. 500';
    field.appendChild(input);

    var actions = document.createElement('div');
    actions.className = 'modal-actions';

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'modal-btn modal-btn-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', removeModal);
    actions.appendChild(cancelBtn);

    var okBtn = document.createElement('button');
    okBtn.className = 'modal-btn modal-btn-primary';
    okBtn.textContent = 'Create Connection';
    okBtn.addEventListener('click', function () {
        var val = parseFloat(input.value);
        if (isNaN(val) || val < 0) val = 0;
        removeModal();
        onConfirm(val);
    });
    actions.appendChild(okBtn);

    dialog.appendChild(actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    _modalEscHandler = function (e) { if (e.key === 'Escape') removeModal(); };
    document.addEventListener('keydown', _modalEscHandler);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) removeModal(); });
    setTimeout(function () { input.focus(); }, 100);
}

function cancelConnectDrag() {
    if (connectDrag && connectDrag.tempPath) connectDrag.tempPath.remove();
    connectDrag = null;
    svg.on('mousemove.connect', null);
    svg.on('mouseup.connect', null);
    g.selectAll('.pin-circle').classed('pin-compatible', false);
    // Release custom cursor so SVG/pin defaults take over again
    document.body.classList.remove('dragging-connection');
}

// ---------------------------------------------------------------------
// Generic context menu (Rename / Delete / etc.) for modules, connectors, pins
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// Toast notification system
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// Toast notification system
// ---------------------------------------------------------------------
// Small, non-invasive toast notifications — bottom-right corner, max 2
// visible at a time, auto-dismiss after 2 seconds.
function showToast(message, type) {
    var container = document.getElementById('toast-container');
    if (!container) return;

    // Enforce max 2 visible toasts — remove oldest if at limit
    while (container.children.length >= 2) {
        var oldest = container.firstChild;
        if (oldest) container.removeChild(oldest);
    }

    var toast = document.createElement('div');
    toast.className = 'toast toast-' + (type || 'info');

    var icon = document.createElement('span');
    icon.className = 'toast-icon';
    icon.textContent = type === 'success' ? '\u2713' : type === 'error' ? '\u2717' : '\u2139';
    toast.appendChild(icon);

    var msg = document.createElement('span');
    msg.textContent = message;
    toast.appendChild(msg);

    container.appendChild(toast);

    // Trigger CSS transition
    requestAnimationFrame(function () {
        toast.classList.add('show');
    });

    toastCount++;

    // Auto-dismiss after 2 seconds
    setTimeout(function () { removeToast(toast); }, 2000);
}

function removeToast(toast) {
    if (!toast) return;
    toast.classList.remove('show');
    setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 200);
}

// ---------------------------------------------------------------------
// Loading overlay control
// ---------------------------------------------------------------------
function hideLoading() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) overlay.classList.add('hidden');
}

// ---------------------------------------------------------------------
// Selection hint — shown briefly on first load so users know about
// rectangle select (drag on empty canvas).
// ---------------------------------------------------------------------
function showSelectionHint() {
    var hint = document.getElementById('hint-select');
    if (!hint) return;
    // Show after a short delay (after fitView animation)
    setTimeout(function () {
        hint.classList.remove('hidden');
        // Auto-hide after 4 seconds
        setTimeout(function () {
            hint.classList.add('hidden');
        }, 4000);
    }, 1500);
    // Also hide on first interaction
    function dismissHint() {
        hint.classList.add('hidden');
        document.removeEventListener('mousedown', dismissHint);
        document.removeEventListener('keydown', dismissHint);
    }
    document.addEventListener('mousedown', dismissHint, { once: true });
    document.addEventListener('keydown', function (e) {
        if (e.key === ' ') { /* don't dismiss on space (pan) */ return; }
        dismissHint();
    }, { once: true });
}

// ---------------------------------------------------------------------
// Context menu
// ---------------------------------------------------------------------

// The currently open menu (if any), with the timestamp it was opened at.
// A single dismiss listener is registered once on `document` (capture phase)
// and consults this reference, so re-opening the menu never leaks listeners.
let currentContextMenu = null;
const CONTEXT_MENU_GRACE_MS = 150;

function setupContextMenuDismiss() {
    if (setupContextMenuDismiss.installed) return;
    setupContextMenuDismiss.installed = true;
    document.addEventListener('mousedown', contextMenuOutside, true);
    document.addEventListener('click', contextMenuOutside, true);
    document.addEventListener('contextmenu', contextMenuOutside, true);
}

function contextMenuOutside(e) {
    const m = currentContextMenu;
    if (!m) return;
    // Absorb interactions that arrive in the grace window right after the
    // menu opens: some Windows mouse/touchpad drivers emit a stray left-click
    // after the right-click that opened the menu. Without this the menu is
    // dismissed (or the item under the cursor is activated) before it ever
    // paints — the "context menu doesn't open" symptom on some Windows
    // machines. The window is kept short so deliberate fast clicks still work.
    if (Date.now() - m.openedAt < CONTEXT_MENU_GRACE_MS) return;
    if (m.menu.contains(e.target)) return;  // item clicks handled separately
    removeContextMenu();
}

function showContextMenu(event, items) {
    if (IS_READONLY) return;  // belt & braces — per-item menus are also gated by `editable`
    removeContextMenu();

    const openedAt = Date.now();

    const menu = document.createElement('div');
    menu.id = 'schematic-context-menu';
    menu.style.left = event.clientX + 'px';
    menu.style.top = event.clientY + 'px';

    items.forEach(function (item) {
        if (item.divider) {
            const divider = document.createElement('div');
            divider.className = 'menu-divider';
            menu.appendChild(divider);
            return;
        }

        const el = document.createElement('div');
        el.className = 'menu-item';

        if (item.icon) {
            const iconSpan = document.createElement('span');
            iconSpan.textContent = item.icon;
            el.appendChild(iconSpan);
        }

        const textSpan = document.createElement('span');
        textSpan.textContent = item.label;
        el.appendChild(textSpan);

        if (item.shortcut) {
            const scSpan = document.createElement('span');
            scSpan.className = 'shortcut';
            scSpan.textContent = item.shortcut;
            el.appendChild(scSpan);
        }

        el.addEventListener('click', function (e) {
            e.stopPropagation();
            // Absorb a stray click that lands on an item under the cursor
            // within the grace window (Windows right-click drivers).
            if (Date.now() - openedAt < CONTEXT_MENU_GRACE_MS) return;
            item.action();
            removeContextMenu();
        });
        menu.appendChild(el);
    });

    document.body.appendChild(menu);
    currentContextMenu = { menu: menu, openedAt: openedAt };
    setupContextMenuDismiss();
}

function removeContextMenu() {
    currentContextMenu = null;
    const existing = document.getElementById('schematic-context-menu');
    if (existing) existing.remove();
}

function showModuleContextMenu(event, moduleDatum) {
    // If the module is not editable, show no context menu at all.
    if (moduleDatum.editable === false) return;
    // Pending items cannot be edited until the system admin approves them.
    if (moduleDatum.pending || moduleDatum.pending_delete) {
        showToast('This item is pending approval', 'error');
        return;
    }
    showContextMenu(event, [
        { icon: '\u2795', label: 'Add Connector', action: function () { addConnectorPrompt(moduleDatum); }, shortcut: 'N' },
        { icon: '\u2699\uFE0F', label: 'Edit Properties', action: function () { showModuleEditDialog(moduleDatum); } },
        { icon: '\u274C', label: 'Delete', action: function () { deleteModuleConfirm(moduleDatum); }, shortcut: 'Del' },
    ]);
}

function showConnectorContextMenu(event, connectorDatum) {
    // Derive editability from the parent module
    var parentModule = sceneData.modules.find(function (m) { return String(m.id) === String(connectorDatum.module_id); });
    if (!parentModule || parentModule.editable === false) return;
    if (connectorDatum.pending || connectorDatum.pending_delete || parentModule.pending) {
        showToast('This item is pending approval', 'error');
        return;
    }
    showContextMenu(event, [
        { icon: '\uD83D\uDD04', label: 'Reorder Pins', action: function () { openPinOrderDialog(connectorDatum); } },
        { icon: '\u2795', label: 'Add Pin', action: function () { addPinPrompt(connectorDatum); }, shortcut: 'N' },
        { icon: '\u2699\uFE0F', label: 'Edit Properties', action: function () { showConnectorEditDialog(connectorDatum); } },
        { icon: '\u274C', label: 'Delete', action: function () { deleteConnectorConfirm(connectorDatum); }, shortcut: 'Del' },
    ]);
}

function showPinContextMenu(event, pinDatum, connectorDatum) {
    // Derive editability from the parent module
    var parentModule = sceneData.modules.find(function (m) { return String(m.id) === String(connectorDatum.module_id); });
    if (!parentModule || parentModule.editable === false) return;
    if (pinDatum.pending || pinDatum.pending_delete || connectorDatum.pending || parentModule.pending) {
        showToast('This item is pending approval', 'error');
        return;
    }
    showContextMenu(event, [
        { icon: '\u2699\uFE0F', label: 'Edit Properties', action: function () { showPinEditDialog(pinDatum, connectorDatum); } },
        { icon: '\u274C', label: 'Delete', action: function () { deletePinConfirm(pinDatum); }, shortcut: 'Del' },
    ]);
}

function showInterfaceContextMenu(event, iface) {
    // Only show the context menu if the interface is editable
    if (iface.editable === false) return;
    showContextMenu(event, [
        { icon: '\uD83D\uDDD1\uFE0F', label: 'Delete Connection', action: function () { deleteInterfaceConfirm(iface); }, shortcut: 'Del' },
    ]);
}

function deleteInterfaceConfirm(iface) {
    if (bridge && confirm('Delete this connection?')) {
        bridge.delete_interface(iface.id);
    }
}

function showInterfaceDragHandleContextMenu(event, ifaceId, pointIndex, scene) {
    // Find the interface by ID
    var iface = null;
    for (var k = 0; k < scene.interfaces.length; k++) {
        if (scene.interfaces[k].id === ifaceId) {
            iface = scene.interfaces[k];
            break;
        }
    }
    
    // Only show the context menu if the interface is editable
    if (!iface || iface.editable === false) return;
    
    if (!iface || !iface.points || iface.points.length <= 3) {
        // Can't remove a pivot if there are only 2 endpoints + 0 interior points,
        // or only 1 interior point.
        // Actually need at least 3 points to have an interior pivot to remove
        // (2 endpoints + at least 1 interior pivot).
        // Minimum 3 points: start, interior, end. So removing one leaves 2 = straight line.
        // Only show if there are interior pivots to remove (points.length > 2).
        if (!iface || !iface.points || iface.points.length <= 2) return;
    }
    
    showContextMenu(event, [
        { icon: '\u2796', label: 'Remove Pivot', action: function () { removePivotFromInterface(ifaceId, pointIndex); } },
    ]);
}

function removePivotFromInterface(ifaceId, pointIndex) {
    // Find the interface in sceneData
    var iface = null;
    var ifaceIdx = -1;
    for (var k = 0; k < sceneData.interfaces.length; k++) {
        if (sceneData.interfaces[k].id === ifaceId) {
            iface = sceneData.interfaces[k];
            ifaceIdx = k;
            break;
        }
    }
    
    if (!iface || !iface.points || iface.points.length <= 2) return;
    
    // Remove the interior point at the given index (virtual — runtime only)
    iface.points.splice(pointIndex, 1);
    
    // If only 2 points remain (start + end), clear manual override so
    // the wire falls back to A* routing on next full re-render.  This
    // prevents a stale manual flag with no pivots (which would bypass
    // A* and potentially display a diagonal).
    if (iface.points.length <= 2) {
        iface._manualOverride = false;
        iface.manual_override = false;
    }
    
    // Re-render the interface layer
    g.select('.interfaces-layer').remove();
    renderInterfaces(sceneData, buildPinLookup(sceneData), new Set());
    
    // Persist the removal to DB so the change survives tab switches
    persistCurrentRoutes();
}

function renameModulePrompt(moduleDatum) {
    const newName = prompt('Module name:', moduleDatum.name);
    if (newName && newName.trim() && bridge) {
        bridge.rename_module(moduleDatum.id, newName.trim());
    }
}

function deleteModuleConfirm(moduleDatum) {
    if (bridge && confirm(`Delete module "${moduleDatum.name}" and all its connections?`)) {
        bridge.delete_module(moduleDatum.id);
    }
}

function addConnectorPrompt(moduleDatum) {
    if (!bridge) return;
    showConnectorDialog(moduleDatum);
}

function renameConnectorPrompt(connectorDatum) {
    const newName = prompt('Connector name:', connectorDatum.name);
    if (newName && newName.trim() && bridge) {
        bridge.rename_connector(connectorDatum.id, newName.trim());
    }
}

function deleteConnectorConfirm(connectorDatum) {
    if (bridge && confirm(`Delete connector "${connectorDatum.name}" and its pins/connections?`)) {
        bridge.delete_connector(connectorDatum.id);
    }
}

function addPinPrompt(connectorDatum) {
    if (!bridge) return;
    showPinDialog(connectorDatum);
}

function renamePinPrompt(pinDatum) {
    const newName = prompt('Pin name:', pinDatum.name);
    if (newName && newName.trim() && bridge) {
        bridge.rename_pin(pinDatum.id, newName.trim());
    }
}

function deletePinConfirm(pinDatum) {
    if (bridge && confirm(`Delete pin "${pinDatum.name}"?`)) {
        bridge.delete_pin(pinDatum.id);
    }
}

// Opens the native PinOrderDialog (Qt) via the bridge -- this call blocks
// on the Python/GUI thread until the modal dialog is closed, which is
// expected since it's only triggered by a deliberate double-click.
function openPinOrderDialog(connectorDatum) {
    if (IS_READONLY) return;
    if (!bridge) return;
    const pinNames = connectorDatum.pins.map(p => p.name);
    bridge.request_pin_order_dialog(connectorDatum.id, connectorDatum.name, JSON.stringify(pinNames));
}

// ---------------------------------------------------------------------
// Helper: copy manual interface points and update endpoints to match
// current pin positions from pinLookup.  Also prunes any interior points
// that became collinear after the endpoint move.
// ---------------------------------------------------------------------
function updateManualEndpoints(iface, pinLookup) {
    var pts = iface.points.map(function (p) {
        return Array.isArray(p) ? [p[0], p[1]] : [p.x, p.y];
    });
    var pinA = pinLookup[iface.from_pin];
    var pinB = pinLookup[iface.to_pin];
    if (pinA) pts[0] = [pinA.x, pinA.y];
    if (pinB) pts[pts.length - 1] = [pinB.x, pinB.y];
    return removeCollinearPoints(pts);
}

const ROUTE_MARGIN = 14; // must stay < CONNECTOR_STUB so pins sit outside inflated obstacle rects
const ROUTE_LEAD = 16;

// Render interfaces (wires between pins).
//   movedPinIds: null    → recompute ALL routes (full scene render)
//   movedPinIds: Set     → only recompute routes touching these pin IDs;
//                           ALL other interfaces use their cached points.
//   movedPinIds: empty Set → use cached points for everything.
function renderInterfaces(scene, pinLookup, movedPinIds) {
    const interfaceGroup = g.append('g').attr('class', 'interfaces-layer');
    const obstacleRects = scene.modules.map(m => ({ x: m.x, y: m.y, width: m.width, height: m.height }));
    // Build a set of hidden pin IDs for quick lookup
    const hiddenPins = new Set(scene.hidden_pins || []);

    // Determine which interfaces are power-related (connected to Power subsystem)
    var poweredInterfaceIds = _getPoweredInterfaceIds(scene);

    scene.interfaces.forEach(function (iface) {
        // Skip interfaces connected to hidden pins (unchecked in sidebar tree)
        if (hiddenPins.has(iface.from_pin) || hiddenPins.has(iface.to_pin)) return;

        // Determine points: prefer manual-override points (loaded from DB or runtime),
        // otherwise compute via A* router.
        var isManual = (iface.manual_override || iface._manualOverride);
        var points = null;

        if (isManual && iface.points && iface.points.length >= 2) {
            // Use the saved manual pivot points, but update endpoints to
            // match current pin positions so wires follow moved connectors.
            points = updateManualEndpoints(iface, pinLookup);
        } else {
            // Decide if this interface needs fresh A* routing
            var needsRecompute = !movedPinIds; // null → full recompute
            if (movedPinIds && movedPinIds.size > 0) {
                needsRecompute = movedPinIds.has(iface.from_pin) || movedPinIds.has(iface.to_pin);
            }

            points = (!needsRecompute && iface.points && iface.points.length >= 2) ? iface.points : null;

            if (!points && iface.from_pin && iface.to_pin) {
                var a = pinLookup[iface.from_pin];
                var b = pinLookup[iface.to_pin];
                if (a && b) {
                    points = computeRoutePoints(a, b, obstacleRects);
                }
            }
        }

        if (!points || points.length < 2) return;

        // Store points so persistence uses them
        iface.points = points;

        const line = d3.line().x(p => p[0] !== undefined ? p[0] : p.x).y(p => p[1] !== undefined ? p[1] : p.y);
        // Display the path through enforceOrthogonalRoute so diagonal segments
        // from user-placed pivots are automatically rendered with bend points.
        // The stored points (iface.points) are NOT modified — bend points are
        // display-only and never persisted.
        var displayPoints = isManual ? enforceOrthogonalRoute(points) : points;
        var isPowerIface = poweredInterfaceIds.has(iface.id);
        var ifacePendingCls = (iface.pending || iface.pending_delete)
            ? (iface.pending === 'delete' || iface.pending_delete ? ' pending-delete' : ' pending')
            : '';
        var path = interfaceGroup.append('path')
            .attr('class', (isPowerIface ? 'interface-path interface-powered' : 'interface-path') + ifacePendingCls)
            .attr('data-interface-id', iface.id)
            .attr('d', line(displayPoints))
            .on('click', function (event) {
                event.stopPropagation();
                if (event.shiftKey) {
                    // Shift+click: toggle this interface in/out of selection
                    if (selectedInterfaceIds.has(iface.id)) {
                        selectedInterfaceIds.delete(iface.id);
                    } else {
                        selectedInterfaceIds.add(iface.id);
                    }
                    selectedModuleIds.clear();
                } else {
                    selectedModuleIds.clear();
                    selectedInterfaceIds = new Set([iface.id]);
                }
                g.selectAll('.module-box').classed('selected', false);
                g.selectAll('.interface-path').classed('selected', function () {
                    return selectedInterfaceIds.has(Number(d3.select(this).attr('data-interface-id')));
                });
            })
            .on('contextmenu', function (event) {
                event.preventDefault();
                event.stopPropagation();
                showInterfaceContextMenu(event, iface);
            });

        // --- Wire dragging: show draggable handles only at interior vertices
        // of user-created manual overrides (persisted from DB) that the
        // current user has permission to edit.
        // A*-computed waypoints and virtual pivots get NO handles — they
        // are routing aids, not user-placed pivots.
        var isManual = (iface.manual_override || iface._manualOverride);
        if (isManual && !IS_READONLY && iface.editable !== false) {
            for (var i = 0; i < points.length; i++) {
                if (i === 0 || i === points.length - 1) continue;

                (function (pi) {
                    var pt = points[pi];
                    var cx = pt[0] !== undefined ? pt[0] : pt.x;
                    var cy = pt[1] !== undefined ? pt[1] : pt.y;

                    interfaceGroup.append('circle')
                        .attr('class', 'interface-drag-handle')
                        .attr('data-interface-id', iface.id)
                        .attr('data-point-index', pi)
                        .attr('cx', cx)
                        .attr('cy', cy)
                        .attr('r', 10)
                        .attr('fill', 'transparent')
                        .attr('stroke', 'var(--accent)')
                        .attr('stroke-width', 1.5)
                        .attr('stroke-dasharray', '2,2')
                        .attr('cursor', 'move')
                        .attr('opacity', 0)
                        .on('mouseenter', function () { d3.select(this).attr('opacity', 0.6); })
                        .on('mouseleave', function () { d3.select(this).attr('opacity', 0); })
                        .on('contextmenu', function (event) {
                            event.preventDefault();
                            event.stopPropagation();
                            showInterfaceDragHandleContextMenu(event, iface.id, pi, sceneData);
                        })
                        .call(d3.drag()
                            .on('start', function (event) {
                                event.sourceEvent.stopPropagation();
                                d3.select(this).raise().attr('opacity', 0.9).attr('fill', 'rgba(91, 141, 239, 0.35)');
                            })
                            .on('drag', function (event) {
                                var pt = d3.pointer(event.sourceEvent, g.node());
                                var newX = snapToGrid(pt[0]);
                                var newY = snapToGrid(pt[1]);
                                points[pi] = [newX, newY];
                                iface.points = points;
                                d3.select(this).attr('cx', newX).attr('cy', newY);
                                path.attr('d', line(enforceOrthogonalRoute(points)));
                            })
                            .on('end', function () {
                                d3.select(this).attr('opacity', 0.6).attr('fill', 'transparent');
                                // Remove collinear waypoints — our stored points should only
                                // contain the actual user-placed pivots, not intermediate
                                // points that lie on a straight line between other points.
                                var cleanPoints = removeCollinearPoints(iface.points);
                                iface.points = cleanPoints;
                                // Display with orthogonal enforcement (bend points are
                                // computed on-the-fly for display only, never stored).
                                var displayPts = enforceOrthogonalRoute(iface.points);
                                path.attr('d', line(displayPts));
                                persistCurrentRoutes();
                            })
                        );
                })(i);
            }
        }

        // --- Drag any point ON the path (not just vertices) to reroute ---
        // When user clicks and drags on a path segment, a new waypoint is
        // inserted at that position and becomes draggable.
        // Non-editable interfaces get no path drag behavior, and
        // read-only mode blocks it entirely.
        (function (iface, points, path, interfaceGroup, line) {
            if (IS_READONLY || iface.editable === false) return;
            var dragState = null;

            function distToSegmentSq(px, py, ax, ay, bx, by) {
                var dx = bx - ax, dy = by - ay;
                var lenSq = dx * dx + dy * dy;
                if (lenSq === 0) {
                    var ex = px - ax, ey = py - ay;
                    return ex * ex + ey * ey;
                }
                var t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
                t = Math.max(0, Math.min(1, t));
                var cx = ax + t * dx, cy = ay + t * dy;
                var rx = px - cx, ry = py - cy;
                return rx * rx + ry * ry;
            }

            path.call(d3.drag()
                .on('start', function (event) {
                    event.sourceEvent.stopPropagation();
                    var pt = d3.pointer(event.sourceEvent, g.node());
                    var px = pt[0], py = pt[1];

                    // Find nearest segment and insert a new vertex there
                    var bestDist = Infinity, bestIdx = -1, insertX = px, insertY = py;
                    for (var j = 0; j < points.length - 1; j++) {
                        var p1 = points[j], p2 = points[j + 1];
                        var a = [Array.isArray(p1) ? p1[0] : (p1.x !== undefined ? p1.x : p1[0]), Array.isArray(p1) ? p1[1] : (p1.y !== undefined ? p1.y : p1[1])];
                        var b = [Array.isArray(p2) ? p2[0] : (p2.x !== undefined ? p2.x : p2[0]), Array.isArray(p2) ? p2[1] : (p2.y !== undefined ? p2.y : p2[1])];
                        var dist = distToSegmentSq(px, py, a[0], a[1], b[0], b[1]);
                        if (dist < bestDist) {
                            bestDist = dist;
                            bestIdx = j;
                            // Closest point on the segment
                            var dx = b[0] - a[0], dy = b[1] - a[1];
                            var lenSq = dx * dx + dy * dy;
                            var t = lenSq > 0 ? Math.max(0, Math.min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / lenSq)) : 0;
                            insertX = a[0] + t * dx;
                            insertY = a[1] + t * dy;
                        }
                    }

                    if (bestIdx >= 0) {
                        dragState = { pointIndex: bestIdx + 1, hadDrag: false };
                        // Constrain the snapped insertion point to stay on the
                        // segment's axis. For vertical segments, snap Y only and
                        // keep X on the segment. For horizontal, snap X only.
                        // This prevents diagonal segments when existing points
                        // aren't perfectly grid-aligned.
                        var segAx = points[bestIdx], segBx = points[bestIdx + 1];
                        var ax = Array.isArray(segAx) ? segAx[0] : (segAx.x !== undefined ? segAx.x : segAx[0]);
                        var ay = Array.isArray(segAx) ? segAx[1] : (segAx.y !== undefined ? segAx.y : segAx[1]);
                        var bx = Array.isArray(segBx) ? segBx[0] : (segBx.x !== undefined ? segBx.x : segBx[0]);
                        var by = Array.isArray(segBx) ? segBx[1] : (segBx.y !== undefined ? segBx.y : segBx[1]);
                        if (Math.abs(ax - bx) < 0.001) {
                            // Vertical segment: keep X aligned, snap Y only
                            insertX = ax;
                            insertY = snapToGrid(insertY);
                        } else if (Math.abs(ay - by) < 0.001) {
                            // Horizontal segment: keep Y aligned, snap X only
                            insertX = snapToGrid(insertX);
                            insertY = ay;
                        } else {
                            // Diagonal or single-point segment: snap both
                            insertX = snapToGrid(insertX);
                            insertY = snapToGrid(insertY);
                        }
                        points.splice(bestIdx + 1, 0, [insertX, insertY]);
                        iface.points = points;
                        path.attr('d', line(points));

                        // Show a temporary drag handle at the insertion point
                        var handle = interfaceGroup.insert('circle', ':first-child')
                            .attr('r', 8)
                            .attr('fill', 'rgba(91, 141, 239, 0.45)')
                            .attr('stroke', 'var(--accent)')
                            .attr('stroke-width', 2)
                            .attr('cx', insertX)
                            .attr('cy', insertY);
                        dragState.handle = handle;
                    }
                })
                .on('drag', function (event) {
                    if (!dragState) return;
                    dragState.hadDrag = true;
                    var pt = d3.pointer(event.sourceEvent, g.node());
                    var sx = snapToGrid(pt[0]);
                    var sy = snapToGrid(pt[1]);
                    var idx = dragState.pointIndex;
                    points[idx] = [sx, sy];
                    iface.points = points;
                    // Show the orthogonal preview live during drag
                    path.attr('d', line(enforceOrthogonalRoute(points)));
                    if (dragState.handle) {
                        dragState.handle.attr('cx', sx).attr('cy', sy);
                    }
                })
                .on('end', function () {
                    if (!dragState) return;
                    
                    // Remove the temporary drag handle
                    if (dragState.handle) {
                        dragState.handle.remove();
                    }
                    
                    // If no actual drag occurred (just a click), undo the insertion
                    if (!dragState.hadDrag) {
                        points.splice(dragState.pointIndex, 1);
                        iface.points = points;
                        // Display via orthogonal enforcement so the path stays clean
                        path.attr('d', line(enforceOrthogonalRoute(points)));
                        dragState = null;
                        return;
                    }
                    
                    if (iface && iface.points && iface.points.length > 2) {
                        // User placed a pivot point on the wire — this IS a
                        // user-interacted point. Store it (without any bend points
                        // that enforceOrthogonalRoute might insert) and mark as
                        // manual override so the route is preserved.
                        // Collinear waypoints are pruned so stored points contain
                        // only the meaningful routing information.
                        var cleanPoints = removeCollinearPoints(iface.points);
                        iface.points = cleanPoints;
                        iface._manualOverride = true;
                        // Re-render the interface layer — renderInterfaces will
                        // display through enforceOrthogonalRoute for a clean
                        // orthogonal path while keeping stored points pristine.
                        g.select('.interfaces-layer').remove();
                        renderInterfaces(sceneData, buildPinLookup(sceneData), new Set());
                        // Persist the new pivot to DB immediately so it survives
                        // tab switches / scene reloads.
                        persistCurrentRoutes();
                    }
                    dragState = null;
                })
            );
        })(iface, points, path, interfaceGroup, line);

        // --- Wire current indicator ---
        // The current belongs to the connection (set when it is created), so
        // show it right on the wire: a small label at the route midpoint.
        var wireCurrent = Number(iface.current) || 0;
        if (wireCurrent > 0 && displayPoints && displayPoints.length >= 2) {
            var midIdx = Math.floor((displayPoints.length - 1) / 2);
            var midPt = displayPoints[midIdx];
            var mx = midPt[0] !== undefined ? midPt[0] : midPt.x;
            var my = midPt[1] !== undefined ? midPt[1] : midPt.y;
            interfaceGroup.append('text')
                .attr('class', 'wire-current-label')
                .attr('data-interface-id', iface.id)
                .attr('x', mx + 5)
                .attr('y', my - 5)
                .attr('text-anchor', 'middle')
                .text(formatCurrentText(wireCurrent));
        }
    });
}

// Human-friendly current label: A for >= 1000 mA, mA otherwise.
function formatCurrentText(mA) {
    mA = Number(mA) || 0;
    if (mA >= 1000) return (mA / 1000) + ' A';
    return (Math.round(mA * 10) / 10) + ' mA';
}

// Uses the ported A* orthogonal router from schematic_routing.js when
// available; falls back to a straight line so the connection never just
// disappears if that script failed to load.
// After any drag operation, scan the route and insert bend points wherever
// a diagonal segment exists. This ensures the wire always stays orthogonal
// (horizontal + vertical segments only) even when pivot points are dragged
// to positions that don't align with their neighbors.
function enforceOrthogonalRoute(pts) {
    if (!pts || pts.length < 2) return pts;

    // Special case: 2-point diagonal — insert a single bend so even
    // a manual route with only endpoints (no user pivots) displays
    // as horizontal-then-vertical rather than a straight diagonal.
    if (pts.length === 2) {
        var p0 = pts[0], p1 = pts[1];
        var x0 = Array.isArray(p0) ? p0[0] : (p0.x !== undefined ? p0.x : 0);
        var y0 = Array.isArray(p0) ? p0[1] : (p0.y !== undefined ? p0.y : 0);
        var x1 = Array.isArray(p1) ? p1[0] : (p1.x !== undefined ? p1.x : 0);
        var y1 = Array.isArray(p1) ? p1[1] : (p1.y !== undefined ? p1.y : 0);
        if (Math.abs(x0 - x1) > 0.001 && Math.abs(y0 - y1) > 0.001) {
            // Diagonal: insert bend at (x0, y1) — go horizontal first, then vertical
            var bend = Array.isArray(p0) ? [x0, y1] : { x: x0, y: y1 };
            return [pts[0], bend, pts[1]];
        }
        return pts;
    }

    var result = [pts[0]];
    for (var i = 1; i < pts.length; i++) {
        var prev = result[result.length - 1];
        var cur = pts[i];
        var px = Array.isArray(prev) ? prev[0] : prev.x;
        var py = Array.isArray(prev) ? prev[1] : prev.y;
        var cx = Array.isArray(cur) ? cur[0] : cur.x;
        var cy = Array.isArray(cur) ? cur[1] : cur.y;
        
        if (Math.abs(px - cx) > 0.001 && Math.abs(py - cy) > 0.001) {
            // Diagonal segment — insert a bend point using prev's X and cur's Y
            // to create an L-shaped (horizontal-then-vertical) path
            var bend = Array.isArray(cur) ? [px, cy] : { x: px, y: cy };
            result.push(bend);
        }
        result.push(cur);
    }
    return result;
}

// ---------------------------------------------------------------------
// Remove pivot points that lie on a straight line between neighbors.
// A point is collinear (unnecessary) when it shares the same x-coordinate
// with both neighbors (vertical line) OR the same y-coordinate with both
// neighbors (horizontal line). Such points add zero routing information
// and can be safely pruned.
// ---------------------------------------------------------------------
function removeCollinearPoints(points) {
    if (!points || points.length < 3) return points;
    var result = [points[0]];
    for (var i = 1; i < points.length - 1; i++) {
        var prev = result[result.length - 1];
        var cur = points[i];
        var next = points[i + 1];
        var px = Array.isArray(prev) ? prev[0] : prev.x;
        var py = Array.isArray(prev) ? prev[1] : prev.y;
        var cx = Array.isArray(cur) ? cur[0] : cur.x;
        var cy = Array.isArray(cur) ? cur[1] : cur.y;
        var nx = Array.isArray(next) ? next[0] : next.x;
        var ny = Array.isArray(next) ? next[1] : next.y;

        // Same x (vertical line) or same y (horizontal line) with both neighbors?
        var sameX = (Math.abs(px - cx) <= 0.001 && Math.abs(cx - nx) <= 0.001);
        var sameY = (Math.abs(py - cy) <= 0.001 && Math.abs(cy - ny) <= 0.001);

        if (sameX || sameY) {
            // This point is collinear — skip it; the line runs straight through
            continue;
        }
        result.push(cur);
    }
    result.push(points[points.length - 1]);
    return result;
}

function snapRoutePointsToGrid(points) {
    if (!points) return points;
    return points.map(function (p) {
        var x = p[0] !== undefined ? p[0] : p.x;
        var y = p[1] !== undefined ? p[1] : p.y;
        var sx = snapToGrid(x);
        var sy = snapToGrid(y);
        return Array.isArray(p) ? [sx, sy] : { x: sx, y: sy };
    });
}

function computeRoutePoints(fromPin, toPin, obstacleRects) {
    if (window.SchematicRouting) {
        const routed = window.SchematicRouting.routeOrthogonal(
            fromPin, toPin, obstacleRects, ROUTE_MARGIN, ROUTE_LEAD
        );
        return snapRoutePointsToGrid(routed.map(p => [p.x, p.y]));
    }
    var direct = [[fromPin.x, fromPin.y], [toPin.x, toPin.y]];
    return snapRoutePointsToGrid(direct);
}

// A module can be dragged/resized when it is editable and not a pending
// create or pending delete. The schematic view is read-only for everyone
// except the system admin (subsystem admins included), so `IS_READONLY`
// short-circuits before any of the pending-state checks.
function isDraggableModule(d) {
    if (IS_READONLY || d == null || d.editable === false) return false;
    if (d.pending_delete || d.pending === 'create' || d.pending === 'delete') return false;
    return true;
}

// ---------------------------------------------------------------------
// Drag-to-move (auto-saves each module's new position on drag end)
// ---------------------------------------------------------------------
function dragBehavior() {
    return d3.drag()
        .filter(function (event, d) {
            // Left button only. d3.drag's default filter already restricts
            // to the primary button, but a custom filter replaces it — a
            // right-click must NEVER start a drag, or it will capture the
            // mouse and swallow the contextmenu event on Windows.
            return event.button === 0 && isDraggableModule(d);
        })
        .on('start', function (event, d) {
            d3.select(this).raise();
            // Record initial positions of ALL selected modules for batch drag
            batchDragState = { startPositions: {} };
            var idsToMove = new Set(selectedModuleIds);
            if (!idsToMove.has(d.id)) {
                if (event.sourceEvent && event.sourceEvent.shiftKey) {
                    // Shift+drag: add to existing selection
                    idsToMove.add(d.id);
                } else {
                    // Normal drag: replace selection with just this module
                    idsToMove = new Set([d.id]);
                }
                selectedModuleIds = idsToMove;
                g.selectAll('.module-box').classed('selected', function (dd) { return selectedModuleIds.has(dd.id); });
            }
            // Filter out modules that can't move from a batch drag
            idsToMove.forEach(function (mid) {
                var mod = sceneData.modules.find(function (m) { return m.id === mid; });
                if (mod != null && !isDraggableModule(mod)) {
                    idsToMove.delete(mid);
                }
            });
            // If after filtering the dragged module itself is no longer editable, abort
            if (!idsToMove.has(d.id)) {
                batchDragState = null;
                return;
            }
            idsToMove.forEach(function (mid) {
                var mod = sceneData.modules.find(function (m) { return m.id === mid; });
                if (mod) {
                    batchDragState.startPositions[mid] = { x: mod.x, y: mod.y };
                }
            });

            // Store original interior pivot points of manual_override interfaces connected to moved modules
            batchDragState.originalPivots = {};
            var movedPinIds = new Set();
            idsToMove.forEach(function (mid) {
                sceneData.connectors.forEach(function (c) {
                    if (String(c.module_id) === String(mid)) {
                        c.pins.forEach(function (p) { movedPinIds.add(p.id); });
                    }
                });
            });
            sceneData.interfaces.forEach(function (iface) {
                if ((iface._manualOverride || iface.manual_override) && iface.points && iface.points.length > 2) {
                    if (movedPinIds.has(iface.from_pin) || movedPinIds.has(iface.to_pin)) {
                        var originals = [];
                        for (var pi = 1; pi < iface.points.length - 1; pi++) {
                            var pt = iface.points[pi];
                            originals.push({ x: Array.isArray(pt) ? pt[0] : pt.x, y: Array.isArray(pt) ? pt[1] : pt.y });
                        }
                        batchDragState.originalPivots[iface.id] = originals;
                    }
                }
            });
        })
        .on('drag', function (event, d) {
            if (!batchDragState) return;
            // Compute delta from the dragged module's start position
            var startPos = batchDragState.startPositions[d.id];
            if (!startPos) {
                // Fallback: just move the dragged module
                d.x = event.x;
                d.y = event.y;
                d3.select(this).attr('transform', `translate(${d.x}, ${d.y})`);
                updateInterfacesInPlace(d);
                updateSubsystemHalosInPlace(sceneData);
                return;
            }
            var dx = event.x - startPos.x;
            var dy = event.y - startPos.y;

            // Move ALL selected modules by the same delta
            var modulesToUpdate = [];
            selectedModuleIds.forEach(function (mid) {
                var mod = sceneData.modules.find(function (m) { return m.id === mid; });
                if (!mod) return;
                var sp = batchDragState.startPositions[mid];
                if (!sp) return;
                mod.x = sp.x + dx;
                mod.y = sp.y + dy;
                modulesToUpdate.push(mod);
                // Update the DOM transform for this module
                g.selectAll('.module-box').filter(function (md) { return md.id === mod.id; })
                    .attr('transform', `translate(${mod.x}, ${mod.y})`);
            });

            // Shift interior pivot points of manual_override interfaces to follow the batch drag
            if (batchDragState.originalPivots) {
                Object.keys(batchDragState.originalPivots).forEach(function (ifaceId) {
                    var iface = sceneData.interfaces.find(function (i) { return String(i.id) === ifaceId; });
                    if (!iface || !iface.points || iface.points.length < 2) return;
                    var originals = batchDragState.originalPivots[ifaceId];
                    for (var pi = 1; pi < iface.points.length - 1; pi++) {
                        var orig = originals[pi - 1];
                        if (orig && Array.isArray(iface.points[pi])) {
                            iface.points[pi][0] = orig.x + dx;
                            iface.points[pi][1] = orig.y + dy;
                        }
                    }
                });
            }

            // Update wire routing for all moved modules
            modulesToUpdate.forEach(function (mod) {
                updateInterfacesInPlace(mod);
            });
            // Recalculate halo bounds from scratch using current module and wire positions
            updateSubsystemHalosInPlace(sceneData);
        })
        .on('end', function (event, d) {
            if (!bridge) return;

            // Update manual-override interface endpoints for ALL moved modules
            // (interior pivot points were already shifted during drag)
            var pinLookup = buildPinLookup(sceneData);
            var movedPinIds = new Set();
            var movedModIds = selectedModuleIds.size > 0 ? selectedModuleIds : new Set([d.id]);
            movedModIds.forEach(function (mid) {
                sceneData.connectors.forEach(function (c) {
                    if (String(c.module_id) === String(mid)) {
                        c.pins.forEach(function (p) { movedPinIds.add(p.id); });
                    }
                });
            });
            var anyManualUpdated = false;
            sceneData.interfaces.forEach(function (iface) {
                if ((iface._manualOverride || iface.manual_override) && iface.points && iface.points.length >= 2) {
                    var updated = false;
                    var pinA = pinLookup[iface.from_pin];
                    var pinB = pinLookup[iface.to_pin];
                    if (movedPinIds.has(iface.from_pin) && pinA) {
                        iface.points[0] = [pinA.x, pinA.y];
                        updated = true;
                    }
                    if (movedPinIds.has(iface.to_pin) && pinB) {
                        iface.points[iface.points.length - 1] = [pinB.x, pinB.y];
                        updated = true;
                    }
                    if (updated) {
                        iface.points = removeCollinearPoints(iface.points);
                        anyManualUpdated = true;
                    }
                }
            });
            if (anyManualUpdated) {
                g.select('.interfaces-layer').remove();
                renderInterfaces(sceneData, pinLookup, new Set());
                // Re-apply selection highlight after re-render
                g.selectAll('.interface-path').classed('selected', function () {
                    return selectedInterfaceIds.has(Number(d3.select(this).attr('data-interface-id')));
                });
            }

            // Save positions of ALL moved modules
            var payload = {};
            movedModIds.forEach(function (mid) {
                var mod = sceneData.modules.find(function (m) { return m.id === mid; });
                if (mod) {
                    payload[mid] = { x: mod.x, y: mod.y };
                }
            });
            if (Object.keys(payload).length > 0) {
                bridge.save_module_positions(JSON.stringify(payload));
            }
            // Persist routing so wires stay at their new positions on scene reload.
            // The bridge silently skips interfaces the user can't edit.
            persistCurrentRoutes();
            // Force a full halo redraw to ensure bounds are correct after the drag
            g.select('.subsystem-halos').remove();
            renderSubsystemHalos(sceneData);
            batchDragState = null;
        });
}

// ---------------------------------------------------------------------
// Module resize behavior with 8 handles (corners + edge midpoints)
// Updates connectors, pins, AND interfaces in real-time during resize.
// ---------------------------------------------------------------------
function resizeBehavior(direction) {
    return d3.drag()
        .filter(function (event, d) {
            return event.button === 0 && isDraggableModule(d);
        })
        .on('start', function (event, d) {
            event.sourceEvent.stopPropagation();
            d3.select(this).raise();
            // Show all resize handles on the module being resized
            d3.select(this.parentNode).selectAll('.resize-handle')
                .transition().duration(100)
                .attr('opacity', 1)
                .attr('fill', 'rgba(91, 141, 239, 0.2)');

            // Store initial t-fractions for all connectors so we can
            // recompute their positions relative to the new module size
            d._connectorFractions = {};
            sceneData.connectors.forEach(function (c) {
                if (String(c.module_id) !== String(d.id)) return;
                var side = normalizeSide(c.side);
                var normal = SIDE_AXIS[side].normal;
                var oldW = Math.max(MODULE_MIN_WIDTH, d.width);
                var oldH = Math.max(MODULE_MIN_HEIGHT, d.height);
                var t;
                if (side === 'top' || side === 'bottom') {
                    // c.x = t * oldW + normal.x * CONNECTOR_STUB
                    t = (c.x - normal.x * CONNECTOR_STUB) / oldW;
                } else {
                    // c.y = t * oldH + normal.y * CONNECTOR_STUB
                    t = (c.y - normal.y * CONNECTOR_STUB) / oldH;
                }
                d._connectorFractions[c.id] = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, t));
            });
        })
        .on('drag', function (event, d) {
            var minW = MODULE_MIN_WIDTH;
            var minH = MODULE_MIN_HEIGHT;
            var w = d.width;
            var h = d.height;
            var dx = event.dx;
            var dy = event.dy;
            const hh = 7; // half handle size (14/2)

            // Adjust width/height and position based on which handle is dragged
            if (direction.indexOf('e') >= 0) {
                w = Math.max(minW, d.width + dx);
            }
            if (direction.indexOf('w') >= 0) {
                w = Math.max(minW, d.width - dx);
                d.x = d.x + (d.width - w);
            }
            if (direction.indexOf('s') >= 0) {
                h = Math.max(minH, d.height + dy);
            }
            if (direction.indexOf('n') >= 0) {
                h = Math.max(minH, d.height - dy);
                d.y = d.y + (d.height - h);
            }

            d.width = w;
            d.height = h;

            var modGroup = d3.select(this.parentNode);

            // Update module group position
            modGroup.attr('transform', 'translate(' + d.x + ',' + d.y + ')');

            // Update rect size
            modGroup.select('.module-rect')
                .attr('width', w)
                .attr('height', h);

            // Update label position
            modGroup.select('.module-label')
                .attr('x', w / 2)
                .attr('y', h / 2 - 6);

            // Update power label position
            modGroup.select('.module-power-label')
                .attr('x', w / 2)
                .attr('y', h / 2 + 12);

            // Reposition all resize handles
            modGroup.select('.resize-nw').attr('x', -hh).attr('y', -hh);
            modGroup.select('.resize-ne').attr('x', w - hh).attr('y', -hh);
            modGroup.select('.resize-sw').attr('x', -hh).attr('y', h - hh);
            modGroup.select('.resize-se').attr('x', w - hh).attr('y', h - hh);
            modGroup.select('.resize-n').attr('x', w / 2 - hh).attr('y', -hh);
            modGroup.select('.resize-s').attr('x', w / 2 - hh).attr('y', h - hh);
            modGroup.select('.resize-w').attr('x', -hh).attr('y', h / 2 - hh);
            modGroup.select('.resize-e').attr('x', w - hh).attr('y', h / 2 - hh);

            // Update connector positions and ALL their SVG elements (connector line,
            // handle, bbox, label + pin circles + pin labels) in real-time
            updateModuleConnectorsSvg(d, modGroup);

            // Update interfaces (wires) — in-place, no flicker
            updateInterfacesInPlace(d);
            // Update subsystem halos in-place to follow the resized module
            updateSubsystemHalosInPlace(sceneData);
        })
        .on('end', function (event, d) {
            // Hide resize handles
            d3.select(this.parentNode).selectAll('.resize-handle')
                .transition().duration(200)
                .attr('opacity', 0)
                .attr('fill', 'transparent');

            // Clean up stored fractions
            delete d._connectorFractions;
            // Mark this module as explicitly sized so subsequent renders
            // (tab switch) don't auto-shrink it.
            d._sized = true;

            if (!bridge) return;
            // Save module position AND size to DB
            bridge.save_module_positions(JSON.stringify({
                [d.id]: { x: d.x, y: d.y, width: d.width, height: d.height }
            }));
            persistCurrentRoutes();
        });
}

// Update ALL connector and pin SVG elements for a module in real-time.
// Called during module resize and module drag to keep visuals in sync.
function updateModuleConnectorsSvg(module, modGroup) {
    var w = Math.max(MODULE_MIN_WIDTH, module.width);
    var h = Math.max(MODULE_MIN_HEIGHT, module.height);
    var fractions = module._connectorFractions || {};

    sceneData.connectors.forEach(function (c) {
        if (String(c.module_id) !== String(module.id)) return;
        var side = normalizeSide(c.side);
        var normal = SIDE_AXIS[side].normal;
        var tangent = SIDE_AXIS[side].tangent;

        // Recompute connector position using stored fraction t (captured at resize start).
        // If no stored fraction (should never happen during normal drag), derive from
        // current c.x/c.y against current module dimensions.
        var t = fractions[c.id];
        if (t === undefined) {
            if (side === 'top' || side === 'bottom') {
                t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, c.x / w));
            } else {
                t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, c.y / h));
            }
        }
        t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, t));

        var ep = edgePoint(module, side, t);
        c.x = ep.x + normal.x * CONNECTOR_STUB;
        c.y = ep.y + normal.y * CONNECTOR_STUB;

        // 1. Update connector line (from module edge to stub tip)
        var edge = connectorEdgeAnchor(module, c);
        modGroup.select('.connector-line[data-connector-id="' + c.id + '"]')
            .attr('x1', edge.x).attr('y1', edge.y)
            .attr('x2', c.x).attr('y2', c.y);

        // 2. Update drag handle (diamond at stub tip)
        var hs = 6;
        modGroup.select('.connector-drag-handle[data-connector-id="' + c.id + '"]')
            .attr('d', 'M' + c.x + ',' + (c.y - hs) + ' L' + (c.x + hs) + ',' + c.y + ' L' + c.x + ',' + (c.y + hs) + ' L' + (c.x - hs) + ',' + c.y + ' Z');

        // 3. Update connector bounding box
        var bbox = connectorBBox(module, c);
        modGroup.select('.connector-bbox[data-connector-id="' + c.id + '"]')
            .attr('x', bbox.x).attr('y', bbox.y)
            .attr('width', bbox.w).attr('height', bbox.h);
        modGroup.select('.connector-hitbox[data-connector-id="' + c.id + '"]')
            .attr('x', bbox.x).attr('y', bbox.y)
            .attr('width', bbox.w).attr('height', bbox.h);

        // 4. Update connector label position (using data-connector-id for direct selection)
        var isHoriz = (side === 'top' || side === 'bottom');
        var labelOffX = isHoriz ? 10 : (side === 'left' ? -8 : 8);
        var labelOffY = -6;
        modGroup.select('.connector-label[data-connector-id="' + c.id + '"]')
            .attr('x', c.x + labelOffX)
            .attr('y', c.y + labelOffY)
            .attr('text-anchor', isHoriz ? 'start' : (side === 'left' ? 'end' : 'start'));

        // 5. Update pin symbols and labels
        var count = c.pins.length;
        var mid = connectorBodyMidpoint(module, c);

        c.pins.forEach(function (p, i) {
            var offset = -(count - 1) * PIN_HALF_STEP + i * (PIN_HALF_STEP * 2);
            var px = mid.x + tangent.x * offset;
            var py = mid.y + tangent.y * offset;

            // Move pin symbol group
            modGroup.select('g.pin-circle[data-pin-id="' + p.id + '"]')
                .attr('transform', 'translate(' + px + ',' + py + ')');

            // Move pin label
            var isVert = (side === 'top' || side === 'bottom');
            var labelDx = isVert ? 0 : (side === 'left' ? -PIN_RADIUS - 3 : PIN_RADIUS + 3);
            var labelDy = isVert ? (side === 'top' ? -PIN_RADIUS - 3 : PIN_RADIUS + 11) : 0;
            var anchor = isVert ? 'middle' : (side === 'left' ? 'end' : 'start');

            modGroup.select('text.pin-label[data-pin-id="' + p.id + '"]')
                .attr('x', px + labelDx)
                .attr('y', py + labelDy)
                .attr('text-anchor', anchor);

            // Move data type caption
            var tDx = 0, tDy = 0, tAnchor = 'middle';
            if (side === 'right') { tDx = -PIN_RADIUS - 4; tAnchor = 'end'; }
            else if (side === 'left') { tDx = PIN_RADIUS + 4; tAnchor = 'start'; }
            else if (side === 'top') { tDy = PIN_RADIUS + 4; }
            else { tDy = -PIN_RADIUS - 4; }
            modGroup.select('text.pin-type-label[data-pin-id="' + p.id + '"]')
                .attr('x', px + tDx)
                .attr('y', py + tDy)
                .attr('text-anchor', tAnchor);
        });
    });
}

// Show resize handles on module hover (via CSS might not work for dynamically added elements)
// Override the module-box hover to toggle handles

// Recompute every interface's route against the current module layout and
// push the result to the DB via bridge.save_routing(). Called once per
// drag (on 'end'), not on every drag frame -- routing.js recomputation is
// cheap for a handful of modules but there is no need to hit the DB that often.
function persistCurrentRoutes() {
    if (!bridge) return;
    const pinLookup = buildPinLookup(sceneData);
    const obstacleRects = sceneData.modules.map(m => ({ x: m.x, y: m.y, width: m.width, height: m.height }));
    const payload = {};

    sceneData.interfaces.forEach(function (iface) {
        if ((iface._manualOverride || iface.manual_override) && iface.points && iface.points.length >= 2) {
            // Clean collinear waypoints before saving — only the actual user-placed
            // pivots get persisted. Intermediate points on straight lines add no
            // routing information and would otherwise multiply on repeated edits.
            var cleanPoints = removeCollinearPoints(iface.points);
            iface.points = cleanPoints;
            payload[iface.id] = { points: cleanPoints, manual_override: true, locked: false };
        } else {
            // Compute the route via A* orthogonal router
            const a = pinLookup[iface.from_pin];
            const b = pinLookup[iface.to_pin];
            if (!a || !b) return;
            const points = computeRoutePoints(a, b, obstacleRects);
            iface.points = points; // keep in-memory scene consistent
            payload[iface.id] = { points: points, manual_override: false, locked: false };
        }
    });

    if (Object.keys(payload).length) {
        bridge.save_routing(JSON.stringify(payload));
    }
}

// Update interface paths in-place (no DOM removal/recreation) to avoid flicker.
// Only re-routes wires connected to the moved module.
function updateInterfacesInPlace(movedModule) {
    const pinLookup = buildPinLookup(sceneData);
    const obstacleRects = sceneData.modules.map(m => ({ x: m.x, y: m.y, width: m.width, height: m.height }));
    const hiddenPins = new Set(sceneData.hidden_pins || []);

    // Collect pin IDs belonging to the moved module
    var movedPinIds = new Set();
    sceneData.connectors.forEach(function (c) {
        if (String(c.module_id) === String(movedModule.id)) {
            c.pins.forEach(function (p) {
                // Skip hidden pins — their wiring is not shown
                if (!hiddenPins.has(p.id)) movedPinIds.add(p.id);
            });
        }
    });

    sceneData.interfaces.forEach(function (iface) {
        // Skip interfaces connected to hidden pins
        if (hiddenPins.has(iface.from_pin) || hiddenPins.has(iface.to_pin)) return;
        if (!movedPinIds.has(iface.from_pin) && !movedPinIds.has(iface.to_pin)) return;

        // For manual-override wires, preserve the user's pivot points and
        // only update the endpoints that connect to the moved module's pins.
        // The interior pivots stay in their absolute workspace positions —
        // the wire routes from the new pin position through those pivots to
        // the other pin.  Display via enforceOrthogonalRoute so bend points
        // are computed on-the-fly and never stored.
        // For auto-routed wires, compute a fresh A* route.
        var isManual = (iface._manualOverride || iface.manual_override);
        var points;

        if (isManual && iface.points && iface.points.length >= 2) {
            points = updateManualEndpoints(iface, pinLookup);
            points = enforceOrthogonalRoute(points);
        } else {
            var a = pinLookup[iface.from_pin];
            var b = pinLookup[iface.to_pin];
            if (!a || !b) return;
            points = computeRoutePoints(a, b, obstacleRects);
        }

        if (!points || points.length < 2) return;

        const line = d3.line().x(p => p[0] !== undefined ? p[0] : p.x).y(p => p[1] !== undefined ? p[1] : p.y);
        var path = g.select('.interface-path[data-interface-id="' + iface.id + '"]');
        if (path.node()) {
            path.attr('d', line(points));
        }
        // Keep the wire current label glued to the route midpoint
        var wl = g.select('.wire-current-label[data-interface-id="' + iface.id + '"]');
        if (wl.node() && points.length >= 2) {
            var li = Math.floor((points.length - 1) / 2);
            var lp = points[li];
            wl.attr('x', (lp[0] !== undefined ? lp[0] : lp.x) + 5)
              .attr('y', (lp[1] !== undefined ? lp[1] : lp.y) - 5);
        }
    });
}

// Full redraw (removes and recreates) — used only on resize end or full render.
// NOTE: During interactive resize/drag, use updateInterfacesInPlace() instead
// to avoid flicker.
function redrawConnectorsFor(movedModule) {
    const pinLookup = buildPinLookup(sceneData);

    // Collect all pin IDs belonging to the module being dragged
    var movedPinIds = new Set();
    sceneData.connectors.forEach(function (c) {
        if (String(c.module_id) === String(movedModule.id)) {
            c.pins.forEach(function (p) { movedPinIds.add(p.id); });
        }
    });

    g.select('.interfaces-layer').remove();
    renderInterfaces(sceneData, pinLookup, movedPinIds);
    // Also update subsystem halos dynamically
    g.select('.subsystem-halos').remove();
    renderSubsystemHalos(sceneData);
}

// ---------------------------------------------------------------------
// Fit / export contract expected by schematic_view_tab.py
// (same functions, same names, as Component_Tree_Window.py's tree.html)
// ---------------------------------------------------------------------
function setExportOverlayVisible(visible) {
    const overlay = document.getElementById('zoom-info');
    if (overlay) overlay.style.display = visible ? 'block' : 'none';
}

function getExportBounds() {
    if (!g || !g.node()) {
        return JSON.stringify({ widthPx: 1200, heightPx: 800 });
    }
    const bbox = g.node().getBBox();
    const padding = 40;
    const widthPx = Math.max(320, Math.ceil(bbox.width + padding));
    const heightPx = Math.max(240, Math.ceil(bbox.height + padding));
    return JSON.stringify({ widthPx, heightPx });
}

// ---------------------------------------------------------------------
// Zoom state save/restore for export — saves the current d3-zoom transform
// before resizing for PDF/PNG export, so the user's view can be restored
// without any visible animation or size change.
// ---------------------------------------------------------------------
function getZoomState() {
    if (!g || !g.node()) return '{"x":0,"y":0,"k":1}';
    var t = d3.zoomTransform(g.node());
    return JSON.stringify({x: t.x, y: t.y, k: t.k});
}

function setZoomState(json) {
    if (!g || !g.node() || !svg) return;
    try {
        var state = JSON.parse(json);
        svg.call(zoom.transform, d3.zoomIdentity.translate(state.x, state.y).scale(state.k));
    } catch (e) {
        console.error('Error in setZoomState:', e);
    }
}

function fitView(duration) {
    if (!g.node() || !sceneData) return;
    if (duration === undefined) duration = 650;
    try {
        requestAnimationFrame(() => {
            const parent = svg.node().getBoundingClientRect();
            const width = parent.width;
            const height = parent.height;
            if (width <= 0 || height <= 0) return;

            // Compute bounding box from actual scene data (modules + interfaces)
            // instead of g.node().getBBox() which includes the giant grid
            // background rect (20000x20000), making fitView zoom out to nothing.
            var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

            sceneData.modules.forEach(function (m) {
                var mx = Number(m.x) || 0;
                var my = Number(m.y) || 0;
                var mw = Math.max(Number(m.width) || 120, 120);
                var mh = Math.max(Number(m.height) || 60, 60);
                if (mx < minX) minX = mx;
                if (my < minY) minY = my;
                if (mx + mw > maxX) maxX = mx + mw;
                if (my + mh > maxY) maxY = my + mh;
            });

            sceneData.interfaces.forEach(function (iface) {
                if (!iface.points) return;
                iface.points.forEach(function (pt) {
                    var x = pt[0] !== undefined ? pt[0] : (pt.x !== undefined ? pt.x : 0);
                    var y = pt[1] !== undefined ? pt[1] : (pt.y !== undefined ? pt.y : 0);
                    if (x < minX) minX = x;
                    if (y < minY) minY = y;
                    if (x > maxX) maxX = x;
                    if (y > maxY) maxY = y;
                });
            });

            if (!isFinite(minX) || !isFinite(maxX) || maxX <= minX || maxY <= minY) {
                // No content — center on origin with a default zoom
                svg.transition()
                    .duration(duration)
                    .call(zoom.transform, d3.zoomIdentity.translate(width / 2, height / 2).scale(0.5));
                return;
            }

            var bboxW = maxX - minX;
            var bboxH = maxY - minY;

            const pad = 40;
            const scaleX = (width - 2 * pad) / bboxW;
            const scaleY = (height - 2 * pad) / bboxH;
            let scale = Math.min(scaleX, scaleY);

            const minK = 0.1, maxK = 6;
            scale = Math.max(minK, Math.min(maxK, scale));

            const cx = (minX + maxX) / 2;
            const cy = (minY + maxY) / 2;
            const tx = (width / 2) - scale * cx;
            const ty = (height / 2) - scale * cy;

            svg.transition()
                .duration(duration)
                .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
        });
    } catch (e) {
        console.error('Error in fitView:', e);
    }
}

// ---------------------------------------------------------------------
// Manual "Save Layout" button (schematic_view_tab.py -> save_layout())
// ---------------------------------------------------------------------
function triggerSaveLayout() {
    if (IS_READONLY) {
        showToast('Read-only view — nothing to save.', 'error');
        return;
    }
    if (!bridge) return;

    const modulePositions = {};
    sceneData.modules.forEach(function (m) {
        modulePositions[m.id] = { x: m.x, y: m.y };
    });
    bridge.save_module_positions(JSON.stringify(modulePositions));

    const connectorPositions = {};
    sceneData.connectors.forEach(function (c) {
        connectorPositions[c.id] = { x: c.x, y: c.y };
    });
    bridge.save_connector_positions(JSON.stringify(connectorPositions));
}

// Reorder connectors in sceneData.connectors so that the dragged connector
// appears at the position matching where it was dropped among its peers
// on the same module+side. This enables reordering: when
// assignFallbackConnectorPositions redistributes evenly, the new array
// order determines the visual order.
function reorderConnectorsAfterDrag(module, draggedConnector, currentSide) {
    // Collect all connectors on the same module+side (excluding the dragged one)
    var sameSide = [];
    sceneData.connectors.forEach(function (conn) {
        if (String(conn.module_id) === String(module.id) &&
            normalizeSide(conn.side) === currentSide &&
            conn.id !== draggedConnector.id) {
            sameSide.push(conn);
        }
    });
    
    if (sameSide.length === 0) return;
    
    // Calculate t-fraction for each connector
    var w = Math.max(MODULE_MIN_WIDTH, module.width);
    var h = Math.max(MODULE_MIN_HEIGHT, module.height);
    var draggedT;
    if (currentSide === 'top' || currentSide === 'bottom') {
        draggedT = (draggedConnector.x - SIDE_AXIS[currentSide].normal.x * CONNECTOR_STUB) / w;
    } else {
        draggedT = (draggedConnector.y - SIDE_AXIS[currentSide].normal.y * CONNECTOR_STUB) / h;
    }
    
    // Find insertion position: count how many connectors are "before" the dragged one
    var insertBeforeIdx = 0;
    sameSide.forEach(function (other) {
        var otherT;
        if (currentSide === 'top' || currentSide === 'bottom') {
            otherT = (other.x - SIDE_AXIS[currentSide].normal.x * CONNECTOR_STUB) / w;
        } else {
            otherT = (other.y - SIDE_AXIS[currentSide].normal.y * CONNECTOR_STUB) / h;
        }
        if (otherT < draggedT) {
            insertBeforeIdx++;
        }
    });
    
    // Remove dragged connector from its current position and insert at new position
    var connArray = sceneData.connectors;
    var oldIdx = connArray.indexOf(draggedConnector);
    if (oldIdx >= 0) connArray.splice(oldIdx, 1);
    
    // Find the target index among the same-side group after removal
    var targetConn = null;
    var count = 0;
    for (var i = 0; i < connArray.length; i++) {
        if (String(connArray[i].module_id) === String(module.id) &&
            normalizeSide(connArray[i].side) === currentSide) {
            if (count === insertBeforeIdx) {
                targetConn = connArray[i];
                break;
            }
            count++;
        }
    }
    
    if (targetConn) {
        var newIdx = connArray.indexOf(targetConn);
        connArray.splice(newIdx, 0, draggedConnector);
    } else {
        connArray.push(draggedConnector);
    }
}

function enableConnectorSideDrag(group, c, module) {
    var dragState = { currentSide: normalizeSide(c.side), hadDrag: false };

    group.call(d3.drag()
        .filter(function (event) {
            // Left button only — a right-click must never start a drag or it
            // will capture the mouse and swallow the contextmenu on Windows.
            // Only allow dragging on editable modules, and never in read-only
            // mode (see dragBehavior's filter for why). Pending connectors
            // must not be side-dragged before approval.
            return event.button === 0
                && !IS_READONLY && module.editable !== false
                && !c.pending && !c.pending_delete && !module.pending;
        })
        .on('start', function (event) {
            event.sourceEvent.stopPropagation();
            dragState.currentSide = normalizeSide(c.side);
            dragState.hadDrag = false;
        })
        .on('drag', function (event) {
            dragState.hadDrag = true;
            var w = Math.max(MODULE_MIN_WIDTH, module.width);
            var h = Math.max(MODULE_MIN_HEIGHT, module.height);
            var sceneCoords = d3.pointer(event.sourceEvent, g.node());
            var mx = sceneCoords[0] - module.x; // module-local X
            var my = sceneCoords[1] - module.y; // module-local Y

            // Project pointer onto the current side's axis to allow same-side
            // reordering. Only switch sides when pointer is clearly on a
            // different edge (beyond the module's exterior margin).
            var newSide = dragState.currentSide;
            var t;
            var switchThreshold = 30; // px beyond edge before side-switch

            if (dragState.currentSide === 'top') {
                t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, mx / w));
                if (my > h + switchThreshold) newSide = 'bottom';
                else if (mx < -switchThreshold) newSide = 'left';
                else if (mx > w + switchThreshold) newSide = 'right';
                // else stay on top
            } else if (dragState.currentSide === 'bottom') {
                t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, mx / w));
                if (my < -switchThreshold) newSide = 'top';
                else if (mx < -switchThreshold) newSide = 'left';
                else if (mx > w + switchThreshold) newSide = 'right';
            } else if (dragState.currentSide === 'left') {
                t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, my / h));
                if (mx > w + switchThreshold) newSide = 'right';
                else if (my < -switchThreshold) newSide = 'top';
                else if (my > h + switchThreshold) newSide = 'bottom';
            } else { // right
                t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, my / h));
                if (mx < -switchThreshold) newSide = 'left';
                else if (my < -switchThreshold) newSide = 'top';
                else if (my > h + switchThreshold) newSide = 'bottom';
            }

            // If side changed, update t for the new side axis
            if (newSide !== dragState.currentSide) {
                if (newSide === 'top' || newSide === 'bottom') {
                    t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, mx / w));
                } else {
                    t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, my / h));
                }
            }

            // Update connector data
            c.side = newSide;
            dragState.currentSide = newSide;
            var ep = edgePoint(module, newSide, t);
            var n = SIDE_AXIS[newSide].normal;
            c.x = ep.x + n.x * CONNECTOR_STUB;
            c.y = ep.y + n.y * CONNECTOR_STUB;

            // Update SVG elements
            var edge = connectorEdgeAnchor(module, c);
            group.select('.connector-line')
                .attr('x1', edge.x).attr('y1', edge.y)
                .attr('x2', c.x).attr('y2', c.y);
            var hs = 6;
            group.select('.connector-drag-handle')
                .attr('d', 'M' + c.x + ',' + (c.y - hs) + ' L' + (c.x + hs) + ',' + c.y + ' L' + c.x + ',' + (c.y + hs) + ' L' + (c.x - hs) + ',' + c.y + ' Z');

            var bbox = connectorBBox(module, c);
            group.select('.connector-bbox')
                .attr('x', bbox.x).attr('y', bbox.y)
                .attr('width', bbox.w).attr('height', bbox.h);

            // Update connector label position
            var isHoriz = (newSide === 'top' || newSide === 'bottom');
            var labelOffX = isHoriz ? 10 : (newSide === 'left' ? -8 : 8);
            var labelOffY = -6;
            group.select('.connector-label')
                .attr('x', c.x + labelOffX)
                .attr('y', c.y + labelOffY)
                .attr('text-anchor', isHoriz ? 'start' : (newSide === 'left' ? 'end' : 'start'))
                .attr('dominant-baseline', 'auto');

            // CRITICAL: Update pin circles and labels to move in sync with the connector
            var moduleGroup = d3.select(group.node().parentNode);
            var count = c.pins.length;
            var tangent = SIDE_AXIS[newSide].tangent;
            var mid = connectorBodyMidpoint(module, c);

            c.pins.forEach(function (p, i) {
                var offset = -(count - 1) * PIN_HALF_STEP + i * (PIN_HALF_STEP * 2);
                var px = mid.x + tangent.x * offset;
                var py = mid.y + tangent.y * offset;

                // Move pin symbol group
                moduleGroup.select('g.pin-circle[data-pin-id="' + p.id + '"]')
                    .attr('transform', 'translate(' + px + ',' + py + ')');

                // Move pin label
                var isVert = (newSide === 'top' || newSide === 'bottom');
                var labelDx = isVert ? 0 : (newSide === 'left' ? -PIN_RADIUS - 3 : PIN_RADIUS + 3);
                var labelDy = isVert ? (newSide === 'top' ? -PIN_RADIUS - 3 : PIN_RADIUS + 11) : 0;
                var anchor = isVert ? 'middle' : (newSide === 'left' ? 'end' : 'start');

                moduleGroup.select('text.pin-label')
                    .filter(function () { return d3.select(this).text() === p.name; })
                    .attr('x', px + labelDx)
                    .attr('y', py + labelDy)
                    .attr('text-anchor', anchor);

                // Move data type caption (precise data-pin-id selector, so
                // duplicate types on the same connector never swap labels)
                var tDx = 0, tDy = 0, tAnchor = 'middle';
                if (newSide === 'right') { tDx = -PIN_RADIUS - 4; tAnchor = 'end'; }
                else if (newSide === 'left') { tDx = PIN_RADIUS + 4; tAnchor = 'start'; }
                else if (newSide === 'top') { tDy = PIN_RADIUS + 4; }
                else { tDy = -PIN_RADIUS - 4; }
                moduleGroup.select('text.pin-type-label[data-pin-id="' + p.id + '"]')
                    .attr('x', px + tDx)
                    .attr('y', py + tDy)
                    .attr('text-anchor', tAnchor);
            });

            // Mark as manually positioned so assignFallbackConnectorPositions won't redistribute
            c._dragPositioned = true;

            // Update wires
            redrawConnectorsFor(module);
        })
        .on('end', function () {
            if (dragState.hadDrag && bridge) {
                // Reorder connectors in the scene data array so the new order
                // is preserved when assignFallbackConnectorPositions redistributes
                reorderConnectorsAfterDrag(module, c, dragState.currentSide);
                
                bridge.save_connector_positions(JSON.stringify(
                    { [c.id]: { x: c.x, y: c.y, side: dragState.currentSide } }
                ));
                
                // Redistribute evenly & save routes
                render(sceneData);
                persistCurrentRoutes();
            }
            // is preserved via the _dragPositioned flag on the connector data.
        })
    );
}


// Expose the contract on window explicitly (matches Component_Tree_Window.py
// checking `typeof X === 'function'` from Python via runJavaScript).
window.fitView = fitView;
window.getExportBounds = getExportBounds;
window.setExportOverlayVisible = setExportOverlayVisible;
window.triggerSaveLayout = triggerSaveLayout;
window.getZoomState = getZoomState;
window.setZoomState = setZoomState;

// ---------------------------------------------------------------------
// Modal dialogs for Connector & Pin creation with all DB fields
// ---------------------------------------------------------------------

// Predefined connector colors matching the Architecture view
const CONNECTOR_COLORS = [
    { name: 'Default', hex: '#F8913C' },
    { name: 'Red', hex: '#FF0000' },
    { name: 'Green', hex: '#00FF00' },
    { name: 'Blue', hex: '#0000FF' },
    { name: 'Yellow', hex: '#FFFF00' },
    { name: 'Purple', hex: '#800080' },
    { name: 'Gray', hex: '#5D5A5A' },
];
function showConnectorDialog(moduleDatum) {
    if (!bridge) return;
    removeModal();

    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';

    var dialog = document.createElement('div');
    dialog.className = 'modal-dialog';
    dialog.innerHTML = '';

    var title = document.createElement('h3');
    title.textContent = 'Add Connector to ' + moduleDatum.name;
    dialog.appendChild(title);

    // Name field
    var nameField = createModalField(dialog, 'Connector Name');
    var nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.value = 'J' + (sceneData.connectors.length + 1);
    nameInput.placeholder = 'Enter connector name';
    nameField.appendChild(nameInput);

    // Side selector
    var sideField = createModalField(dialog, 'Side');
    var sideSelect = document.createElement('select');
    ['right', 'left', 'top', 'bottom'].forEach(function (s) {
        var opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s.charAt(0).toUpperCase() + s.slice(1);
        if (s === 'right') opt.selected = true;
        sideSelect.appendChild(opt);
    });
    sideField.appendChild(sideSelect);

    // Number of pins
    var pinsField = createModalField(dialog, 'Number of Pins');
    var pinsInput = document.createElement('input');
    pinsInput.type = 'number';
    pinsInput.value = '1';
    pinsInput.min = '0';
    pinsInput.max = '100';
    pinsField.appendChild(pinsInput);

    // Color picker
    var colorField = createModalField(dialog, 'Color');
    var colorInput = document.createElement('input');
    colorInput.type = 'hidden';
    colorInput.value = '';
    colorField.appendChild(colorInput);

    var colorPresets = document.createElement('div');
    colorPresets.className = 'color-presets';

    var selectedSwatch = null;
    CONNECTOR_COLORS.forEach(function (c) {
        var swatch = document.createElement('div');
        swatch.className = 'color-swatch';
        swatch.style.backgroundColor = c.hex;
        if (c.name === 'Default') {
            swatch.classList.add('selected');
            selectedSwatch = swatch;
            colorInput.value = c.hex;
        }
        swatch.addEventListener('click', function () {
            if (selectedSwatch) selectedSwatch.classList.remove('selected');
            swatch.classList.add('selected');
            selectedSwatch = swatch;
            colorInput.value = c.hex;
        });
        colorPresets.appendChild(swatch);
    });

    // Custom color input
    var customColorRow = document.createElement('div');
    customColorRow.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:6px;';

    var customColorLabel = document.createElement('span');
    customColorLabel.textContent = 'Custom:';
    customColorLabel.style.cssText = 'color:var(--text-primary);font-size:11px;opacity:0.7;';
    customColorRow.appendChild(customColorLabel);

    var customColorInput = document.createElement('input');
    customColorInput.type = 'color';
    customColorInput.value = '#F8913C';
    customColorInput.style.cssText = 'width:32px;height:26px;border:none;border-radius:4px;cursor:pointer;background:none;padding:0;';
    customColorInput.addEventListener('input', function () {
        if (selectedSwatch) selectedSwatch.classList.remove('selected');
        selectedSwatch = null;
        colorInput.value = customColorInput.value;
    });
    customColorRow.appendChild(customColorInput);

    colorField.appendChild(colorPresets);
    colorField.appendChild(customColorRow);

    // Actions
    var actions = document.createElement('div');
    actions.className = 'modal-actions';

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'modal-btn modal-btn-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', removeModal);
    actions.appendChild(cancelBtn);

    var createBtn = document.createElement('button');
    createBtn.className = 'modal-btn modal-btn-primary';
    createBtn.textContent = 'Create Connector';
    createBtn.addEventListener('click', function () {
        var name = nameInput.value.trim();
        if (!name) { showToast('Connector name is required', 'error'); return; }
        var side = sideSelect.value;
        var numPins = parseInt(pinsInput.value, 10) || 0;
        var color = colorInput.value || '';
        removeModal();
        bridge.create_connector(moduleDatum.id, name, side, color, numPins);
    });
    actions.appendChild(createBtn);

    dialog.appendChild(actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    // Focus name input
    setTimeout(function () { nameInput.focus(); }, 100);

    // Close on overlay click (outside dialog)
    overlay.addEventListener('click', function (e) {

    // Document-level Escape handler (works reliably regardless of focus)
    _modalEscHandler = function (e) {
        if (e.key === "Escape") removeModal();
    };
    document.addEventListener("keydown", _modalEscHandler);
        if (e.target === overlay) removeModal();
    });
}

// Fill a pin type <select> with the managed data types plus the generic
// Two-stage pin type selector mirroring the Architecture tab: pick the
// category (Data / Power), then the concrete type in a second combo right
// next to it (Data → generic "Data" + admin-managed types; Power → VCC/GND).
// Resolves to { pin_type, is_ground } for the bridge. Any type already on
// the pin that is not in the managed list is preserved as a custom option.
function createPinTypeSelector(initialType, initialIsGround) {
    var catSelect = document.createElement('select');
    catSelect.className = 'pin-type-cat';
    ['Data', 'Power'].forEach(function (t) {
        var opt = document.createElement('option');
        opt.value = t; opt.textContent = t;
        catSelect.appendChild(opt);
    });

    // Data type combo (visible when "Data" is selected)
    var dataSelect = document.createElement('select');
    var dataItems = ['Data'].concat(sceneData.pin_types || []);
    var normInitial = String(initialType || 'Data').trim().toUpperCase();
    dataItems.forEach(function (t) {
        var opt = document.createElement('option');
        opt.value = t; opt.textContent = t;
        dataSelect.appendChild(opt);
    });
    // Keep a custom data type editable even if it was removed from the list.
    // (Power/ground aliases never belong here — they are resolved via the
    // category + power subtype below.)
    if (initialType && dataItems.map(function (s) { return String(s).toUpperCase(); }).indexOf(normInitial) < 0) {
        var cls0 = pinClassOf({ pin_type: initialType, is_ground: initialIsGround });
        if (cls0.cls === 'data' || cls0.cls === 'untyped') {
            var opt = document.createElement('option');
            opt.value = initialType; opt.textContent = initialType + ' (custom)';
            dataSelect.appendChild(opt);
        }
    }

    // Power subtype combo (visible when "Power" is selected)
    var powerSelect = document.createElement('select');
    ['VCC', 'GND'].forEach(function (t) {
        var opt = document.createElement('option');
        opt.value = t; opt.textContent = t;
        powerSelect.appendChild(opt);
    });

    // Resolve the initial state from the stored pin_type + is_ground.
    // For power/ground pins the data subtype is irrelevant, so it is reset
    // to "Data" rather than carrying a power alias (e.g. "VCC") into the
    // Data combo if the user switches categories.
    var cls = pinClassOf({ pin_type: initialType, is_ground: initialIsGround });
    var category = 'Data';
    var dataType = initialType || 'Data';
    var powerType = 'VCC';
    if (cls.cls === 'ground') { category = 'Power'; powerType = 'GND'; dataType = 'Data'; }
    else if (cls.cls === 'power') { category = 'Power'; powerType = 'VCC'; dataType = 'Data'; }

    catSelect.value = category;
    for (var di = 0; di < dataSelect.options.length; di++) {
        if (String(dataSelect.options[di].value).toUpperCase() === String(dataType).toUpperCase()) {
            dataSelect.selectedIndex = di; break;
        }
    }
    powerSelect.value = powerType;

    function updateVisibility() {
        var isData = catSelect.value === 'Data';
        dataSelect.style.display = isData ? '' : 'none';
        powerSelect.style.display = isData ? 'none' : '';
    }
    catSelect.addEventListener('change', updateVisibility);

    return {
        catSelect: catSelect,
        dataSelect: dataSelect,
        powerSelect: powerSelect,
        getType: function () {
            if (catSelect.value === 'Power') {
                return { pin_type: 'Voltage', is_ground: powerSelect.value === 'GND' };
            }
            return { pin_type: dataSelect.value || 'Data', is_ground: false };
        },
        updateVisibility: updateVisibility,
    };
}

function showPinDialog(connectorDatum) {
    if (!bridge) return;
    removeModal();

    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';

    var dialog = document.createElement('div');
    dialog.className = 'modal-dialog';

    var title = document.createElement('h3');
    title.textContent = 'Add Pin to ' + connectorDatum.name;
    dialog.appendChild(title);

    // Name field
    var nameField = createModalField(dialog, 'Pin Name');
    var nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.value = 'PIN' + (connectorDatum.pins.length + 1);
    nameInput.placeholder = 'Enter pin name';
    nameField.appendChild(nameInput);

    // Pin Type — two-stage selector (Data → data types, Power → VCC/GND)
    var typeField = createModalField(dialog, 'Pin Type');
    var typeSel = createPinTypeSelector(null, false);
    var typeRow = document.createElement('div');
    typeRow.className = 'pin-type-selector';
    typeRow.appendChild(typeSel.catSelect);
    typeRow.appendChild(typeSel.dataSelect);
    typeRow.appendChild(typeSel.powerSelect);
    typeField.appendChild(typeRow);

    // Voltage value (only for Power/VCC type)
    var voltField = createModalField(dialog, 'Voltage (V)');
    var voltInput = document.createElement('input');
    voltInput.type = 'number';
    voltInput.value = '0';
    voltInput.min = '0';
    voltInput.step = '0.1';
    voltInput.placeholder = 'e.g. 3.3';
    voltField.appendChild(voltInput);

    // NOTE: pins no longer carry a current value — the current belongs to
    // the connection and is asked for when wiring two pins together.

    // Description
    var descField = createModalField(dialog, 'Description (optional)');
    var descInput = document.createElement('input');
    descInput.type = 'text';
    descInput.value = '';
    descInput.placeholder = 'e.g. Main power input';
    descField.appendChild(descInput);

    // Show/hide voltage based on category and power subtype
    function updateVoltageVisibility() {
        var sel = typeSel.getType();
        voltField.style.display = (sel.pin_type === 'Voltage' && !sel.is_ground) ? '' : 'none';
    }
    typeSel.catSelect.addEventListener('change', updateVoltageVisibility);
    typeSel.powerSelect.addEventListener('change', updateVoltageVisibility);
    typeSel.updateVisibility();
    updateVoltageVisibility();

    // Actions
    var actions = document.createElement('div');
    actions.className = 'modal-actions';

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'modal-btn modal-btn-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', removeModal);
    actions.appendChild(cancelBtn);

    var createBtn = document.createElement('button');
    createBtn.className = 'modal-btn modal-btn-primary';
    createBtn.textContent = 'Create Pin';
    createBtn.addEventListener('click', function () {
        var name = nameInput.value.trim();
        if (!name) { showToast('Pin name is required', 'error'); return; }
        var sel = typeSel.getType();
        var pinType = sel.pin_type;
        var isGround = sel.is_ground;
        var voltage = isGround ? 0 : (parseFloat(voltInput.value) || 0);
        var description = descInput.value.trim();
        removeModal();
        bridge.create_pin(connectorDatum.id, name, pinType, isGround, voltage, 0, description);
    });
    actions.appendChild(createBtn);

    dialog.appendChild(actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    // Focus name input
    setTimeout(function () { nameInput.focus(); }, 100);

    overlay.addEventListener('click', function (e) {

    // Document-level Escape handler (works reliably regardless of focus)
    _modalEscHandler = function (e) {
        if (e.key === "Escape") removeModal();
    };
    document.addEventListener("keydown", _modalEscHandler);
        if (e.target === overlay) removeModal();
    });
}

function createModalField(parent, labelText) {
    var field = document.createElement('div');
    field.className = 'modal-field';
    if (labelText) {
        var label = document.createElement('label');
        label.textContent = labelText;
        field.appendChild(label);
    }
    parent.appendChild(field);
    return field;
}

// Track active modal Escape handler for cleanup
let _modalEscHandler = null;

function removeModal() {
    var existing = document.querySelector('.modal-overlay');
    if (existing) existing.remove();
    // Clean up document-level Escape listener
    if (_modalEscHandler) {
        document.removeEventListener('keydown', _modalEscHandler);
        _modalEscHandler = null;
    }
}

// ---------------------------------------------------------------------
// Module Creation & Edit Dialogs
// ---------------------------------------------------------------------

const MODULE_COLORS = [
    { name: 'Default', hex: '#33A444' },
    { name: 'Red', hex: '#FF0000' },
    { name: 'Blue', hex: '#0000FF' },
    { name: 'Yellow', hex: '#FFFF00' },
    { name: 'Purple', hex: '#800080' },
    { name: 'Orange', hex: '#FFA500' },
    { name: 'Gray', hex: '#5D5A5A' },
];

function showModuleDialog() {
    if (!bridge) return;
    removeModal();

    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    var dialog = document.createElement('div');
    dialog.className = 'modal-dialog';

    var title = document.createElement('h3');
    title.textContent = 'Create New Module';
    dialog.appendChild(title);

    // Name
    var nameF = createModalField(dialog, 'Module Name');
    var nameI = document.createElement('input');
    nameI.type = 'text'; nameI.placeholder = 'Enter module name';
    nameF.appendChild(nameI);

    // Mass
    var massF = createModalField(dialog, 'Mass (kg)');
    var massI = document.createElement('input');
    massI.type = 'number'; massI.value = '0'; massI.min = '0'; massI.step = '0.1';
    massF.appendChild(massI);

    // Power
    var powerF = createModalField(dialog, 'Power (mW)');
    var powerI = document.createElement('input');
    powerI.type = 'number'; powerI.value = '0'; powerI.min = '0'; powerI.step = '0.1';
    powerF.appendChild(powerI);

    // Subsystem (required)
    var hasSubsystems = sceneData.subsystems && sceneData.subsystems.length > 0;
    var subSelect = null;
    if (hasSubsystems) {
        var subF = createModalField(dialog, 'Subsystem *');
        subSelect = document.createElement('select');
        var placeholderOpt = document.createElement('option');
        placeholderOpt.value = ''; placeholderOpt.textContent = '— Select Subsystem —'; placeholderOpt.disabled = true; placeholderOpt.selected = true;
        subSelect.appendChild(placeholderOpt);
        sceneData.subsystems.forEach(function (ss) {
            var opt = document.createElement('option');
            opt.value = ss.id; opt.textContent = ss.name;
            subSelect.appendChild(opt);
        });
        subF.appendChild(subSelect);
    }

    // Color
    var colorF = createModalField(dialog, 'Color');
    var colorI = document.createElement('input');
    colorI.type = 'hidden'; colorI.value = '';
    colorF.appendChild(colorI);
    var cPresets = document.createElement('div');
    cPresets.className = 'color-presets';
    var selSwatch = null;
    MODULE_COLORS.forEach(function (c) {
        var sw = document.createElement('div');
        sw.className = 'color-swatch';
        sw.style.backgroundColor = c.hex;
        if (c.name === 'Default') { sw.classList.add('selected'); selSwatch = sw; colorI.value = c.hex; }
        sw.addEventListener('click', function () {
            if (selSwatch) selSwatch.classList.remove('selected');
            sw.classList.add('selected'); selSwatch = sw; colorI.value = c.hex;
        });
        cPresets.appendChild(sw);
    });
    var custRow = document.createElement('div');
    custRow.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:6px;';
    var custLab = document.createElement('span');
    custLab.textContent = 'Custom:'; custLab.style.cssText = 'color:var(--text-primary);font-size:11px;opacity:0.7;';
    custRow.appendChild(custLab);
    var custCol = document.createElement('input');
    custCol.type = 'color'; custCol.value = '#33A444';
    custCol.style.cssText = 'width:32px;height:26px;border:none;border-radius:4px;cursor:pointer;background:none;padding:0;';
    custCol.addEventListener('input', function () {
        if (selSwatch) selSwatch.classList.remove('selected'); selSwatch = null;
        colorI.value = custCol.value;
    });
    custRow.appendChild(custCol);
    colorF.appendChild(cPresets);
    colorF.appendChild(custRow);

    // Actions
    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'modal-btn modal-btn-cancel'; cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', removeModal);
    actions.appendChild(cancelBtn);
    var createBtn = document.createElement('button');
    createBtn.className = 'modal-btn modal-btn-primary';
    if (!hasSubsystems) {
        createBtn.textContent = 'No Subsystems Available';
        createBtn.disabled = true;
        createBtn.style.opacity = '0.5';
        createBtn.style.cursor = 'not-allowed';
    } else {
        createBtn.textContent = 'Create Module';
    }
    createBtn.addEventListener('click', function () {
        var name = nameI.value.trim();
        if (!name) { showToast('Module name is required', 'error'); return; }
        if (!hasSubsystems) { showToast('You need to create a subsystem first', 'error'); return; }
        var mass = parseFloat(massI.value) || 0;
        var power = parseFloat(powerI.value) || 0;
        var subId = subSelect ? parseInt(subSelect.value) : -1;
        if (!subSelect || subId < 0) { showToast('A subsystem must be selected', 'error'); return; }
        var color = colorI.value || '';
        removeModal();
        bridge.create_module(name, subId, mass, power, color);
    });
    actions.appendChild(createBtn);
    dialog.appendChild(actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    _modalEscHandler = function (e) { if (e.key === 'Escape') removeModal(); };
    document.addEventListener('keydown', _modalEscHandler);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) removeModal(); });
    setTimeout(function () { nameI.focus(); }, 100);
}

function showModuleEditDialog(moduleDatum) {
    if (!bridge) return;
    removeModal();

    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    var dialog = document.createElement('div');
    dialog.className = 'modal-dialog';

    var title = document.createElement('h3');
    title.textContent = 'Edit Module: ' + moduleDatum.name;
    dialog.appendChild(title);

    var nameF = createModalField(dialog, 'Module Name');
    var nameI = document.createElement('input');
    nameI.type = 'text'; nameI.value = moduleDatum.name;
    nameF.appendChild(nameI);

    var massF = createModalField(dialog, 'Mass (kg)');
    var massI = document.createElement('input');
    massI.type = 'number'; massI.value = moduleDatum.mass || 0; massI.min = '0'; massI.step = '0.1';
    massF.appendChild(massI);

    var powerF = createModalField(dialog, 'Power (mW)');
    var powerI = document.createElement('input');
    powerI.type = 'number'; powerI.value = moduleDatum.power || 0; powerI.min = '0'; powerI.step = '0.1';
    powerF.appendChild(powerI);

    // Subsystem (required)
    var hasSubsystems = sceneData.subsystems && sceneData.subsystems.length > 0;
    var subSelect = null;
    var dimSubWarning = null;
    if (hasSubsystems) {
        var subF = createModalField(dialog, 'Subsystem *');
        subSelect = document.createElement('select');
        sceneData.subsystems.forEach(function (ss) {
            var opt = document.createElement('option');
            opt.value = ss.id; opt.textContent = ss.name;
            if (String(ss.id) === String(moduleDatum.subsystem_id)) opt.selected = true;
            subSelect.appendChild(opt);
        });
        subF.appendChild(subSelect);
    } else {
        dimSubWarning = document.createElement('div');
        dimSubWarning.style.cssText = 'color:var(--text-primary);font-size:11px;opacity:0.6;padding:8px 0;';
        dimSubWarning.textContent = 'No subsystems available — subsystem assignment cannot be changed.';
        dialog.appendChild(dimSubWarning);
    }

    // Color
    var colorF = createModalField(dialog, 'Color');
    var colorI = document.createElement('input');
    colorI.type = 'hidden'; colorI.value = moduleDatum.color || '';
    colorF.appendChild(colorI);
    var cPresets = document.createElement('div');
    cPresets.className = 'color-presets';
    var selSwatch = null;
    var curCol = moduleDatum.color || '#33A444';
    MODULE_COLORS.forEach(function (c) {
        var sw = document.createElement('div');
        sw.className = 'color-swatch';
        sw.style.backgroundColor = c.hex;
        if (c.hex === curCol || (!curCol && c.name === 'Default')) {
            sw.classList.add('selected'); selSwatch = sw; colorI.value = c.hex;
        }
        sw.addEventListener('click', function () {
            if (selSwatch) selSwatch.classList.remove('selected');
            sw.classList.add('selected'); selSwatch = sw; colorI.value = c.hex;
        });
        cPresets.appendChild(sw);
    });
    var custRow = document.createElement('div');
    custRow.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:6px;';
    var custLab = document.createElement('span');
    custLab.textContent = 'Custom:'; custLab.style.cssText = 'color:var(--text-primary);font-size:11px;opacity:0.7;';
    custRow.appendChild(custLab);
    var custCol = document.createElement('input');
    custCol.type = 'color'; custCol.value = curCol;
    custCol.style.cssText = 'width:32px;height:26px;border:none;border-radius:4px;cursor:pointer;background:none;padding:0;';
    custCol.addEventListener('input', function () {
        if (selSwatch) selSwatch.classList.remove('selected'); selSwatch = null;
        colorI.value = custCol.value;
    });
    custRow.appendChild(custCol);
    colorF.appendChild(cPresets);
    colorF.appendChild(custRow);

    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'modal-btn modal-btn-cancel'; cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', removeModal);
    actions.appendChild(cancelBtn);
    var saveBtn = document.createElement('button');
    saveBtn.className = 'modal-btn modal-btn-primary'; saveBtn.textContent = 'Save Changes';
    saveBtn.addEventListener('click', function () {
        var name = nameI.value.trim();
        if (!name) { showToast('Module name is required', 'error'); return; }
        var mass = parseFloat(massI.value) || 0;
        var power = parseFloat(powerI.value) || 0;
        var color = colorI.value || '';
        var subId = subSelect ? parseInt(subSelect.value) : (moduleDatum.subsystem_id != null ? moduleDatum.subsystem_id : -1);
        removeModal();
        bridge.update_module(moduleDatum.id, name, mass, power, color, subId);
    });
    actions.appendChild(saveBtn);
    dialog.appendChild(actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    _modalEscHandler = function (e) { if (e.key === 'Escape') removeModal(); };
    document.addEventListener('keydown', _modalEscHandler);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) removeModal(); });
    setTimeout(function () { nameI.focus(); }, 100);
}

function showConnectorEditDialog(connectorDatum) {
    if (!bridge) return;
    removeModal();

    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    var dialog = document.createElement('div');
    dialog.className = 'modal-dialog';

    var title = document.createElement('h3');
    title.textContent = 'Edit Connector: ' + connectorDatum.name;
    dialog.appendChild(title);

    var nameF = createModalField(dialog, 'Connector Name');
    var nameI = document.createElement('input');
    nameI.type = 'text'; nameI.value = connectorDatum.name;
    nameF.appendChild(nameI);

    var sideF = createModalField(dialog, 'Side');
    var sideS = document.createElement('select');
    ['right', 'left', 'top', 'bottom'].forEach(function (s) {
        var opt = document.createElement('option');
        opt.value = s; opt.textContent = s.charAt(0).toUpperCase() + s.slice(1);
        if (s === (connectorDatum.side || 'right')) opt.selected = true;
        sideS.appendChild(opt);
    });
    sideF.appendChild(sideS);

    var pinsF = createModalField(dialog, 'Number of Pins');
    var pinsI = document.createElement('input');
    pinsI.type = 'number'; pinsI.value = connectorDatum.pins.length; pinsI.min = '0'; pinsI.max = '100';
    pinsF.appendChild(pinsI);

    // Color
    var colorF = createModalField(dialog, 'Color');
    var colorI = document.createElement('input');
    colorI.type = 'hidden'; colorI.value = connectorDatum.color || '';
    colorF.appendChild(colorI);
    var cPresets = document.createElement('div');
    cPresets.className = 'color-presets';
    var selSwatch = null;
    var curCol = connectorDatum.color || CONNECTOR_COLORS[0].hex;
    CONNECTOR_COLORS.forEach(function (c) {
        var sw = document.createElement('div');
        sw.className = 'color-swatch';
        sw.style.backgroundColor = c.hex;
        if (c.hex === curCol) { sw.classList.add('selected'); selSwatch = sw; colorI.value = c.hex; }
        sw.addEventListener('click', function () {
            if (selSwatch) selSwatch.classList.remove('selected');
            sw.classList.add('selected'); selSwatch = sw; colorI.value = c.hex;
        });
        cPresets.appendChild(sw);
    });
    var custRow = document.createElement('div');
    custRow.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:6px;';
    var custLab = document.createElement('span');
    custLab.textContent = 'Custom:'; custLab.style.cssText = 'color:var(--text-primary);font-size:11px;opacity:0.7;';
    custRow.appendChild(custLab);
    var custCol = document.createElement('input');
    custCol.type = 'color'; custCol.value = curCol;
    custCol.style.cssText = 'width:32px;height:26px;border:none;border-radius:4px;cursor:pointer;background:none;padding:0;';
    custCol.addEventListener('input', function () {
        if (selSwatch) selSwatch.classList.remove('selected'); selSwatch = null;
        colorI.value = custCol.value;
    });
    custRow.appendChild(custCol);
    colorF.appendChild(cPresets);
    colorF.appendChild(custRow);

    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'modal-btn modal-btn-cancel'; cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', removeModal);
    actions.appendChild(cancelBtn);
    var saveBtn = document.createElement('button');
    saveBtn.className = 'modal-btn modal-btn-primary'; saveBtn.textContent = 'Save Changes';
    saveBtn.addEventListener('click', function () {
        var name = nameI.value.trim();
        if (!name) { showToast('Connector name is required', 'error'); return; }
        var side = sideS.value;
        var numPins = parseInt(pinsI.value, 10) || 0;
        var color = colorI.value || '';
        removeModal();
        bridge.update_connector(connectorDatum.id, name, color, side, numPins);
    });
    actions.appendChild(saveBtn);
    dialog.appendChild(actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    _modalEscHandler = function (e) { if (e.key === 'Escape') removeModal(); };
    document.addEventListener('keydown', _modalEscHandler);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) removeModal(); });
    setTimeout(function () { nameI.focus(); }, 100);
}

function showPinEditDialog(pinDatum, connectorDatum) {
    if (!bridge) return;
    removeModal();

    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    var dialog = document.createElement('div');
    dialog.className = 'modal-dialog';

    var title = document.createElement('h3');
    title.textContent = 'Edit Pin: ' + pinDatum.name;
    dialog.appendChild(title);

    var nameF = createModalField(dialog, 'Pin Name');
    var nameI = document.createElement('input');
    nameI.type = 'text'; nameI.value = pinDatum.name;
    nameF.appendChild(nameI);

    var typeF = createModalField(dialog, 'Pin Type');
    var typeSel = createPinTypeSelector(pinDatum.pin_type, pinDatum.is_ground);
    var typeRow = document.createElement('div');
    typeRow.className = 'pin-type-selector';
    typeRow.appendChild(typeSel.catSelect);
    typeRow.appendChild(typeSel.dataSelect);
    typeRow.appendChild(typeSel.powerSelect);
    typeF.appendChild(typeRow);

    var voltF = createModalField(dialog, 'Voltage (V)');
    var voltI = document.createElement('input');
    voltI.type = 'number'; voltI.value = (pinDatum.voltage != null ? pinDatum.voltage : '0'); voltI.min = '0'; voltI.step = '0.1';
    voltF.appendChild(voltI);

    var descF = createModalField(dialog, 'Description (optional)');
    var descI = document.createElement('input');
    descI.type = 'text'; descI.value = (pinDatum.description || ''); descI.placeholder = 'e.g. Main power input';
    descF.appendChild(descI);

    // NOTE: pins no longer carry a current value — the current belongs to
    // the connection and is asked for when wiring two pins together.

    function updateVis() {
        var sel = typeSel.getType();
        voltF.style.display = (sel.pin_type === 'Voltage' && !sel.is_ground) ? '' : 'none';
    }
    typeSel.catSelect.addEventListener('change', updateVis);
    typeSel.powerSelect.addEventListener('change', updateVis);
    typeSel.updateVisibility();
    updateVis();

    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'modal-btn modal-btn-cancel'; cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', removeModal);
    actions.appendChild(cancelBtn);
    var saveBtn = document.createElement('button');
    saveBtn.className = 'modal-btn modal-btn-primary'; saveBtn.textContent = 'Save Changes';
    saveBtn.addEventListener('click', function () {
        var name = nameI.value.trim();
        if (!name) { showToast('Pin name is required', 'error'); return; }
        var sel = typeSel.getType();
        var pinType = sel.pin_type;
        var isGround = sel.is_ground;
        var voltage = isGround ? 0 : (parseFloat(voltI.value) || 0);
        var description = descI.value.trim();
        removeModal();
        bridge.update_pin(pinDatum.id, name, pinType, isGround, voltage, 0, description);
    });
    actions.appendChild(saveBtn);
    dialog.appendChild(actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    _modalEscHandler = function (e) { if (e.key === 'Escape') removeModal(); };
    document.addEventListener('keydown', _modalEscHandler);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) removeModal(); });
    setTimeout(function () { nameI.focus(); }, 100);
}
