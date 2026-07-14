/* Theme management — matches rexfinhub pattern */
(function() {
    const html = document.documentElement;
    const saved = localStorage.getItem('sn-theme');
    if (saved) html.setAttribute('data-theme', saved);
})();

function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('sn-theme', next);

    // Re-render charts with new theme colors
    if (typeof updateChartTheme === 'function') {
        updateChartTheme(next);
    }
}

/* Helpers for Chart.js theming */
function getThemeColors() {
    const style = getComputedStyle(document.documentElement);
    return {
        text: style.getPropertyValue('--chart-text').trim(),
        grid: style.getPropertyValue('--chart-grid').trim(),
        accent: style.getPropertyValue('--accent').trim(),
        surface: style.getPropertyValue('--surface-1').trim(),
    };
}
