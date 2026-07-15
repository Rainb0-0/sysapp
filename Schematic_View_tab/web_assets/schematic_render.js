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

let selectedModuleId = null;
let selectedInterfaceId = null;
let connectDrag = null; // { fromPinId, tempPath }
let toastCount = 0;

const MODULE_MIN_WIDTH = 120;
const MODULE_MIN_HEIGHT = 60;
const PIN_RADIUS = 7;      // larger hitbox for easier pin-to-pin dragging
const PIN_HOVER_RADIUS = 11;
const CONNECTOR_STUB = 30;  // length of connector stub from module edge to tip

// Corner offset to avoid placing connectors too close to corners
const CORNER_OFFSET = 0.08;
const CONNECTOR_MARGIN = 34; // distance from module edge to connector tip (pins live here)
const PIN_HALF_STEP = 10;  // half the distance between adjacent pins (full step = 20px)
const CONNECTOR_GAP = 12;  // minimum gap between connector edges on same side
const CONNECTOR_BBOX_PAD = 8; // extra padding around connector bounding box


// Real DB side values are 'left' | 'right' | 'top' | 'bottom' (default 'top').
// normal = direction the stub sticks out; tangent = direction pins spread along the stub.
const SIDE_AXIS = {
    left:   { normal: { x: -1, y: 0 }, tangent: { x: 0, y: 1 } },
    right:  { normal: { x: 1, y: 0 },  tangent: { x: 0, y: 1 } },
    top:    { normal: { x: 0, y: -1 }, tangent: { x: 1, y: 0 } },
    bottom: { normal: { x: 0, y: 1 },  tangent: { x: 1, y: 0 } },
};

function normalizeSide(side) {
    return SIDE_AXIS[side] ? side : 'top';
}

// Point on a module's boundary, in module-local coordinates, at fraction
// t (0..1) along that edge.
function edgePoint(module, side, t) {
    const w = Math.max(MODULE_MIN_WIDTH, module.width);
    const h = Math.max(MODULE_MIN_HEIGHT, module.height);
    switch (normalizeSide(side)) {
        case 'right':  return { x: w, y: t * h };
        case 'top':    return { x: t * w, y: 0 };
        case 'bottom': return { x: t * w, y: h };
        case 'left':
        default:       return { x: 0, y: t * h };
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
        case 'right':  return { x: w, y: c.y };
        case 'top':    return { x: c.x, y: 0 };
        case 'bottom': return { x: c.x, y: h };
        case 'left':
        default:       return { x: 0, y: c.y };
    }
}

document.addEventListener('DOMContentLoaded', function () {
    setupScene();
    initializeWebChannel();
    setupKeyboardShortcuts();
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

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', function (event) {
        if (event.target.tagName === 'INPUT') return;
        if (event.key === ' ') {
            fitView();
            event.preventDefault();
        } else if (event.key === 'Delete' || event.key === 'Backspace') {
            handleDeleteKey();
            event.preventDefault();
        } else if (event.key === 'Escape') {
            clearSelection();
            cancelConnectDrag();
        }
    });

    svg.on('click', function (event) {
        // Clicking empty canvas (not a module/pin/interface) clears selection.
        if (event.target === svg.node()) clearSelection();
    });
}

function handleDeleteKey() {
    if (selectedInterfaceId != null) {
        if (bridge && confirm('Delete this connection?')) {
            bridge.delete_interface(selectedInterfaceId);
        }
        clearSelection();
    } else if (selectedModuleId != null) {
        if (bridge && confirm('Delete this module and all its connections?')) {
            bridge.delete_module(selectedModuleId);
        }
        clearSelection();
    }
}

function clearSelection() {
    selectedModuleId = null;
    selectedInterfaceId = null;
    g.selectAll('.module-box').classed('selected', false);
    g.selectAll('.interface-path').classed('selected', false);
}

// ---------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------
function render(scene) {
    g.selectAll('*').remove();

    assignFallbackConnectorPositions(scene);
    const pinLookup = buildPinLookup(scene);

    // Draw subsystem halos first (behind everything)
    renderSubsystemHalos(scene);

    renderInterfaces(scene, pinLookup, false);
    renderModules(scene);

    // Hide loading overlay once we have rendered content
    hideLoading();

    if (!render.hasFitOnce) {
        setTimeout(() => { fitView(); render.hasFitOnce = true; }, 300);
    }
}
render.hasFitOnce = false;

