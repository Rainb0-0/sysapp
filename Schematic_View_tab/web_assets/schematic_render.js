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

const MODULE_MIN_WIDTH = 140;
const MODULE_MIN_HEIGHT = 80;
const PIN_RADIUS = 4;
const CONNECTOR_STUB = 18; // how far a connector's pin row sticks out from the module edge


const EDGE_THRESHOLD   = 0.12;   // fraction از طول edge که trigger می‌کنه side switch
const SIDE_SWITCH_DELAY_MS = 400;
const CORNER_OFFSET    = 0.08;   // حداقل فاصله از گوشه (fraction)
const CONNECTOR_MARGIN = 20;     // px بیرون از edge برای tip


// داخل forEach connector، به‌جای append مستقیم line:
const cg = parent.append('g')
    .attr('class', 'connector-group')
    .attr('data-connector-id', c.id);

cg.append('line')
    .attr('class', 'connector-line')
    .attr('data-connector-id', c.id)
    .attr('x1', edge.x).attr('y1', edge.y)
    .attr('x2', c.x)   .attr('y2', c.y)
    .on('dblclick', function (event) {
        event.stopPropagation();
        openPinOrderDialog(c);
    })
    .on('contextmenu', function (event) {
        event.preventDefault();
        event.stopPropagation();
        showConnectorContextMenu(event, c);
    });

cg.append('circle')
    .attr('class', 'connector-drag-handle')
    .attr('cx', c.x).attr('cy', c.y)
    .attr('r', 6);

enableConnectorSideDrag(cg, c, module);


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
        });

        setTimeout(() => bridge.get_scene_data(), 100);
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

    renderInterfaces(scene, pinLookup, false);
    renderModules(scene);

    if (!render.hasFitOnce) {
        setTimeout(() => { fitView(); render.hasFitOnce = true; }, 300);
    }
}

// If a connector/pin has never been saved with an explicit x/y, stack it
// evenly along its module's edge (whichever side it's assigned to).
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
        const count = group.length;
        group.forEach(function (c, i) {
            if (c.x !== null && c.x !== undefined && c.y !== null && c.y !== undefined) return;
            const t = (i + 1) / (count + 1);
            const edge = edgePoint(module, side, t);
            const normal = SIDE_AXIS[side].normal;
            c.x = edge.x + normal.x * CONNECTOR_STUB;
            c.y = edge.y + normal.y * CONNECTOR_STUB;
        });
    });
}

