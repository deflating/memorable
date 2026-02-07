/**
 * Search results rendering.
 * Displays matching observations and prompts from the search API.
 */

import { esc, formatTime, highlight } from './utils.js';
import { renderFiles, badgeLabel } from './components.js';

function renderSearchObservation(obs, query, i = 0) {
  const type = obs.observation_type || obs.type || 'change';
  const delay = Math.min(i * 0.03, 0.5);

  return `<div class="card" style="animation-delay: ${delay}s">
    <div class="card-header">
      <span class="badge badge-${type}">${badgeLabel(type)}</span>
      <span class="card-title">${highlight(obs.title, query)}</span>
      <span class="card-meta">${formatTime(obs.created_at)}</span>
    </div>
    ${obs.summary && obs.summary !== obs.title
      ? `<div class="card-body">${highlight(obs.summary, query)}</div>`
      : ''}
    ${renderFiles(obs.files)}
  </div>`;
}

function renderSearchPrompt(p, query, i = 0) {
  const delay = Math.min(i * 0.03, 0.5);
  const text = p.prompt_text || p.summary || '';

  return `<div class="card prompt-card" style="animation-delay: ${delay}s">
    <div class="card-header">
      <span class="badge badge-prompt">prompt</span>
      <span class="card-meta" style="margin-left: auto">${formatTime(p.created_at)}</span>
    </div>
    <div class="prompt-text">${highlight(text, query)}</div>
  </div>`;
}

export function renderSearch(items, query) {
  if (!items.length) {
    return `<div class="empty">
      <div class="empty-icon">?</div>
      No results found${query ? ' for \u201c' + esc(query) + '\u201d' : ''}
    </div>`;
  }

  const countHeader = `<div style="padding:8px 4px 12px;color:#8a919e;font-size:0.85rem">${items.length} result${items.length !== 1 ? 's' : ''} for \u201c${esc(query)}\u201d</div>`;

  const cards = items.map((item, i) => {
    if (item.kind === 'prompt') {
      return renderSearchPrompt({ prompt_text: item.summary, created_at: item.created_at }, query, i);
    }
    return renderSearchObservation(item, query, i);
  }).join('');

  return countHeader + cards;
}
