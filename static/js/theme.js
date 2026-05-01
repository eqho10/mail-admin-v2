(() => {
  const THEME_KEY  = 'mail-admin-theme';
  const ACCENT_KEY = 'mail-admin-accent';
  const ACCENTS = ['fluent', 'royal', 'bright']; // K15 v2.3 — 3 Office 365 blue variants
  const root = document.documentElement;

  // ============ THEME (light/dark) ============
  const storedTheme = localStorage.getItem(THEME_KEY);
  if (storedTheme === 'dark' || storedTheme === 'light') {
    root.setAttribute('data-theme', storedTheme);
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    root.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
  }
  window.toggleTheme = () => {
    const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    localStorage.setItem(THEME_KEY, next);
  };

  // ============ ACCENT (fluent/royal/bright) ============
  const storedAccent = localStorage.getItem(ACCENT_KEY);
  const accent = ACCENTS.indexOf(storedAccent) >= 0 ? storedAccent : 'fluent';
  if (accent !== 'fluent') root.setAttribute('data-accent', accent);
  // Public API: window.cycleAccent()
  window.cycleAccent = () => {
    const cur = root.getAttribute('data-accent') || 'fluent';
    const next = ACCENTS[(ACCENTS.indexOf(cur) + 1) % ACCENTS.length];
    if (next === 'fluent') root.removeAttribute('data-accent');
    else root.setAttribute('data-accent', next);
    localStorage.setItem(ACCENT_KEY, next);
    // Update button visuals immediately
    const btn = document.getElementById('accent-toggle');
    if (btn) {
      btn.dataset.current = next;
      btn.title = 'Vurgu rengi: ' + (next === 'fluent' ? 'Fluent' : next === 'royal' ? 'Royal' : 'Bright');
    }
  };
})();
