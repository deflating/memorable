/**
 * Knowledge Graph visualization.
 * Force-directed graph rendered on a canvas element.
 * Supports pan, zoom, drag, hover tooltips, connection highlighting,
 * curved edges with arrows, node clustering, click-to-focus, search,
 * and double-click to expand.
 */

import { esc } from './utils.js';

export const KG_COLORS = {
  person: '#f0c000',
  project: '#4ade80',
  technology: '#60a5fa',
  organization: '#c084fc',
  file: '#94a3b8',
  concept: '#fb923c',
  tool: '#f87171',
  service: '#a78bfa',
  language: '#22d3ee',
};

const KG_SIZES = {
  person: 9,
  project: 8,
  technology: 6,
  organization: 7,
  file: 4,
  concept: 5,
  tool: 5,
  service: 5,
  language: 6,
};

let kgSim = null;

export function stopKG() {
  if (kgSim) {
    cancelAnimationFrame(kgSim);
    kgSim = null;
  }
}

export async function loadKG(container, apiFn) {
  const data = await apiFn('/api/kg');
  if (!data.nodes || !data.nodes.length) {
    container.innerHTML = `<div class="empty">
      <div class="empty-icon">~</div>
      Knowledge graph is empty.<br>Entities will appear as the watcher processes observations.
    </div>`;
    return;
  }

  const nodeMap = {};
  const nodes = [];
  data.nodes.forEach(n => {
    const key = n.name;
    if (!nodeMap[key]) {
      nodeMap[key] = {
        id: key,
        name: n.name,
        type: n.type,
        x: 0, y: 0,
        vx: 0, vy: 0,
      };
      nodes.push(nodeMap[key]);
    }
  });

  const edges = data.edges.map(e => ({
    source: nodeMap[e.source],
    target: nodeMap[e.target],
    type: e.rel_type,
  })).filter(e => e.source && e.target);

  const connections = {};
  const adjacency = {};
  edges.forEach(e => {
    connections[e.source.id] = (connections[e.source.id] || 0) + 1;
    connections[e.target.id] = (connections[e.target.id] || 0) + 1;
    if (!adjacency[e.source.id]) adjacency[e.source.id] = [];
    if (!adjacency[e.target.id]) adjacency[e.target.id] = [];
    adjacency[e.source.id].push({ node: e.target, rel: e.type });
    adjacency[e.target.id].push({ node: e.source, rel: e.type });
  });

  const typesPresent = [...new Set(nodes.map(n => n.type))].sort();

  container.innerHTML = `
    <div id="kg-container">
      <canvas id="kg-canvas"></canvas>
      <input type="text" class="kg-search" id="kg-search" placeholder="Search nodes..." style="
        position: absolute; top: 12px; left: 12px; z-index: 10;
        background: rgba(15, 23, 42, 0.85); border: 1px solid rgba(148, 163, 184, 0.2);
        color: #e2e8f0; padding: 6px 10px; border-radius: 6px; font-size: 12px;
        width: 170px; outline: none; backdrop-filter: blur(8px);
        font-family: inherit; transition: border-color 0.2s;
      " />
      <div class="kg-legend">
        ${typesPresent.map(t => `
          <div class="kg-legend-item">
            <span class="kg-legend-dot" style="background:${KG_COLORS[t] || '#94a3b8'}; color:${KG_COLORS[t] || '#94a3b8'}"></span>
            ${t}
          </div>
        `).join('')}
      </div>
      <div class="kg-stats">${nodes.length} entities &middot; ${edges.length} relationships</div>
      <div class="kg-tooltip" id="kg-tooltip"></div>
    </div>`;

  // Style the search focus state
  const searchInput = document.getElementById('kg-search');
  if (searchInput) {
    searchInput.addEventListener('focus', () => {
      searchInput.style.borderColor = 'rgba(240, 192, 0, 0.5)';
    });
    searchInput.addEventListener('blur', () => {
      searchInput.style.borderColor = 'rgba(148, 163, 184, 0.2)';
    });
  }

  requestAnimationFrame(() => startGraph(nodes, edges, connections, adjacency));
}

