/**
 * Session detail view: full session with its observations and prompts.
 * Renders the detail page when a session card is clicked.
 */

import { esc } from './utils.js';
import { renderTimelineItem } from './components.js';

export function renderSessionDetail(session, observations, prompts) {
  const s = session;

  let html = `<button class="back-btn" onclick="window._goBack()">
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M10 3L5 8l5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
    Sessions
  </button>`;

  html += `<div class="detail-header">
    <h2>${esc(s.title || 'Untitled session')}</h2>
    <div class="detail-meta">
      <span>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M5 1v2M11 1v2M2 6h12M2 4c0-1.1.9-2 2-2h8c1.1 0 2 .9 2 2v8c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V4z" stroke="currentColor" stroke-width="1.3" fill="none"/></svg>
        ${s.date || ''}
      </span>
      <span>${s.message_count || 0} messages</span>
      <span>${(s.word_count || 0).toLocaleString()} words</span>
    </div>
    ${s.summary || s.header
      ? `<div class="detail-summary">${esc(s.summary || s.header)}</div>`
      : ''}
  </div>`;

  const timeline = [
    ...observations.map(o => ({ ...o, kind: 'observation' })),
    ...prompts.map(p => ({ ...p, kind: 'prompt' })),
  ].sort((a, b) => (a.created_at || 0) - (b.created_at || 0));

  if (timeline.length) {
    html += `<div class="detail-section"><h3>Activity (${timeline.length})</h3>`;
    html += timeline.map(renderTimelineItem).join('');
    html += `</div>`;
  } else {
    html += `<div class="empty">
      <div class="empty-icon">~</div>
      No observations or prompts recorded for this session.
    </div>`;
  }

  return html;
}
