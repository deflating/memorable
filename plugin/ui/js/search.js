/**
 * Search results rendering.
 * Supports both legacy keyword search and semantic search with grouped results.
 * Falls back to keyword search if semantic endpoint is unavailable.
 */

import { esc, formatTime, highlight } from './utils.js';
import { renderFiles, badgeLabel } from './components.js';

function renderSearchObservation(obs, query, i = 0) {
  const type = obs.observation_type || obs.type || 'change';
  const delay = Math.min(i * 0.03, 0.5);
  const score = obs.score != null ? `<span class="search-score">${Math.round(obs.score * 100)}%</span>` : '';

  return `<div class="card" style="animation-delay: ${delay}s">
    <div class="card-header">
      <span class="badge badge-${type}">${badgeLabel(type)}</span>
      <span class="card-title">${highlight(obs.title, query)}</span>
      ${score}
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
  const text = p.prompt_text || p.text || p.summary || '';
  const score = p.score != null ? `<span class="search-score">${Math.round(p.score * 100)}%</span>` : '';

  return `<div class="card prompt-card" style="animation-delay: ${delay}s">
    <div class="card-header">
      <span class="badge badge-prompt">prompt</span>
      ${score}
      <span class="card-meta" style="margin-left: auto">${formatTime(p.created_at)}</span>
    </div>
    <div class="prompt-text">${highlight(text, query)}</div>
  </div>`;
}

function renderSearchSession(s, query, i = 0) {
  const delay = Math.min(i * 0.03, 0.5);
  const tid = s.transcript_id || '';
  const score = s.score != null ? `<span class="search-score">${Math.round(s.score * 100)}%</span>` : '';

  return `<div class="card session-card" onclick="window._loadSessionDetail('${esc(tid)}')" style="animation-delay: ${delay}s">
    <div class="card-header">
      <span class="badge" style="background: rgba(96,165,250,0.12); color: var(--blue); font-size: 10px; padding: 2px 8px; border-radius: 100px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px;">session</span>
      <span class="card-title">${highlight(s.title || 'Untitled session', query)}</span>
      ${score}
      <span class="card-meta">${s.date || ''}</span>
    </div>
    ${s.header ? `<div class="card-body">${esc(s.header)}</div>` : ''}
  </div>`;
}

/** Render grouped semantic results */
function renderSemanticSearch(data, query) {
  const observations = data.observations || [];
  const prompts = data.prompts || [];
  const sessions = data.sessions || [];
  const totalCount = observations.length + prompts.length + sessions.length;

  if (totalCount === 0) {
    return `<div class="empty">
      <div class="empty-icon">?</div>
      No results found${query ? ' for \u201c' + esc(query) + '\u201d' : ''}
    </div>`;
  }

  let html = `<div class="search-header">
    <span class="search-count">${totalCount} result${totalCount !== 1 ? 's' : ''} for \u201c${esc(query)}\u201d</span>
    <div class="search-type-pills">
      ${sessions.length ? `<span class="search-type-pill" style="--pill-color: var(--blue)">${sessions.length} session${sessions.length !== 1 ? 's' : ''}</span>` : ''}
      ${observations.length ? `<span class="search-type-pill" style="--pill-color: var(--green)">${observations.length} observation${observations.length !== 1 ? 's' : ''}</span>` : ''}
      ${prompts.length ? `<span class="search-type-pill" style="--pill-color: var(--accent)">${prompts.length} prompt${prompts.length !== 1 ? 's' : ''}</span>` : ''}
    </div>
  </div>`;

  if (sessions.length) {
    html += `<div class="search-group">
      <div class="search-group-title">Sessions</div>
      ${sessions.map((s, i) => renderSearchSession(s, query, i)).join('')}
    </div>`;
  }

  if (observations.length) {
    html += `<div class="search-group">
      <div class="search-group-title">Observations</div>
      ${observations.map((o, i) => renderSearchObservation(o, query, i)).join('')}
    </div>`;
  }

  if (prompts.length) {
    html += `<div class="search-group">
      <div class="search-group-title">Prompts</div>
      ${prompts.map((p, i) => renderSearchPrompt(p, query, i)).join('')}
    </div>`;
  }

  return html;
}

/** Render legacy flat results */
function renderLegacySearch(items, query) {
  if (!items.length) {
    return `<div class="empty">
      <div class="empty-icon">?</div>
      No results found${query ? ' for \u201c' + esc(query) + '\u201d' : ''}
    </div>`;
  }

  const countHeader = `<div class="search-header">
    <span class="search-count">${items.length} result${items.length !== 1 ? 's' : ''} for \u201c${esc(query)}\u201d</span>
  </div>`;

  const cards = items.map((item, i) => {
    if (item.kind === 'prompt') {
      return renderSearchPrompt({ prompt_text: item.summary, created_at: item.created_at }, query, i);
    }
    return renderSearchObservation(item, query, i);
  }).join('');

  return countHeader + cards;
}

/** Detect whether data is semantic (grouped) or legacy (flat array) */
export function renderSearch(data, query) {
  // If data is an array, it's legacy flat results
  if (Array.isArray(data)) {
    return renderLegacySearch(data, query);
  }
  // If data has grouped keys, it's semantic search results
  if (data.observations || data.prompts || data.sessions) {
    return renderSemanticSearch(data, query);
  }
  // Fallback to legacy
  return renderLegacySearch(data.items || [], query);
}
