// Schematic_View_tab/web_assets/schematic_routing.js
//
// Orthogonal grid-router ported from smart_connection.py's _GridRouter
// (build_coords -> build_graph -> A* -> collinear compression) plus the
// lead-stub extrusion and first/last-leg snapping from
// SmartOrthogonalConnector._extruded_lead_point / _snap_first_last_legs.
//
// Pure geometry/pathfinding, no DOM/Qt dependency, so it can run in the
// browser during drag for live re-routing.
//
// NOT ported in this pass (left as future work, same as the Python
// version's more advanced features): per-segment manual dragging, route
// locking / "manual override" persistence. Those sit on top of the base
// route computed here and can be layered on once this matches the Python
// output closely enough in practice.

const ORTHO_EPS = 1.5;
const SIDE_VECTORS = {
    right:  { x: +1, y: 0 },
    left:   { x: -1, y: 0 },
    top:    { x: 0, y: -1 },
    bottom: { x: 0, y: +1 },
};

function round3(v) { return Math.round(v * 1000) / 1000; }

function uniqueSorted(vals) {
    const seen = new Set();
    vals.forEach(v => seen.add(round3(v)));
    return Array.from(seen).sort((a, b) => a - b);
}

class GridRouter {
    constructor(rects, margin) {
        // rects: [{x, y, width, height}, ...] in scene coordinates (NOT yet inflated)
        this.rects = rects.map(r => ({
            left: r.x - margin, top: r.y - margin,
            right: r.x + r.width + margin, bottom: r.y + r.height + margin,
        }));
        this.margin = margin;
    }

    _insideAnyRect(p) {
        return this.rects.some(r => p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom);
    }

    _segmentHitsAnyRect(a, b) {
        const EPS = 1e-9;
        if (Math.abs(a.y - b.y) <= EPS) { // horizontal
            const y = a.y;
            let x1 = a.x, x2 = b.x;
            if (x1 > x2) [x1, x2] = [x2, x1];
            return this.rects.some(r =>
                (r.top - EPS <= y && y <= r.bottom + EPS) &&
                !(x2 <= r.left - EPS || x1 >= r.right + EPS));
        }
        if (Math.abs(a.x - b.x) <= EPS) { // vertical
            const x = a.x;
            let y1 = a.y, y2 = b.y;
            if (y1 > y2) [y1, y2] = [y2, y1];
            return this.rects.some(r =>
                (r.left - EPS <= x && x <= r.right + EPS) &&
                !(y2 <= r.top - EPS || y1 >= r.bottom + EPS));
        }
        return true; // diagonal segments are never allowed on an orthogonal grid
    }

    _buildCoords(startPt, endPt) {
        const xs = [startPt.x, endPt.x];
        const ys = [startPt.y, endPt.y];
        this.rects.forEach(r => { xs.push(r.left, r.right); ys.push(r.top, r.bottom); });
        return [uniqueSorted(xs), uniqueSorted(ys)];
    }

    _buildGraph(xs, ys) {
        const coord = (ix, iy) => ({ x: xs[ix], y: ys[iy] });
        const key = (ix, iy) => ix + ',' + iy;

        const blocked = new Set();
        const nodes = [];
        for (let ix = 0; ix < xs.length; ix++) {
            for (let iy = 0; iy < ys.length; iy++) {
                const p = coord(ix, iy);
                if (this._insideAnyRect(p)) blocked.add(key(ix, iy));
                else nodes.push(key(ix, iy));
            }
        }

        const adj = new Map();
        nodes.forEach(k => adj.set(k, []));

        // Horizontal neighbors
        for (let iy = 0; iy < ys.length; iy++) {
            let prevIx = null;
            for (let ix = 0; ix < xs.length; ix++) {
                if (blocked.has(key(ix, iy))) { prevIx = null; continue; }
                if (prevIx !== null) {
                    const a = coord(prevIx, iy), b = coord(ix, iy);
                    if (!this._segmentHitsAnyRect(a, b)) {
                        const w = Math.abs(xs[ix] - xs[prevIx]);
                        adj.get(key(prevIx, iy)).push([key(ix, iy), w]);
                        adj.get(key(ix, iy)).push([key(prevIx, iy), w]);
                    }
                }
                prevIx = ix;
            }
        }

        // Vertical neighbors
        for (let ix = 0; ix < xs.length; ix++) {
            let prevIy = null;
            for (let iy = 0; iy < ys.length; iy++) {
                if (blocked.has(key(ix, iy))) { prevIy = null; continue; }
                if (prevIy !== null) {
                    const a = coord(ix, prevIy), b = coord(ix, iy);
                    if (!this._segmentHitsAnyRect(a, b)) {
                        const w = Math.abs(ys[iy] - ys[prevIy]);
                        adj.get(key(ix, prevIy)).push([key(ix, iy), w]);
                        adj.get(key(ix, iy)).push([key(ix, prevIy), w]);
                    }
                }
                prevIy = iy;
            }
        }
        return adj;
    }

    _ensureOnGrid(xs, ys, p) {
        const rx = round3(p.x), ry = round3(p.y);
        if (!xs.includes(rx)) { xs.push(rx); xs.sort((a, b) => a - b); }
        if (!ys.includes(ry)) { ys.push(ry); ys.sort((a, b) => a - b); }
        return [xs.indexOf(rx), ys.indexOf(ry)];
    }

