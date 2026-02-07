/**
 * Search results rendering.
 * Displays matching observations and prompts from the search API.
 */

import { renderObservation, renderPrompt } from './components.js';

export function renderSearch(items) {
  if (!items.length) {
    return `<div class="empty">
      <div class="empty-icon">?</div>
      No results found
    </div>`;
  }
  return items.map((item, i) => {
    if (item.kind === 'prompt') {
      return renderPrompt({ prompt_text: item.summary, created_at: item.created_at }, i);
    }
    return renderObservation(item, i);
  }).join('');
}
