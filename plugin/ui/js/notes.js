import { api, formatDate, formatTime, renderMarkdown, stripMarkdown, debounce, ewClass, el } from './utils.js';

// Module state
let notes = [];
let tags = [];
let machines = [];
let total = 0;
let offset = 0;
let expandedId = null;
let currentSearch = '';
let currentTag = '';
let currentSort = 'date';
let currentMachine = '';

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function fetchNotes(append = false) {
    const params = new URLSearchParams({
        limit: '50',
        offset: String(append ? offset : 0),
        sort: currentSort,
    });
    if (currentSearch) params.set('search', currentSearch);
    if (currentTag) params.set('tag', currentTag);
    if (currentMachine) params.set('machine', currentMachine);

    const data = await api(`/api/notes?${params}`);
    total = data.total;

    if (append) {
        notes = notes.concat(data.notes);
    } else {
        notes = data.notes;
        offset = 0;
    }
    offset = notes.length;
}

async function fetchTags() {
    try {
        const data = await api('/api/notes/tags');
        tags = data.tags || [];
    } catch {
        tags = [];
    }
}

async function fetchMachines() {
    try {
        const data = await api('/api/machines');
        machines = data.machines || [];
    } catch {
        machines = [];
    }
}

// ---------------------------------------------------------------------------
// Rendering helpers
// ---------------------------------------------------------------------------

function highlightText(html, query) {
    if (!query) return html;
    const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp(`(${escaped})`, 'gi');
    return html.replace(re, '<mark>$1</mark>');
}

function machineName(machine) {
    if (!machine) return '';
    return machine.split('.')[0];
}

function truncate(text, len) {
    if (!text) return '';
    if (text.length <= len) return text;
    return text.slice(0, len) + '\u2026';
}

// ---------------------------------------------------------------------------
// Filter bar
// ---------------------------------------------------------------------------

function renderDeviceTabs(container, onSwitch) {
    if (machines.length <= 1) return;
    const bar = el('div', 'device-tabs');

    const allTab = el('span', currentMachine === '' ? 'device-tab active' : 'device-tab');
    allTab.textContent = 'All';
    allTab.addEventListener('click', () => { currentMachine = ''; onSwitch(); });
    bar.appendChild(allTab);

    for (const m of machines) {
        const shortName = m.split('.')[0];
        const tab = el('span', currentMachine === m ? 'device-tab active' : 'device-tab');
        tab.textContent = shortName;
        tab.addEventListener('click', () => { currentMachine = m; onSwitch(); });
        bar.appendChild(tab);
    }

    container.appendChild(bar);
}

function renderFilterBar(container) {
    const bar = el('div', { className: 'filter-bar' });

    // Search input
    const searchInput = el('input', {
        type: 'search',
        placeholder: 'Search notes\u2026',
    });

    const onSearch = debounce(() => {
        currentSearch = searchInput.value.trim();
        expandedId = null;
        fetchNotes().then(() => {
            renderNotes(container);
            appendLoadMore(container);
        });
    }, 300);

    searchInput.addEventListener('keyup', onSearch);
    searchInput.addEventListener('search', onSearch);
    bar.appendChild(searchInput);

    // Tag dropdown
    const tagSelect = el('select');
    tagSelect.appendChild(el('option', { value: '', textContent: 'All tags' }));
    for (const tag of tags) {
        tagSelect.appendChild(el('option', {
            value: tag.name,
            textContent: `${tag.name} (${tag.count})`,
        }));
    }
    tagSelect.addEventListener('change', () => {
        currentTag = tagSelect.value;
        expandedId = null;
        fetchNotes().then(() => {
            renderNotes(container);
            appendLoadMore(container);
        });
    });
    bar.appendChild(tagSelect);

    // Sort dropdown
    const sortSelect = el('select');
    const sortOptions = [
        { value: 'date', label: 'Newest first' },
        { value: 'date_asc', label: 'Oldest first' },
        { value: 'salience', label: 'Highest salience' },
    ];
    for (const opt of sortOptions) {
        sortSelect.appendChild(el('option', { value: opt.value, textContent: opt.label }));
    }
    sortSelect.value = currentSort;
    sortSelect.addEventListener('change', () => {
        currentSort = sortSelect.value;
        expandedId = null;
        fetchNotes().then(() => {
            renderNotes(container);
            appendLoadMore(container);
        });
    });
    bar.appendChild(sortSelect);

    container.appendChild(bar);
}

// ---------------------------------------------------------------------------
// Note card
// ---------------------------------------------------------------------------