// ---------------------------------------------------------------------
// Subsystem halos — draw a colored bounding box behind modules grouped
// by subsystem_id, with a label indicating the subsystem name.
// ---------------------------------------------------------------------
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

    const haloGroup = g.append('g').attr('class', 'subsystem-halos');
    const padding = 24;

    Object.keys(groups).forEach(function (ssId) {
        const group = groups[ssId];
        if (group.modules.length < 2) return;

        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        group.modules.forEach(function (m) {
            const right = m.x + m.width;
            const bottom = m.y + m.height;
            if (m.x < minX) minX = m.x;
            if (m.y < minY) minY = m.y;
            if (right > maxX) maxX = right;
            if (bottom > maxY) maxY = bottom;
        });

        minX -= padding;
        minY -= padding;
        maxX += padding;
        maxY += padding;

        const boxW = maxX - minX;
        const boxH = maxY - minY;

        // Background bounding box with dashed border
        haloGroup.append('rect')
            .attr('x', minX)
            .attr('y', minY)
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
        const labelX = minX + 10;
        const labelY = minY + 16;
        const textLength = group.name.length * 8 + 20;

        haloGroup.append('rect')
            .attr('x', labelX - 4)
            .attr('y', labelY - 11)
            .attr('width', Math.max(textLength, 30))
            .attr('height', 18)
            .attr('rx', 4)
            .attr('ry', 4)
            .attr('fill', group.color || '#555')
            .attr('opacity', 0.7);

        haloGroup.append('text')
            .attr('x', labelX + 2)
            .attr('y', labelY)
            .attr('fill', '#ffffff')
            .attr('font-size', 11)
            .attr('font-weight', '600')
            .attr('font-family', '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif')
            .text(group.name);
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

// Distribute connectors along a module edge so they never overlap.
// For each side of each module, compute the total space needed for all
// connectors (pin spreads + gaps), then distribute them evenly.
// Connectors with saved positions keep their position; unsaved ones are
// placed to fill gaps without overlapping saved ones.
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
        const edgeMargin = 14; // min distance from edge corners

        // Build entries with extents for ALL connectors
        const entries = group.map(function (c) {
            const extent = connectorEdgeExtent(c, side);
            const hasSaved = c.x !== null && c.x !== undefined && c.y !== null && c.y !== undefined;
            return { c: c, extent: extent, hasSaved: hasSaved };
        });

        if (entries.every(function (e) { return e.hasSaved; })) return;

        if (entries.length === 1) {
            // Single connector: center it on the edge
            const e = entries[0];
            if (!e.hasSaved) {
                const t = 0.5;
                const edge = edgePoint(module, side, t);
                const normal = SIDE_AXIS[side].normal;
                e.c.x = edge.x + normal.x * CONNECTOR_MARGIN;
                e.c.y = edge.y + normal.y * CONNECTOR_MARGIN;
            }
            return;
        }

        // Multi-connector: compute total space needed
        const totalExtents = entries.reduce(function (s, e) { return s + e.extents; }, 0);
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

            if (!entry.hasSaved) {
                const t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, center / edgeSize));
                const edge = edgePoint(module, side, t);
                const normal = SIDE_AXIS[side].normal;
                entry.c.x = edge.x + normal.x * CONNECTOR_MARGIN;
                entry.c.y = edge.y + normal.y * CONNECTOR_MARGIN;
            }

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
    // Midpoint is halfway from edge to tip along the normal
    return {
        x: edge.x + normal.x * (CONNECTOR_MARGIN / 2),
        y: edge.y + normal.y * (CONNECTOR_MARGIN / 2),
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
            selectedInterfaceId = null;
            selectedModuleId = d.id;
            g.selectAll('.module-box').classed('selected', dd => dd.id === d.id);
            g.selectAll('.interface-path').classed('selected', false);
        })
        .on('contextmenu', function (event, d) {
            event.preventDefault();
            event.stopPropagation();
            showModuleContextMenu(event, d);
        });

    moduleSel.append('rect')
        .attr('class', 'module-rect')
        .attr('width', d => Math.max(MODULE_MIN_WIDTH, d.width))
        .attr('height', d => Math.max(MODULE_MIN_HEIGHT, d.height))
        .attr('rx', 6).attr('ry', 6)
        .attr('fill', d => d.color || '#e67e22');

    moduleSel.append('text')
        .attr('class', 'module-label')
        .attr('x', d => Math.max(MODULE_MIN_WIDTH, d.width) / 2)
        .attr('y', d => Math.max(MODULE_MIN_HEIGHT, d.height) / 2)
        .text(d => d.name);

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
        const connectorColor = '#27ae60';   // tree view connector color
        const pinColor = '#e74c3c';         // tree view pin color

        // Connector interactive group — contains visible line, handle, hitbox
        var connGroup = parent.append('g').attr('class', 'connector-interactive');

        // --- Connector bounding box (visible background rect) ---
        const bbox = connectorBBox(module, c);
        connGroup.append('rect')
            .attr('class', 'connector-bbox')
            .attr('data-connector-id', c.id)
            .attr('x', bbox.x).attr('y', bbox.y)
            .attr('width', bbox.w).attr('height', bbox.h)
            .attr('rx', 6).attr('ry', 6)
            .attr('fill', 'rgba(39, 174, 96, 0.08)')
            .attr('stroke', 'rgba(39, 174, 96, 0.25)')
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

        // Visible drag handle at the stub tip
        connGroup.append('circle')
            .attr('class', 'connector-drag-handle')
            .attr('cx', c.x).attr('cy', c.y)
            .attr('r', 5);

        // Connector name label near the drag handle
        const isHorizSide = (side === 'top' || side === 'bottom');
        const labelOffset = isHorizSide ? { x: 10, y: -6 } :
            (side === 'left' ? { x: -8, y: -6 } : { x: 8, y: -6 });
        connGroup.append('text')
            .attr('class', 'connector-label')
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
            .attr('cursor', 'context-menu');

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

            parent.append('circle')
                .attr('class', 'pin-circle')
                .attr('data-pin-id', p.id)
                .attr('cx', px).attr('cy', py)
                .attr('fill', pinColor)
                .attr('r', PIN_RADIUS)
                .on('mousedown', function (event) {
                    event.stopPropagation();
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
                .attr('x', px + labelDx)
                .attr('y', py + labelDy)
                .attr('text-anchor', anchor)
                .attr('dominant-baseline', 'middle')
                .text(p.name);
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
// Pin-to-pin drag to create a new connection
// ---------------------------------------------------------------------
function startConnectDrag(pinId, localX, localY, moduleSelection) {
    cancelConnectDrag();

    const d = moduleSelection.datum();
    const startAbs = { x: d.x + localX, y: d.y + localY };

    const tempPath = g.append('path')
        .attr('class', 'interface-path temp-drag')
        .attr('d', `M${startAbs.x},${startAbs.y} L${startAbs.x},${startAbs.y}`);

    connectDrag = { fromPinId: pinId, fromPoint: startAbs, tempPath: tempPath };

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
    cancelConnectDrag();

    if (!bridge || toPinId === fromPinId) return;
    bridge.create_interface(fromPinId, toPinId, '');
    // The bridge's create_interface() already triggers get_scene_data()
    // indirectly via save_finished handling on the Python side is not
    // required here -- ask explicitly so the new wire shows up immediately.
    setTimeout(() => bridge.get_scene_data(), 100);
}

function cancelConnectDrag() {
    if (connectDrag && connectDrag.tempPath) connectDrag.tempPath.remove();
    connectDrag = null;
    svg.on('mousemove.connect', null);
    svg.on('mouseup.connect', null);
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
// Context menu
// ---------------------------------------------------------------------
function showContextMenu(event, items) {
    removeContextMenu();

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
            item.action();
            removeContextMenu();
        });
        menu.appendChild(el);
    });

    document.body.appendChild(menu);

    // Dismiss on any click outside
    setTimeout(function () {
        document.addEventListener('click', function dismiss(e) {
            if (!document.getElementById('schematic-context-menu')) return;
            removeContextMenu();
            document.removeEventListener('click', dismiss);
        }, { once: true });
    }, 0);
}

