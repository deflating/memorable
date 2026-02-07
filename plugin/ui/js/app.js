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
let kbFocusIndex = -1;

// ── Inject kb-focus style ──

const kbStyle = document.createElement('style');
kbStyle.textContent = `.kb-focus { outline: 2px solid #f0c000; outline-offset: 2px; border-radius: 10px; }`;
document.head.appendChild(kbStyle);

// ── API ──

async function api(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// ── Hash Routing ──

function updateHash(tab, extra) {
  let hash = '#' + tab;
  if (extra) hash += extra;
  if (location.hash !== hash) history.replaceState(null, '', hash);
}

function parseHash() {
  const hash = location.hash.slice(1);
  if (!hash) return { tab: 'timeline' };
  if (hash.startsWith('session/')) return { tab: 'sessions', sessionId: hash.slice(8) };
  if (hash.startsWith('search?')) {
    const params = new URLSearchParams(hash.slice(7));
    return { tab: currentTab, query: params.get('q') || '' };
  }
  if (['timeline', 'sessions', 'kg'].includes(hash)) return { tab: hash };
  return { tab: 'timeline' };
}

function setActiveTab(tab) {
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  currentTab = tab;
}

// ── Searching Indicator ──

function showSearchingIndicator() {
  let el = document.getElementById('searching-indicator');
  if (!el) {
    el = document.createElement('div');
    el.id = 'searching-indicator';
    el.style.cssText = 'position:absolute;right:36px;top:50%;transform:translateY(-50%);color:#8a919e;font-size:0.75rem;pointer-events:none';
    el.textContent = 'Searching\u2026';
    const box = document.querySelector('.search-box');
    if (box) {
      box.style.position = 'relative';
      box.appendChild(el);
    }
  }
  el.style.display = 'block';
}

function hideSearchingIndicator() {
  const el = document.getElementById('searching-indicator');
  if (el) el.style.display = 'none';
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
  kbFocusIndex = -1;
  const content = document.getElementById('content');

  // Update hash
  if (query) {
    updateHash('search', '?q=' + encodeURIComponent(query));
  } else {
    updateHash(tab);
  }

  // Show skeleton loading
  if (tab !== 'kg') {
    content.innerHTML = Array(5).fill(0).map((_, i) =>
      `<div class="skeleton" style="animation-delay: ${i * 0.08}s"></div>`
    ).join('');
  } else {
    content.innerHTML = '<div class="loading"><span class="loading-dots">Loading graph</span></div>';
  }

  hideSearchingIndicator();

  try {
    if (query) {
      const data = await api(`/api/search?q=${encodeURIComponent(query)}&limit=50`);
      content.innerHTML = renderSearch(data, query);
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
  updateHash('session', '/' + tid);
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
  updateHash('sessions');
  loadTab('sessions');
}

// Expose to window for inline onclick handlers
window._loadSessionDetail = loadSessionDetailView;
window._goBack = goBack;

// ── Keyboard Navigation ──

function getCards() {
  return Array.from(document.querySelectorAll('#content .card'));
}

function updateKbFocus(newIndex) {
  const cards = getCards();
  if (!cards.length) return;

  // Remove old focus
  cards.forEach(c => c.classList.remove('kb-focus'));

  // Clamp index
  kbFocusIndex = Math.max(0, Math.min(newIndex, cards.length - 1));
  const card = cards[kbFocusIndex];
  card.classList.add('kb-focus');
  card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

// ── Event Handlers ──

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    setActiveTab(tab.dataset.tab);
    const q = document.getElementById('search').value.trim();
    loadTab(currentTab, q);
  });
});

const searchInput = document.getElementById('search');
searchInput.placeholder = 'Search observations, prompts\u2026  \u2318K';

searchInput.addEventListener('input', (e) => {
  clearTimeout(searchTimeout);
  const val = e.target.value.trim();
  if (val) {
    showSearchingIndicator();
  } else {
    hideSearchingIndicator();
  }
  searchTimeout = setTimeout(() => {
    hideSearchingIndicator();
    loadTab(currentTab, val);
  }, 300);
});

// Keyboard shortcuts
document.addEventListener('keydown', e => {
  // Cmd/Ctrl+K to focus search
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    searchInput.focus();
    searchInput.select();
    return;
  }
  // Esc to clear search
  if (e.key === 'Escape' && document.activeElement === searchInput) {
    searchInput.value = '';
    searchInput.blur();
    hideSearchingIndicator();
    clearTimeout(searchTimeout);
    loadTab(currentTab);
    return;
  }

  // Arrow key navigation between cards
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (document.activeElement === searchInput) return;
    e.preventDefault();
    const cards = getCards();
    if (!cards.length) return;
    if (e.key === 'ArrowDown') {
      updateKbFocus(kbFocusIndex + 1);
    } else {
      updateKbFocus(kbFocusIndex - 1);
    }
    return;
  }

  // Enter to open focused session card
  if (e.key === 'Enter' && document.activeElement !== searchInput) {
    const cards = getCards();
    if (kbFocusIndex >= 0 && kbFocusIndex < cards.length) {
      const card = cards[kbFocusIndex];
      if (card.classList.contains('session-card')) {
        card.click();
      }
    }
    return;
  }

  // Auto-focus search on typing (printable characters, not in search already)
  if (document.activeElement !== searchInput
      && !e.metaKey && !e.ctrlKey && !e.altKey
      && e.key.length === 1) {
    searchInput.focus();
    // The keystroke will naturally be captured by the now-focused input
  }
});

// ── Hash Change Listener ──

window.addEventListener('hashchange', () => {
  const route = parseHash();
  if (route.sessionId) {
    loadSessionDetailView(route.sessionId);
  } else {
    document.querySelector('.tabs').style.display = 'flex';
    setActiveTab(route.tab);
    if (route.query) {
      searchInput.value = route.query;
      loadTab(route.tab, route.query);
    } else {
      loadTab(route.tab);
    }
  }
});

// ── Init ──

loadStats();

// Route from hash on initial load
const initRoute = parseHash();
if (initRoute.sessionId) {
  setActiveTab('sessions');
  loadSessionDetailView(initRoute.sessionId);
} else {
  setActiveTab(initRoute.tab);
  if (initRoute.query) {
    searchInput.value = initRoute.query;
    loadTab(initRoute.tab, initRoute.query);
  } else {
    loadTab(initRoute.tab);
  }
}
