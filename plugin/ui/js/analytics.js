/**
 * Analytics dashboard tab.
 * Activity heatmap (GitHub-style), bar charts, stats cards,
 * observation type breakdown, top entities list.
 * All charts rendered as inline SVG for simplicity and dark theme consistency.
 */

import { esc } from './utils.js';
import { typeColors, badgeLabel } from './components.js';

// ── API helper ──

let _apiFn = null;
export function setAnalyticsApi(fn) { _apiFn = fn; }

// ── Constants ──

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const DAYS_SHORT = ['', 'M', '', 'W', '', 'F', ''];

// ── Heatmap ──

function buildHeatmapData(daily, days) {
  const map = {};
  (daily || []).forEach(d => { map[d.date] = d; });

  const cells = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    const entry = map[key] || { date: key, sessions: 0, observations: 0, prompts: 0, words: 0 };
    cells.push({
      date: key,
      dayOfWeek: d.getDay(),
      total: entry.sessions + entry.observations + entry.prompts,
      sessions: entry.sessions,
      observations: entry.observations,
      prompts: entry.prompts,
      words: entry.words,
    });
  }
  return cells;
}

function getHeatColor(value, max) {
  if (value === 0) return 'var(--bg-card)';
  const intensity = Math.min(value / Math.max(max, 1), 1);
  if (intensity < 0.25) return 'rgba(240, 192, 0, 0.15)';
  if (intensity < 0.5) return 'rgba(240, 192, 0, 0.30)';
  if (intensity < 0.75) return 'rgba(240, 192, 0, 0.55)';
  return 'rgba(240, 192, 0, 0.85)';
}

function renderHeatmap(daily, days = 90) {
  const cells = buildHeatmapData(daily, days);
  const max = Math.max(1, ...cells.map(c => c.total));

  const cellSize = 13;
  const gap = 3;
  const step = cellSize + gap;

  // Group into weeks
  const weeks = [];
  let currentWeek = [];
  cells.forEach((cell, i) => {
    if (i === 0) {
      // Pad the first week with empty cells
      for (let d = 0; d < cell.dayOfWeek; d++) {
        currentWeek.push(null);
      }
    }
    currentWeek.push(cell);
    if (cell.dayOfWeek === 6 || i === cells.length - 1) {
      weeks.push(currentWeek);
      currentWeek = [];
    }
  });

  const svgWidth = weeks.length * step + 30;
  const svgHeight = 7 * step + 24;

  let rects = '';
  weeks.forEach((week, wi) => {
    week.forEach((cell, di) => {
      if (!cell) return;
      const x = wi * step + 28;
      const y = di * step;
      const color = getHeatColor(cell.total, max);
      const title = `${cell.date}: ${cell.sessions} sessions, ${cell.observations} obs, ${cell.prompts} prompts`;
      rects += `<rect x="${x}" y="${y}" width="${cellSize}" height="${cellSize}" rx="2"
        fill="${color}" data-date="${cell.date}" data-total="${cell.total}">
        <title>${esc(title)}</title>
      </rect>`;
    });
  });

  // Day labels
  let dayLabels = '';
  DAYS_SHORT.forEach((label, i) => {
    if (label) {
      dayLabels += `<text x="12" y="${i * step + cellSize - 2}" fill="var(--text-dim)" font-size="10" text-anchor="middle" font-family="var(--font-sans)">${label}</text>`;
    }
  });

  // Month labels along the top
  let monthLabels = '';
  let lastMonth = -1;
  weeks.forEach((week, wi) => {
    const firstCell = week.find(c => c !== null);
    if (firstCell) {
      const d = new Date(firstCell.date);
      if (d.getMonth() !== lastMonth) {
        lastMonth = d.getMonth();
        monthLabels += `<text x="${wi * step + 28}" y="${7 * step + 16}" fill="var(--text-dim)" font-size="10" font-family="var(--font-sans)">${MONTHS[lastMonth]}</text>`;
      }
    }
  });

  return `<div class="analytics-card">
    <h3 class="analytics-card-title">Activity</h3>
    <div class="heatmap-scroll">
      <svg width="${svgWidth}" height="${svgHeight}" class="heatmap-svg">
        ${dayLabels}
        ${rects}
        ${monthLabels}
      </svg>
    </div>
    <div class="heatmap-legend">
      <span>Less</span>
      <span class="heatmap-legend-cell" style="background: var(--bg-card)"></span>
      <span class="heatmap-legend-cell" style="background: rgba(240, 192, 0, 0.15)"></span>
      <span class="heatmap-legend-cell" style="background: rgba(240, 192, 0, 0.30)"></span>
      <span class="heatmap-legend-cell" style="background: rgba(240, 192, 0, 0.55)"></span>
      <span class="heatmap-legend-cell" style="background: rgba(240, 192, 0, 0.85)"></span>
      <span>More</span>
    </div>
  </div>`;
}