function startGraph(nodes, edges, connections, adjacency) {
  const canvas = document.getElementById('kg-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const tooltip = document.getElementById('kg-tooltip');
  const container = document.getElementById('kg-container');
  const searchInput = document.getElementById('kg-search');

  const dpr = window.devicePixelRatio || 1;
  let W, H;

  function resize() {
    const rect = container.getBoundingClientRect();
    W = rect.width;
    H = rect.height;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
  }
  resize();

  // Group nodes by type for clustering initial layout
  const typeGroups = {};
  nodes.forEach(n => {
    if (!typeGroups[n.type]) typeGroups[n.type] = [];
    typeGroups[n.type].push(n);
  });
  const typeKeys = Object.keys(typeGroups);

  // Clustered circular initial layout
  nodes.forEach((n, i) => {
    const typeIdx = typeKeys.indexOf(n.type);
    const groupAngle = (2 * Math.PI * typeIdx) / typeKeys.length;
    const groupR = Math.min(W, H) * 0.15;
    const cx = W / 2 + groupR * Math.cos(groupAngle);
    const cy = H / 2 + groupR * Math.sin(groupAngle);
    const subIdx = typeGroups[n.type].indexOf(n);
    const subAngle = (2 * Math.PI * subIdx) / typeGroups[n.type].length;
    const subR = Math.min(60, typeGroups[n.type].length * 12);
    n.x = cx + subR * Math.cos(subAngle);
    n.y = cy + subR * Math.sin(subAngle);
  });

  // Compute edge curve offsets for parallel edges
  const edgeCurveOffset = new Map();
  const edgePairCount = {};
  edges.forEach((e, idx) => {
    const key = [e.source.id, e.target.id].sort().join('|||');
    if (!edgePairCount[key]) edgePairCount[key] = [];
    edgePairCount[key].push(idx);
  });
  for (const key in edgePairCount) {
    const indices = edgePairCount[key];
    indices.forEach((idx, i) => {
      if (indices.length === 1) {
        edgeCurveOffset.set(idx, 25); // subtle curve even for single edges
      } else {
        const spread = 30;
        edgeCurveOffset.set(idx, (i - (indices.length - 1) / 2) * spread + 20);
      }
    });
  }

  // Type centroid targets for clustering force
  const typeCentroids = {};

  let camX = 0, camY = 0, zoom = 1;
  let dragging = null, dragOffX = 0, dragOffY = 0;
  let panning = false, panStartX = 0, panStartY = 0, panCamX = 0, panCamY = 0;
  let hoveredNode = null;
  let focusedNode = null;    // click-to-focus
  let expandedNode = null;   // double-click expanded (2-hop)
  let hoveredEdge = null;
  let searchQuery = '';
  let alpha = 1;

  const maxVelocity = 8;

  // Sort nodes by connection count for draw order (fewer connections first, hubs on top)
  function getSortedNodes() {
    return [...nodes].sort((a, b) => (connections[a.id] || 0) - (connections[b.id] || 0));
  }
  let sortedNodes = getSortedNodes();

  // Get 1-hop neighbors
  function getNeighbors(node) {
    const set = new Set();
    edges.forEach(e => {
      if (e.source === node) set.add(e.target);
      if (e.target === node) set.add(e.source);
    });
    return set;
  }

  // Get 2-hop neighbors
  function get2HopNeighbors(node) {
    const hop1 = getNeighbors(node);
    const hop2 = new Set(hop1);
    hop1.forEach(n => {
      getNeighbors(n).forEach(nn => hop2.add(nn));
    });
    hop2.delete(node);
    return hop2;
  }

  // Get active highlight set
  function getHighlightedSet() {
    if (expandedNode) {
      const set = get2HopNeighbors(expandedNode);
      set.add(expandedNode);
      return set;
    }
    if (focusedNode) {
      const set = getNeighbors(focusedNode);
      set.add(focusedNode);
      return set;
    }
    return null;
  }

  // Search matching nodes
  function getSearchMatches() {
    if (!searchQuery) return null;
    const q = searchQuery.toLowerCase();
    const matches = new Set();
    nodes.forEach(n => {
      if (n.name.toLowerCase().includes(q) || n.type.toLowerCase().includes(q)) {
        matches.add(n);
      }
    });
    return matches;
  }

  function tick() {
    if (alpha < 0.001) return;

    const N = nodes.length;
    const useBarnesHut = N > 100;
    const repulsion = Math.min(1200, 600 + N * 3);
    const attraction = 0.004;
    const centerForce = 0.006;
    const damping = 0.86;
    const clusterForce = 0.0015;

    // Compute type centroids for clustering
    const typeSums = {};
    const typeCounts = {};
    nodes.forEach(n => {
      if (!typeSums[n.type]) { typeSums[n.type] = { x: 0, y: 0 }; typeCounts[n.type] = 0; }
      typeSums[n.type].x += n.x;
      typeSums[n.type].y += n.y;
      typeCounts[n.type]++;
    });
    for (const t in typeSums) {
      typeCentroids[t] = {
        x: typeSums[t].x / typeCounts[t],
        y: typeSums[t].y / typeCounts[t],
      };
    }

    if (useBarnesHut) {
      // Barnes-Hut approximation via quadtree
      const qt = buildQuadtree(nodes);
      nodes.forEach(n => {
        applyBarnesHut(qt, n, repulsion, 0.7);
      });
    } else {
      // Direct O(n^2) repulsion
      for (let i = 0; i < N; i++) {
        for (let j = i + 1; j < N; j++) {
          const a = nodes[i], b = nodes[j];
          let dx = b.x - a.x, dy = b.y - a.y;
          let dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = repulsion / (dist * dist);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          a.vx -= fx; a.vy -= fy;
          b.vx += fx; b.vy += fy;
        }
      }
    }

    // Edge attraction
    edges.forEach(e => {
      const dx = e.target.x - e.source.x;
      const dy = e.target.y - e.source.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = (dist - 140) * attraction;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      e.source.vx += fx; e.source.vy += fy;
      e.target.vx -= fx; e.target.vy -= fy;
    });

    // Center gravity + type clustering
    nodes.forEach(n => {
      n.vx += (W / 2 - n.x) * centerForce;
      n.vy += (H / 2 - n.y) * centerForce;

      // Gentle attraction toward type centroid
      const c = typeCentroids[n.type];
      if (c) {
        n.vx += (c.x - n.x) * clusterForce;
        n.vy += (c.y - n.y) * clusterForce;
      }
    });

    // Integrate with velocity limiting
    nodes.forEach(n => {
      if (n === dragging) return;
      n.vx *= damping;
      n.vy *= damping;
      // Clamp velocity
      const speed = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
      if (speed > maxVelocity) {
        n.vx = (n.vx / speed) * maxVelocity;
        n.vy = (n.vy / speed) * maxVelocity;
      }
      n.x += n.vx * alpha;
      n.y += n.vy * alpha;
    });

    alpha *= 0.994;
  }

  function getNodeRadius(n) {
    const base = KG_SIZES[n.type] || 5;
    const conn = connections[n.id] || 0;
    return base + Math.min(conn * 1.5, 10);
  }

  // Compute quadratic bezier control point for a curved edge
  function getEdgeControlPoint(e, idx) {
    const mx = (e.source.x + e.target.x) / 2;
    const my = (e.source.y + e.target.y) / 2;
    const dx = e.target.x - e.source.x;
    const dy = e.target.y - e.source.y;
    const len = Math.sqrt(dx * dx + dy * dy) || 1;
    // Normal perpendicular to edge
    const nx = -dy / len;
    const ny = dx / len;
    const offset = edgeCurveOffset.get(idx) || 25;
    return {
      x: mx + nx * offset,
      y: my + ny * offset,
    };
  }

  // Point on quadratic bezier at t
  function bezierPoint(x0, y0, cx, cy, x1, y1, t) {
    const u = 1 - t;
    return {
      x: u * u * x0 + 2 * u * t * cx + t * t * x1,
      y: u * u * y0 + 2 * u * t * cy + t * t * y1,
    };
  }

  // Draw arrow at target end of edge
  function drawArrow(ctx, e, cp) {
    const targetR = getNodeRadius(e.target) + 2;
    // Find the point along the curve that's targetR away from center
    // Approximate by stepping backward from t=1
    let t = 1;
    for (let i = 0; i < 10; i++) {
      const p = bezierPoint(e.source.x, e.source.y, cp.x, cp.y, e.target.x, e.target.y, t);
      const d = Math.sqrt((p.x - e.target.x) ** 2 + (p.y - e.target.y) ** 2);
      if (d >= targetR) break;
      t -= 0.02;
    }
    const tip = bezierPoint(e.source.x, e.source.y, cp.x, cp.y, e.target.x, e.target.y, t);
    const back = bezierPoint(e.source.x, e.source.y, cp.x, cp.y, e.target.x, e.target.y, Math.max(0, t - 0.06));
    const angle = Math.atan2(tip.y - back.y, tip.x - back.x);
    const arrowLen = 7;
    const arrowWidth = 3.5;
    ctx.beginPath();
    ctx.moveTo(tip.x, tip.y);
    ctx.lineTo(
      tip.x - arrowLen * Math.cos(angle - Math.PI / 6),
      tip.y - arrowLen * Math.sin(angle - Math.PI / 6)
    );
    ctx.lineTo(
      tip.x - arrowLen * Math.cos(angle + Math.PI / 6),
      tip.y - arrowLen * Math.sin(angle + Math.PI / 6)
    );
    ctx.closePath();
    ctx.fill();
  }

  // Distance from point to quadratic bezier curve
  function distToEdge(px, py, e, idx) {
    const cp = getEdgeControlPoint(e, idx);
    let minDist = Infinity;
    for (let t = 0; t <= 1; t += 0.05) {
      const p = bezierPoint(e.source.x, e.source.y, cp.x, cp.y, e.target.x, e.target.y, t);
      const d = Math.sqrt((px - p.x) ** 2 + (py - p.y) ** 2);
      if (d < minDist) minDist = d;
    }
    return minDist;
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(camX, camY);
    ctx.scale(zoom, zoom);

    const highlightSet = getHighlightedSet();
    const activeNode = expandedNode || focusedNode;
    const searchMatches = getSearchMatches();
    const font = getComputedStyle(document.body).fontFamily;

    // Edges
    edges.forEach((e, idx) => {
      const isHighlighted = hoveredNode && (e.source === hoveredNode || e.target === hoveredNode);
      const isFocusEdge = activeNode && (e.source === activeNode || e.target === activeNode);
      const isFocusNeighborEdge = highlightSet && highlightSet.has(e.source) && highlightSet.has(e.target);
      const dimmedByFocus = activeNode && !isFocusEdge && !isFocusNeighborEdge;
      const dimmedByHover = hoveredNode && !isHighlighted;
      const dimmedBySearch = searchMatches && (!searchMatches.has(e.source) || !searchMatches.has(e.target));
      const dimmed = dimmedByFocus || dimmedByHover || dimmedBySearch;
      const bright = isHighlighted || isFocusEdge;

      ctx.globalAlpha = dimmed ? 0.04 : (bright ? 0.6 : 0.15);
      ctx.strokeStyle = bright ? '#e2e8f0' : '#64748b';
      ctx.lineWidth = bright ? 1.5 : 0.8;

      const cp = getEdgeControlPoint(e, idx);

      // Curved edge
      ctx.beginPath();
      ctx.moveTo(e.source.x, e.source.y);
      ctx.quadraticCurveTo(cp.x, cp.y, e.target.x, e.target.y);
      ctx.stroke();

      // Arrow
      ctx.fillStyle = ctx.strokeStyle;
      drawArrow(ctx, e, cp);

      // Edge label only on hover over edge
      if (hoveredEdge === idx) {
        const midT = 0.5;
        const mid = bezierPoint(e.source.x, e.source.y, cp.x, cp.y, e.target.x, e.target.y, midT);
        const before = bezierPoint(e.source.x, e.source.y, cp.x, cp.y, e.target.x, e.target.y, 0.45);
        const after = bezierPoint(e.source.x, e.source.y, cp.x, cp.y, e.target.x, e.target.y, 0.55);
        const angle = Math.atan2(after.y - before.y, after.x - before.x);
        // Flip text if upside down
        const displayAngle = (angle > Math.PI / 2 || angle < -Math.PI / 2) ? angle + Math.PI : angle;

        ctx.save();
        ctx.translate(mid.x, mid.y);
        ctx.rotate(displayAngle);
        ctx.globalAlpha = 0.85;
        ctx.fillStyle = '#e2e8f0';
        ctx.font = '10px ' + font;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';
        ctx.fillText(e.type, 0, -4);
        ctx.restore();
      }
    });

    ctx.globalAlpha = 1;

    // Nodes (sorted by connections - fewer first, hubs on top)
    sortedNodes.forEach(n => {
      const r = getNodeRadius(n);
      const color = KG_COLORS[n.type] || '#94a3b8';
      const isHovered = n === hoveredNode;
      const isFocused = n === activeNode;
      const isInHighlightSet = highlightSet && highlightSet.has(n);
      const isSearchMatch = searchMatches && searchMatches.has(n);
      const connectedToHover = hoveredNode && edges.some(e =>
        (e.source === hoveredNode && e.target === n) ||
        (e.target === hoveredNode && e.source === n)
      );
      const dimmedByFocus = activeNode && !isFocused && !isInHighlightSet;
      const dimmedByHover = hoveredNode && !isHovered && !connectedToHover;
      const dimmedBySearch = searchMatches && !isSearchMatch;
      const dimmed = dimmedByFocus || dimmedByHover || dimmedBySearch;

      ctx.globalAlpha = dimmed ? 0.1 : 1;

      // Hover/focus outer glow
      if (isHovered || isFocused) {
        const gradient = ctx.createRadialGradient(n.x, n.y, r, n.x, n.y, r + 16);
        gradient.addColorStop(0, color + '44');
        gradient.addColorStop(1, color + '00');
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 16, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();
      }

      // Focus ring
      if (isFocused) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 3, 0, Math.PI * 2);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.globalAlpha = 0.6;
        ctx.stroke();
        ctx.globalAlpha = dimmed ? 0.1 : 1;
      }

      // Node circle
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();

      // Subtle inner highlight
      if (!dimmed) {
        const ig = ctx.createRadialGradient(n.x - r * 0.3, n.y - r * 0.3, 0, n.x, n.y, r);
        ig.addColorStop(0, 'rgba(255,255,255,0.2)');
        ig.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = ig;
        ctx.fill();
      }

      // Search match highlight ring
      if (searchMatches && isSearchMatch && !dimmed) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
        ctx.strokeStyle = '#f0c000';
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      // Label
      const fontSize = isHovered || isFocused ? 13 : 11;
      ctx.font = `${isHovered || isFocused ? '600' : '500'} ${fontSize}px ${font}`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';

      // Text shadow
      ctx.fillStyle = dimmed ? 'transparent' : 'rgba(10, 14, 20, 0.8)';
      ctx.fillText(n.name, n.x + 1, n.y + r + 5);
      ctx.fillText(n.name, n.x - 1, n.y + r + 5);
      ctx.fillText(n.name, n.x, n.y + r + 6);

      // Text
      ctx.fillStyle = dimmed ? '#475569' : (isHovered || isFocused ? '#f8fafc' : '#cbd5e1');
      ctx.fillText(n.name, n.x, n.y + r + 5);

      ctx.globalAlpha = 1;
    });

    ctx.restore();
  }

  function animate() {
    tick();
    draw();
    kgSim = requestAnimationFrame(animate);
  }

  function screenToWorld(sx, sy) {
    return [(sx - camX) / zoom, (sy - camY) / zoom];
  }

  function findNode(sx, sy) {
    const [wx, wy] = screenToWorld(sx, sy);
    // Search in reverse draw order (most connected first)
    for (let i = sortedNodes.length - 1; i >= 0; i--) {
      const n = sortedNodes[i];
      const r = getNodeRadius(n) + 6;
      if ((wx - n.x) ** 2 + (wy - n.y) ** 2 < r * r) return n;
    }
    return null;
  }

  function findEdge(sx, sy) {
    const [wx, wy] = screenToWorld(sx, sy);
    let bestIdx = -1;
    let bestDist = 12; // threshold in world coords
    edges.forEach((e, idx) => {
      const d = distToEdge(wx, wy, e, idx);
      if (d < bestDist) {
        bestDist = d;
        bestIdx = idx;
      }
    });
    return bestIdx;
  }

  // Smooth camera animation
  let animTarget = null;
  function animateCamera(targetX, targetY, targetZoom, duration = 400) {
    const startX = camX, startY = camY, startZoom = zoom;
    const startTime = performance.now();
    animTarget = { targetX, targetY, targetZoom };
    function step(now) {
      const elapsed = now - startTime;
      const t = Math.min(1, elapsed / duration);
      const ease = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      camX = startX + (targetX - startX) * ease;
      camY = startY + (targetY - startY) * ease;
      zoom = startZoom + (targetZoom - startZoom) * ease;
      if (t < 1) requestAnimationFrame(step);
      else animTarget = null;
    }
    requestAnimationFrame(step);
  }

  // Click handler for focus
  let clickTimer = null;
  canvas.addEventListener('mousedown', e => {
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    const node = findNode(sx, sy);
    if (node) {
      dragging = node;
      dragOffX = node.x - (sx - camX) / zoom;
      dragOffY = node.y - (sy - camY) / zoom;
      alpha = Math.max(alpha, 0.3);
    } else {
      panning = true;
      panStartX = e.clientX;
      panStartY = e.clientY;
      panCamX = camX;
      panCamY = camY;
    }
  });

  let dragMoved = false;
  canvas.addEventListener('mousemove', e => {
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;

    if (dragging) {
      dragMoved = true;
      dragging.x = (sx - camX) / zoom + dragOffX;
      dragging.y = (sy - camY) / zoom + dragOffY;
      dragging.vx = 0;
      dragging.vy = 0;
    } else if (panning) {
      camX = panCamX + (e.clientX - panStartX);
      camY = panCamY + (e.clientY - panStartY);
    } else {
      const node = findNode(sx, sy);
      hoveredNode = node;

      // Check for edge hover when not hovering a node
      if (!node) {
        hoveredEdge = findEdge(sx, sy);
      } else {
        hoveredEdge = null;
      }

      canvas.style.cursor = node ? 'pointer' : (hoveredEdge >= 0 ? 'pointer' : 'grab');

      if (node && tooltip) {
        const conn = connections[node.id] || 0;
        const color = KG_COLORS[node.type] || '#94a3b8';
        const adj = adjacency[node.id] || [];

        // Build connection list (max 5)
        let connHTML = '';
        if (adj.length > 0) {
          const shown = adj.slice(0, 5);
          const remaining = adj.length - shown.length;
          connHTML = '<div style="margin-top:5px;border-top:1px solid rgba(148,163,184,0.15);padding-top:4px;font-size:11px;color:#94a3b8;">';
          shown.forEach(c => {
            const cColor = KG_COLORS[c.node.type] || '#94a3b8';
            connHTML += `<div style="margin:2px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
              <span style="color:#64748b;font-size:10px;">${esc(c.rel)}</span>
              <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${cColor};margin:0 3px;vertical-align:middle;"></span>
              ${esc(c.node.name)}
            </div>`;
          });
          if (remaining > 0) {
            connHTML += `<div style="color:#64748b;font-size:10px;margin-top:2px;">+${remaining} more</div>`;
          }
          connHTML += '</div>';
        }

        tooltip.style.display = 'block';
        tooltip.style.left = Math.min(e.clientX - r.left + 16, W - 220) + 'px';
        tooltip.style.top = (e.clientY - r.top - 10) + 'px';
        tooltip.innerHTML = `
          <div class="tt-name">${esc(node.name)}</div>
          <div class="tt-type">
            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:5px;vertical-align:middle"></span>
            ${node.type} &middot; ${conn} connection${conn !== 1 ? 's' : ''}
          </div>
          ${connHTML}`;
      } else if (hoveredEdge >= 0 && tooltip) {
        const e2 = edges[hoveredEdge];
        const srcColor = KG_COLORS[e2.source.type] || '#94a3b8';
        const tgtColor = KG_COLORS[e2.target.type] || '#94a3b8';
        tooltip.style.display = 'block';
        tooltip.style.left = Math.min(e.clientX - r.left + 16, W - 220) + 'px';
        tooltip.style.top = (e.clientY - r.top - 10) + 'px';
        tooltip.innerHTML = `
          <div class="tt-name" style="font-size:12px;">${esc(e2.type)}</div>
          <div class="tt-type" style="margin-top:3px;">
            <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${srcColor};margin-right:3px;vertical-align:middle"></span>
            ${esc(e2.source.name)}
            <span style="color:#64748b;margin:0 4px;">&#8594;</span>
            <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${tgtColor};margin-right:3px;vertical-align:middle"></span>
            ${esc(e2.target.name)}
          </div>`;
      } else if (tooltip) {
        tooltip.style.display = 'none';
      }
    }
  });

  canvas.addEventListener('mouseup', e => {
    const wasDragging = dragging;
    const moved = dragMoved;
    dragging = null;
    panning = false;
    dragMoved = false;
  });

  // Single click to focus, click background to unfocus
  canvas.addEventListener('click', e => {
    if (dragMoved) return;
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    const node = findNode(sx, sy);

    if (clickTimer) {
      clearTimeout(clickTimer);
      clickTimer = null;
      return; // double click will handle
    }

    clickTimer = setTimeout(() => {
      clickTimer = null;
      if (node) {
        if (focusedNode === node) {
          // Click same node again = unfocus
          focusedNode = null;
          expandedNode = null;
        } else {
          focusedNode = node;
          expandedNode = null;
          // Animate camera to center on node
          const targetZoom = Math.max(zoom, 1.2);
          const targetX = W / 2 - node.x * targetZoom;
          const targetY = H / 2 - node.y * targetZoom;
          animateCamera(targetX, targetY, targetZoom);
        }
      } else {
        // Click background = reset
        focusedNode = null;
        expandedNode = null;
      }
    }, 250);
  });

  // Double click to expand 2-hop
  canvas.addEventListener('dblclick', e => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    const node = findNode(sx, sy);
    if (node) {
      if (expandedNode === node) {
        expandedNode = null;
        focusedNode = null;
      } else {
        expandedNode = node;
        focusedNode = node;
        const targetZoom = Math.max(zoom, 1.0);
        const targetX = W / 2 - node.x * targetZoom;
        const targetY = H / 2 - node.y * targetZoom;
        animateCamera(targetX, targetY, targetZoom);
      }
    }
  });

  canvas.addEventListener('mouseleave', () => {
    dragging = null;
    panning = false;
    hoveredNode = null;
    hoveredEdge = null;
    dragMoved = false;
    if (tooltip) tooltip.style.display = 'none';
  });

  canvas.addEventListener('wheel', e => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    const factor = e.deltaY > 0 ? 0.92 : 1.08;
    const newZoom = Math.max(0.15, Math.min(4, zoom * factor));
    camX = sx - (sx - camX) * (newZoom / zoom);
    camY = sy - (sy - camY) * (newZoom / zoom);
    zoom = newZoom;
  }, { passive: false });

  // Search input handler
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      searchQuery = searchInput.value.trim();
      if (searchQuery) {
        // Clear focus when searching
        focusedNode = null;
        expandedNode = null;
      }
    });
    searchInput.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        searchInput.value = '';
        searchQuery = '';
        searchInput.blur();
      }
      // Prevent global shortcuts from firing
      e.stopPropagation();
    });
  }

  const ro = new ResizeObserver(() => resize());
  ro.observe(container);

  animate();
}

