/* Chart.js configurations — Structured Notes Dashboard */

// Color palette (institutional, muted)
const PALETTE = [
    '#6366F1', // indigo
    '#0EA5E9', // sky
    '#A855F7', // violet
    '#22C55E', // green
    '#F97316', // orange
    '#EF4444', // red
    '#EC4899', // pink
    '#14B8A6', // teal
    '#F59E0B', // amber
    '#8B5CF6', // purple
    '#06B6D4', // cyan
    '#84CC16', // lime
    '#E11D48', // rose
    '#3B82F6', // blue
    '#10B981', // emerald
];

const PALETTE_ALPHA = PALETTE.map(c => c + '33');

// Chart.js defaults
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.font.size = 12;
Chart.defaults.plugins.legend.display = false;
Chart.defaults.plugins.datalabels = { display: false };
Chart.defaults.animation.duration = 600;

function themeColors() {
    return getThemeColors();  // from app.js
}

function baseScaleOpts(axis = 'x') {
    const tc = themeColors();
    return {
        ticks: { color: tc.text, font: { size: 11 } },
        grid: { color: tc.grid },
        border: { display: false },
    };
}

// -------------------------------------------------------
// Monthly Issuance (Line)
// -------------------------------------------------------
if (DATA.monthly && DATA.monthly.length > 0) {
    new Chart(document.getElementById('monthlyChart'), {
        type: 'line',
        data: {
            labels: DATA.monthly.map(d => d.month),
            datasets: [{
                label: 'Products',
                data: DATA.monthly.map(d => d.count),
                borderColor: PALETTE[0],
                backgroundColor: PALETTE_ALPHA[0],
                fill: true,
                tension: 0.3,
                pointRadius: 2,
                pointHoverRadius: 5,
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                tooltip: {
                    backgroundColor: themeColors().surface,
                    titleColor: themeColors().text,
                    bodyColor: themeColors().text,
                    borderColor: themeColors().grid,
                    borderWidth: 1,
                    callbacks: {
                        label: ctx => `${ctx.parsed.y.toLocaleString()} products`
                    }
                }
            },
            scales: {
                x: { ...baseScaleOpts('x'), ticks: { ...baseScaleOpts('x').ticks, maxTicksLimit: 12 } },
                y: { ...baseScaleOpts('y'), beginAtZero: true,
                     ticks: { ...baseScaleOpts('y').ticks, callback: v => v.toLocaleString() } },
            }
        }
    });
}

// -------------------------------------------------------
// Product Types (Donut)
// -------------------------------------------------------
if (DATA.types && DATA.types.length > 0) {
    new Chart(document.getElementById('typeChart'), {
        type: 'doughnut',
        data: {
            labels: DATA.types.map(d => d.type),
            datasets: [{
                data: DATA.types.map(d => d.count),
                backgroundColor: PALETTE.slice(0, DATA.types.length),
                borderWidth: 0,
                hoverOffset: 4,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '55%',
            plugins: {
                legend: {
                    display: true,
                    position: 'right',
                    labels: {
                        color: themeColors().text,
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 12,
                        font: { size: 11 },
                        generateLabels: chart => {
                            const data = chart.data;
                            const total = data.datasets[0].data.reduce((a, b) => a + b, 0);
                            return data.labels.map((label, i) => ({
                                text: `${label} (${(data.datasets[0].data[i] / total * 100).toFixed(1)}%)`,
                                fillStyle: data.datasets[0].backgroundColor[i],
                                strokeStyle: 'transparent',
                                pointStyle: 'circle',
                                index: i,
                            }));
                        }
                    }
                },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
                            const pct = (ctx.parsed / total * 100).toFixed(1);
                            return `${ctx.label}: ${ctx.parsed.toLocaleString()} (${pct}%)`;
                        }
                    }
                }
            }
        }
    });
}

