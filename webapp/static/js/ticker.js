/* Scrolling Ticker Bar — Market Indices + REX Products */
(function() {
  var track = document.getElementById('tickerTrack');
  if (!track) return;

  function buildTicker(data) {
    if (!data) { track.parentElement.style.display = 'none'; return; }

    var html = '';

    // Market indices (if provided)
    if (data.indices && data.indices.length) {
      data.indices.forEach(function(idx) {
        var pct = idx.change_pct || 0;
        var color = pct >= 0 ? 'var(--data-positive)' : 'var(--data-negative)';
        var arrow = pct >= 0 ? '\u25B2' : '\u25BC';
        var sign = pct >= 0 ? '+' : '';
        html += '<span class="ticker-item ticker-index">';
        html += '<span class="ticker-sym">' + idx.name + '</span>';
        html += '<span class="ticker-val">' + idx.value + '</span>';
        html += '<span style="color:' + color + ';font-weight:600;font-size:11px;">' + arrow + ' ' + sign + pct.toFixed(1) + '%</span>';
        html += '</span>';
      });
      html += '<span class="ticker-divider">|</span>';
    }

    // REX products
    if (data.products && data.products.length) {
      data.products.forEach(function(p) {
        var pct = p.change_pct || 0;
        var color = pct >= 0 ? 'var(--data-positive)' : 'var(--data-negative)';
        var arrow = pct >= 0 ? '\u25B2' : '\u25BC';
        var sign = pct >= 0 ? '+' : '';
        html += '<span class="ticker-item">';
        html += '<span class="ticker-sym">' + p.ticker + '</span>';
        html += '<span class="ticker-val">' + p.value + '</span>';
        html += '<span style="color:' + color + ';font-weight:600;font-size:11px;">' + arrow + ' ' + sign + pct.toFixed(1) + '%</span>';
        html += '</span>';
      });
    }

    if (!html) { track.parentElement.style.display = 'none'; return; }

    // Duplicate for seamless loop
    track.innerHTML = html + html;

    // Mark duplicate half as aria-hidden for screen readers
    var items = track.children;
    var half = items.length / 2;
    for (var i = Math.floor(half); i < items.length; i++) {
      items[i].setAttribute('aria-hidden', 'true');
    }

    var w = track.scrollWidth / 2;
    var dur = Math.max(w / 50, 20);
    track.style.animation = 'tickerScroll ' + dur + 's linear infinite';
  }

  // Pause on hover
  track.parentElement.addEventListener('mouseenter', function() {
    track.style.animationPlayState = 'paused';
  });
  track.parentElement.addEventListener('mouseleave', function() {
    track.style.animationPlayState = 'running';
  });

  // Fetch data — API returns { indices: [...], products: [...] }
  fetch('/api/v1/ticker-strip')
    .then(function(r) { return r.ok ? r.json() : Promise.reject(); })
    .then(function(d) { buildTicker(d); })
    .catch(function() { track.parentElement.style.display = 'none'; });
})();