// ── Stats Cards ──

function renderStatsCards(totals) {
  const stats = [
    { label: 'Sessions', value: totals.sessions || 0, icon: 'session' },
    { label: 'Observations', value: totals.observations || 0, icon: 'observation' },
    { label: 'Words Processed', value: totals.words || 0, icon: 'words' },
    { label: 'KG Entities', value: totals.entities || 0, icon: 'entity' },
    { label: 'Avg Session', value: totals.avg_session_words || 0, suffix: ' words', icon: 'avg' },
    { label: 'Day Streak', value: totals.streak || 0, suffix: ' days', icon: 'streak' },
  ];

  const icons = {
    session: '<svg width="18" height="18" viewBox="0 0 16 16" fill="none"><path d="M8 2C4.7 2 2 4.7 2 8s2.7 6 6 6 6-2.7 6-6-2.7-6-6-6zm0 10.5c-2.5 0-4.5-2-4.5-4.5S5.5 3.5 8 3.5s4.5 2 4.5 4.5-2 4.5-4.5 4.5zM8.5 5H7v3.7l3.1 1.8.7-1.2-2.3-1.4V5z" fill="currentColor"/></svg>',
    observation: '<svg width="18" height="18" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="5.5" stroke="currentColor" stroke-width="1.3" fill="none"/><path d="M8 5v3.5l2.5 1.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    words: '<svg width="18" height="18" viewBox="0 0 16 16" fill="none"><path d="M2 4.5h12M2 8h9M2 11.5h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    entity: '<svg width="18" height="18" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="2.5" fill="currentColor"/><circle cx="8" cy="8" r="5.5" stroke="currentColor" stroke-width="1.2" fill="none"/></svg>',
    avg: '<svg width="18" height="18" viewBox="0 0 16 16" fill="none"><path d="M3 12l3-4 3 2 4-6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    streak: '<svg width="18" height="18" viewBox="0 0 16 16" fill="none"><path d="M8 2l1.5 4H14l-3.5 2.5L12 13 8 10l-4 3 1.5-4.5L2 6h4.5L8 2z" fill="currentColor"/></svg>',
  };

  return `<div class="analytics-stats-grid">
    ${stats.map(s => {
      const formatted = s.value >= 10000
        ? (s.value / 1000).toFixed(1) + 'k'
        : s.value.toLocaleString();
      return `<div class="analytics-stat-card">
        <div class="analytics-stat-icon">${icons[s.icon] || ''}</div>
        <div class="analytics-stat-value">${formatted}${s.suffix || ''}</div>
        <div class="analytics-stat-label">${s.label}</div>
      </div>`;
    }).join('')}
  </div>`;
}

// ── Observation Type Breakdown (Donut Chart) ──