// Barnes-Hut quadtree for efficient repulsion
function buildQuadtree(nodes) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  nodes.forEach(n => {
    if (n.x < minX) minX = n.x;
    if (n.y < minY) minY = n.y;
    if (n.x > maxX) maxX = n.x;
    if (n.y > maxY) maxY = n.y;
  });
  const size = Math.max(maxX - minX, maxY - minY, 1) + 20;

  function createNode(x, y, w) {
    return { x, y, w, cx: 0, cy: 0, mass: 0, body: null, children: null };
  }

  function insert(qnode, body) {
    if (qnode.mass === 0) {
      qnode.body = body;
      qnode.cx = body.x;
      qnode.cy = body.y;
      qnode.mass = 1;
      return;
    }

    if (!qnode.children) {
      qnode.children = [];
      const hw = qnode.w / 2;
      for (let i = 0; i < 4; i++) {
        const cx = qnode.x + (i % 2) * hw;
        const cy = qnode.y + Math.floor(i / 2) * hw;
        qnode.children.push(createNode(cx, cy, hw));
      }
      if (qnode.body) {
        insertIntoChild(qnode, qnode.body);
        qnode.body = null;
      }
    }

    insertIntoChild(qnode, body);
    qnode.cx = (qnode.cx * qnode.mass + body.x) / (qnode.mass + 1);
    qnode.cy = (qnode.cy * qnode.mass + body.y) / (qnode.mass + 1);
    qnode.mass++;
  }

  function insertIntoChild(qnode, body) {
    const hw = qnode.w / 2;
    const ix = body.x < qnode.x + hw ? 0 : 1;
    const iy = body.y < qnode.y + hw ? 0 : 1;
    insert(qnode.children[iy * 2 + ix], body);
  }

  const root = createNode(minX - 10, minY - 10, size);
  nodes.forEach(n => insert(root, n));
  return root;
}

function applyBarnesHut(qnode, body, repulsion, theta) {
  if (qnode.mass === 0) return;

  const dx = qnode.cx - body.x;
  const dy = qnode.cy - body.y;
  const dist = Math.sqrt(dx * dx + dy * dy) || 1;

  if (qnode.body === body) return;

  if (!qnode.children || (qnode.w / dist) < theta) {
    const force = repulsion * qnode.mass / (dist * dist);
    body.vx -= (dx / dist) * force;
    body.vy -= (dy / dist) * force;
    return;
  }

  if (qnode.children) {
    qnode.children.forEach(c => applyBarnesHut(c, body, repulsion, theta));
  }
}
