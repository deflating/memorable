/**
 * Shared utility functions for the Memorable viewer.
 * HTML escaping, date/time formatting, text truncation.
 */

export function esc(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function formatTime(epoch) {
  if (!epoch) return '';
  const d = new Date(epoch * 1000);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  if (diff < 172800) return 'yesterday';
  if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
  return d.toLocaleDateString('en-AU', { month: 'short', day: 'numeric' });
}

export function formatDate(epoch) {
  if (!epoch) return '';
  const d = new Date(epoch * 1000);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const itemDate = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.floor((today - itemDate) / 86400000);

  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return d.toLocaleDateString('en-AU', { weekday: 'long' });
  return d.toLocaleDateString('en-AU', { weekday: 'short', month: 'short', day: 'numeric' });
}

export function formatFullDate(epoch) {
  if (!epoch) return '';
  const d = new Date(epoch * 1000);
  return d.toLocaleDateString('en-AU', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' })
    + ' at ' + d.toLocaleTimeString('en-AU', { hour: 'numeric', minute: '2-digit' });
}

export function truncate(str, max) {
  if (!str || str.length <= max) return str || '';
  return str.substring(0, max).replace(/\s+\S*$/, '') + '\u2026';
}

export function highlight(text, query) {
  if (!text || !query) return esc(text);
  const escaped = esc(text);
  const terms = query.trim().split(/\s+/).filter(Boolean);
  if (!terms.length) return escaped;
  const pattern = terms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
  const re = new RegExp(`(${pattern})`, 'gi');
  return escaped.replace(re, '<mark style="background:#f0c000;color:#0a0e14;border-radius:2px;padding:0 2px">$1</mark>');
}

export function formatRelativeDate(epoch) {
  if (!epoch) return '';
  const d = new Date(epoch * 1000);
  const now = new Date();
  const diffMs = now - d;
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return 'now';
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return diffMin + 'm ago';
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return diffHr + 'h ago';
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 7) return diffDay + 'd ago';
  const diffWeek = Math.floor(diffDay / 7);
  if (diffWeek < 5) return diffWeek + 'w ago';
  const diffMonth = Math.floor(diffDay / 30);
  if (diffMonth < 12) return diffMonth + 'mo ago';
  const diffYear = Math.floor(diffDay / 365);
  return diffYear + 'y ago';
}