function renderTypeBreakdown(byType) {
  const entries = Object.entries(byType || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, v]) => sum + v, 0);
  if (!total) return '';

  // SVG donut
  const cx = 60, cy = 60, r = 48, strokeWidth = 16;
  const circumference = 2 * Math.PI * r;
  let offset = 0;
  let arcs = '';

  entries.forEach(([type, count]) => {
    const pct = count / total;
    const dashLen = pct * circumference;
    const color = typeColors[type] || '#94a3b8';
    arcs += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
      stroke="${color}" stroke-width="${strokeWidth}"
      stroke-dasharray="${dashLen} ${circumference - dashLen}"
      stroke-dashoffset="${-offset}"
      transform="rotate(-90 ${cx} ${cy})" />`;
    offset += dashLen;
  });

  const legend = entries.map(([type, count]) => {
    const pct = Math.round((count / total) * 100);
    const color = typeColors[type] || '#94a3b8';
    return `<div class="analytics-legend-item">
      <span class="analytics-legend-dot" style="background: ${color}"></span>
      <span class="analytics-legend-label">${badgeLabel(type)}</span>
      <span class="analytics-legend-value">${count}</span>
      <span class="analytics-legend-pct">${pct}%</span>
    </div>`;
  }).join('');

  return `<div class="analytics-card">
    <h3 class="analytics-card-title">Observation Types</h3>
    <div class="analytics-donut-layout">
      <svg width="120" height="120" viewBox="0 0 120 120" class="analytics-donut">
        ${arcs}
        <text x="${cx}" y="${cy - 4}" text-anchor="middle" fill="var(--text)" font-size="18" font-weight="600" font-family="var(--font-sans)">${total}</text>
        <text x="${cx}" y="${cy + 12}" text-anchor="middle" fill="var(--text-dim)" font-size="10" font-family="var(--font-sans)">total</text>
      </svg>
      <div class="analytics-legend">${legend}</div>
    </div>
  </div>`;
}

// ── Hourly Activity Bar Chart ──

function renderHourlyChart(hourly) {
  if (!hourly || !hourly.length) return '';

  // Ensure all 24 hours
  const hours = new Array(24).fill(0);
  hourly.forEach(h => { hours[h.hour] = h.count; });
  const max = Math.max(1, ...hours);

  const barWidth = 20;
  const gap = 4;
  const chartWidth = 24 * (barWidth + gap);
  const chartHeight = 100;

  let bars = '';
  hours.forEach((count, hour) => {
    const h = (count / max) * (chartHeight - 20);
    const x = hour * (barWidth + gap);
    const y = chartHeight - h - 16;
    const opacity = count > 0 ? 0.4 + (count / max) * 0.6 : 0.08;
    bars += `<rect x="${x}" y="${y}" width="${barWidth}" height="${h}" rx="3" fill="var(--accent)" opacity="${opacity.toFixed(2)}">
      <title>${hour}:00 - ${count} events</title>
    </rect>`;
    // Hour label every 3 hours
    if (hour % 3 === 0) {
      bars += `<text x="${x + barWidth / 2}" y="${chartHeight}" text-anchor="middle" fill="var(--text-dim)" font-size="9" font-family="var(--font-sans)">${hour}</text>`;
    }
  });

  return `<div class="analytics-card">
    <h3 class="analytics-card-title">Activity by Hour</h3>
    <div class="chart-scroll">
      <svg width="${chartWidth}" height="${chartHeight + 4}" class="analytics-bar-chart">
        ${bars}
      </svg>
    </div>
  </div>`;
}

// ── Day of Week Chart ──

function renderDayOfWeekChart(byDay) {
  if (!byDay || !byDay.length) return '';

  const max = Math.max(1, ...byDay.map(d => d.count));
  const barHeight = 24;
  const gap = 6;
  const chartHeight = byDay.length * (barHeight + gap);
  const chartWidth = 300;
  const labelWidth = 36;

  let bars = '';
  byDay.forEach((d, i) => {
    const w = (d.count / max) * (chartWidth - labelWidth - 50);
    const y = i * (barHeight + gap);
    bars += `<text x="${labelWidth - 6}" y="${y + barHeight / 2 + 4}" text-anchor="end" fill="var(--text-secondary)" font-size="12" font-family="var(--font-sans)">${d.day}</text>`;
    bars += `<rect x="${labelWidth}" y="${y}" width="${Math.max(w, 2)}" height="${barHeight}" rx="4" fill="var(--accent)" opacity="0.5">
      <title>${d.day}: ${d.count} events</title>
    </rect>`;
    bars += `<text x="${labelWidth + w + 8}" y="${y + barHeight / 2 + 4}" fill="var(--text-dim)" font-size="11" font-family="var(--font-sans)">${d.count}</text>`;
  });

  return `<div class="analytics-card">
    <h3 class="analytics-card-title">Activity by Day</h3>
    <svg width="${chartWidth}" height="${chartHeight}" class="analytics-bar-chart">${bars}</svg>
  </div>`;
}

// ── Top Entities ──

function renderTopEntities(topEntities, entityTypes) {
  if ((!topEntities || !topEntities.length) && !entityTypes) return '';

  const entityColorMap = {
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

  let entitiesHtml = '';
  if (topEntities && topEntities.length) {
    entitiesHtml = topEntities.map((e, i) => {
      const color = entityColorMap[e.type] || '#94a3b8';
      return `<div class="analytics-entity-row">
        <span class="analytics-entity-rank">${i + 1}</span>
        <span class="analytics-entity-dot" style="background: ${color}"></span>
        <span class="analytics-entity-name">${esc(e.name)}</span>
        <span class="analytics-entity-type">${e.type}</span>
        <span class="analytics-entity-count">${e.count}</span>
      </div>`;
    }).join('');
  }

  // Entity type breakdown
  let typesHtml = '';
  if (entityTypes && Object.keys(entityTypes).length) {
    const sorted = Object.entries(entityTypes).sort((a, b) => b[1] - a[1]);
    const total = sorted.reduce((sum, [, v]) => sum + v, 0);
    typesHtml = `<div class="analytics-entity-types">
      ${sorted.map(([type, count]) => {
        const color = entityColorMap[type] || '#94a3b8';
        const pct = Math.round((count / total) * 100);
        return `<div class="analytics-entity-type-bar">
          <span class="analytics-entity-dot" style="background: ${color}"></span>
          <span class="analytics-entity-type-label">${type}</span>
          <div class="analytics-entity-type-fill" style="width: ${pct}%; background: ${color}"></div>
          <span class="analytics-entity-type-count">${count}</span>
        </div>`;
      }).join('')}
    </div>`;
  }

  return `<div class="analytics-card">
    <h3 class="analytics-card-title">Top Entities</h3>
    ${entitiesHtml}
    ${typesHtml ? `<div style="margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border-subtle);">
      <div style="font-size: 11px; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;">By Type</div>
      ${typesHtml}
    </div>` : ''}
  </div>`;
}

// ── Main Render ──

export async function renderAnalytics(container) {
  container.innerHTML = `<div class="loading"><span class="loading-dots">Loading analytics</span></div>`;

  let activityData = null;
  let entityData = null;
  let activityError = false;
  let entityError = false;

  // Fetch both endpoints in parallel, handle failures gracefully
  try {
    const results = await Promise.allSettled([
      _apiFn('/api/analytics/activity?days=90'),
      _apiFn('/api/analytics/entities?limit=20'),
    ]);
    if (results[0].status === 'fulfilled') activityData = results[0].value;
    else activityError = true;
    if (results[1].status === 'fulfilled') entityData = results[1].value;
    else entityError = true;
  } catch {
    activityError = true;
    entityError = true;
  }

  // If both failed, try falling back to the basic stats endpoint
  if (activityError && entityError) {
    try {
      const stats = await _apiFn('/api/stats');
      // Build minimal data from stats
      activityData = {
        daily: [],
        hourly: [],
        by_day_of_week: [],
        by_type: {},
        totals: {
          sessions: stats.sessions || 0,
          observations: stats.observations || 0,
          words: stats.total_words_processed || 0,
          entities: stats.kg_entities || 0,
          avg_session_words: 0,
          streak: 0,
        },
      };
    } catch {
      container.innerHTML = `<div class="empty">
        <div class="empty-icon">!</div>
        Analytics data unavailable.<br>The analytics API endpoints may not be ready yet.
      </div>`;
      return;
    }
  }

  // Build the dashboard
  let html = '<div class="analytics-dashboard">';

  // Stats cards
  if (activityData && activityData.totals) {
    html += renderStatsCards(activityData.totals);
  }

  // Heatmap
  if (activityData && activityData.daily) {
    html += renderHeatmap(activityData.daily, 90);
  }

  // Two-column layout for charts
  html += '<div class="analytics-grid">';

  // Observation type breakdown
  if (activityData && activityData.by_type) {
    html += renderTypeBreakdown(activityData.by_type);
  }

  // Top entities
  if (entityData) {
    html += renderTopEntities(entityData.top_entities, entityData.entity_types);
  }

  // Hourly chart
  if (activityData && activityData.hourly && activityData.hourly.length) {
    html += renderHourlyChart(activityData.hourly);
  }

  // Day of week chart
  if (activityData && activityData.by_day_of_week && activityData.by_day_of_week.length) {
    html += renderDayOfWeekChart(activityData.by_day_of_week);
  }

  html += '</div>'; // analytics-grid
  html += '</div>'; // analytics-dashboard

  container.innerHTML = html;
}