function renderNoteCard(note) {
    const isExpanded = expandedId === note.session;
    const ewCls = ewClass(note.emotional_weight);

    const card = el('div', {
        className: `card note-card ${ewCls}${isExpanded ? ' expanded' : ''}`,
    });

    // Salience bar
    const salienceWidth = Math.min((note.effective_salience || 0) * 100, 100);
    card.appendChild(el('div', {
        className: 'salience-bar',
        style: `width:${salienceWidth}%`,
    }));

    // Card header
    const header = el('div', { className: 'card-header' });

    // Tag pills (first 4 + overflow)
    const topicTags = note.topic_tags || [];
    const visibleTags = topicTags.slice(0, 4);
    for (const t of visibleTags) {
        header.appendChild(el('span', { className: 'tag', textContent: t }));
    }
    if (topicTags.length > 4) {
        header.appendChild(el('span', {
            className: 'tag',
            textContent: `+${topicTags.length - 4}`,
        }));
    }

    // Metadata
    const metaParts = [];
    if (note.message_count != null) metaParts.push(`${note.message_count} msgs`);
    if (note.first_ts) metaParts.push(formatDate(note.first_ts));
    if (note.emotional_weight != null) metaParts.push(`ew ${note.emotional_weight}`);

    header.appendChild(el('span', {
        className: 'meta',
        textContent: metaParts.join(' \u00b7 '),
    }));

    // Machine badge
    if (note.machine) {
        header.appendChild(el('span', {
            className: 'tag',
            textContent: machineName(note.machine),
        }));
    }

    card.appendChild(header);

    // Note summary (visible when collapsed)
    const summaryText = truncate(stripMarkdown(note.note), 150);
    card.appendChild(el('div', {
        className: 'note-summary',
        textContent: summaryText,
    }));

    // Card body (visible when expanded)
    const body = el('div', { className: 'card-body' });

    // Rendered markdown
    let mdHtml = renderMarkdown(note.note);
    if (currentSearch) {
        mdHtml = highlightText(mdHtml, currentSearch);
    }
    body.appendChild(el('div', {
        className: 'rendered-md',
        innerHTML: mdHtml,
    }));

    // Metadata section below the markdown
    const metaDiv = el('div', {
        className: 'meta',
        style: 'margin-top:12px;padding-top:10px;border-top:1px solid var(--border-subtle)',
    });

    if (note.session) {
        metaDiv.appendChild(el('span', {
            className: 'meta-item',
            style: 'font-family:var(--mono)',
            textContent: note.session.slice(0, 8),
        }));
    }
    if (note.machine) {
        metaDiv.appendChild(el('span', {
            className: 'meta-item',
            textContent: note.machine,
        }));
    }
    if (note.first_ts || note.last_ts) {
        const timeRange = [formatTime(note.first_ts), formatTime(note.last_ts)]
            .filter(Boolean)
            .join(' \u2013 ');
        metaDiv.appendChild(el('span', {
            className: 'meta-item',
            textContent: timeRange,
        }));
    }
    if (note.reference_count > 0) {
        metaDiv.appendChild(el('span', {
            className: 'meta-item',
            textContent: `${note.reference_count} ref${note.reference_count === 1 ? '' : 's'}`,
        }));
    }

    body.appendChild(metaDiv);
    card.appendChild(body);

    // Accordion click — whole card is the touch target
    card.style.cursor = 'pointer';
    card.addEventListener('click', () => {
        const wasExpanded = card.classList.contains('expanded');

        // Collapse any currently expanded card
        if (expandedId !== null) {
            const prev = card.parentElement?.querySelector('.card.expanded');
            if (prev && prev !== card) {
                prev.classList.remove('expanded');
            }
        }

        if (wasExpanded) {
            card.classList.remove('expanded');
            expandedId = null;
        } else {
            card.classList.add('expanded');
            expandedId = note.session;
        }
    });

    return card;
}

// ---------------------------------------------------------------------------
// Note list
// ---------------------------------------------------------------------------

function renderNotes(container) {
    const existing = container.querySelector('.notes-list');
    if (existing) existing.remove();

    const list = el('div', { className: 'notes-list' });

    if (notes.length === 0) {
        list.appendChild(el('div', {
            className: 'empty',
            innerHTML: '<h3>No notes found</h3><p>Try adjusting your search or filters.</p>',
        }));
    } else {
        for (const note of notes) {
            list.appendChild(renderNoteCard(note));
        }
    }

    container.appendChild(list);
}

// ---------------------------------------------------------------------------
// Load more
// ---------------------------------------------------------------------------

function appendLoadMore(container) {
    const existing = container.querySelector('.load-more-wrap');
    if (existing) existing.remove();

    if (notes.length >= total) return;

    const wrap = el('div', {
        className: 'load-more-wrap',
        style: 'text-align:center;padding:16px 0',
    });

    const btn = el('button', {
        className: 'btn btn-ghost',
        textContent: `Load more (${notes.length} of ${total})`,
    });

    btn.addEventListener('click', async () => {
        btn.textContent = 'Loading\u2026';
        btn.disabled = true;
        try {
            await fetchNotes(true);
            renderNotes(container);
            appendLoadMore(container);
        } catch (err) {
            btn.textContent = 'Error loading \u2014 retry';
            btn.disabled = false;
            console.error(err);
        }
    });

    wrap.appendChild(btn);
    container.appendChild(wrap);
}

// ---------------------------------------------------------------------------
// Public render
// ---------------------------------------------------------------------------

export async function render(container, state) {
    // Reset module state
    notes = [];
    tags = [];
    machines = [];
    total = 0;
    offset = 0;
    expandedId = null;
    currentSearch = '';
    currentTag = '';
    currentSort = 'date';
    currentMachine = '';

    // Fetch tags + machines
    await Promise.all([fetchTags(), fetchMachines()]);

    await renderPage(container);
}

async function renderPage(container) {
    container.innerHTML = '';

    // Device tabs + filter bar
    renderDeviceTabs(container, () => {
        expandedId = null;
        notes = [];
        offset = 0;
        fetchNotes().then(() => {
            renderPage(container);
        });
    });
    renderFilterBar(container);

    // Fetch initial notes if not already loaded
    if (notes.length === 0) {
        try {
            await fetchNotes();
        } catch (err) {
            container.appendChild(el('div', {
                className: 'empty',
                innerHTML: `<h3>Failed to load notes</h3><p>${err.message}</p>`,
            }));
            return;
        }
    }

    // Render note cards
    renderNotes(container);

    // Load more button
    appendLoadMore(container);
}
