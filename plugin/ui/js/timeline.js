/**
 * Timeline tab: chronological list of observations and prompts,
 * grouped by date (Today, Yesterday, This week, etc.).
 */

import { formatDate } from './utils.js';
import { renderTimelineItem } from './components.js';

export function renderTimeline(items) {
  if (!items.length) {
    return `<div class="empty">
      <div class="empty-icon">~</div>
      No activity yet.<br>Observations and prompts will appear as you use Claude Code.
    </div>`;
  }

  let html = '';
  let lastDate = '';

  items.forEach((item, i) => {
    const date = formatDate(item.created_at);
    if (date !== lastDate) {
      html += `<div class="date-separator">${date}</div>`;
      lastDate = date;
    }
    html += renderTimelineItem(item, i);
  });

  return html;
}
