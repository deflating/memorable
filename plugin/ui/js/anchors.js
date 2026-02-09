import { api, formatDate, formatTime, el } from './utils.js';

let currentMachine = '';
let machines = [];

async function fetchMachines() {
    try {
        const data = await api('/api/machines');
        machines = data.machines || [];
    } catch {
        machines = [];
    }
}

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

export async function render(container) {
    currentMachine = '';
    await fetchMachines();
    renderPage(container);
}

async function renderPage(container) {
    container.innerHTML = '';
    renderDeviceTabs(container, () => {
        renderPage(container);
    });
    await renderAnchors(container);
}

async function renderAnchors(container) {
    const params = new URLSearchParams();
    if (currentMachine) params.set('machine', currentMachine);

    let anchorsData;
    try {
        const data = await api('/api/anchors?' + params);
        anchorsData = data.anchors || [];
    } catch (err) {
        container.innerHTML += '<div class="empty-state">Failed to load anchors: ' + err.message + '</div>';
        return;
    }

    if (anchorsData.length === 0) {
        container.appendChild(el('div', { className: 'empty-state', textContent: 'No anchors yet. The daemon generates these during sessions.' }));
        return;
    }

    // Group anchors by session
    const groups = {};
    for (const anchor of anchorsData) {
        const sid = anchor.session || 'unknown';
        if (!groups[sid]) groups[sid] = [];
        groups[sid].push(anchor);
    }

    // Sort within each group by timestamp (chunk numbers reset after compaction)
    for (const sid of Object.keys(groups)) {
        groups[sid].sort((a, b) => {
            const ta = a.ts ? new Date(a.ts).getTime() : 0;
            const tb = b.ts ? new Date(b.ts).getTime() : 0;
            return tb - ta;
        });
    }

    // Sort session groups newest first (by latest anchor, not first)
    const sortedSessions = Object.keys(groups).sort((a, b) => {
        const lastA = groups[a][0];
        const lastB = groups[b][0];
        const ta = lastA.ts ? new Date(lastA.ts).getTime() : 0;
        const tb = lastB.ts ? new Date(lastB.ts).getTime() : 0;
        return tb - ta;
    });

    for (const sid of sortedSessions) {
        const group = el('div', 'session-group');

        const header = el('div', 'session-header');
        const shortId = sid.length > 8 ? sid.substring(0, 8) : sid;
        const firstTs = groups[sid][0].ts;
        header.textContent = 'Session ' + shortId + ' \u2014 ' + formatDate(firstTs);
        group.appendChild(header);

        for (const anchor of groups[sid]) {
            group.appendChild(renderAnchorCard(anchor));
        }

        container.appendChild(group);
    }
}

// Render a single anchor card
// Expected schema: topic, doing, next, decided, blocked, mood, unresolved, keywords[], quote
// + mechanical: files[], commands[], human_messages[]
function renderAnchorCard(anchor) {
    const card = el('div', 'anchor-card');

    // Topic as card title
    if (anchor.topic) {
        const topic = el('div', 'anchor-topic');
        topic.textContent = anchor.topic;
        card.appendChild(topic);
    }

    // AFM fields as labeled rows
    const fields = [
        { key: 'doing', label: 'DOING' },
        { key: 'next', label: 'NEXT' },
        { key: 'decided', label: 'DECIDED', skip: 'none' },
        { key: 'blocked', label: 'BLOCKED', skip: 'none' },
        { key: 'unresolved', label: 'UNRESOLVED', skip: 'none' },
    ];

    for (const f of fields) {
        const val = anchor[f.key];
        if (!val) continue;
        if (f.skip && val.toLowerCase() === f.skip) continue;

        const row = el('div', 'anchor-field');
        const label = el('div', 'anchor-label');
        label.textContent = f.label;
        const value = el('div', 'anchor-value');
        value.textContent = val;
        row.appendChild(label);
        row.appendChild(value);
        card.appendChild(row);
    }

    // Quote
    if (anchor.quote) {
        const quote = el('div', 'anchor-quote');
        quote.textContent = anchor.quote;
        card.appendChild(quote);
    }

    // Keywords as pills
    if (anchor.keywords && anchor.keywords.length > 0) {
        const tagsDiv = el('div', 'anchor-tags');
        for (const kw of anchor.keywords) {
            const tag = el('span', 'tag');
            tag.textContent = kw;
            tagsDiv.appendChild(tag);
        }
        card.appendChild(tagsDiv);
    }

    // Mechanical metadata: files touched
    if (anchor.files && anchor.files.length > 0) {
        const filesDiv = el('div', 'anchor-files');
        const label = el('div', 'anchor-label');
        label.textContent = 'FILES';
        filesDiv.appendChild(label);
        for (const f of anchor.files) {
            const fileEl = el('span', 'anchor-file');
            // Show just filename, not full path
            const parts = f.split('/');
            fileEl.textContent = parts[parts.length - 1];
            fileEl.title = f;
            filesDiv.appendChild(fileEl);
        }
        card.appendChild(filesDiv);
    }

    // Mechanical metadata: human messages
    if (anchor.human_messages && anchor.human_messages.length > 0) {
        const msgsDiv = el('div', 'anchor-field');
        const label = el('div', 'anchor-label');
        label.textContent = 'HUMAN';
        msgsDiv.appendChild(label);
        for (const msg of anchor.human_messages) {
            const msgEl = el('div', 'anchor-human-msg');
            msgEl.textContent = msg;
            msgsDiv.appendChild(msgEl);
        }
        card.appendChild(msgsDiv);
    }

    // Mood pill + meta line
    const footer = el('div', 'anchor-footer');

    if (anchor.mood) {
        const moodSpan = el('span', 'anchor-mood');
        moodSpan.textContent = anchor.mood;
        footer.appendChild(moodSpan);
    }

    const metaParts = [];
    if (anchor.chunk != null) metaParts.push('Chunk ' + anchor.chunk);
    if (anchor.ts) metaParts.push(formatTime(anchor.ts));
    if (anchor.machine) metaParts.push(anchor.machine.split('.')[0]);
    if (metaParts.length) {
        const meta = el('span', 'meta');
        meta.textContent = metaParts.join(' \u00b7 ');
        footer.appendChild(meta);
    }

    card.appendChild(footer);

    return card;
}