function removeContextMenu() {
    const existing = document.getElementById('schematic-context-menu');
    if (existing) existing.remove();
}

function showModuleContextMenu(event, moduleDatum) {
    showContextMenu(event, [
        { icon: '\u2795', label: 'Add Connector', action: function () { addConnectorPrompt(moduleDatum); }, shortcut: 'N' },
        { icon: '\u270F\uFE0F', label: 'Rename', action: function () { renameModulePrompt(moduleDatum); } },
        { icon: '\u274C', label: 'Delete', action: function () { deleteModuleConfirm(moduleDatum); }, shortcut: 'Del' },
    ]);
}

function showConnectorContextMenu(event, connectorDatum) {
    showContextMenu(event, [
        { icon: '\uD83D\uDD04', label: 'Reorder Pins', action: function () { openPinOrderDialog(connectorDatum); } },
        { icon: '\u2795', label: 'Add Pin', action: function () { addPinPrompt(connectorDatum); }, shortcut: 'N' },
        { icon: '\u270F\uFE0F', label: 'Rename', action: function () { renameConnectorPrompt(connectorDatum); } },
        { icon: '\u274C', label: 'Delete', action: function () { deleteConnectorConfirm(connectorDatum); }, shortcut: 'Del' },
    ]);
}

function showPinContextMenu(event, pinDatum, connectorDatum) {
    showContextMenu(event, [
        { icon: '\u270F\uFE0F', label: 'Rename', action: function () { renamePinPrompt(pinDatum); } },
        { icon: '\u274C', label: 'Delete', action: function () { deletePinConfirm(pinDatum); }, shortcut: 'Del' },
    ]);
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
    const name = prompt('Connector name:', 'J' + (sceneData.connectors.length + 1));
    if (!name || !name.trim()) return;
    const side = prompt('Side (left/right/top/bottom):', 'right');
    if (!side) return;
    bridge.create_connector(moduleDatum.id, name.trim(), side.trim().toLowerCase());
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
    const name = prompt('Pin name:', 'PIN' + (connectorDatum.pins.length + 1));
    if (name && name.trim()) {
        bridge.create_pin(connectorDatum.id, name.trim());
    }
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
    if (!bridge) return;
    const pinNames = connectorDatum.pins.map(p => p.name);
    bridge.request_pin_order_dialog(connectorDatum.id, connectorDatum.name, JSON.stringify(pinNames));
}

