import { api, formatDate } from './utils.js';
import { render as renderNotes } from './notes.js';
import { render as renderAnchors } from './anchors.js';
import { render as renderSeeds } from './seeds.js';
import { render as renderSalience } from './salience.js';

// Tab renderers keyed by hash name
const tabs = {
  notes:    renderNotes,
  anchors:  renderAnchors,
  seeds:    renderSeeds,
  salience: renderSalience,
};

let currentTab = 'notes';
const scrollPositions = {};

function navigate(tab) {
  // Save scroll
  scrollPositions[currentTab] = window.scrollY;

  if (location.hash !== '#' + tab) {
    history.pushState(null, '', '#' + tab);
  }

  currentTab = tab;

  // Update active tab style
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('active', t.getAttribute('href') === '#' + tab);
  });

  // Render
  const app = document.getElementById('app');
  app.innerHTML = '<div class="loading">Loading\u2026</div>';

  const renderer = tabs[tab] || tabs.notes;
  app.innerHTML = '';
  renderer(app);

  // Restore scroll
  const pos = scrollPositions[tab] || 0;
  requestAnimationFrame(() => window.scrollTo(0, pos));
}

function loadStats() {
  api('/api/stats').then(data => {
    const bar = document.getElementById('stats-bar');
    if (bar && data) {
      const parts = [];
      if (data.note_count) parts.push(data.note_count + ' notes');
      if (data.anchor_count) parts.push(data.anchor_count + ' anchors');
      if (data.seed_count) parts.push(data.seed_count + ' seeds');
      if (data.date_range && data.date_range.earliest) {
        parts.push(formatDate(data.date_range.earliest) + ' \u2013 ' + formatDate(data.date_range.latest));
      }
      bar.textContent = parts.join(' \u00b7 ');
    }
  }).catch(e => {
    console.error('Failed to load stats:', e);
  });
}

document.addEventListener('DOMContentLoaded', () => {
  loadStats();

  // Tab click handlers
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', e => {
      e.preventDefault();
      const hash = tab.getAttribute('href').slice(1);
      navigate(hash);
    });
  });

  // Hash change
  window.addEventListener('hashchange', () => {
    const hash = location.hash.slice(1) || 'notes';
    navigate(hash);
  });

  // Initial route
  const hash = location.hash.slice(1) || 'notes';
  navigate(hash);
});
