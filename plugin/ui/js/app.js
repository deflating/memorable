/**
 * Main application entry point.
 * Manages state, routing, data loading, and event binding.
 */

import { esc } from './utils.js';
import { renderSession } from './components.js';
import { renderTimeline } from './timeline.js';
import { renderSessionDetail } from './sessions.js';
import { renderSearch } from './search.js';
import { loadKG, stopKG } from './kg.js';

// ── State ──

const API = '';
let currentTab = 'timeline';
let searchTimeout = null;

// ── API ──

async function api(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// ── Data Loading ──

async function loadStats() {
  try {
    const s = await api('/api/stats');
    document.getElementById('stats').innerHTML = `
      <div class="stat"><span class="num">${s.sessions}</span> sessions</div>
      <div class="stat"><span class="num">${(s.total_words_processed || 0).toLocaleString()}</span> words</div>
      <div class="stat"><span class="num">${s.observations}</span> observations</div>
      <div class="stat"><span class="num">${s.user_prompts || 0}</span> prompts</div>
    `;
  } catch {}
}

async function loadTab(tab, query) {
  stopKG();
  const content = document.getElementById('content');

  // Show skeleton loading
  if (tab !== 'kg') {
    content.innerHTML = Array(5).fill(0).map((_, i) =>
      `<div class="skeleton" style="animation-delay: ${i * 0.08}s"></div>`
    ).join('');
  } else {
    content.innerHTML = '<div class="loading"><span class="loading-dots">Loading graph</span></div>';
  }

  try {
    if (query) {
      const data = await api(`/api/search?q=${encodeURIComponent(query)}&limit=50`);
      content.innerHTML = renderSearch(data);
      return;
    }

    if (tab === 'timeline') {
      const data = await api('/api/timeline?limit=200');
      content.innerHTML = renderTimeline(data);
    } else if (tab === 'sessions') {
      const data = await api('/api/sessions?limit=50');
      if (!data.length) {
        content.innerHTML = `<div class="empty">
          <div class="empty-icon">~</div>
          No sessions stored yet.
        </div>`;
        return;
      }
      content.innerHTML = data.map(renderSession).join('');
    } else if (tab === 'kg') {
      await loadKG(content, api);
    }
  } catch (err) {
    content.innerHTML = `<div class="empty">
      <div class="empty-icon">!</div>
      Error loading data: ${esc(err.message)}
    </div>`;
  }
}

// ── Session Detail ──

async function loadSessionDetailView(tid) {
  if (!tid) return;
  const content = document.getElementById('content');
  content.innerHTML = '<div class="loading"><span class="loading-dots">Loading session</span></div>';
  document.querySelector('.tabs').style.display = 'none';

  try {
    const data = await api(`/api/session?id=${encodeURIComponent(tid)}`);
    content.innerHTML = renderSessionDetail(data.session, data.observations || [], data.prompts || []);
  } catch (err) {
    content.innerHTML = `<button class="back-btn" onclick="window._goBack()">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M10 3L5 8l5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
      Back
    </button>
    <div class="empty">
      <div class="empty-icon">!</div>
      Error loading session: ${esc(err.message)}
    </div>`;
  }
}

function goBack() {
  document.querySelector('.tabs').style.display = 'flex';
  loadTab('sessions');
}

// Expose to window for inline onclick handlers
window._loadSessionDetail = loadSessionDetailView;
window._goBack = goBack;

// ── Event Handlers ──

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelector('.tab.active').classList.remove('active');
    tab.classList.add('active');
    currentTab = tab.dataset.tab;
    const q = document.getElementById('search').value.trim();
    loadTab(currentTab, q);
  });
});

const searchInput = document.getElementById('search');
searchInput.addEventListener('input', (e) => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => {
    loadTab(currentTab, e.target.value.trim());
  }, 300);
});

// Keyboard shortcuts
document.addEventListener('keydown', e => {
  // Cmd/Ctrl+K to focus search
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    searchInput.focus();
    searchInput.select();
  }
  // Esc to clear search
  if (e.key === 'Escape' && document.activeElement === searchInput) {
    searchInput.value = '';
    searchInput.blur();
    loadTab(currentTab);
  }
});

// ── Init ──

loadStats();
loadTab('timeline');
