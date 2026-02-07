/**
 * Knowledge Graph visualization.
 * Force-directed graph rendered on a canvas element.
 * Supports pan, zoom, drag, hover tooltips, and connection highlighting.
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
        priority: n.priority,
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
  edges.forEach(e => {
    connections[e.source.id] = (connections[e.source.id] || 0) + 1;
    connections[e.target.id] = (connections[e.target.id] || 0) + 1;
  });

  const typesPresent = [...new Set(nodes.map(n => n.type))].sort();

  container.innerHTML = `
    <div id="kg-container">
      <canvas id="kg-canvas"></canvas>
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

  requestAnimationFrame(() => startGraph(nodes, edges, connections));
}

function startGraph(nodes, edges, connections) {
  const canvas = document.getElementById('kg-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const tooltip = document.getElementById('kg-tooltip');
  const container = document.getElementById('kg-container');

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

  // Circular initial layout
  nodes.forEach((n, i) => {
    const angle = (2 * Math.PI * i) / nodes.length;
    const r = Math.min(W, H) * 0.3;
    n.x = W / 2 + r * Math.cos(angle);
    n.y = H / 2 + r * Math.sin(angle);
  });

  let camX = 0, camY = 0, zoom = 1;
  let dragging = null, dragOffX = 0, dragOffY = 0;
  let panning = false, panStartX = 0, panStartY = 0, panCamX = 0, panCamY = 0;
  let hoveredNode = null;
  let alpha = 1;

  function tick() {
    if (alpha < 0.001) return;

    const repulsion = 900;
    const attraction = 0.004;
    const centerForce = 0.008;
    const damping = 0.88;

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
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

    nodes.forEach(n => {
      n.vx += (W / 2 - n.x) * centerForce;
      n.vy += (H / 2 - n.y) * centerForce;
    });

    nodes.forEach(n => {
      if (n === dragging) return;
      n.vx *= damping;
      n.vy *= damping;
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

  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(camX, camY);
    ctx.scale(zoom, zoom);

    // Edges
    edges.forEach(e => {
      const isHighlighted = hoveredNode && (e.source === hoveredNode || e.target === hoveredNode);
      const dimmed = hoveredNode && !isHighlighted;

      ctx.globalAlpha = dimmed ? 0.06 : (isHighlighted ? 0.6 : 0.15);
      ctx.strokeStyle = isHighlighted ? '#e2e8f0' : '#64748b';
      ctx.lineWidth = isHighlighted ? 1.5 : 0.8;
      ctx.beginPath();
      ctx.moveTo(e.source.x, e.source.y);
      ctx.lineTo(e.target.x, e.target.y);
      ctx.stroke();

      if (isHighlighted) {
        const mx = (e.source.x + e.target.x) / 2;
        const my = (e.source.y + e.target.y) / 2;
        ctx.globalAlpha = 0.7;
        ctx.fillStyle = '#94a3b8';
        ctx.font = '10px ' + getComputedStyle(document.body).fontFamily;
        ctx.textAlign = 'center';
        ctx.fillText(e.type, mx, my - 5);
      }
    });

    ctx.globalAlpha = 1;

    // Nodes
    nodes.forEach(n => {
      const r = getNodeRadius(n);
      const color = KG_COLORS[n.type] || '#94a3b8';
      const isHovered = n === hoveredNode;
      const isConnected = hoveredNode && edges.some(e =>
        (e.source === hoveredNode && e.target === n) ||
        (e.target === hoveredNode && e.source === n)
      );
      const dimmed = hoveredNode && !isHovered && !isConnected;

      ctx.globalAlpha = dimmed ? 0.12 : 1;

      // Outer glow
      if (isHovered) {
        const gradient = ctx.createRadialGradient(n.x, n.y, r, n.x, n.y, r + 16);
        gradient.addColorStop(0, color + '44');
        gradient.addColorStop(1, color + '00');
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 16, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();
      }

      // Node
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

      // Label with shadow for readability
      const fontSize = isHovered ? 13 : 11;
      ctx.font = `${isHovered ? '600' : '500'} ${fontSize}px ` + getComputedStyle(document.body).fontFamily;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';

      // Text shadow
      ctx.fillStyle = dimmed ? 'transparent' : 'rgba(10, 14, 20, 0.8)';
      ctx.fillText(n.name, n.x + 1, n.y + r + 5);
      ctx.fillText(n.name, n.x - 1, n.y + r + 5);
      ctx.fillText(n.name, n.x, n.y + r + 6);

      // Text
      ctx.fillStyle = dimmed ? '#475569' : (isHovered ? '#f8fafc' : '#cbd5e1');
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
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const r = getNodeRadius(n) + 6;
      if ((wx - n.x) ** 2 + (wy - n.y) ** 2 < r * r) return n;
    }
    return null;
  }

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

  canvas.addEventListener('mousemove', e => {
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;

    if (dragging) {
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
      canvas.style.cursor = node ? 'pointer' : 'grab';

      if (node && tooltip) {
        const conn = connections[node.id] || 0;
        const color = KG_COLORS[node.type] || '#94a3b8';
        tooltip.style.display = 'block';
        tooltip.style.left = Math.min(e.clientX - r.left + 16, W - 200) + 'px';
        tooltip.style.top = (e.clientY - r.top - 10) + 'px';
        tooltip.innerHTML = `
          <div class="tt-name">${esc(node.name)}</div>
          <div class="tt-type">
            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:5px;vertical-align:middle"></span>
            ${node.type} &middot; ${conn} connection${conn !== 1 ? 's' : ''}
          </div>`;
      } else if (tooltip) {
        tooltip.style.display = 'none';
      }
    }
  });

  canvas.addEventListener('mouseup', () => { dragging = null; panning = false; });

  canvas.addEventListener('mouseleave', () => {
    dragging = null;
    panning = false;
    hoveredNode = null;
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

  const ro = new ResizeObserver(() => resize());
  ro.observe(container);

  animate();
}
