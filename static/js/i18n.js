// /static/js/i18n.js — Mail Admin v2 — basit TR/EN dil dictionary
// Kullanım:
//   <span data-i18n="nav.overview">Genel Bakış</span>  → tr ise "Genel Bakış", en ise "Overview"
//   localStorage'da "mav2_lang" tutulur. Toggle butonu sidebar.html'de.
(function () {
  const KEY = "mav2_lang";

  // En → TR + diğer dil mapping. TR varsayılan.
  const STRINGS = {
    tr: {
      "brand": 'Mail Admin <span class="sidebar-brand-version">v2</span>',
      "sec.monitoring":   "İzleme",
      "sec.recipients":   "Alıcılar",
      "sec.mailflow":     "Posta Akışı",
      "sec.admin":        "Yönetim",
      "nav.overview":         "Genel Bakış",
      "nav.activity":         "Aktivite",
      "nav.queue":            "Kuyruk",
      "nav.deliverability":   "Teslim Edilebilirlik",
      "nav.reports":          "Raporlar",
      "nav.mailboxes":        "Mail Kutuları",
      "nav.domains":          "Domain'ler",
      "nav.suppression":      "Engelli Adresler",
      "nav.quarantine":       "Karantina",
      "nav.blacklist":        "Kara Liste",
      "nav.filters":          "Filtreler",
      "nav.settings":         "Ayarlar",
      // page titles
      "page.overview":        "Genel Bakış",
      "page.activity":        "Aktivite",
      "page.queue":           "Kuyruk",
      "page.deliverability":  "Teslim Edilebilirlik",
      "page.reports":         "Raporlar",
      "page.mailboxes":       "Mail Kutuları",
      "page.domains":         "Domain'ler",
      "page.suppression":     "Engelli Adresler",
      "page.quarantine":      "Karantina",
      "page.blacklist":       "Kara Liste",
      "page.filters":         "Filtreler",
      "page.settings":        "Ayarlar",
    },
    en: {
      "brand": 'Mail Admin <span class="sidebar-brand-version">v2</span>',
      "sec.monitoring":   "Monitoring",
      "sec.recipients":   "Recipients",
      "sec.mailflow":     "Mail Flow",
      "sec.admin":        "Administration",
      "nav.overview":         "Overview",
      "nav.activity":         "Activity",
      "nav.queue":            "Queue",
      "nav.deliverability":   "Deliverability",
      "nav.reports":          "Reports",
      "nav.mailboxes":        "Mailboxes",
      "nav.domains":          "Domains",
      "nav.suppression":      "Suppression",
      "nav.quarantine":       "Quarantine",
      "nav.blacklist":        "Blacklist",
      "nav.filters":          "Filters",
      "nav.settings":         "Settings",
      "page.overview":        "Overview",
      "page.activity":        "Activity",
      "page.queue":           "Queue",
      "page.deliverability":  "Deliverability",
      "page.reports":         "Reports",
      "page.mailboxes":       "Mailboxes",
      "page.domains":         "Domains",
      "page.suppression":     "Suppression",
      "page.quarantine":      "Quarantine",
      "page.blacklist":       "Blacklist",
      "page.filters":         "Filters",
      "page.settings":        "Settings",
    },
  };

  function currentLang() {
    return localStorage.getItem(KEY) || "tr";
  }

  function applyLang(lang) {
    const dict = STRINGS[lang] || STRINGS.tr;
    document.documentElement.setAttribute("lang", lang);
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      const key = el.getAttribute("data-i18n");
      if (dict[key] != null) el.innerHTML = dict[key];
    });
    // page <title>
    const pageKey = (document.querySelector(".admin-shell")?.dataset.page) || "";
    if (pageKey && dict["page." + pageKey]) {
      document.title = dict["page." + pageKey] + " · Mail Admin";
    }
    // Toggle label
    const tlbl = document.getElementById("lang-toggle-label");
    if (tlbl) tlbl.textContent = lang.toUpperCase();
  }

  function setLang(lang) {
    localStorage.setItem(KEY, lang);
    applyLang(lang);
  }

  function init() {
    applyLang(currentLang());
    const btn = document.getElementById("lang-toggle");
    if (btn) {
      btn.addEventListener("click", function () {
        setLang(currentLang() === "tr" ? "en" : "tr");
      });
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
  window.MAv2I18n = { setLang: setLang, currentLang: currentLang, apply: applyLang };
})();
