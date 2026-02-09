// ---------------------------------------------------------------------------
// Utility functions for the Memorable web viewer
// ---------------------------------------------------------------------------

// DOM helper — el(tag) or el(tag, 'className') or el(tag, { className, textContent, ... })
export function el(tag, propsOrClass) {
  const node = document.createElement(tag);
  if (!propsOrClass) return node;
  if (typeof propsOrClass === 'string') {
    node.className = propsOrClass;
    return node;
  }
  for (const [key, val] of Object.entries(propsOrClass)) {
    if (key === 'className') node.className = val;
    else if (key === 'textContent') node.textContent = val;
    else if (key === 'innerHTML') node.innerHTML = val;
    else if (key === 'style' && typeof val === 'string') node.style.cssText = val;
    else if (key === 'value') node.value = val;
    else node.setAttribute(key, val);
  }
  return node;
}

// API fetch wrapper
export async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error('API error: ' + res.status);
  return res.json();
}

export async function apiPut(path, body) {
  const res = await fetch(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error('API error: ' + res.status);
  return res.json();
}

// Date formatting
export function formatDate(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return months[d.getMonth()] + ' ' + d.getDate();
}

export function formatTime(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  let h = d.getHours();
  const m = String(d.getMinutes()).padStart(2, '0');
  const ampm = h >= 12 ? 'p' : 'a';
  h = h % 12 || 12;
  return h + ':' + m + ampm;
}

export function formatDateTime(isoStr) {
  return formatDate(isoStr) + ' ' + formatTime(isoStr);
}

// Markdown rendering (via marked.js CDN)
export function renderMarkdown(text) {
  if (!text) return '';
  if (typeof marked !== 'undefined') return marked.parse(text);
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\n/g, '<br>');
}

// Strip markdown for summaries
export function stripMarkdown(text) {
  if (!text) return '';
  return text
    .replace(/#{1,6}\s+/g, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/`(.+?)`/g, '$1')
    .replace(/\[(.+?)\]\(.+?\)/g, '$1')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/\n/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// Debounce
export function debounce(fn, ms) {
  let timer;
  return function(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}

// Toast notification
export function toast(message, type) {
  type = type || 'success';
  const node = document.createElement('div');
  node.className = 'toast ' + type;
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 2200);
}

// Emotional weight CSS class
export function ewClass(ew) {
  const tier = Math.round((ew || 0.3) * 10);
  if (tier <= 2) return 'ew-2';
  if (tier <= 3) return 'ew-3';
  if (tier <= 4) return 'ew-4';
  if (tier <= 5) return 'ew-5';
  if (tier <= 6) return 'ew-6';
  return 'ew-7';
}

// Emotional weight color (for charts)
export function ewColor(ew) {
  if (ew >= 0.7) return '#7b1818';
  if (ew >= 0.6) return '#a03020';
  if (ew >= 0.5) return '#c25a28';
  if (ew >= 0.4) return '#b8860b';
  if (ew >= 0.3) return '#8b7355';
  return '#a09588';
}