// -------------------------------------------------------
// Issuer Market Share (Horizontal Bar)
// -------------------------------------------------------
if (DATA.issuers && DATA.issuers.length > 0) {
    new Chart(document.getElementById('issuerChart'), {
        type: 'bar',
        data: {
            labels: DATA.issuers.map(d => d.issuer),
            datasets: [{
                data: DATA.issuers.map(d => d.count),
                backgroundColor: PALETTE[1],
                borderRadius: 3,
                barThickness: 18,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                tooltip: {
                    callbacks: {
                        label: ctx => `${ctx.parsed.x.toLocaleString()} products`
                    }
                }
            },
            scales: {
                x: { ...baseScaleOpts('x'), beginAtZero: true,
                     ticks: { ...baseScaleOpts('x').ticks, callback: v => v.toLocaleString() } },
                y: { ...baseScaleOpts('y'),
                     ticks: { ...baseScaleOpts('y').ticks, font: { size: 11 } } },
            }
        }
    });
}

// -------------------------------------------------------
// Top Underliers (Horizontal Bar)
// -------------------------------------------------------
if (DATA.underliers && DATA.underliers.length > 0) {
    new Chart(document.getElementById('underlierChart'), {
        type: 'bar',
        data: {
            labels: DATA.underliers.map(d => d.name),
            datasets: [{
                data: DATA.underliers.map(d => d.count),
                backgroundColor: PALETTE[2],
                borderRadius: 3,
                barThickness: 18,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const d = DATA.underliers[ctx.dataIndex];
                            return `${ctx.parsed.x.toLocaleString()} products (${d.pct}%)`;
                        }
                    }
                }
            },
            scales: {
                x: { ...baseScaleOpts('x'), beginAtZero: true },
                y: { ...baseScaleOpts('y'),
                     ticks: { ...baseScaleOpts('y').ticks, font: { size: 11 } } },
            }
        }
    });
}

// -------------------------------------------------------
// Barrier Distribution (Bar)
// -------------------------------------------------------
if (DATA.barriers && DATA.barriers.length > 0) {
    new Chart(document.getElementById('barrierChart'), {
        type: 'bar',
        data: {
            labels: DATA.barriers.map(d => d.bucket),
            datasets: [{
                data: DATA.barriers.map(d => d.count),
                backgroundColor: PALETTE[3],
                borderRadius: 3,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                tooltip: {
                    callbacks: {
                        label: ctx => `${ctx.parsed.y.toLocaleString()} products`
                    }
                }
            },
            scales: {
                x: baseScaleOpts('x'),
                y: { ...baseScaleOpts('y'), beginAtZero: true,
                     ticks: { ...baseScaleOpts('y').ticks, callback: v => v.toLocaleString() } },
            }
        }
    });
}

// -------------------------------------------------------
// Coupon Distribution (Bar)
// -------------------------------------------------------
if (DATA.coupons && DATA.coupons.length > 0) {
    new Chart(document.getElementById('couponChart'), {
        type: 'bar',
        data: {
            labels: DATA.coupons.map(d => d.bucket),
            datasets: [{
                data: DATA.coupons.map(d => d.count),
                backgroundColor: PALETTE[4],
                borderRadius: 3,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: baseScaleOpts('x'),
                y: { ...baseScaleOpts('y'), beginAtZero: true,
                     ticks: { ...baseScaleOpts('y').ticks, callback: v => v.toLocaleString() } },
            }
        }
    });
}

// -------------------------------------------------------
// Annual Issuance (Bar)
// -------------------------------------------------------
if (DATA.annual && DATA.annual.length > 0) {
    new Chart(document.getElementById('annualChart'), {
        type: 'bar',
        data: {
            labels: DATA.annual.map(d => d.year),
            datasets: [{
                data: DATA.annual.map(d => d.count),
                backgroundColor: PALETTE[7],
                borderRadius: 3,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const d = DATA.annual[ctx.dataIndex];
                            const notional = d.notional > 1e9
                                ? `$${(d.notional / 1e9).toFixed(1)}B`
                                : `$${(d.notional / 1e6).toFixed(0)}M`;
                            return [`${ctx.parsed.y.toLocaleString()} products`, `Notional: ${notional}`];
                        }
                    }
                }
            },
            scales: {
                x: baseScaleOpts('x'),
                y: { ...baseScaleOpts('y'), beginAtZero: true,
                     ticks: { ...baseScaleOpts('y').ticks, callback: v => v.toLocaleString() } },
            }
        }
    });
}

// -------------------------------------------------------
// Theme update (called from app.js on toggle)
// -------------------------------------------------------
function updateChartTheme() {
    // Chart.js caches colors — simplest fix is reload
    // For a production app, iterate Chart.instances and update
    location.reload();
}
