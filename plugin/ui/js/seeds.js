import { api, apiPut, renderMarkdown, toast, el } from './utils.js';

// Module state
let editingSeed = null;
let seedsData = [];

function createSeedCard(seed) {
    const card = el('div', `seed-card ${seed.name}`);

    const header = el('div', 'seed-header');

    const nameSpan = el('span', 'seed-name');
    nameSpan.style.fontFamily = 'var(--mono)';
    nameSpan.style.fontSize = '13px';
    nameSpan.textContent = seed.filename;

    const actions = el('div', 'seed-actions');
    const editBtn = el('button', 'btn btn-ghost btn-sm');
    editBtn.textContent = 'Edit';
    actions.appendChild(editBtn);

    header.appendChild(nameSpan);
    header.appendChild(actions);

    const content = el('div', 'seed-content');

    function showView() {
        editingSeed = null;
        content.innerHTML = '';
        const rendered = el('div', 'rendered-md');
        rendered.innerHTML = renderMarkdown(seed.content);
        content.appendChild(rendered);
        editBtn.textContent = 'Edit';
    }

    function showEdit() {
        editingSeed = seed.name;
        content.innerHTML = '';

        const textarea = el('textarea', 'seed-editor');
        textarea.value = seed.content;
        content.appendChild(textarea);

        const btnRow = el('div', 'seed-actions');
        btnRow.style.marginTop = '8px';
        btnRow.style.display = 'flex';
        btnRow.style.gap = '8px';

        const saveBtn = el('button', 'btn btn-sm');
        saveBtn.textContent = 'Save';
        saveBtn.addEventListener('click', async () => {
            try {
                const newContent = textarea.value;
                await apiPut('/api/seeds/' + seed.name, { content: newContent });
                seed.content = newContent;
                toast('Seed saved');
                showView();
            } catch (err) {
                toast('Save failed: ' + err.message);
            }
        });

        const cancelBtn = el('button', 'btn btn-ghost btn-sm');
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', () => {
            showView();
        });

        btnRow.appendChild(saveBtn);
        btnRow.appendChild(cancelBtn);
        content.appendChild(btnRow);

        editBtn.textContent = 'Edit';
    }

    editBtn.addEventListener('click', () => {
        if (editingSeed === seed.name) {
            showView();
        } else {
            showEdit();
        }
    });

    card.appendChild(header);
    card.appendChild(content);

    showView();

    return card;
}

export async function render(container, state) {
    editingSeed = null;

    try {
        const data = await api('/api/seeds');
        seedsData = data.seeds || [];
    } catch (err) {
        container.innerHTML = '<div class="empty-state">Failed to load seeds: ' + err.message + '</div>';
        return;
    }

    if (seedsData.length === 0) {
        container.innerHTML = '<div class="empty-state">No seed files found.</div>';
        return;
    }

    const grid = el('div', 'seeds-grid');

    for (const seed of seedsData) {
        grid.appendChild(createSeedCard(seed));
    }

    container.appendChild(grid);
}