const ROUTE_MARGIN = 14; // must stay < CONNECTOR_STUB so pins sit outside inflated obstacle rects
const ROUTE_LEAD = 16;

function renderInterfaces(scene, pinLookup, forceRecompute) {
    const interfaceGroup = g.append('g').attr('class', 'interfaces-layer');
    const obstacleRects = scene.modules.map(m => ({ x: m.x, y: m.y, width: m.width, height: m.height }));

    scene.interfaces.forEach(function (iface) {
        let points = (!forceRecompute && iface.points && iface.points.length >= 2) ? iface.points : null;

        if (!points && iface.from_pin && iface.to_pin) {
            const a = pinLookup[iface.from_pin];
            const b = pinLookup[iface.to_pin];
            if (a && b) {
                points = computeRoutePoints(a, b, obstacleRects);
            }
        }
        if (!points || points.length < 2) return;

        const line = d3.line().x(p => p[0] !== undefined ? p[0] : p.x).y(p => p[1] !== undefined ? p[1] : p.y);
        interfaceGroup.append('path')
            .attr('class', 'interface-path')
            .attr('data-interface-id', iface.id)
            .attr('d', line(points))
            .on('click', function (event) {
                event.stopPropagation();
                selectedModuleId = null;
                selectedInterfaceId = iface.id;
                g.selectAll('.module-box').classed('selected', false);
                g.selectAll('.interface-path').classed('selected', function () {
                    return Number(d3.select(this).attr('data-interface-id')) === iface.id;
                });
            });
    });
}

// Uses the ported A* orthogonal router from schematic_routing.js when
// available; falls back to a straight line so the connection never just
// disappears if that script failed to load.
function computeRoutePoints(fromPin, toPin, obstacleRects) {
    if (window.SchematicRouting) {
        const routed = window.SchematicRouting.routeOrthogonal(
            fromPin, toPin, obstacleRects, ROUTE_MARGIN, ROUTE_LEAD
        );
        return routed.map(p => [p.x, p.y]);
    }
    return [[fromPin.x, fromPin.y], [toPin.x, toPin.y]];
}

// ---------------------------------------------------------------------
// Drag-to-move (auto-saves each module's new position on drag end)
// ---------------------------------------------------------------------
function dragBehavior() {
    return d3.drag()
        .on('start', function (event, d) {
            d3.select(this).raise();
        })
        .on('drag', function (event, d) {
            d.x = event.x;
            d.y = event.y;
            d3.select(this).attr('transform', `translate(${d.x}, ${d.y})`);
            redrawConnectorsFor(d);
        })
        .on('end', function (event, d) {
            if (!bridge) return;
            const payload = {};
            payload[d.id] = { x: d.x, y: d.y };
            bridge.save_module_positions(JSON.stringify(payload));
            persistCurrentRoutes();
        });
}

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
        const a = pinLookup[iface.from_pin];
        const b = pinLookup[iface.to_pin];
        if (!a || !b) return;
        const points = computeRoutePoints(a, b, obstacleRects);
        iface.points = points; // keep in-memory scene consistent with what we just saved
        payload[iface.id] = { points: points, manual_override: false, locked: false };
    });

    if (Object.keys(payload).length) {
        bridge.save_routing(JSON.stringify(payload));
    }
}

