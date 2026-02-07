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

export function renderObservation(obs, i = 0) {
  const type = obs.observation_type || obs.type || 'change';
  const delay = Math.min(i * 0.03, 0.5);

  return `<div class="card" style="animation-delay: ${delay}s">
    <div class="card-header">
      <span class="badge badge-${type}">${badgeLabel(type)}</span>
      <span class="card-title">${esc(obs.title)}</span>
      <span class="card-meta">${formatTime(obs.created_at)}</span>
    </div>
    ${obs.summary && obs.summary !== obs.title
      ? `<div class="card-body">${esc(obs.summary)}</div>`
      : ''}
    ${renderFiles(obs.files)}
  </div>`;
}

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

export function renderSession(s, i = 0) {
  const tid = s.transcript_id || '';
  const delay = Math.min(i * 0.03, 0.5);
  const words = s.word_count || 0;

  return `<div class="card session-card" onclick="window._loadSessionDetail('${esc(tid)}')" style="animation-delay: ${delay}s">
    <div class="card-header">
      <span class="card-title">${esc(s.title || 'Untitled session')}</span>
      <span class="card-meta">${s.date || ''}</span>
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
