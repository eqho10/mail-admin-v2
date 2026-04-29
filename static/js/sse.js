/* static/js/sse.js — Activity tail toggle + reconnect backoff (1,2,5,15,30s cap) */
(function () {
  const BACKOFF = [1000, 2000, 5000, 15000, 30000];
  const STATE = { eventSource: null, attempts: 0, topic: null, button: null };

  function start(topic) {
    if (STATE.eventSource) STATE.eventSource.close();
    STATE.topic = topic;
    const url = `/api/events/stream?topic=${encodeURIComponent(topic)}`;
    const es = new EventSource(url);
    STATE.eventSource = es;
    es.addEventListener('connected', () => {
      STATE.attempts = 0;
      if (window.toast) window.toast.show({type:'success', message:`Real-time ${topic} bağlandı`});
      if (STATE.button) STATE.button.textContent = `■ Real-time tail kapat`;
    });
    es.addEventListener('line', (ev) => {
      try {
        const data = JSON.parse(ev.data);
        const tbody = document.querySelector('#activity-table-wrapper tbody');
        if (!tbody) return;
        const tr = document.createElement('tr');
        tr.dataset.rowId = data.msgid || '';
        tr.innerHTML = `<td>${data.time||''}</td><td>${data.from||'—'}</td><td>${data.to||'—'}</td><td>${data.subject||'—'}</td><td><span class="badge badge-info">live</span></td>`;
        tbody.insertBefore(tr, tbody.firstChild);
      } catch (e) { /* parse fail — atla */ }
    });
    es.onerror = () => {
      es.close();
      STATE.eventSource = null;
      const wait = BACKOFF[Math.min(STATE.attempts, BACKOFF.length - 1)];
      STATE.attempts++;
      if (window.toast) window.toast.show({type:'error', message:`SSE bağlantı düştü, ${wait/1000}s sonra tekrar denenecek`});
      setTimeout(() => { if (STATE.topic) start(STATE.topic); }, wait);
    };
  }

  function stop() {
    if (STATE.eventSource) STATE.eventSource.close();
    STATE.eventSource = null;
    STATE.topic = null;
    STATE.attempts = 0;
    if (STATE.button) STATE.button.textContent = `▶ Real-time tail aç`;
  }

  function toggle(topic) {
    if (STATE.eventSource) stop();
    else start(topic);
  }

  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('tail-toggle');
    if (!btn) return;
    STATE.button = btn;
    btn.addEventListener('click', () => toggle(btn.dataset.sseTopic || 'activity'));
  });

  window.sse = { start, stop, toggle };
})();
