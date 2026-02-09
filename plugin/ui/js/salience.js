import { api, ewColor, el } from './utils.js';

let charts = {};

export async function render(container) {
    container.innerHTML = '<div class="loading">Loading salience data\u2026</div>';

    // Destroy old chart instances
    for (const k of Object.keys(charts)) {
        if (charts[k]) charts[k].destroy();
    }
    charts = {};

    let salience, tagsData, stats;
    try {
        [salience, tagsData, stats] = await Promise.all([
            api('/api/salience'),
            api('/api/notes/tags'),
            api('/api/stats'),
        ]);
    } catch (err) {
        container.innerHTML = '<div class="empty">Failed to load salience data</div>';
        console.error(err);
        return;
    }

    container.innerHTML = '';
    const points = salience.points || [];

    renderStats(container, stats, points);
    renderDecayCurves(container, points);

    const row = el('div', 'chart-row');
    const ewContainer = el('div', 'chart-container');
    row.appendChild(ewContainer);
    const tagContainer = el('div', 'chart-container');
    row.appendChild(tagContainer);
    container.appendChild(row);

    renderEwDistribution(ewContainer, points);
    renderTagChart(tagContainer, (tagsData.tags || []).slice(0, 15));
}

function renderStats(container, stats, points) {
    const cards = el('div', 'stat-cards');
    let ewSum = 0;
    points.forEach(p => { ewSum += (p.emotional_weight || 0.3); });
    const avgEw = points.length ? (ewSum / points.length).toFixed(2) : '0';
    const earliest = stats.date_range ? stats.date_range.earliest : '';
    const latest = stats.date_range ? stats.date_range.latest : '';
    let daySpan = 0;
    if (earliest && latest) daySpan = Math.round((new Date(latest) - new Date(earliest)) / 86400000);

    const items = [
        { num: stats.note_count || 0, label: 'Total Notes' },
        { num: daySpan, label: 'Days Span' },
        { num: stats.unique_tags || 0, label: 'Unique Tags' },
        { num: avgEw, label: 'Avg EW' },
    ];

    for (const item of items) {
        const card = el('div', 'stat-card');
        card.innerHTML = '<div class="stat-number">' + item.num + '</div><div class="stat-label">' + item.label + '</div>';
        cards.appendChild(card);
    }
    container.appendChild(cards);
}

function renderDecayCurves(container, points) {
    const chartDiv = el('div', 'chart-container');
    const canvas = document.createElement('canvas');
    canvas.height = 300;
    chartDiv.appendChild(canvas);
    container.appendChild(chartDiv);

    const tiers = [
        { ew: 0.2, color: '#a09588', label: 'ew 0.2' },
        { ew: 0.3, color: '#8b7355', label: 'ew 0.3' },
        { ew: 0.4, color: '#b8860b', label: 'ew 0.4' },
        { ew: 0.5, color: '#c25a28', label: 'ew 0.5', width: 2 },
        { ew: 0.6, color: '#a03020', label: 'ew 0.6' },
        { ew: 0.7, color: '#7b1818', label: 'ew 0.7', width: 2 },
    ];

    const days = [];
    for (let i = 0; i <= 60; i++) days.push(i);

    const datasets = tiers.map(tier => ({
        label: tier.label,
        data: days.map(d => Math.max(0.05, Math.pow(0.97, d * (1.0 - tier.ew * 0.5)))),
        borderColor: tier.color,
        borderWidth: tier.width || 1.5,
        pointRadius: 0,
        tension: 0.3,
        borderDash: tier.ew <= 0.3 ? [4, 4] : [],
    }));

    const now = Date.now();
    const scatterData = points.map(p => ({
        x: Math.round((now - new Date(p.ts || p.date).getTime()) / 86400000 * 10) / 10,
        y: p.effective_salience,
        ew: p.emotional_weight,
    })).filter(d => d.x >= 0 && d.x <= 60);

    datasets.push({
        label: 'Actual notes',
        data: scatterData,
        type: 'scatter',
        pointRadius: 4,
        pointBackgroundColor: scatterData.map(d => ewColor(d.ew)),
        pointBorderColor: 'rgba(255,255,255,0.8)',
        pointBorderWidth: 1,
    });

    datasets.push({
        label: 'Threshold (0.1)',
        data: days.map(() => 0.1),
        borderColor: 'rgba(160,149,136,0.4)',
        borderWidth: 1,
        borderDash: [6, 3],
        pointRadius: 0,
    });

    charts.decay = new Chart(canvas, {
        type: 'line',
        data: { labels: days, datasets },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 }, color: '#7a6f63' } },
                tooltip: {
                    callbacks: {
                        label(ctx) {
                            if (ctx.dataset.type === 'scatter') return 'Day ' + ctx.parsed.x.toFixed(0) + ': salience ' + ctx.parsed.y.toFixed(3);
                            return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(3);
                        },
                    },
                },
            },
            scales: {
                x: { title: { display: true, text: 'Days from creation', font: { size: 11 }, color: '#7a6f63' }, grid: { color: 'rgba(0,0,0,0.08)' }, ticks: { color: '#7a6f63' } },
                y: { title: { display: true, text: 'Effective salience', font: { size: 11 }, color: '#7a6f63' }, min: 0, max: 1.05, grid: { color: 'rgba(0,0,0,0.08)' }, ticks: { color: '#7a6f63' } },
            },
        },
    });
}

function renderEwDistribution(container, points) {
    const title = el('div');
    title.style.cssText = 'font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:12px';
    title.textContent = 'Emotional Weight Distribution';
    container.appendChild(title);
    const canvas = document.createElement('canvas');
    canvas.height = 200;
    container.appendChild(canvas);

    const buckets = { '0.2': 0, '0.3': 0, '0.4': 0, '0.5': 0, '0.6': 0, '0.7': 0 };
    for (const p of points) {
        const key = (Math.round((p.emotional_weight || 0.3) * 10) / 10).toFixed(1);
        if (buckets[key] !== undefined) buckets[key]++;
    }
    const labels = Object.keys(buckets);

    charts.ew = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: labels.map(l => 'ew ' + l),
            datasets: [{ data: labels.map(k => buckets[k]), backgroundColor: ['#a09588','#8b7355','#b8860b','#c25a28','#a03020','#7b1818'], borderRadius: 4 }],
        },
        options: { indexAxis: 'y', responsive: true, plugins: { legend: { display: false } }, scales: { x: { grid: { color: 'rgba(0,0,0,0.08)' }, ticks: { color: '#7a6f63' } }, y: { grid: { display: false }, ticks: { color: '#7a6f63' } } } },
    });
}

function renderTagChart(container, tags) {
    const title = el('div');
    title.style.cssText = 'font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:12px';
    title.textContent = 'Top Tags';
    container.appendChild(title);
    const canvas = document.createElement('canvas');
    canvas.height = 200;
    container.appendChild(canvas);

    charts.tags = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: tags.map(t => t.name),
            datasets: [{ data: tags.map(t => t.count), backgroundColor: 'rgba(194,90,40,0.7)', borderRadius: 4 }],
        },
        options: {
            indexAxis: 'y', responsive: true, plugins: { legend: { display: false } },
            scales: { x: { grid: { color: 'rgba(0,0,0,0.08)' }, ticks: { color: '#7a6f63' } }, y: { grid: { display: false }, ticks: { font: { size: 10 }, color: '#7a6f63' } } },
            onClick(evt, items) {
                if (items.length > 0) {
                    location.hash = '#notes';
                }
            },
        },
    });
}
