/**
 * Lightweight client-side i18n for DNS Debug Web UI.
 * Exposes window.DnsDebugI18n — no external dependencies.
 */
(function (global) {
  "use strict";

  const DEFAULT_LOCALE = "en";
  const LOCALE_MAP = { en: "en-US", ru: "ru-RU" };

  let cfg = {
    enabled: true,
    defaultLang: DEFAULT_LOCALE,
    supportedLangs: ["en", "ru"],
    localeStorage: true,
    storageKey: "dns-debug-lang",
    lang: DEFAULT_LOCALE,
    messages: {},
    basePath: "/dns-debug",
  };
  let fallbackMessages = {};
  let onChangeCallback = null;

  function normalizeLang(code) {
    return String(code || DEFAULT_LOCALE).toLowerCase().split("-")[0];
  }

  function intlLocale() {
    return LOCALE_MAP[normalizeLang(cfg.lang)] || cfg.lang;
  }

  function getNested(obj, key) {
    if (!obj || !key) return undefined;
    let node = obj;
    const parts = key.split(".");
    for (let i = 0; i < parts.length; i++) {
      if (node == null || typeof node !== "object" || !(parts[i] in node)) return undefined;
      node = node[parts[i]];
    }
    return node;
  }

  function interpolate(text, params) {
    if (!params || typeof text !== "string") return text;
    return text.replace(/\{\{(\w+)\}\}/g, function (_, name) {
      return params[name] != null ? String(params[name]) : "";
    });
  }

  function lookup(key, bundle) {
    const val = getNested(bundle, key);
    if (typeof val === "string") return val;
    return undefined;
  }

  function t(key, params) {
    if (!key) return "";
    let text = lookup(key, cfg.messages);
    if (text === undefined) text = lookup(key, fallbackMessages);
    if (text === undefined) {
      if (global.console && global.console.warn) {
        global.console.warn("[i18n] missing key:", key);
      }
      return lookup(key, fallbackMessages) || "";
    }
    return interpolate(text, params);
  }

  function formatNumber(value, options) {
    const n = Number(value);
    if (Number.isNaN(n)) return String(value ?? "—");
    return new Intl.NumberFormat(intlLocale(), options || {}).format(n);
  }

  function formatPercent(value, digits) {
    const n = Number(value);
    if (Number.isNaN(n)) return "—";
    const d = digits != null ? digits : 1;
    return new Intl.NumberFormat(intlLocale(), {
      style: "percent",
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    }).format(n);
  }

  function formatDateTime(value) {
    if (!value) return "—";
    const d = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return new Intl.DateTimeFormat(intlLocale(), {
      dateStyle: "short",
      timeStyle: "medium",
    }).format(d);
  }

  function formatDurationMs(ms) {
    const n = Number(ms);
    if (Number.isNaN(n)) return "—";
    return formatNumber(n, { maximumFractionDigits: 1 }) + " ms";
  }

  function applyDom(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach(function (el) {
      const key = el.getAttribute("data-i18n");
      if (key) el.textContent = t(key);
    });
    scope.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      const key = el.getAttribute("data-i18n-title");
      if (key) el.setAttribute("title", t(key));
    });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      const key = el.getAttribute("data-i18n-placeholder");
      if (key) el.setAttribute("placeholder", t(key));
    });
    scope.querySelectorAll("[data-i18n-aria-label]").forEach(function (el) {
      const key = el.getAttribute("data-i18n-aria-label");
      if (key) el.setAttribute("aria-label", t(key));
    });
    scope.querySelectorAll("option[data-i18n]").forEach(function (el) {
      const key = el.getAttribute("data-i18n");
      if (key) el.textContent = t(key);
    });
    scope.querySelectorAll("label[data-i18n]").forEach(function (el) {
      const key = el.getAttribute("data-i18n");
      if (key) {
        const child = el.querySelector("input, select");
        if (child && el.childNodes.length > 1) {
          const textNode = Array.from(el.childNodes).find(function (n) {
            return n.nodeType === Node.TEXT_NODE && n.textContent.trim();
          });
          if (textNode) textNode.textContent = t(key) + " ";
        } else {
          el.textContent = t(key);
        }
      }
    });
  }

  function resolveInitialLang(initCfg) {
    const supported = (initCfg.supportedLangs || ["en"]).map(normalizeLang);
    if (!initCfg.enabled) return "en";

    if (initCfg.localeStorage !== false) {
      try {
        const stored = localStorage.getItem(initCfg.storageKey || "dns-debug-lang");
        if (stored && supported.indexOf(normalizeLang(stored)) >= 0) {
          return normalizeLang(stored);
        }
      } catch (e) { /* ignore */ }
    }

    const fromServer = normalizeLang(initCfg.lang);
    if (supported.indexOf(fromServer) >= 0) return fromServer;
    const def = normalizeLang(initCfg.defaultLang);
    if (supported.indexOf(def) >= 0) return def;
    return "en";
  }

  async function fetchLocale(lang) {
    const base = (cfg.basePath || "/dns-debug").replace(/\/$/, "");
    const res = await fetch(base + "/static/i18n/" + lang + ".json");
    if (!res.ok) throw new Error("locale " + lang + " " + res.status);
    return res.json();
  }

  async function setLang(lang) {
    const next = normalizeLang(lang);
    const supported = (cfg.supportedLangs || []).map(normalizeLang);
    if (supported.indexOf(next) < 0) return;

    if (next === "en" && Object.keys(fallbackMessages).length) {
      cfg.messages = fallbackMessages;
    } else if (normalizeLang(next) === normalizeLang(cfg.lang) && cfg.messages && Object.keys(cfg.messages).length) {
      /* keep current bundle */
    } else {
      cfg.messages = await fetchLocale(next);
    }

    cfg.lang = next;
    document.documentElement.setAttribute("lang", next);
    document.title = t("dashboard.page_title");

    if (cfg.localeStorage !== false) {
      try {
        localStorage.setItem(cfg.storageKey || "dns-debug-lang", next);
      } catch (e) { /* ignore */ }
    }

    applyDom();
    updateLangSwitcher();
    if (typeof onChangeCallback === "function") onChangeCallback(next);
  }

  function updateLangSwitcher() {
    const wrap = document.getElementById("lang-switcher");
    if (!wrap) return;
    wrap.querySelectorAll(".lang-btn").forEach(function (btn) {
      const active = normalizeLang(btn.dataset.lang) === normalizeLang(cfg.lang);
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function initLangSwitcher() {
    const wrap = document.getElementById("lang-switcher");
    if (!wrap || !cfg.enabled) return;
    wrap.querySelectorAll(".lang-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setLang(btn.dataset.lang);
      });
    });
    updateLangSwitcher();
  }

  function onLangChange(fn) {
    onChangeCallback = fn;
  }

  function init(initCfg) {
    cfg = Object.assign({}, cfg, initCfg || {});
    cfg.basePath = cfg.basePath || (global.DNS_DEBUG_UI && global.DNS_DEBUG_UI.basePath) || "/dns-debug";
    if (initCfg && initCfg.fallbackMessages) {
      fallbackMessages = initCfg.fallbackMessages;
    } else if (initCfg && initCfg.messages && normalizeLang(initCfg.lang || "en") === "en") {
      fallbackMessages = initCfg.messages;
    }
    const resolved = resolveInitialLang(cfg);
    cfg.lang = resolved;
    if (initCfg && initCfg.messages && Object.keys(initCfg.messages).length) {
      cfg.messages = initCfg.messages;
    }
    if (!fallbackMessages || !Object.keys(fallbackMessages).length) {
      fallbackMessages = cfg.messages || {};
    }
    if (normalizeLang(resolved) !== normalizeLang(initCfg && initCfg.lang)) {
      /* client storage may override server preload — fetch if needed */
    }
    document.documentElement.setAttribute("lang", cfg.lang);
    document.title = t("dashboard.page_title");
    applyDom();
    initLangSwitcher();
  }

  async function bootstrap(initCfg) {
    init(initCfg);
    const preloaded = normalizeLang(initCfg && initCfg.lang);
    const resolved = normalizeLang(cfg.lang);
    if (resolved !== preloaded) {
      await setLang(resolved);
    }
  }

  global.DnsDebugI18n = {
    init: init,
    bootstrap: bootstrap,
    t: t,
    getLang: function () { return cfg.lang; },
    isEnabled: function () { return cfg.enabled !== false; },
    setLang: setLang,
    onLangChange: onLangChange,
    applyDom: applyDom,
    formatNumber: formatNumber,
    formatPercent: formatPercent,
    formatDateTime: formatDateTime,
    formatDurationMs: formatDurationMs,
    translateSignal: function (signal) {
      if (!signal || !signal.code) return signal && signal.message ? signal.message : "";
      const raw = signal.params || {};
      const params = Object.assign({}, raw);
      if (raw.error_rate != null) params.error_rate = formatPercent(raw.error_rate);
      if (raw.garbage_ratio != null) params.garbage_ratio = formatPercent(raw.garbage_ratio);
      if (raw.threshold != null && raw.garbage_ratio != null) {
        params.threshold = formatPercent(raw.threshold, 0);
      } else if (raw.threshold != null && raw.error_rate != null) {
        params.threshold = formatPercent(raw.threshold);
      }
      if (raw.p95_ms != null) params.p95_ms = formatNumber(raw.p95_ms, { maximumFractionDigits: 0 });
      if (raw.error_qps != null) params.error_qps = formatNumber(raw.error_qps, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
      const key = "signals." + signal.code;
      const translated = t(key, params);
      if (translated) return translated;
      return signal.message || "";
    },
    translateWarning: function (code, params) {
      return t("history.warnings." + code, params) || code;
    },
    translateCompareNote: function (note) {
      if (!note) return "";
      const map = {
        "missing value": "compare.note.missing_value",
        "baseline is zero": "compare.note.baseline_zero",
      };
      return t(map[note]) || note;
    },
    translateLevel: function (level) {
      return t("common.level." + level) || level;
    },
    translateViewMode: function (mode) {
      return t("filters.mode." + mode) || mode;
    },
    translateDataSource: function (source) {
      return t("history.data_source." + source) || source;
    },
  };
})(window);
