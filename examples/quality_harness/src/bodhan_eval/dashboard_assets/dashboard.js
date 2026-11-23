"use strict";
(function () {
  var DATA = JSON.parse(document.getElementById("data").textContent || "{}");
  var ITEMS = DATA.items || [];
  var SUMMARY = DATA.summary || {};
  var CAL = DATA.calibration || {};
  var DIFF = DATA.diff || null;
  var SEV_RANK = { minor: 0, major: 1, critical: 2 };
  var TARGETS = { recall: 0.8, false_alarm_rate: 0.1, localization: 0.7, category_accuracy: 0.7, judge: 0.9 };

  // ---------- helpers ----------
  function $(sel, root) { return (root || document).querySelector(sel); }
  function ce(tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "class") e.className = attrs[k];
      else if (k === "text") e.textContent = attrs[k];
      else if (k === "html") e.textContent = attrs[k]; // never raw html
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function fmtPct(x) { return x == null ? "—" : (x * 100).toFixed(0) + "%"; }
  function fmtNum(x) { return x == null ? "—" : (+x).toFixed(2); }

  // ---------- theme ----------
  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem("qh-theme"); } catch (e) {}
    if (!saved) saved = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "midnight" : "paper";
    document.documentElement.setAttribute("data-theme", saved);
  }
  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme");
    var next = cur === "midnight" ? "paper" : "midnight";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("qh-theme", next); } catch (e) {}
  }

  // ---------- header ----------
  function renderHeader() {
    var t = SUMMARY.totals || {};
    var cost = (SUMMARY.cost || {}).logical_total_inr;
    var jc = SUMMARY.judge_consistency || {};
    var meta = $("#strip-meta");
    var bits = [
      ["items", t.items], ["clean", t.clean], ["flagged", t.flagged],
      ["uncertain", t.uncertain], ["skipped", t.skipped]
    ];
    bits.forEach(function (b) {
      if (b[1] == null) return;
      meta.appendChild(ce("span", {}, [b[0] + " ", ce("b", { text: String(b[1]) })]));
    });
    if (cost != null) meta.appendChild(ce("span", {}, ["cost ", ce("b", { text: "₹" + cost })]));
    var langs = Object.keys(SUMMARY.by_language || {}).join(", ");
    $("#run-sub").textContent = "The proofreader's desk — " + (langs || "no languages");
    $("#foot-meta").textContent = jc.measured
      ? "judge consistency " + fmtPct(jc.verdict_agreement) + " verdict / " + fmtPct(jc.category_agreement) + " category"
      : "judge consistency not measured";
  }

  // ---------- KPIs ----------
  function kpi(label, value, target, higherBetter) {
    var met = value == null ? null : (higherBetter ? value >= target : value <= target);
    var g = ce("div", { class: "gauge" + (met === false ? " miss" : "") });
    var fill = Math.max(0, Math.min(1, value == null ? 0 : value));
    g.appendChild(ce("i", { style: "width:" + (fill * 100).toFixed(1) + "%" }));
    g.appendChild(ce("span", { class: "tick", style: "left:" + (target * 100).toFixed(1) + "%", title: "target" }));
    return ce("div", { class: "kpi" }, [
      ce("div", { class: "label", text: label }),
      ce("div", { class: "value", text: value == null ? "—" : fmtPct(value) }),
      ce("div", { class: "target", text: "target " + (higherBetter ? "≥ " : "≤ ") + fmtPct(target) }),
      g
    ]);
  }
  function renderKpis() {
    var cs = SUMMARY.calibration_summary || {};
    var jc = SUMMARY.judge_consistency || {};
    var host = $("#kpis");
    host.appendChild(kpi("Recall", cs.recall, TARGETS.recall, true));
    host.appendChild(kpi("False alarms", cs.false_alarm_rate, TARGETS.false_alarm_rate, false));
    host.appendChild(kpi("Localization", cs.localization, TARGETS.localization, true));
    host.appendChild(kpi("Category accuracy", cs.category_accuracy, TARGETS.category_accuracy, true));
    host.appendChild(kpi("Judge consistency", jc.measured ? jc.verdict_agreement : null, TARGETS.judge, true));
  }

  // ---------- heatmap ----------
  function heatColor(recall) {
    if (recall == null) return "var(--rule)";
    var hue = Math.round(145 * recall); // 0 red -> 145 green
    return "hsl(" + hue + ",48%,42%)";
  }
  function renderHeatmap() {
    var cells = CAL.cells || {};
    var langs = Object.keys(SUMMARY.by_language || {}).sort();
    var cats = {};
    Object.keys(cells).forEach(function (k) { var c = cells[k]; if (c.lang !== "_all" && c.category !== "_all") cats[c.category] = 1; });
    var catList = Object.keys(cats).sort();
    var hm = $("#heatmap");
    if (!langs.length || !catList.length) { $("#heatmap-hint").textContent = "No labelled cells to chart (run with --calibration or labelled items)."; return; }
    hm.style.gridTemplateColumns = "90px repeat(" + catList.length + ",1fr)";
    hm.appendChild(ce("div", { class: "hm-cell hm-head" }));
    catList.forEach(function (c) { hm.appendChild(ce("div", { class: "hm-cell hm-head", text: c.replace("_", " ") })); });
    langs.forEach(function (lang) {
      hm.appendChild(ce("div", { class: "hm-cell hm-head", text: lang }));
      catList.forEach(function (cat) {
        var c = cells[lang + "|" + cat];
        var recall = c ? c.recall : null;
        var cell = ce("div", {
          class: "hm-cell" + (c && c.low_support ? " low-support" : ""),
          style: "background:" + heatColor(recall),
          title: c ? (lang + " / " + cat + " — recall " + fmtNum(recall) + ", precision " + fmtNum(c.precision) + ", n=" + c.labels) : "no labels",
          onclick: function () { if (c) { setFilter("lang", lang); setFilter("category", cat); switchTab("items"); } }
        }, [c ? (recall == null ? "·" : fmtNum(recall)) : ""]);
        hm.appendChild(cell);
      });
    });
    $("#heatmap-hint").textContent = "Cell = recall (greener is higher). Hover for precision & n. Dashed = low support. Click to filter.";
  }

  // ---------- filters + list ----------
  var state = { search: "", lang: "", category: "", severity: "", status: "", disagree: false };
  function allCategories() {
    var s = {};
    ITEMS.forEach(function (it) {
      (it.flags || []).forEach(function (f) { s[f.category] = 1; });
      (it.uncertain_categories || []).forEach(function (c) { s[c] = 1; });
    });
    return Object.keys(s).sort();
  }
  function populateSelects() {
    var langs = Object.keys(SUMMARY.by_language || {}).sort();
    var lsel = $("#f-lang"); lsel.appendChild(ce("option", { value: "", text: "All languages" }));
    langs.forEach(function (l) { lsel.appendChild(ce("option", { value: l, text: l })); });
    var csel = $("#f-category"); csel.appendChild(ce("option", { value: "", text: "All categories" }));
    allCategories().forEach(function (c) { csel.appendChild(ce("option", { value: c, text: c })); });
  }
  function matches(it) {
    if (state.lang && it.lang !== state.lang) return false;
    if (state.status && it.status !== state.status) return false;
    if (state.disagree && !it.checker_disagreement) return false;
    if (state.category) {
      var has = (it.flags || []).some(function (f) { return f.category === state.category; })
        || (it.uncertain_categories || []).indexOf(state.category) >= 0;
      if (!has) return false;
    }
    if (state.severity) {
      if (!(it.flags || []).some(function (f) { return f.severity === state.severity; })) return false;
    }
    if (state.search) {
      var q = state.search.toLowerCase();
      var hay = (it.id + " " + it.source + " " + it.translation).toLowerCase();
      if (hay.indexOf(q) < 0) return false;
    }
    return true;
  }
  function filtered() { return ITEMS.filter(matches); }

  function fchip(f) {
    return ce("span", { class: "fchip " + f.severity, title: "confidence " + fmtNum(f.confidence) },
      [ce("span", { class: "dot" }), f.category]);
  }
  function renderList() {
    var list = $("#item-list"); list.textContent = "";
    var rows = filtered();
    $("#result-count").textContent = rows.length + " / " + ITEMS.length + " items";
    if (!rows.length) { list.appendChild(ce("li", { class: "empty", text: "No items match these filters." })); return; }
    rows.forEach(function (it) {
      var chips = ce("div", { class: "flagchips rcat" });
      (it.flags || []).forEach(function (f) { chips.appendChild(fchip(f)); });
      (it.uncertain_categories || []).forEach(function (c) {
        chips.appendChild(ce("span", { class: "fchip minor", title: "uncertain" }, [ce("span", { class: "dot" }), c + " ?"]));
      });
      var row = ce("li", {
        class: "row", tabindex: "0", role: "button",
        onclick: function () { openProof(it.id); },
        onkeydown: function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openProof(it.id); } }
      }, [
        ce("span", { class: "rid", text: it.id }),
        ce("span", { class: "chip", lang: it.lang, text: it.lang }),
        ce("span", { class: "status " + it.status, text: it.status }),
        chips,
        ce("span", { class: "cost", text: "₹" + (it.cost_inr || 0) })
      ]);
      list.appendChild(row);
    });
  }

  function setFilter(key, val) {
    state[key] = val;
    var map = { search: "#f-search", lang: "#f-lang", category: "#f-category", severity: "#f-severity", status: "#f-status" };
    if (map[key]) $(map[key]).value = val;
    syncHash(); renderList();
  }
  function syncHash() {
    var parts = [];
    ["lang", "category", "severity", "status", "search"].forEach(function (k) { if (state[k]) parts.push(k + "=" + encodeURIComponent(state[k])); });
    if (state.disagree) parts.push("disagree=1");
    history.replaceState(null, "", parts.length ? "#" + parts.join("&") : "#");
  }
  function readHash() {
    var h = (location.hash || "").replace(/^#/, "");
    if (!h) return;
    h.split("&").forEach(function (p) {
      var kv = p.split("="); var k = kv[0], v = decodeURIComponent(kv[1] || "");
      if (k === "disagree") state.disagree = v === "1";
      else if (k in state) state[k] = v;
    });
    ["search:#f-search", "lang:#f-lang", "category:#f-category", "severity:#f-severity", "status:#f-status"].forEach(function (m) {
      var a = m.split(":"); if ($(a[1])) $(a[1]).value = state[a[0]];
    });
    $("#f-disagree").checked = state.disagree;
  }
  function wireFilters() {
    $("#f-search").addEventListener("input", function (e) { state.search = e.target.value; syncHash(); renderList(); });
    ["lang", "category", "severity", "status"].forEach(function (k) {
      $("#f-" + k).addEventListener("change", function (e) { state[k] = e.target.value; syncHash(); renderList(); });
    });
    $("#f-disagree").addEventListener("change", function (e) { state.disagree = e.target.checked; syncHash(); renderList(); });
    $("#f-reset").addEventListener("click", function () {
      state = { search: "", lang: "", category: "", severity: "", status: "", disagree: false };
      ["#f-search", "#f-lang", "#f-category", "#f-severity", "#f-status"].forEach(function (s) { $(s).value = ""; });
      $("#f-disagree").checked = false; syncHash(); renderList();
    });
  }

  // ---------- proof sheet ----------
  var proofOrder = [];
  function highlight(text, spans) {
    var host = ce("div", { class: "texthost" });
    if (!text) { host.textContent = "(empty)"; return host; }
    var valid = (spans || []).filter(function (s) { return s.span && s.span[1] > s.span[0] && s.span[0] >= 0; })
      .sort(function (a, b) { return a.span[0] - b.span[0]; });
    var pos = 0;
    valid.forEach(function (s) {
      var a = Math.max(pos, s.span[0]), b = Math.min(text.length, s.span[1]);
      if (b <= pos) return; // overlap: skip
      if (a > pos) host.appendChild(document.createTextNode(text.slice(pos, a)));
      var m = ce("mark", { class: s.severity }, [text.slice(a, b)]);
      m.appendChild(ce("span", { class: "marker", text: "[" + s.idx + "]" }));
      host.appendChild(m);
      pos = b;
    });
    if (pos < text.length) host.appendChild(document.createTextNode(text.slice(pos)));
    return host;
  }
  function evidenceCard(f, idx) {
    var head = ce("div", { class: "evhead" }, [
      ce("span", { class: "cat", text: "[" + idx + "] " + f.category + " · " + f.severity }),
      ce("span", { text: "conf " + fmtNum(f.confidence) })
    ]);
    var card = ce("div", { class: "evcard" }, [head]);
    (f.evidence || []).forEach(function (e) {
      var line = ce("div", { class: "evhead" }, [
        ce("span", { text: e.check }),
        ce("span", { class: "noisy", text: e.strength + (e.noisy ? " · noisy" : "") })
      ]);
      card.appendChild(line);
      if (e.explanation) card.appendChild(ce("div", { text: e.explanation }));
      var det = ce("details", {}, [ce("summary", { text: "evidence" }),
        ce("pre", { text: JSON.stringify(e.details || {}, null, 1) })]);
      card.appendChild(det);
    });
    return card;
  }
  function waveform(signal) {
    if (!signal || !signal.rms || !signal.rms.length) return null;
    var w = ce("div", { class: "wave", role: "img", "aria-label": "audio energy, duration " + (signal.duration_s || "?") + "s" });
    signal.rms.forEach(function (v) { w.appendChild(ce("i", { style: "height:" + Math.max(2, v * 100).toFixed(0) + "%" })); });
    return w;
  }
  function whyLine(it) {
    if (it.status === "flagged") return "Flagged: at least one strong deterministic check, or a corroborated signal.";
    if (it.status === "uncertain") return "Uncertain: only noisy evidence (e.g. judge or back-translation) with no corroboration — routed to review, not flagged.";
    if (it.status === "skipped") return "Skipped — see items.jsonl for the reason.";
    return "Clean: no check raised corroborated evidence.";
  }
  function openProof(id) {
    proofOrder = filtered().map(function (x) { return x.id; });
    var it = ITEMS.filter(function (x) { return x.id === id; })[0];
    if (!it) return;
    $("#proof-id").textContent = it.id;
    $("#proof-lang").textContent = it.lang;
    var st = $("#proof-status"); st.textContent = it.status; st.className = "chip status " + it.status;
    var body = $("#proof-body"); body.textContent = "";

    var srcSpans = [], tgtSpans = [];
    (it.flags || []).forEach(function (f, i) {
      if (f.source_span) srcSpans.push({ span: f.source_span, severity: f.severity, idx: i + 1 });
      if (f.target_span) tgtSpans.push({ span: f.target_span, severity: f.severity, idx: i + 1 });
    });
    var src = ce("div", { class: "textcard" }, [ce("h4", { text: "Source (English)" })]);
    src.appendChild(highlight(it.source, srcSpans));
    body.appendChild(src);
    var trh = highlight(it.translation, tgtSpans); trh.setAttribute("lang", it.lang); trh.className = "texthost indic";
    body.appendChild(ce("div", { class: "textcard" }, [ce("h4", { text: "Translation (" + it.lang + ")" }), trh]));
    if (it.reference) {
      var rh = highlight(it.reference, []); rh.setAttribute("lang", it.lang); rh.className = "texthost indic";
      body.appendChild(ce("div", { class: "textcard" }, [ce("h4", { text: "Reference" }), rh]));
    }
    var wave = waveform(it.signal);
    if (wave) body.appendChild(ce("div", { class: "textcard" }, [ce("h4", { text: "Audio energy" }), wave]));

    body.appendChild(ce("p", { class: "why", text: whyLine(it) }));
    if (it.judge_verdict) body.appendChild(ce("p", { class: "why", text: "Judge verdict: " + it.judge_verdict + (it.checker_disagreement ? " (disagrees with checks)" : "") }));

    (it.flags || []).forEach(function (f, i) { body.appendChild(evidenceCard(f, i + 1)); });
    (it.uncertain_categories || []).forEach(function (c) {
      body.appendChild(ce("div", { class: "evcard" }, [ce("div", { class: "evhead" }, [ce("span", { class: "cat", text: c + " — uncertain" })])]));
    });

    $("#proof").hidden = false; $("#scrim").hidden = false;
    $("#proof-close").focus();
  }
  function closeProof() { $("#proof").hidden = true; $("#scrim").hidden = true; }
  function stepProof(d) {
    var cur = $("#proof-id").textContent;
    var i = proofOrder.indexOf(cur);
    if (i < 0) return;
    var j = i + d;
    if (j >= 0 && j < proofOrder.length) openProof(proofOrder[j]);
  }

  // ---------- tabs ----------
  function switchTab(name) {
    document.querySelectorAll(".tab").forEach(function (t) { t.setAttribute("aria-selected", t.dataset.tab === name ? "true" : "false"); });
    document.querySelectorAll(".panel").forEach(function (p) { p.hidden = p.id !== "tab-" + name; });
    if (name === "calibration") renderCalibration();
    if (name === "cost") renderCost();
    if (name === "review") renderReview();
    if (name === "diff") renderDiff();
  }
  function gridTable(headers, rows) {
    var t = ce("table", { class: "grid" });
    t.appendChild(ce("tr", {}, headers.map(function (h) { return ce("th", { text: h }); })));
    rows.forEach(function (r) { t.appendChild(ce("tr", {}, r.map(function (c) { return c instanceof Node ? ce("td", {}, [c]) : ce("td", { text: String(c) }); }))); });
    return t;
  }
  function renderCalibration() {
    var p = $("#tab-calibration"); if (p.dataset.done) return; p.dataset.done = "1";
    var cells = CAL.cells || {};
    var keys = Object.keys(cells).sort();
    if (!keys.length) { p.appendChild(ce("p", { class: "empty", text: "No calibration (no labelled items)." })); return; }
    p.appendChild(ce("h2", { text: "Precision / recall per language × category" }));
    var rows = keys.map(function (k) {
      var c = cells[k];
      return [c.lang, c.category, c.labels, fmtNum(c.recall), fmtNum(c.precision), fmtNum(c.f1),
        fmtNum(c.localization), fmtNum(c.category_accuracy), c.low_support ? "low" : ""];
    });
    p.appendChild(gridTable(["lang", "category", "n", "recall", "precision", "F1", "loc", "cat-acc", "support"], rows));
    var fa = CAL.false_alarms || {};
    p.appendChild(ce("h2", { text: "False-alarm rate (clean items flagged)" }));
    p.appendChild(gridTable(["lang", "clean", "flagged", "rate"],
      Object.keys(fa).sort().map(function (l) { return [l, fa[l].clean_items, fa[l].flagged, fmtNum(fa[l].rate)]; })));
  }
  function renderCost() {
    var p = $("#tab-cost"); if (p.dataset.done) return; p.dataset.done = "1";
    var cost = SUMMARY.cost || {};
    p.appendChild(ce("h2", { text: "Cost (logical, cache-independent)" }));
    p.appendChild(ce("p", { text: "Total logical model cost: ₹" + (cost.logical_total_inr != null ? cost.logical_total_inr : "—") + ". A cached rerun costs ₹0 (actual spend is in meta.json)." }));
    var n = (SUMMARY.totals || {}).items || 1;
    var per100 = cost.logical_total_inr != null ? (cost.logical_total_inr / n * 100) : null;
    p.appendChild(gridTable(["metric", "value"], [
      ["items", n], ["₹ this run (logical)", cost.logical_total_inr != null ? cost.logical_total_inr : "—"],
      ["₹ per 100 items (extrapolated)", per100 != null ? per100.toFixed(4) : "—"],
      ["skipped for budget", (SUMMARY.skipped_for_budget || []).length]
    ]));
  }
  function renderReview() {
    var p = $("#tab-review"); if (p.dataset.done) return; p.dataset.done = "1";
    var unc = ITEMS.filter(function (it) { return it.status === "uncertain"; });
    p.appendChild(ce("h2", { text: "Review queue — " + unc.length + " uncertain item(s)" }));
    p.appendChild(ce("button", { class: "btn", onclick: function () { exportCsv(unc); } }, ["Download filtered CSV"]));
    if (!unc.length) { p.appendChild(ce("p", { class: "empty", text: "Nothing to review." })); return; }
    p.appendChild(gridTable(["id", "lang", "uncertain categories", "open"],
      unc.map(function (it) {
        return [it.id, it.lang, (it.uncertain_categories || []).join("; "),
          ce("button", { class: "btn ghost", onclick: function () { openProof(it.id); } }, ["open"])];
      })));
  }
  function exportCsv(rows) {
    var lines = ["id,lang,status,uncertain_categories"];
    rows.forEach(function (it) {
      lines.push([it.id, it.lang, it.status, '"' + (it.uncertain_categories || []).join(";") + '"'].join(","));
    });
    var blob = new Blob([lines.join("\n")], { type: "text/csv" });
    var a = ce("a", { href: URL.createObjectURL(blob), download: "review_queue.csv" });
    document.body.appendChild(a); a.click(); a.remove();
  }
  function renderDiff() {
    var p = $("#tab-diff"); if (p.dataset.done) return; p.dataset.done = "1";
    if (!DIFF) { p.appendChild(ce("p", { class: "empty", text: "No baseline diff (run with --baseline)." })); return; }
    p.appendChild(ce("h2", { text: "Regressions " + (DIFF.regression_count || 0) + " · Improvements " + (DIFF.improvement_count || 0) }));
    p.appendChild(ce("h3", { text: "New flags (regressions)" }));
    p.appendChild(gridTable(["id", "category", "severity"], (DIFF.new_flags || []).map(function (f) { return [f.id, f.category, f.severity]; })));
    p.appendChild(ce("h3", { text: "Resolved flags" }));
    p.appendChild(gridTable(["id", "category", "severity"], (DIFF.resolved_flags || []).map(function (f) { return [f.id, f.category, f.severity]; })));
  }

  // ---------- keyboard ----------
  function onKey(e) {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") { if (e.key === "Escape") e.target.blur(); return; }
    if (e.key === "Escape") { closeProof(); $("#help").hidden = true; }
    else if (e.key === "j") stepProof(1);
    else if (e.key === "k") stepProof(-1);
    else if (e.key === "/") { e.preventDefault(); $("#f-search").focus(); }
    else if (e.key === "t") toggleTheme();
    else if (e.key === "?") $("#help").hidden = !$("#help").hidden;
  }

  // ---------- init ----------
  function init() {
    initTheme();
    renderHeader(); renderKpis(); renderHeatmap(); populateSelects(); wireFilters();
    readHash(); renderList();
    $("#theme-toggle").addEventListener("click", toggleTheme);
    $("#help-toggle").addEventListener("click", function () { $("#help").hidden = !$("#help").hidden; });
    $("#help-close").addEventListener("click", function () { $("#help").hidden = true; });
    $("#proof-close").addEventListener("click", closeProof);
    $("#scrim").addEventListener("click", closeProof);
    $("#proof-next").addEventListener("click", function () { stepProof(1); });
    $("#proof-prev").addEventListener("click", function () { stepProof(-1); });
    document.querySelectorAll(".tab").forEach(function (t) { t.addEventListener("click", function () { switchTab(t.dataset.tab); }); });
    document.addEventListener("keydown", onKey);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