function buildPinLookup(scene) {
    // pin_id -> absolute {x, y, side} in scene coordinates, used to draw interfaces
    const lookup = {};
    scene.connectors.forEach(function (c) {
        const module = scene.modules.find(m => String(m.id) === String(c.module_id));
        if (!module) return;
        const side = normalizeSide(c.side);
        const tangent = SIDE_AXIS[side].tangent;
        const count = c.pins.length;
        c.pins.forEach(function (p, i) {
            const offset = -(count - 1) * 6 + i * 12;
            lookup[p.id] = {
                x: module.x + c.x + tangent.x * offset,
                y: module.y + c.y + tangent.y * offset,
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
        .attr('fill', d => d.color || 'var(--primary-light)');

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

        parent.append('line')
            .attr('class', 'connector-line')
            .attr('data-connector-id', c.id)
            .attr('x1', edge.x).attr('y1', edge.y)
            .attr('x2', c.x).attr('y2', c.y)
            .on('dblclick', function (event) {
                event.stopPropagation();
                openPinOrderDialog(c);
            })
            .on('contextmenu', function (event) {
                event.preventDefault();
                event.stopPropagation();
                showConnectorContextMenu(event, c);
            });

        c.pins.forEach(function (p, i) {
            const offset = -(count - 1) * 6 + i * 12;
            const px = c.x + tangent.x * offset;
            const py = c.y + tangent.y * offset;

            parent.append('circle')
                .attr('class', 'pin-circle')
                .attr('data-pin-id', p.id)
                .attr('cx', px).attr('cy', py)
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
// Pin-to-pin drag to create a new connection
// ---------------------------------------------------------------------
function startConnectDrag(pinId, localX, localY, moduleSelection) {
    cancelConnectDrag();

    const d = moduleSelection.datum();
    const startAbs = { x: d.x + localX, y: d.y + localY };

    const tempPath = g.append('path')
        .attr('class', 'interface-path')
        .attr('stroke-dasharray', '4,3')
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
function showContextMenu(event, items) {
    removeContextMenu();

    const menu = document.createElement('div');
    menu.id = 'schematic-context-menu';
    menu.style.cssText = `
        position: fixed; left: ${event.clientX}px; top: ${event.clientY}px;
        background: var(--primary-dark); border: 1px solid var(--primary-light);
        border-radius: 6px; padding: 4px 0; z-index: 1000;
        box-shadow: 0 4px 12px rgba(0,0,0,0.4); font-size: 13px; min-width: 150px;
    `;

    items.forEach(function (item) {
        const el = document.createElement('div');
        el.textContent = item.label;
        el.style.cssText = 'padding: 6px 14px; cursor: pointer; color: var(--text-primary);';
        el.addEventListener('mouseenter', () => el.style.background = 'var(--primary-light)');
        el.addEventListener('mouseleave', () => el.style.background = 'transparent');
        el.addEventListener('click', function () {
            item.action();
            removeContextMenu();
        });
        menu.appendChild(el);
    });

    document.body.appendChild(menu);
    setTimeout(() => document.addEventListener('click', removeContextMenu, { once: true }), 0);
}

function removeContextMenu() {
    const existing = document.getElementById('schematic-context-menu');
    if (existing) existing.remove();
}

function showModuleContextMenu(event, moduleDatum) {
    showContextMenu(event, [
        { label: 'Add Connector', action: () => addConnectorPrompt(moduleDatum) },
        { label: 'Rename', action: () => renameModulePrompt(moduleDatum) },
        { label: 'Delete', action: () => deleteModuleConfirm(moduleDatum) },
    ]);
}

function showConnectorContextMenu(event, connectorDatum) {
    showContextMenu(event, [
        { label: 'Reorder Pins', action: () => openPinOrderDialog(connectorDatum) },
        { label: 'Add Pin', action: () => addPinPrompt(connectorDatum) },
        { label: 'Rename', action: () => renameConnectorPrompt(connectorDatum) },
        { label: 'Delete', action: () => deleteConnectorConfirm(connectorDatum) },
    ]);
}

function showPinContextMenu(event, pinDatum, connectorDatum) {
    showContextMenu(event, [
        { label: 'Rename', action: () => renamePinPrompt(pinDatum) },
        { label: 'Delete', action: () => deletePinConfirm(pinDatum) },
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



function getConnectorBounds(side, moduleRect, connW, connH, cornerOffset = CORNER_OFFSET) {
  if (side === 'top' || side === 'bottom') {
    return [moduleRect.x + cornerOffset,
            moduleRect.x + moduleRect.width - connW - cornerOffset];
  }
  return [moduleRect.y + cornerOffset,
          moduleRect.y + moduleRect.height - connH - cornerOffset];
}

function getFixedPosition(side, axisVal, moduleRect, connW, connH, margin = CONNECTOR_MARGIN) {
  switch (side) {
    case 'top':    return { x: axisVal, y: moduleRect.y - connH - margin };
    case 'bottom': return { x: axisVal, y: moduleRect.y + moduleRect.height + margin };
    case 'left':   return { x: moduleRect.x - connW - margin, y: axisVal };
    case 'right':  return { x: moduleRect.x + moduleRect.width + margin, y: axisVal };
  }
}

function getNeighborSide(side, atMax) {
  const map = {
    top:    atMax ? 'right' : 'left',
    bottom: atMax ? 'right' : 'left',
    left:   atMax ? 'bottom' : 'top',
    right:  atMax ? 'bottom' : 'top',
  };
  return map[side];
}


function enableConnectorSideDrag(group, c, module) {
    let switchTimer = null;

    group.call(d3.drag()
        .on('start', function (event) {
            event.sourceEvent.stopPropagation();
            clearTimeout(switchTimer);
        })
        .on('drag', function (event) {
            clearTimeout(switchTimer);

            const w = Math.max(MODULE_MIN_WIDTH, module.width);
            const h = Math.max(MODULE_MIN_HEIGHT, module.height);
            const side = normalizeSide(c.side);
            const isHoriz = (side === 'top' || side === 'bottom');

            // محاسبه fraction و clamp
            let t = isHoriz
                ? Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, event.x / w))
                : Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, event.y / h));

            const fraction = isHoriz ? event.x / w : event.y / h;

            // بررسی نزدیکی به گوشه → side switch
            if (fraction < EDGE_THRESHOLD || fraction > 1 - EDGE_THRESHOLD) {
                const newSide = NEIGHBOR[side];
                switchTimer = setTimeout(() => performSideSwitch(c, newSide), SIDE_SWITCH_DELAY_MS);
            }

            // به‌روزرسانی مختصات روی edge فعلی
            const ep = edgePoint(module, side, t);
            const n  = SIDE_AXIS[side].normal;
            c.x = ep.x + n.x * CONNECTOR_MARGIN;
            c.y = ep.y + n.y * CONNECTOR_MARGIN;

            // آپدیت مستقیم SVG (بدون re-render کامل)
            group.select('.connector-line')
                .attr('x1', connectorEdgeAnchor(module, c).x)
                .attr('y1', connectorEdgeAnchor(module, c).y)
                .attr('x2', c.x).attr('y2', c.y);
            group.select('.connector-drag-handle')
                .attr('cx', c.x).attr('cy', c.y);

            // آپدیت wire‌ها
            redrawConnectorsFor(module);
        })
        .on('end', function () {
            clearTimeout(switchTimer);
            if (bridge)
                bridge.save_connector_positions(JSON.stringify({ [c.id]: { x: c.x, y: c.y } }));
            render(sceneData);
        })
    );
}


// schematic_render.js — اضافه کردن به انتهای فایل

function performSideSwitch(c, newSide) {
    const module = sceneData.modules.find(m => String(m.id) === String(c.module_id));
    if (!module) return;

    const w = Math.max(MODULE_MIN_WIDTH, module.width);
    const h = Math.max(MODULE_MIN_HEIGHT, module.height);
    const oldSide = normalizeSide(c.side);

    // محاسبه fraction روی edge قدیمی
    let t;
    if (oldSide === 'left' || oldSide === 'right') t = c.y / h;
    else t = c.x / w;
    t = Math.max(CORNER_OFFSET, Math.min(1 - CORNER_OFFSET, t));

    const newSideNorm = normalizeSide(newSide);
    const ep = edgePoint(module, newSideNorm, t);
    const n  = SIDE_AXIS[newSideNorm].normal;

    c.side = newSideNorm;
    c.x = ep.x + n.x * CONNECTOR_MARGIN;
    c.y = ep.y + n.y * CONNECTOR_MARGIN;

    render(sceneData);
    persistCurrentRoutes();

    if (bridge) {
        bridge.set_connector_side(c.id, newSideNorm);
        bridge.save_connector_positions(JSON.stringify({ [c.id]: { x: c.x, y: c.y } }));
    }
}

function computeConnectorFraction(c, module, side) {
    const w = Math.max(MODULE_MIN_WIDTH, module.width || 0);
    const h = Math.max(MODULE_MIN_HEIGHT, module.height || 0);
    if (side === 'top' || side === 'bottom') {
        return w > 0 ? Math.min(1, Math.max(0, c.x / w)) : 0.5;
    } else {
        return h > 0 ? Math.min(1, Math.max(0, c.y / h)) : 0.5;
    }
}

function computeConnectorTip(module, side, fraction) {
    const w = Math.max(MODULE_MIN_WIDTH, module.width || 0);
    const h = Math.max(MODULE_MIN_HEIGHT, module.height || 0);
    const normal = SIDE_AXIS[side].normal;
    const ep = edgePoint(module, side, fraction);  // نقطه روی مرز module
    return {
        x: ep.x + normal.x * CONNECTOR_STUB,
        y: ep.y + normal.y * CONNECTOR_STUB
    };
}


// Expose the contract on window explicitly (matches Component_Tree_Window.py
// checking `typeof X === 'function'` from Python via runJavaScript).
window.fitView = fitView;
window.getExportBounds = getExportBounds;
window.setExportOverlayVisible = setExportOverlayVisible;
window.triggerSaveLayout = triggerSaveLayout;