// Cheap redraw during drag: interface paths that touch this module's pins
// are re-rendered from the current (in-memory) pin positions. This is a
// straight-line placeholder -- real orthogonal re-routing during drag is
// the next increment (schematic_routing.js, ported from smart_connection.py).
function redrawConnectorsFor(movedModule) {
    const pinLookup = buildPinLookup(sceneData);
    g.select('.interfaces-layer').remove();
    renderInterfaces(sceneData, pinLookup, true);
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

function fitView() {
    if (!g.node()) return;
    try {
        requestAnimationFrame(() => {
            const parent = svg.node().getBoundingClientRect();
            const width = parent.width;
            const height = parent.height;
            if (width <= 0 || height <= 0) return;

            const bbox = g.node().getBBox();
            if (!isFinite(bbox.width) || !isFinite(bbox.height) ||
                bbox.width <= 0 || bbox.height <= 0) {
                return;
            }

            const padX = Math.max(8, width * 0.01);
            const padY = Math.max(8, height * 0.01);

            const scaleX = (width - 2 * padX) / bbox.width;
            const scaleY = (height - 2 * padY) / bbox.height;
            let scale = Math.min(scaleX, scaleY);

            const minK = 0.1, maxK = 6;
            scale = Math.max(minK, Math.min(maxK, scale));

            const tx = (width / 2) - scale * (bbox.x + bbox.width / 2);
            const ty = (height / 2) - scale * (bbox.y + bbox.height / 2);

            svg.transition()
                .duration(650)
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

function enableConnectorSideDrag(group, c, module) {
    const dragState = { currentSide: normalizeSide(c.side) };

    group.call(d3.drag()
        .on('start', function (event) {
            event.sourceEvent.stopPropagation();
            dragState.currentSide = normalizeSide(c.side);
        })
        .on('drag', function (event) {
            const w = Math.max(MODULE_MIN_WIDTH, module.width);
            const h = Math.max(MODULE_MIN_HEIGHT, module.height);

            // event.sourceEvent gives us the raw pointer event.
            // We need the pointer position in MODULE-LOCAL coordinates.
            // The module group has transform translate(module.x, module.y),
            // and the SVG has a zoom transform. Use d3.pointer with the
            // module's parent group (scene-root) to get scene coords,
            // then subtract module position to get module-local coords.
            const sceneCoords = d3.pointer(event.sourceEvent, g.node());
            const mx = sceneCoords[0] - module.x; // module-local X
            const my = sceneCoords[1] - module.y; // module-local Y

            // Determine closest side based on which edge the pointer is nearest to
            let newSide;
            const dx = mx - w / 2;
            const dy = my - h / 2;
            const absDx = Math.abs(dx);
            const absDy = Math.abs(dy);

            if (absDx / w > absDy / h) {
                newSide = dx > 0 ? 'right' : 'left';
            } else {
                newSide = dy > 0 ? 'bottom' : 'top';
            }

            // Compute fraction along the new edge, clamped to avoid corners
            let t;
            if (newSide === 'top' || newSide === 'bottom') {
                t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, mx / w));
            } else {
                t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, my / h));
            }

            // Update connector side and position
            c.side = newSide;
            dragState.currentSide = newSide;

            const ep = edgePoint(module, newSide, t);
            const n = SIDE_AXIS[newSide].normal;
            c.x = ep.x + n.x * CONNECTOR_MARGIN;
            c.y = ep.y + n.y * CONNECTOR_MARGIN;

            // Update SVG directly (no full re-render)
            const edge = connectorEdgeAnchor(module, c);
            group.select('.connector-line')
                .attr('x1', edge.x).attr('y1', edge.y)
                .attr('x2', c.x).attr('y2', c.y);
            group.select('.connector-drag-handle')
                .attr('cx', c.x).attr('cy', c.y);

            // Update bounding box
            const bbox = connectorBBox(module, c);
            group.select('.connector-bbox')
                .attr('x', bbox.x).attr('y', bbox.y)
                .attr('width', bbox.w).attr('height', bbox.h);

            // Update wires
            redrawConnectorsFor(module);
        })
        .on('end', function () {
            if (bridge) {
                // save_connector_positions now persists both x, y AND side in one call
                bridge.save_connector_positions(JSON.stringify(
                    { [c.id]: { x: c.x, y: c.y, side: dragState.currentSide } }
                ));
            }
            render(sceneData);
        })
    );
}


// Expose the contract on window explicitly (matches Component_Tree_Window.py
// checking `typeof X === 'function'` from Python via runJavaScript).
window.fitView = fitView;
window.getExportBounds = getExportBounds;
window.setExportOverlayVisible = setExportOverlayVisible;
window.triggerSaveLayout = triggerSaveLayout;
