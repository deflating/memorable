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

    // Group by machine
    const byMachine = {};
    for (const anchor of anchorsData) {
        const m = anchor.machine || 'unknown';
        if (!byMachine[m]) byMachine[m] = [];
        byMachine[m].push(anchor);
    }

    // Sort each group by timestamp (newest first)
    for (const m of Object.keys(byMachine)) {
        byMachine[m].sort((a, b) => (b.ts || '').localeCompare(a.ts || ''));
    }

    for (const m of Object.keys(byMachine).sort()) {
        const group = el('div', 'session-group');

        const header = el('div', 'session-header');
        header.textContent = m.split('.')[0];
        group.appendChild(header);

        for (const anchor of byMachine[m]) {
            const row = el('div', 'anchor-card');

            const meta = el('span', 'meta');
            meta.textContent = anchor.ts || '';
            meta.style.marginRight = '0.5em';
            meta.style.whiteSpace = 'nowrap';
            row.appendChild(meta);

            const text = el('span', '');
            text.textContent = anchor.summary || '';
            row.appendChild(text);

            group.appendChild(row);
        }

        container.appendChild(group);
    }
}
