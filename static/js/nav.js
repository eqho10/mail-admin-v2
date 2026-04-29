/* static/js/nav.js — sidebar collapse + active state + lucide icons render */
(function () {
  const KEY = 'mail-admin-sidebar';
  const shell = document.querySelector('.admin-shell');
  if (!shell) return;

  // Sayfa yüklenince localStorage'dan collapse durumunu al
  const stored = localStorage.getItem(KEY);
  if (stored === 'collapsed') shell.dataset.sidebar = 'collapsed';

  // Lucide icon render (bundled lucide.min.js base.html'de yüklü)
  if (window.lucide && typeof window.lucide.createIcons === 'function') {
    window.lucide.createIcons();
  }

  // Toggle helper
  window.toggleSidebar = function () {
    const next = shell.dataset.sidebar === 'collapsed' ? '' : 'collapsed';
    shell.dataset.sidebar = next;
    localStorage.setItem(KEY, next || 'expanded');
  };

  // "Son güncelleme" zaman damgası — her 30 saniyede güncelle
  function updateClock() {
    const el = document.querySelector('[data-last-updated]');
    if (!el) return;
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    const ss = String(now.getSeconds()).padStart(2, '0');
    el.textContent = 'Son güncelleme: ' + hh + ':' + mm + ':' + ss;
  }
  updateClock();
  setInterval(updateClock, 30 * 1000);
})();
