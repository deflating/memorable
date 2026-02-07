/**
 * Timeline tab: chronological list of observations and prompts,
 * grouped by date (Today, Yesterday, This week, etc.).
 * Includes filter chips for observation type filtering.
 */

import { formatDate } from './utils.js';
import { renderTimelineItem, badgeLabel, typeColors } from './components.js';

const FILTER_TYPES = ['all', 'discovery', 'change', 'bugfix', 'feature', 'refactor', 'decision', 'prompt'];

/** Inline styles for filter chips */
const chipBaseStyle = `
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 14px;
  border-radius: 100px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid #1e2a38;
  background: #151d27;
  color: #94a3b8;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  user-select: none;
  text-transform: capitalize;
  letter-spacing: 0.2px;
`.replace(/\n\s*/g, ' ').trim();

const chipActiveStyle = `
  border-color: #f0c00066;
  background: #f0c00018;
  color: #f0c000;
  box-shadow: 0 0 8px #f0c00022;
`.replace(/\n\s*/g, ' ').trim();

function chipStyle(type, active) {
  if (active && type !== 'all') {
    const color = typeColors[type] || '#94a3b8';
    return `${chipBaseStyle} border-color: ${color}66; background: ${color}18; color: ${color}; box-shadow: 0 0 8px ${color}22;`;
  }
  if (active) {
    return `${chipBaseStyle} ${chipActiveStyle}`;
  }
  return chipBaseStyle;
}

function renderFilterChips(activeFilter, counts) {
  const containerStyle = `
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 18px;
    padding-bottom: 14px;
    border-bottom: 1px solid #172030;
    animation: fadeIn 0.3s ease-out;
  `.replace(/\n\s*/g, ' ').trim();

  return `<div class="feat-filter-chips" style="${containerStyle}">
    ${FILTER_TYPES.map(type => {
      const active = activeFilter === type;
      const count = type === 'all' ? '' : (counts[type] || 0);
      const label = type === 'all' ? 'All' : badgeLabel(type);
      const dot = type !== 'all' ? `<span style="width: 7px; height: 7px; border-radius: 50%; background: ${typeColors[type] || '#94a3b8'}; flex-shrink: 0;"></span>` : '';
      const countBadge = count ? ` <span style="font-size: 10px; opacity: 0.7; margin-left: 1px;">${count}</span>` : '';
      return `<button class="feat-chip" data-filter="${type}" style="${chipStyle(type, active)}" onclick="window._filterTimeline('${type}')">${dot}${label}${countBadge}</button>`;
    }).join('')}
  </div>`;
}

function renderItems(items) {
  if (!items.length) {
    return `<div class="empty">
      <div style="font-size: 32px; margin-bottom: 12px; opacity: 0.3;">&#9776;</div>
      <div style="color: #94a3b8; font-size: 14px; margin-bottom: 6px;">No matching activity</div>
      <div style="color: #64748b; font-size: 13px;">Try selecting a different filter above.</div>
    </div>`;
  }

  let html = '';
  let lastDate = '';
  items.forEach((item, i) => {
    const date = formatDate(item.created_at);
    if (date !== lastDate) {
      html += `<div class="date-separator">${date}</div>`;
      lastDate = date;
    }
    html += renderTimelineItem(item, i);
  });
  return html;
}

/** Cached items and filter state */
let _allItems = [];
let _activeFilter = 'all';

function countTypes(items) {
  const counts = {};
  for (const item of items) {
    const t = item.kind === 'prompt' ? 'prompt' : (item.observation_type || item.type || 'change');
    counts[t] = (counts[t] || 0) + 1;
  }
  return counts;
}

function filterItems(items, filter) {
  if (filter === 'all') return items;
  return items.filter(item => {
    if (filter === 'prompt') return item.kind === 'prompt';
    const t = item.observation_type || item.type || 'change';
    return t === filter;
  });
}

function applyFilter(filter) {
  _activeFilter = filter;
  const counts = countTypes(_allItems);
  const filtered = filterItems(_allItems, filter);

  const container = document.getElementById('content');
  if (!container) return;

  // Re-render chips + filtered items
  const chipsEl = container.querySelector('.feat-filter-chips');
  const itemsWrapper = container.querySelector('.feat-timeline-items');

  if (chipsEl) {
    // Update chip active states
    chipsEl.querySelectorAll('.feat-chip').forEach(btn => {
      const type = btn.dataset.filter;
      btn.setAttribute('style', chipStyle(type, type === filter));
    });
  }

  if (itemsWrapper) {
    itemsWrapper.innerHTML = renderItems(filtered);
  }
}

window._filterTimeline = applyFilter;

export function renderTimeline(items) {
  _allItems = items;
  _activeFilter = 'all';

  if (!items.length) {
    return `<div class="empty">
      <div style="font-size: 40px; margin-bottom: 14px; opacity: 0.25;">&#9998;</div>
      <div style="color: #94a3b8; font-size: 15px; font-weight: 500; margin-bottom: 8px;">No activity yet</div>
      <div style="color: #64748b; font-size: 13px; line-height: 1.6;">Observations and prompts will appear here<br>as you use Claude Code.</div>
    </div>`;
  }

  const counts = countTypes(items);
  let html = renderFilterChips('all', counts);
  html += `<div class="feat-timeline-items">`;
  html += renderItems(items);
  html += `</div>`;

  return html;
}
