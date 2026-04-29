/* static/js/drawer.js — open/close/resize + sekme switch + Activity payload binding */
(function () {
  const STATE = { activeDrawerId: null, dragging: false, startX: 0, startW: 0 };

  /* ── Body fetch helpers ─────────────────────────────────────────────────── */

  async function loadMessageBody(msgid, recipient) {
    try {
      const r = await fetch(`/api/message/${encodeURIComponent(msgid)}/body?recipient=${encodeURIComponent(recipient)}`);
      if (r.status === 404) return { error: 'maildir_not_found' };
      if (!r.ok) return { error: 'fetch_failed' };
      return await r.json();
    } catch (e) {
      return { error: 'network' };
    }
  }

  function renderBodyTab(container, msgid, recipient, rawLines, onAuthReady) {
    container.innerHTML = '<div style="padding:1rem;color:var(--text-secondary);">Yükleniyor...</div>';
    loadMessageBody(msgid, recipient).then(body => {
      if (body.error === 'maildir_not_found') {
        container.innerHTML = '<div style="padding:1rem;color:var(--text-secondary);">Maildir\'de yok (outbound veya silinmiş mesaj).</div>'
          + '<details><summary>Ham log satırları</summary><pre>' + (rawLines || []).join('\n') + '</pre></details>';
        return;
      }
      if (body.error) {
        container.innerHTML = '<div style="padding:1rem;color:var(--text-error);">Yüklenemedi: ' + body.error + '</div>';
        return;
      }

      /* Upgrade auth chips with real values from body response (Option A) */
      if (onAuthReady && body.auth_results) {
        onAuthReady(body.auth_results);
      }

      const escape = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      const defaultMode = body.html ? 'html' : 'plain';
      const attachmentsHtml = (body.attachments || []).length === 0
        ? '<div style="padding:.5rem;color:var(--text-secondary);">Ek yok</div>'
        : '<ul style="list-style:none;padding:0;">' + (body.attachments || []).map(a =>
            '<li style="padding:.25rem 0;">&#128206; <a href="/api/message/' + encodeURIComponent(msgid) + '/attachment/' + a.idx + '?recipient=' + encodeURIComponent(recipient) + '">' + escape(a.filename) + '</a> <span style="color:var(--text-secondary);font-size:12px;">(' + escape(a.content_type) + ', ' + a.size + 'B)</span></li>'
          ).join('') + '</ul>';

      container.innerHTML =
        '<div class="body-toggle" style="display:flex;gap:.5rem;margin-bottom:.75rem;flex-wrap:wrap;">'
        + (body.html ? '<button class="pill active" data-mode="html">HTML</button>' : '')
        + (body.plain ? ('<button class="pill' + (defaultMode === 'plain' ? ' active' : '') + '" data-mode="plain">Plain</button>') : '')
        + '<button class="pill' + ((!body.html && !body.plain) ? ' active' : '') + '" data-mode="raw">Raw</button>'
        + '</div>'
        + '<div id="body-content"></div>'
        + '<div style="margin-top:1rem;">'
        + '<h4 style="margin:.25rem 0;">Ekler</h4>'
        + attachmentsHtml
        + '</div>';

      function showMode(mode) {
        const c = container.querySelector('#body-content');
        if (mode === 'html' && body.html) {
          c.innerHTML = '<iframe sandbox referrerpolicy="no-referrer" srcdoc="' + escape(body.html) + '" style="width:100%;height:400px;border:1px solid var(--border);"></iframe>';
        } else if (mode === 'plain' && body.plain) {
          c.innerHTML = '<pre style="white-space:pre-wrap;background:var(--code-bg);padding:.75rem;border-radius:4px;">' + escape(body.plain) + '</pre>';
        } else {
          c.innerHTML = '<pre style="white-space:pre-wrap;background:var(--code-bg);padding:.75rem;border-radius:4px;">' + escape(body.raw || (rawLines || []).join('\n')) + '</pre>';
        }
        container.querySelectorAll('.body-toggle .pill').forEach(function(b) {
          b.classList.toggle('active', b.dataset.mode === mode);
        });
      }
      container.querySelectorAll('.body-toggle .pill').forEach(function(b) {
        b.addEventListener('click', function() { showMode(b.dataset.mode); });
      });
      showMode(defaultMode);
    });
  }

  function renderAuthChips(authResults) {
    var ar = authResults || {};
    function colorFor(val) {
      if (val === 'pass') return 'var(--success)';
      if (val === 'fail') return 'var(--danger)';
      if (val) return 'var(--warning)';
      return 'var(--text-secondary)';
    }
    return ['dkim', 'spf', 'dmarc'].map(function(k) {
      var v = ar[k];
      return '<span class="chip" style="background:' + colorFor(v) + ';color:#fff;padding:2px 8px;border-radius:4px;margin-right:4px;font-size:12px;">' + k.toUpperCase() + ': ' + (v || 'yok') + '</span>';
    }).join('');
  }

  /* ── Drawer open/close ──────────────────────────────────────────────────── */

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
    // Detay sekmesi: envelope + timeline + DKIM/SPF/DMARC chips (placeholder → upgraded after body fetch)
    const detail = drawer.querySelector('[data-tab-panel="detail"]');
    const m = payload.message || {};
    const events = payload.events || [];
    const toDisplay = Array.isArray(m.to) ? m.to.join(', ') : (m.to || '—');

    detail.innerHTML =
      '<h3>Envelope</h3>'
      + '<dl class="envelope">'
      + '<dt>Message-ID</dt><dd><code>' + (m.msgid || '—') + '</code></dd>'
      + '<dt>Kimden</dt><dd>' + (m.from || '—') + '</dd>'
      + '<dt>Kime</dt><dd>' + toDisplay + '</dd>'
      + '<dt>Konu</dt><dd>' + (m.subject || '—') + '</dd>'
      + '<dt>Boyut</dt><dd>' + (m.size || '—') + ' bytes</dd>'
      + '<dt>Relay</dt><dd>' + (m.relay || '—') + '</dd>'
      + '</dl>'
      + '<h3>Zaman çizelgesi</h3>'
      + '<ol class="timeline">' + events.map(function(e) { return '<li><strong>' + (e.time || '') + '</strong> ' + (e.direction || e.event || '') + '</li>'; }).join('') + '</ol>'
      + '<h3>Auth</h3>'
      + '<div id="auth-chips">' + renderAuthChips({ dkim: null, spf: null, dmarc: null }) + '</div>';

    // Ham Veri sekmesi: 3-mode body toggle + attachment list
    const raw = drawer.querySelector('[data-tab-panel="raw"]');
    raw.innerHTML = '<div id="body-tab-container"></div>';
    const bodyContainer = raw.querySelector('#body-tab-container');
    const recipient = Array.isArray(m.to) ? (m.to[0] || '') : (m.to || '');

    // Option A: upgrade auth chips once body endpoint responds with real auth_results
    function onAuthReady(authResults) {
      const authChipsEl = detail.querySelector('#auth-chips');
      if (authChipsEl) {
        authChipsEl.innerHTML = renderAuthChips(authResults);
      }
    }

    renderBodyTab(bodyContainer, m.msgid || '', recipient, payload.raw_lines, onAuthReady);

    // Aksiyon sekmesi: 4 disabled buttons + tooltip
    const action = drawer.querySelector('[data-tab-panel="action"]');
    action.innerHTML =
      '<p>Bu butonlar <strong>Faz 3+\'da</strong> aktif olur.</p>'
      + '<button disabled title="Faz 3+\'da aktif olur">Forward</button>'
      + '<button disabled title="Faz 3+\'da aktif olur">Resend</button>'
      + '<button disabled title="Faz 3+\'da aktif olur">Bounce raporu</button>'
      + '<button disabled title="Faz 3+\'da aktif olur">Suppress</button>';
  }

  /* ── Resize handle ──────────────────────────────────────────────────────── */

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

  /* ── Global event delegation ────────────────────────────────────────────── */

  document.addEventListener('click', function(e) {
    if (e.target.matches('[data-drawer-close], [data-drawer-backdrop]')) close();
    const tabBtn = e.target.closest('[data-drawer-tabs] [data-tab]');
    if (tabBtn) switchTab(tabBtn.closest('.drawer'), tabBtn.dataset.tab);
  });
  document.addEventListener('mousedown', function(e) {
    if (e.target.matches('[data-drawer-resize]')) onResizeStart(e);
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && STATE.activeDrawerId) close();
  });

  window.drawer = { open, close, switchTab };
})();
