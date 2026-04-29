// static/js/sendas.js — Send-as test modal + drawer + polling

(function() {
  let currentRunId = null;
  let pollTimer = null;

  function renderModal() {
    const existing = document.getElementById('sendas-modal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id = 'sendas-modal';
    modal.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:1000;display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = `
      <div style="background:var(--card-bg);padding:1.5rem;border-radius:8px;min-width:400px;max-width:500px;">
        <h3 style="margin-top:0;">Send-as Test</h3>
        <form id="sendas-form">
          <label style="display:block;margin:.5rem 0;">
            <div>Kaynak</div>
            <select name="from" id="sendas-from" required style="width:100%;padding:.5rem;">
              <option value="">Yükleniyor...</option>
            </select>
          </label>
          <label style="display:block;margin:.5rem 0;">
            <div>Hedef</div>
            <input name="to" type="email" required style="width:100%;padding:.5rem;" placeholder="alici@ornek.com"/>
          </label>
          <label style="display:block;margin:.5rem 0;">
            <div>Konu (opsiyonel)</div>
            <input name="subject" style="width:100%;padding:.5rem;"/>
          </label>
          <label style="display:block;margin:.5rem 0;">
            <div>Gövde (opsiyonel)</div>
            <textarea name="body" style="width:100%;padding:.5rem;min-height:80px;"></textarea>
          </label>
          <div style="margin-top:1rem;text-align:right;">
            <button type="button" id="sendas-cancel" class="btn-ghost">İptal</button>
            <button type="submit" class="btn-primary">Gönder</button>
          </div>
        </form>
      </div>
    `;
    document.body.appendChild(modal);

    fetch('/api/mailboxes/all').then(r => r.ok ? r.json() : { mailboxes: [] }).then(data => {
      const sel = document.getElementById('sendas-from');
      const last = localStorage.getItem('mail-admin-last-test-from');
      sel.innerHTML = (data.mailboxes || []).map(m =>
        `<option value="${m}" ${m === last ? 'selected' : ''}>${m}</option>`
      ).join('');
    });

    document.getElementById('sendas-cancel').addEventListener('click', () => modal.remove());
    document.getElementById('sendas-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const payload = {
        from: fd.get('from'),
        to: fd.get('to'),
        subject: fd.get('subject') || undefined,
        body: fd.get('body') || undefined,
      };
      localStorage.setItem('mail-admin-last-test-from', payload.from);

      try {
        const r = await fetch('/api/sendas/dispatch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!r.ok) throw new Error('dispatch failed: ' + r.status);
        const result = await r.json();
        modal.remove();
        openSendAsDrawer(result);
      } catch (err) {
        alert('Gönderim hatası: ' + err.message);
      }
    });
  }

  function openSendAsDrawer(initial) {
    currentRunId = initial.run_id;
    const drawer = document.createElement('div');
    drawer.id = 'sendas-drawer';
    drawer.style.cssText = 'position:fixed;top:0;right:0;bottom:0;width:480px;background:var(--card-bg);border-left:1px solid var(--border);z-index:999;padding:1.5rem;overflow-y:auto;box-shadow:-4px 0 12px rgba(0,0,0,.15);';
    drawer.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <h3 style="margin:0;">Send-as test #${initial.run_id.slice(0, 8)}</h3>
        <button id="sendas-drawer-close" class="btn-ghost">✕</button>
      </div>
      <div id="sendas-timeline" style="margin-top:1rem;"></div>
      <div style="margin-top:1.5rem;">
        <button id="sendas-retry" class="btn-secondary">Tekrar test</button>
      </div>
    `;
    document.body.appendChild(drawer);

    document.getElementById('sendas-drawer-close').addEventListener('click', () => {
      if (pollTimer) clearInterval(pollTimer);
      drawer.remove();
    });
    document.getElementById('sendas-retry').addEventListener('click', () => {
      if (pollTimer) clearInterval(pollTimer);
      drawer.remove();
      window.openSendAsModal();
    });

    renderTimeline(initial);
    pollTimer = setInterval(async () => {
      try {
        const r = await fetch('/api/sendas/poll/' + currentRunId);
        if (!r.ok) return;
        const state = await r.json();
        renderTimeline(state);
        if (['verified', 'timeout', 'external_no_verify'].includes(state.status)) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      } catch (e) { /* keep polling */ }
    }, 2000);
  }

  function renderTimeline(state) {
    const c = document.getElementById('sendas-timeline');
    if (!c) return;
    const sentTime = state.sent_at ? new Date(state.sent_at + 'Z').toLocaleTimeString() : '';
    const arrivedTime = state.arrived_at ? new Date(state.arrived_at + 'Z').toLocaleTimeString() : '';
    const ar = state.auth_results || {};
    const chip = (label, val) => {
      const color = val === 'pass' ? '#22c55e' : val === 'fail' ? '#ef4444' : val ? '#eab308' : '#999';
      return `<span style="background:${color};color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;margin-right:4px;">${label}: ${val || 'yok'}</span>`;
    };

    let stepDelivery = '';
    if (state.status === 'verified') {
      stepDelivery = `<div>✅ Düştü (${arrivedTime})<br><small>${state.maildir_path || ''}</small><div style="margin-top:.5rem;">${chip('DKIM', ar.dkim)}${chip('SPF', ar.spf)}${chip('DMARC', ar.dmarc)}</div></div>`;
    } else if (state.status === 'timeout') {
      stepDelivery = '<div style="color:var(--warning);">⌛ Timeout (60s) — Maildir\'e düşmedi</div>';
    } else if (state.status === 'external_no_verify') {
      stepDelivery = '<div style="color:var(--text-secondary);">↗ Hedef external — teslim verify edilemez</div>';
    } else {
      stepDelivery = '<div>⏳ Maildir\'e düşmesi bekleniyor...</div>';
    }

    c.innerHTML = `
      <div style="border-left:3px solid var(--success);padding-left:1rem;margin-bottom:1rem;">
        ✅ Gönderildi (${sentTime})<br>
        <small>${state.from || ''} → ${state.to || ''}</small><br>
        <small>msgid: <code>${state.msgid || ''}</code></small>
      </div>
      <div style="border-left:3px solid var(--border);padding-left:1rem;">
        ${stepDelivery}
      </div>
    `;
  }

  // Public API
  window.openSendAsModal = renderModal;
})();