    _astar(adj, startKey, goalKey, xs, ys) {
        const parseKey = k => k.split(',').map(Number);
        const h = (aKey, bKey) => {
            const [ix, iy] = parseKey(aKey), [jx, jy] = parseKey(bKey);
            return Math.abs(xs[ix] - xs[jx]) + Math.abs(ys[iy] - ys[jy]);
        };
        const dir = (uKey, vKey) => {
            const [, uy] = parseKey(uKey), [, vy] = parseKey(vKey);
            return uy === vy ? 0 : 1;
        };
        const TURN = 2.0;

        const g = new Map([[startKey, 0]]);
        const came = new Map();
        const seen = new Set();
        const pq = [[h(startKey, goalKey), startKey]]; // small graphs -> plain array is fine

        while (pq.length) {
            pq.sort((a, b) => a[0] - b[0]);
            const [, u] = pq.shift();
            if (seen.has(u)) continue;
            seen.add(u);
            if (u === goalKey) {
                const path = [u];
                let cur = u;
                while (came.has(cur)) { cur = came.get(cur); path.push(cur); }
                return path.reverse();
            }
            for (const [v, w] of (adj.get(u) || [])) {
                let pen = 0;
                if (came.has(u) && dir(came.get(u), u) !== dir(u, v)) pen = TURN;
                const ng = g.get(u) + w + pen;
                if (ng < (g.has(v) ? g.get(v) : Infinity)) {
                    g.set(v, ng);
                    came.set(v, u);
                    pq.push([ng + h(v, goalKey), v]);
                }
            }
        }
        return null;
    }

    route(startPt, endPt) {
        const [xs, ys] = this._buildCoords(startPt, endPt);
        const [sIx, sIy] = this._ensureOnGrid(xs, ys, startPt);
        const [eIx, eIy] = this._ensureOnGrid(xs, ys, endPt);
        const adj = this._buildGraph(xs, ys);
        const keyPath = this._astar(adj, sIx + ',' + sIy, eIx + ',' + eIy, xs, ys);
        if (!keyPath) return null;

        const pts = keyPath.map(k => {
            const [ix, iy] = k.split(',').map(Number);
            return { x: xs[ix], y: ys[iy] };
        });

        // Compress collinear points (identical logic to _GridRouter.route in Python)
        const out = [pts[0]];
        for (let i = 1; i < pts.length - 1; i++) {
            const a = out[out.length - 1], b = pts[i], c = pts[i + 1];
            const sameX = Math.abs(a.x - b.x) <= 1e-6 && Math.abs(b.x - c.x) <= 1e-6;
            const sameY = Math.abs(a.y - b.y) <= 1e-6 && Math.abs(b.y - c.y) <= 1e-6;
            if (sameX || sameY) continue;
            out.push(b);
        }
        out.push(pts[pts.length - 1]);
        return out;
    }
}

// ---------------------------------------------------------------------
// High-level helper used by schematic_render.js
// ---------------------------------------------------------------------

function extrudedLeadPoint(p, side, length) {
    const v = SIDE_VECTORS[side] || SIDE_VECTORS.right;
    return { x: p.x + v.x * length, y: p.y + v.y * length };
}

/**
 * Compute an orthogonal route between two pins, going around the given
 * module rectangles (obstacles). Mirrors SmartOrthogonalConnector's
 * lead-stub + _GridRouter + leg-snapping pipeline.
 *
 * @param {{x:number,y:number,side:string}} fromPin  absolute scene coords + side ('left'/'right'/'top'/'bottom')
 * @param {{x:number,y:number,side:string}} toPin
 * @param {Array<{x:number,y:number,width:number,height:number}>} obstacleRects  module rects in scene coords (un-inflated)
 * @param {number} margin  clearance kept around each obstacle (matches DEFAULT_MARGIN = 20 in smart_connection.py)
 * @param {number} lead    stub length extruded from the pin before routing (matches DEFAULT_LEAD_LENGTH = 24)
 */
function routeOrthogonal(fromPin, toPin, obstacleRects, margin = 20, lead = 24) {
    const startLead = extrudedLeadPoint(fromPin, fromPin.side || 'right', Math.max(lead, margin + 2));
    const endLead = extrudedLeadPoint(toPin, toPin.side || 'left', Math.max(lead, margin + 2));

    const router = new GridRouter(obstacleRects, margin);
    const middle = router.route(startLead, endLead);
    if (!middle) {
        // No obstacle-free path found -- fall back to a direct line through
        // the lead stubs so the connection never just disappears.
        return [fromPin, startLead, endLead, toPin];
    }

    const points = [fromPin, startLead, ...middle, endLead, toPin];

    // Snap the first/last legs to be perpendicular to each pin
    // (mirrors _snap_first_last_legs in smart_connection.py).
    if (points.length >= 2) {
        const sideS = fromPin.side || 'right';
        if (sideS === 'left' || sideS === 'right') points[1].y = points[0].y;
        else points[1].x = points[0].x;

        const n = points.length;
        const sideE = toPin.side || 'left';
        if (sideE === 'left' || sideE === 'right') points[n - 2].y = points[n - 1].y;
        else points[n - 2].x = points[n - 1].x;
    }

    return points;
}

window.SchematicRouting = { GridRouter, routeOrthogonal, extrudedLeadPoint };
