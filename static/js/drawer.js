/* static/js/drawer.js — open/close/resize + sekme switch + Activity payload binding */
(function () {
  const STATE = { activeDrawerId: null, dragging: false, startX: 0, startW: 0 };

  function open(drawerId, payload) {
    const drawer = document.getElementById(drawerId);
    const backdrop = document.querySelector('[data-drawer-backdrop]');
    if (!drawer) return;
    drawer.hidden = false;
    drawer.setAttribute('aria-hidden', 'false');
    if (backdrop) backdrop.hidden = false;
    STATE.activeDrawerId = drawerId;
    if (payload) renderActivityDrawer(drawer, payload);
  }

  function close() {
    const drawer = document.getElementById(STATE.activeDrawerId);
    const backdrop = document.querySelector('[data-drawer-backdrop]');
    if (drawer) { drawer.hidden = true; drawer.setAttribute('aria-hidden', 'true'); }
    if (backdrop) backdrop.hidden = true;
    STATE.activeDrawerId = null;
  }

  function switchTab(drawer, tabId) {
    drawer.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === tabId ? 'true' : 'false'));
    drawer.querySelectorAll('[data-tab-panel]').forEach(p => p.hidden = p.dataset.tabPanel !== tabId);
  }

  function renderActivityDrawer(drawer, payload) {
    // Detay sekmesi: timeline + envelope + DKIM/SPF/DMARC chip'leri
    const detail = drawer.querySelector('[data-tab-panel="detail"]');
    const m = payload.message || {};
    const events = payload.events || [];
    detail.innerHTML = `
      <h3>Envelope</h3>
      <dl class="envelope">
        <dt>Message-ID</dt><dd><code>${m.msgid || '—'}</code></dd>
        <dt>Kimden</dt><dd>${m.from || '—'}</dd>
        <dt>Kime</dt><dd>${m.to || '—'}</dd>
        <dt>Konu</dt><dd>${m.subject || '—'}</dd>
        <dt>Boyut</dt><dd>${m.size || '—'} bytes</dd>
        <dt>Relay</dt><dd>${m.relay || '—'}</dd>
      </dl>
      <h3>Zaman çizelgesi</h3>
      <ol class="timeline">${events.map(e => `<li><strong>${e.time || ''}</strong> ${e.direction || e.event || ''}</li>`).join('')}</ol>
      <h3>Auth</h3>
      <div>
        <span class="badge badge-${m.dkim_pass ? 'success' : 'danger'}">DKIM ${m.dkim_pass ? 'pass' : 'fail/yok'}</span>
        <span class="badge badge-${m.spf_pass ? 'success' : 'danger'}">SPF ${m.spf_pass ? 'pass' : 'fail/yok'}</span>
        <span class="badge badge-${m.dmarc_pass ? 'success' : 'danger'}">DMARC ${m.dmarc_pass ? 'pass' : 'fail/yok'}</span>
      </div>`;

    // Ham Veri sekmesi: headers (raw_lines) + body empty state
    const raw = drawer.querySelector('[data-tab-panel="raw"]');
    raw.innerHTML = `
      <h3>Headers (raw)</h3>
      <pre class="json-viewer">${(payload.raw_lines || []).join('\n')}</pre>
      <h3>Body</h3>
      <div class="empty-state">
        <h4 class="empty-state-title">Mesaj içeriği Faz 3'te aktif</h4>
        <p class="empty-state-desc">Maildir parser eklendiğinde body burada görünür.</p>
      </div>`;

    // Aksiyon sekmesi: 4 disabled button + tooltip
    const action = drawer.querySelector('[data-tab-panel="action"]');
    action.innerHTML = `
      <p>Bu butonlar <strong>Faz 3+'da</strong> aktif olur.</p>
      <button disabled title="Faz 3+'da aktif olur">Forward</button>
      <button disabled title="Faz 3+'da aktif olur">Resend</button>
      <button disabled title="Faz 3+'da aktif olur">Bounce raporu</button>
      <button disabled title="Faz 3+'da aktif olur">Suppress</button>`;
  }

  // Resize handle
  function onResizeStart(e) {
    const drawer = document.getElementById(STATE.activeDrawerId);
    if (!drawer) return;
    STATE.dragging = true;
    STATE.startX = e.clientX;
    STATE.startW = drawer.getBoundingClientRect().width;
    document.addEventListener('mousemove', onResize);
    document.addEventListener('mouseup', onResizeEnd, { once: true });
  }
  function onResize(e) {
    if (!STATE.dragging) return;
    const drawer = document.getElementById(STATE.activeDrawerId);
    if (!drawer) return;
    const dx = STATE.startX - e.clientX;
    const w = Math.min(720, Math.max(400, STATE.startW + dx));
    drawer.style.width = w + 'px';
  }
  function onResizeEnd() {
    STATE.dragging = false;
    document.removeEventListener('mousemove', onResize);
  }

  // Global click handler — backdrop, close, tab, resize
  document.addEventListener('click', (e) => {
    if (e.target.matches('[data-drawer-close], [data-drawer-backdrop]')) close();
    const tabBtn = e.target.closest('[data-drawer-tabs] [data-tab]');
    if (tabBtn) switchTab(tabBtn.closest('.drawer'), tabBtn.dataset.tab);
  });
  document.addEventListener('mousedown', (e) => {
    if (e.target.matches('[data-drawer-resize]')) onResizeStart(e);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && STATE.activeDrawerId) close();
  });

  window.drawer = { open, close, switchTab };
})();
