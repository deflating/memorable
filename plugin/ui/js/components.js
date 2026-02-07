/**
 * Reusable UI components: observation cards, prompt cards, session cards.
 * Each function returns an HTML string.
 */

import { esc, formatTime } from './utils.js';

export function renderFiles(files) {
  try {
    const f = typeof files === 'string' ? JSON.parse(files) : (files || []);
    if (!f.length) return '';
    return `<div class="card-files">${f.map(p => {
      const short = p.split('/').slice(-2).join('/');
      return `<span class="file-pill" title="${esc(p)}">${esc(short)}</span>`;
    }).join('')}</div>`;
  } catch { return ''; }
}

export function badgeLabel(type) {
  const labels = {
    discovery: 'discovery',
    change: 'change',
    bugfix: 'bug fix',
    feature: 'feature',
    refactor: 'refactor',
    decision: 'decision',
    session_summary: 'summary',
    prompt: 'prompt',
  };
  return labels[type] || type;
}

/** Unique ID counter for expandable cards */
let _cardId = 0;

export function renderObservation(obs, i = 0) {
  const type = obs.observation_type || obs.type || 'change';
  const delay = Math.min(i * 0.03, 0.5);
  const hasSummary = obs.summary && obs.summary !== obs.title;
  const id = `feat-card-${_cardId++}`;

  return `<div class="card feat-expandable" id="${id}" style="animation-delay: ${delay}s; cursor: ${hasSummary ? 'pointer' : 'default'}" ${hasSummary ? `onclick="window._toggleCard('${id}')"` : ''}>
    <div class="card-header">
      <span class="badge badge-${type}">${badgeLabel(type)}</span>
      <span class="card-title">${esc(obs.title)}</span>
      <span class="card-meta">${formatTime(obs.created_at)}</span>
      ${hasSummary ? `<span class="feat-expand-icon" style="color: #64748b; font-size: 10px; flex-shrink: 0; transition: transform 0.2s ease; margin-top: 3px;">&#9660;</span>` : ''}
    </div>
    ${hasSummary
      ? `<div class="card-body feat-card-body" style="transition: max-height 0.3s ease, opacity 0.2s ease; max-height: 4.65em; overflow: hidden; -webkit-line-clamp: 3; display: -webkit-box; -webkit-box-orient: vertical;">${esc(obs.summary)}</div>`
      : ''}
    ${renderFiles(obs.files)}
  </div>`;
}

/** Toggle card expansion. Exposed on window in the init below. */
function toggleCard(id) {
  const card = document.getElementById(id);
  if (!card) return;
  const body = card.querySelector('.feat-card-body');
  const icon = card.querySelector('.feat-expand-icon');
  if (!body) return;

  const expanded = card.dataset.expanded === '1';
  if (expanded) {
    // Collapse
    body.style.maxHeight = '4.65em';
    body.style.display = '-webkit-box';
    body.style.webkitLineClamp = '3';
    body.style.webkitBoxOrient = 'vertical';
    if (icon) icon.style.transform = 'rotate(0deg)';
    card.dataset.expanded = '0';
  } else {
    // Expand
    body.style.maxHeight = 'none';
    body.style.display = 'block';
    body.style.webkitLineClamp = 'unset';
    body.style.webkitBoxOrient = 'unset';
    if (icon) icon.style.transform = 'rotate(180deg)';
    card.dataset.expanded = '1';
  }
}
window._toggleCard = toggleCard;

export function renderPrompt(p, i = 0) {
  const delay = Math.min(i * 0.03, 0.5);

  return `<div class="card prompt-card" style="animation-delay: ${delay}s">
    <div class="card-header">
      <span class="badge badge-prompt">prompt</span>
      <span class="card-meta" style="margin-left: auto">${formatTime(p.created_at)}</span>
    </div>
    <div class="prompt-text">${esc(p.prompt_text || p.summary || '')}</div>
  </div>`;
}

export function renderTimelineItem(item, i) {
  if (item.kind === 'prompt') return renderPrompt(item, i);
  return renderObservation(item, i);
}

/** Format a relative date string from an ISO date or "YYYY-MM-DD" */
function relativeDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.floor((today - target) / 86400000);

  if (diffDays === 0) return 'today';
  if (diffDays === 1) return 'yesterday';
  if (diffDays < 7) return `${diffDays} days ago`;
  if (diffDays < 14) return 'last week';
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
  if (diffDays < 60) return 'last month';
  if (diffDays < 365) return `${Math.floor(diffDays / 30)} months ago`;
  return `${Math.floor(diffDays / 365)}y ago`;
}

/** Count observation types for a session's observations */
export function countObservationTypes(observations) {
  const counts = {};
  for (const o of observations) {
    const t = o.observation_type || o.type || 'change';
    counts[t] = (counts[t] || 0) + 1;
  }
  return counts;
}

/** Badge color map */
export const typeColors = {
  discovery: '#60a5fa',
  change: '#4ade80',
  bugfix: '#f87171',
  feature: '#c084fc',
  refactor: '#fb923c',
  decision: '#f0c000',
  session_summary: '#64748b',
  prompt: '#f0c000',
};

export function renderSession(s, i = 0) {
  const tid = s.transcript_id || '';
  const delay = Math.min(i * 0.03, 0.5);
  const words = s.word_count || 0;
  const rel = relativeDate(s.date);

  return `<div class="card session-card" onclick="window._loadSessionDetail('${esc(tid)}')" style="animation-delay: ${delay}s">
    <div class="card-header">
      <span class="card-title">${esc(s.title || 'Untitled session')}</span>
      <span class="card-meta">${s.date || ''}${rel ? ` <span style="color: #4a5568; font-size: 11px; margin-left: 4px;">${rel}</span>` : ''}</span>
    </div>
    ${s.summary || s.header
      ? `<div class="card-body">${esc(s.summary || s.header)}</div>`
      : ''}
    <div class="session-stats">
      <span>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M8 2C4.7 2 2 4.7 2 8s2.7 6 6 6 6-2.7 6-6-2.7-6-6-6zm0 10.5c-2.5 0-4.5-2-4.5-4.5S5.5 3.5 8 3.5s4.5 2 4.5 4.5-2 4.5-4.5 4.5zM8.5 5H7v3.7l3.1 1.8.7-1.2-2.3-1.4V5z" fill="currentColor"/></svg>
        ${s.message_count || 0} messages
      </span>
      <span>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M2 4.5h12M2 8h9M2 11.5h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
        ${words >= 1000 ? (words / 1000).toFixed(1) + 'k' : words} words
      </span>
    </div>
  </div>`;
}
