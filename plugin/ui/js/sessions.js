/**
 * Session detail view: full session with its observations and prompts.
 * Renders the detail page when a session card is clicked.
 * Includes mini stats (observation type counts) and jump-to-top button.
 */

import { esc } from './utils.js';
import { renderTimelineItem, countObservationTypes, badgeLabel, typeColors } from './components.js';

/** Render mini stat pills showing observation type counts */
function renderMiniStats(observations, prompts) {
  const counts = countObservationTypes(observations);
  if (prompts.length) counts.prompt = prompts.length;

  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return '';

  const containerStyle = `
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 14px;
  `.replace(/\n\s*/g, ' ').trim();

  const pillStyle = (color) => `
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 10px;
    border-radius: 100px;
    font-size: 11px;
    font-weight: 600;
    background: ${color}14;
    color: ${color};
    border: 1px solid ${color}22;
    letter-spacing: 0.2px;
  `.replace(/\n\s*/g, ' ').trim();

  return `<div style="${containerStyle}">
    ${entries.map(([type, count]) => {
      const color = typeColors[type] || '#94a3b8';
      return `<span style="${pillStyle(color)}">
        <span style="width: 6px; height: 6px; border-radius: 50%; background: ${color};"></span>
        ${count} ${badgeLabel(type)}${count !== 1 ? 's' : ''}
      </span>`;
    }).join('')}
  </div>`;
}

/** Inject (or remove) the jump-to-top floating button */
function setupJumpToTop() {
  // Remove any existing button
  const existing = document.getElementById('feat-jump-top');
  if (existing) existing.remove();

  const btn = document.createElement('button');
  btn.id = 'feat-jump-top';
  btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 12V4M4 7l4-4 4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  btn.setAttribute('style', `
    position: fixed;
    bottom: 28px;
    right: 28px;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: #151d27;
    border: 1px solid #1e2a38;
    color: #94a3b8;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    pointer-events: none;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    z-index: 50;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  `.replace(/\n\s*/g, ' ').trim());

  btn.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
  btn.addEventListener('mouseenter', () => {
    btn.style.borderColor = '#f0c00066';
    btn.style.color = '#f0c000';
    btn.style.boxShadow = '0 2px 12px rgba(240,192,0,0.15)';
  });
  btn.addEventListener('mouseleave', () => {
    btn.style.borderColor = '#1e2a38';
    btn.style.color = '#94a3b8';
    btn.style.boxShadow = '0 2px 8px rgba(0,0,0,0.3)';
  });

  document.body.appendChild(btn);

  const onScroll = () => {
    if (window.scrollY > 300) {
      btn.style.opacity = '1';
      btn.style.pointerEvents = 'auto';
    } else {
      btn.style.opacity = '0';
      btn.style.pointerEvents = 'none';
    }
  };

  window.addEventListener('scroll', onScroll, { passive: true });

  // Store cleanup reference
  btn._cleanup = () => {
    window.removeEventListener('scroll', onScroll);
    btn.remove();
  };
}

/** Clean up jump-to-top button when leaving session detail */
function cleanupJumpToTop() {
  const btn = document.getElementById('feat-jump-top');
  if (btn && btn._cleanup) btn._cleanup();
  else if (btn) btn.remove();
}

// Hook into the goBack flow to clean up.
// Wrap lazily since app.js sets window._goBack after this module loads.
let _goBackWrapped = false;
function wrapGoBack() {
  if (_goBackWrapped) return;
  _goBackWrapped = true;
  const originalGoBack = window._goBack;
  window._goBack = function() {
    cleanupJumpToTop();
    if (originalGoBack) originalGoBack();
  };
}

/** Render the related sessions panel (loaded async after main render) */
function renderRelatedSessions(related) {
  if (!related || !related.length) return '';

  return `<div class="detail-section related-sessions-section">
    <h3>Related Sessions</h3>
    ${related.map((r, i) => {
      const tid = r.transcript_id || '';
      const similarity = r.similarity != null ? Math.round(r.similarity * 100) : null;
      const shared = (r.shared_entities || []).slice(0, 4);
      return `<div class="card session-card related-session-card" onclick="window._loadSessionDetail('${esc(tid)}')" style="animation-delay: ${i * 0.05}s">
        <div class="card-header">
          <span class="card-title">${esc(r.title || 'Untitled')}</span>
          ${similarity != null ? `<span class="search-score">${similarity}%</span>` : ''}
          <span class="card-meta">${r.date || ''}</span>
        </div>
        ${r.header ? `<div class="card-body" style="-webkit-line-clamp: 2;">${esc(r.header)}</div>` : ''}
        ${shared.length ? `<div class="related-shared-entities">
          ${shared.map(e => `<span class="related-entity-pill">${esc(e)}</span>`).join('')}
        </div>` : ''}
      </div>`;
    }).join('')}
  </div>`;
}

/** Load related sessions asynchronously */
async function loadRelatedSessions(transcriptId) {
  const container = document.getElementById('related-sessions-container');
  if (!container) return;

  try {
    const r = await fetch(`/api/session/related?id=${encodeURIComponent(transcriptId)}&limit=5`);
    if (!r.ok) throw new Error('not available');
    const data = await r.json();
    const related = Array.isArray(data) ? data : (data.items || []);
    if (related.length) {
      container.innerHTML = renderRelatedSessions(related);
    }
  } catch {
    // Silently fail - related sessions are optional
  }
}

export function renderSessionDetail(session, observations, prompts) {
  const s = session;

  // Wrap goBack to clean up jump-to-top, then setup on next tick
  wrapGoBack();
  setTimeout(setupJumpToTop, 0);

  // Load related sessions async after render
  const tid = s.transcript_id || '';
  if (tid) {
    setTimeout(() => loadRelatedSessions(tid), 100);
  }

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
    ${renderMiniStats(observations, prompts)}
  </div>`;

  // Session Notes section (if available)
  if (s.note_content || s.compressed_50) {
    html += `<div class="detail-section session-notes-section">
      <h3>Session Notes</h3>
      <div id="session-notes-content" class="session-notes-content">`;

    if (s.note_content) {
      // note_content is markdown — render as preformatted with basic formatting
      const noteHtml = s.note_content
        .split('\n')
        .map(line => {
          // Basic markdown-to-HTML: headers, lists, bold
          if (line.startsWith('## ')) return `<h4>${esc(line.slice(3))}</h4>`;
          if (line.startsWith('### ')) return `<h5>${esc(line.slice(4))}</h5>`;
          if (line.startsWith('- ')) return `<li>${esc(line.slice(2))}</li>`;
          if (line.startsWith('**') && line.endsWith('**')) {
            return `<p><strong>${esc(line.slice(2, -2))}</strong></p>`;
          }
          if (line.trim() === '') return '<br>';
          return `<p>${esc(line)}</p>`;
        })
        .join('');
      html += noteHtml;
    } else if (s.compressed_50) {
      html += `<p>${esc(s.compressed_50)}</p>`;
    }

    html += `</div></div>`;
  }

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
      <div style="font-size: 36px; margin-bottom: 14px; opacity: 0.25;">&#128065;</div>
      <div style="color: #94a3b8; font-size: 15px; font-weight: 500; margin-bottom: 8px;">No activity recorded</div>
      <div style="color: #64748b; font-size: 13px; line-height: 1.6;">This session has no observations or prompts.<br>Activity is captured automatically during Claude Code use.</div>
    </div>`;
  }

  // Placeholder for related sessions (loaded async)
  html += `<div id="related-sessions-container"></div>`;

  return html;
}
