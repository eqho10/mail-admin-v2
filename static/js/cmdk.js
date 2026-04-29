/**
 * static/js/cmdk.js — Komut paleti (Cmd+K / Ctrl+K)
 *
 * Registry API:
 * @typedef {Object} CmdkAction
 * @property {string} id - Benzersiz aksiyon ID
 * @property {string} label - Görünen başlık
 * @property {string} group - "Aksiyon" | "Gezinme" | "Domain" | "Mailbox" | "Mesaj"
 * @property {() => void|Promise<void>} run - Aksiyon fonksiyonu
 * @property {(ctx: {page: string}) => boolean} [when] - Görünürlük predikatı (opsiyonel)
 *
 * Faz 3+ feature'lar şöyle ekler:
 *   window.cmdk.registerAction({
 *     id: 'reputation-refresh', label: 'Reputation refresh', group: 'Aksiyon',
 *     run: () => fetch('/api/reputation', {method:'POST'}),
 *     when: ({page}) => page === 'overview'
 *   });
 */
(function () {
  const ACTIONS = [];
  const STATE = { open: false, query: '', selectedIdx: 0, items: [] };

  // 13 sabit aksiyon (Faz 2)
  const PAGE = () => document.querySelector('[data-page]')?.dataset.page || '';
  const onActivityPage = ({page}) => page === 'activity';
  const builtIn = [
    { id:'theme.toggle', label:'Tema değiştir', group:'Aksiyon', run: () => window.toggleTheme && window.toggleTheme() },
    { id:'logout', label:'Çıkış yap', group:'Aksiyon', run: () => fetch('/logout',{method:'POST'}).then(()=>location.href='/login') },
    { id:'test-mail', label:'Test mail at', group:'Aksiyon', run: () => fetch('/api/test-mail',{method:'POST'}).then(r=>r.ok && window.toast.show({type:'success',message:'Test mail gönderildi'})) },
    { id:'sse.toggle', label:'Real-time tail aç/kapa', group:'Aksiyon', run: () => window.sse && window.sse.toggle('activity'), when: onActivityPage },
    { id:'nav.overview', label:"Genel Bakış'a git", group:'Gezinme', run: () => location.href='/' },
    { id:'nav.activity', label:"Aktivite'ye git", group:'Gezinme', run: () => location.href='/aktivite' },
    { id:'nav.queue', label:"Kuyruk'a git", group:'Gezinme', run: () => location.href='/kuyruk' },
    { id:'nav.domains', label:"Domain'lere git", group:'Gezinme', run: () => location.href='/domain' },
    { id:'nav.mailboxes', label:"Mailbox'lara git", group:'Gezinme', run: () => location.href='/mailbox' },
    { id:'nav.deliverability', label:"Deliverability'e git", group:'Gezinme', run: () => location.href='/deliverability' },
    { id:'nav.quarantine', label:"Quarantine'e git", group:'Gezinme', run: () => location.href='/quarantine' },
    { id:'nav.settings', label:"Ayarlar'a git", group:'Gezinme', run: () => location.href='/ayarlar' },
    { id:'dict.add', label:'Sözlüğe çeviri ekle', group:'Aksiyon', run: () => null, disabled: true, tooltip:"Faz 5'te aktif olur" },
  ];

  function registerAction(action) {
    if (!action.id || !action.label || !action.group) throw new Error('cmdk: id/label/group zorunlu');
    if (ACTIONS.find(a => a.id === action.id)) return;
    ACTIONS.push(action);
  }
  builtIn.forEach(registerAction);

  // Dynamic gruplar — açılışta fetch, cache
  const DYNAMIC_CACHE = { domains: null, mailboxes: null };
  async function loadDynamic() {
    if (!DYNAMIC_CACHE.domains) {
      try {
        const r = await fetch('/api/domains'); DYNAMIC_CACHE.domains = await r.json();
      } catch { DYNAMIC_CACHE.domains = []; }
    }
    if (!DYNAMIC_CACHE.mailboxes) {
      try {
        const r = await fetch('/api/mailboxes'); DYNAMIC_CACHE.mailboxes = await r.json();
      } catch { DYNAMIC_CACHE.mailboxes = []; }
    }
  }

  function buildItems(query) {
    const ctx = { page: PAGE() };
    const q = (query || '').toLowerCase();
    const items = [];
    ACTIONS.forEach(a => {
      if (a.when && !a.when(ctx)) return;
      if (q && !a.label.toLowerCase().includes(q)) return;
      items.push({...a});
    });
    // Domain dynamic
    (DYNAMIC_CACHE.domains || []).slice(0, 50).forEach(d => {
      const label = d.domain || d;
      if (q && !label.toLowerCase().includes(q)) return;
      items.push({ id:`domain.${label}`, label, group:'Domain', run:()=>location.href=`/domain?d=${label}` });
    });
    // Mailbox dynamic
    (DYNAMIC_CACHE.mailboxes || []).slice(0, 50).forEach(mb => {
      const label = mb.email || mb;
      if (q && !label.toLowerCase().includes(q)) return;
      items.push({ id:`mailbox.${label}`, label, group:'Mailbox', run:()=>location.href=`/mailbox?q=${encodeURIComponent(label)}` });
    });
    return items;
  }

  function render() {
    const results = document.querySelector('[data-cmdk-results]');
    if (!results) return;
    STATE.items = buildItems(STATE.query);
    let html = '';
    let lastGroup = null;
    STATE.items.forEach((it, idx) => {
      if (it.group !== lastGroup) {
        html += `<div class="cmdk-group-header">${it.group}</div>`;
        lastGroup = it.group;
      }
      const sel = idx === STATE.selectedIdx ? 'aria-selected="true"' : '';
      const dis = it.disabled ? 'aria-disabled="true"' : '';
      const title = it.tooltip ? `title="${it.tooltip}"` : '';
      html += `<div class="cmdk-item" role="option" data-cmdk-idx="${idx}" ${sel} ${dis} ${title}>${it.label}</div>`;
    });
    results.innerHTML = html || '<div class="cmdk-item">Sonuç yok</div>';
  }

  async function open() {
    await loadDynamic();
    document.querySelector('[data-component="command-palette"]').hidden = false;
    STATE.open = true;
    STATE.query = ''; STATE.selectedIdx = 0;
    const input = document.querySelector('[data-cmdk-search]');
    input.value = ''; input.focus();
    render();
  }

  function close() {
    document.querySelector('[data-component="command-palette"]').hidden = true;
    STATE.open = false;
  }

  function runSelected() {
    const it = STATE.items[STATE.selectedIdx];
    if (!it || it.disabled) return;
    close();
    Promise.resolve(it.run()).catch(err => window.toast && window.toast.show({type:'error',message:String(err)}));
  }

  // Keyboard handler — capture phase, preventDefault Cmd+K çakışmasını engeller
  document.addEventListener('keydown', (e) => {
    const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k';
    if (isCmdK) {
      e.preventDefault();
      STATE.open ? close() : open();
      return;
    }
    if (!STATE.open) return;
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); STATE.selectedIdx = Math.min(STATE.items.length-1, STATE.selectedIdx+1); render(); }
    else if (e.key === 'ArrowUp')   { e.preventDefault(); STATE.selectedIdx = Math.max(0, STATE.selectedIdx-1); render(); }
    else if (e.key === 'Enter')     { e.preventDefault(); runSelected(); }
  }, true);

  document.addEventListener('input', (e) => {
    if (e.target.matches('[data-cmdk-search]')) {
      STATE.query = e.target.value;
      STATE.selectedIdx = 0;
      render();
    }
  });
  document.addEventListener('click', (e) => {
    if (e.target.matches('[data-cmdk-trigger]')) open();
    if (e.target.matches('[data-cmdk-backdrop]')) close();
    const item = e.target.closest('[data-cmdk-idx]');
    if (item) { STATE.selectedIdx = parseInt(item.dataset.cmdkIdx); runSelected(); }
  });

  window.cmdk = { open, close, registerAction };
})();
