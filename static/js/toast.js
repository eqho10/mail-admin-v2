/* static/js/toast.js — sağ üst toast stack, success 5s/error 10s, max 3, click dismiss */
(function () {
  const stack = () => document.querySelector('[data-component="toast"]');

  function show({type = 'info', message = '', duration}) {
    const el = stack();
    if (!el) return;
    if (el.children.length >= 3) el.removeChild(el.firstChild);
    const dur = duration ?? (type === 'error' ? 10000 : type === 'success' ? 5000 : 7000);
    const div = document.createElement('div');
    div.className = `toast toast-${type}`;
    div.textContent = message;
    div.addEventListener('click', () => div.remove());
    el.appendChild(div);
    setTimeout(() => div.remove(), dur);
  }

  window.toast = { show };
})();
