// AutoCAM's Onshape panel: pick a part, set the quantity and options, Send. The requests are in panel/api.py.
// Plain JavaScript, no build step. Everything from Onshape or Trello goes in as text, never as HTML.
(function () {
  "use strict";

  var P = new URLSearchParams(location.search);
  function rawParam(name) {
    // URLSearchParams turns "+" into a space, but Onshape's configuration strings use "+" themselves ("19+in")
    var found = "";
    location.search.replace(/^\?/, "").split("&").forEach(function (kv) {
      var i = kv.indexOf("="), k = i < 0 ? kv : kv.slice(0, i);
      try { if (decodeURIComponent(k) === name) found = decodeURIComponent(i < 0 ? "" : kv.slice(i + 1)); } catch (e) { /* not ours */ }
    });
    return found;
  }
  var configuration = rawParam("configuration").trim();
  if (/^(default)?$/i.test(configuration) || configuration.indexOf("{$") === 0) configuration = "";
  var wvm = (P.get("workspaceOrVersion") || "w").charAt(0).toLowerCase();
  var ctx = {
    key: P.get("key") || "",
    d: P.get("documentId") || "",
    wvm: wvm === "v" ? "v" : "w",
    w: P.get("workspaceOrVersionId") || P.get("workspaceId") || "",
    e: P.get("elementId") || "",
    u: P.get("userId") || "",
    server: P.get("server") || "",
    kind: P.get("kind") === "assembly" ? "assembly" : "studio",  // the Assembly extension's link says kind=assembly
    c: configuration                                             // a configured Part Studio's ({$configuration})
  };
  // This page's own build (panel.js?v=<hash>): when the server's is newer, the panel reloads itself at a quiet
  // moment (an open panel kept running old code after a deploy, 2026-11-02)
  var BUILD = ((document.currentScript && document.currentScript.src || "").match(/[?&]v=([0-9a-f]+)/) || [])[1] || "";
  function checkBuild(build) {
    if (!build || !BUILD || build === BUILD || S.busy || typing() || (S.mode !== "list" && S.mode !== "assembly")) return;
    location.reload();
  }
  var bodyEl = document.getElementById("body");
  var actionEl = document.getElementById("action");
  var refreshBtn = document.getElementById("refresh");

  var ICONS = {
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>',
    lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
    minus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M5 12h14"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M5 12h14M12 5v14"/></svg>',
    back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>',
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>'
  };

  // ---------------------------------------------------------------- small helpers
  function icon(name) {
    var t = document.createElement("template");
    t.innerHTML = ICONS[name];               // our own constants only
    return t.content.firstChild;
  }
  function add(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); return; }
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  function h(tag, attrs) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else if (k === "value") el.value = v;
      else if (k === "checked") el.checked = !!v;
      else el.setAttribute(k, v === true ? "" : v);
    });
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function stored(name, value) {
    try {
      if (value === undefined) return localStorage.getItem("autocam." + name) || "";
      localStorage.setItem("autocam." + name, value);
    } catch (e) { /* no storage in this frame: the server keeps names too */ }
    return "";
  }
  function api(method, path, data) {
    var url = path;
    var opts = { method: method, headers: { "X-AutoCAM-Key": ctx.key } };
    if (method === "GET") url += "?" + new URLSearchParams(data).toString();
    else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(data); }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) {
          var err = new Error(j.error || ("AutoCAM answered " + r.status + "."));
          err.status = r.status; err.data = j;
          throw err;
        }
        return j;
      });
    }, function () {
      throw new Error("Can't reach AutoCAM. Is the shop laptop on? Try Refresh in a minute.");
    });
  }
  function where(extra) {
    var q = { d: ctx.d, wvm: ctx.wvm, w: ctx.w, e: ctx.e };
    if (ctx.c) q.c = ctx.c;
    Object.keys(extra || {}).forEach(function (k) { q[k] = extra[k]; });
    return q;
  }

  // ---------------------------------------------------------------- Onshape messages
  function tellOnshape(name) {
    if (window.parent === window) return;
    window.parent.postMessage({ documentId: ctx.d, workspaceId: ctx.w, elementId: ctx.e, messageName: name }, "*");
  }
  var lastKeepAlive = 0;
  function keepAlive() {
    var now = Date.now();
    if (now - lastKeepAlive > 60000) { lastKeepAlive = now; tellOnshape("keepAlive"); }
  }
  ["click", "keydown", "input"].forEach(function (ev) { document.addEventListener(ev, keepAlive, true); });

  var selectionsLogged = 0, repliesLogged = 0;
  window.addEventListener("message", function (ev) {
    if (ctx.server && ev.origin !== ctx.server) return;      // only Onshape's own messages
    var m = ev.data;
    if (!m || typeof m !== "object") return;
    if (m.messageName !== "SELECTION") {                      // Onshape's other messages (its answers): a few logged
      if (m.messageName && repliesLogged < 5) { repliesLogged++; api("POST", "/api/selection", { message: m }).catch(function () {}); }
      return;
    }
    if (selectionsLogged < 5) {                               // what they look like goes in AutoCAM's log
      selectionsLogged++;
      api("POST", "/api/selection", { message: m }).catch(function () {});
    }
    if (S.mode === "assembly" && S.asm) return highlightFromCad(m);
    if (pickFit(m)) return;
    var id = selectedPart(m);
    if (id) {
      if (S.partId !== id || S.mode !== "part") openPart(id);
      return;
    }
    whichPart(m);
  });
  // A part clicked in the assembly: its row in the list lights up (the occurrence path's last id is its instance)
  function highlightFromCad(m) {
    var sel = (Array.isArray(m.selections) ? m.selections : []).filter(function (x) {
      return x && Array.isArray(x.occurrencePath) && x.occurrencePath.length;
    });
    if (!sel.length) { if (S.highlight) { S.highlight = ""; render(); } return; }
    var path = sel[0].occurrencePath, inst = path[path.length - 1];
    if (typeof inst !== "string" || S.busy) return;
    api("GET", "/api/which-row", where({ inst: inst })).then(function (r) {
      if (S.mode !== "assembly") return;
      S.highlight = r.key || "";
      render();
      if (!r.key) return;
      var row = bodyEl.querySelector('[data-key="' + CSS.escape(r.key) + '"]');
      if (!row && !S.showOther) { S.showOther = true; render(); row = bodyEl.querySelector('[data-key="' + CSS.escape(r.key) + '"]'); }
      if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, function () {});
  }
  // A row's name clicked: its part lights up in Onshape (every copy). requestSelectionHighlight is documented for Part
  // Studios only; in an assembly the part is named by its occurrence paths, like Onshape's own SELECTION messages.
  function locate(key, ev) {
    if (ev) { ev.preventDefault(); ev.stopPropagation(); }
    S.highlight = key;
    render();
    api("GET", "/api/occurrences", where({ key: key })).then(function (r) {
      if (!r.paths || !r.paths.length) return;
      var sels = [];
      r.paths.forEach(function (p) {
        sels.push({ selectionType: "BODY", selectionId: r.pid, bodyType: "SOLID", occurrencePath: p });
        sels.push({ selectionType: "ENTITY", entityType: "BODY", selectionId: r.pid, occurrencePath: p });
      });
      window.parent.postMessage({ documentId: ctx.d, workspaceId: ctx.w, elementId: ctx.e,
        messageName: "requestSelectionHighlight", messageId: "autocam-" + Date.now(), selections: sels }, "*");
    }, function () {});
  }
  function whichPart(m) {
    // A face or edge clicked (Onshape sends only its id): the server knows the parts already opened in the panel.
    var ids = (Array.isArray(m.selections) ? m.selections : []).filter(function (s) {
      return s && s.selectionType === "ENTITY" && typeof s.selectionId === "string";
    }).map(function (s) { return s.selectionId; }).slice(0, 20);
    if (!ids.length || !S.studio || (S.mode !== "list" && S.mode !== "part") || S.busy) return;
    api("GET", "/api/which-part", where({ ids: ids.join(",") })).then(function (r) {
      if (S.busy) return;
      if (r.part) {
        S.hint = "";
        if (S.partId !== r.part || S.mode !== "part") openPart(r.part);
      } else if (S.mode === "list") {
        S.hint = "To open a part from the CAD, click its name in Onshape's Parts list.";
        render();
      }
    }, function () {});
  }
  function strings(v, depth, out) {
    if (depth > 5 || v === null || v === undefined) return out;
    if (typeof v === "string") out.push(v);
    else if (Array.isArray(v)) v.forEach(function (x) { strings(x, depth + 1, out); });
    else if (typeof v === "object") Object.keys(v).forEach(function (k) { strings(v[k], depth + 1, out); });
    return out;
  }
  function pickFit(m) {
    // A face of the open part clicked in the CAD: one of its round holes (not drilled) or its outline sets the Fit.
    var f = S.form, c = S.check;
    if (S.mode !== "part" || !f || !c || !c.pick || !S.studio || !S.studio.fit) return false;
    var card = S.detail && S.detail.card;
    if (card && card.status !== "cut" && !S.editing) return false;
    var hit = strings(m.selections || m, 0, []).map(function (s) { return c.pick[s]; }).filter(Boolean)[0];
    if (!hit) return false;
    f.fitKind = hit.kind;
    if (hit.kind === "hole") f.fitSize = String(hit.size);
    f.picked = hit.kind === "hole" ? "Fit set to the " + hit.size + " in hole you clicked." : "Fit set to the outline you clicked.";
    render();
    return true;
  }
  function selectedPart(m) {
    // Onshape's docs don't describe the SELECTION payload: take any id in it that is one of this studio's parts.
    if (!S.studio) return null;
    var known = {};
    S.studio.parts.forEach(function (p) { known[p.id] = true; });
    var found = null;
    (function walk(v, depth) {
      if (found || depth > 4 || v === null || v === undefined) return;
      if (typeof v === "string") { if (known[v]) found = v; return; }
      if (Array.isArray(v)) { v.forEach(function (x) { walk(x, depth + 1); }); return; }
      if (typeof v === "object") {
        ["partId", "selectionId", "id"].forEach(function (k) { walk(v[k], depth + 1); });
        Object.keys(v).forEach(function (k) { walk(v[k], depth + 1); });
      }
    })(m.selections || m, 0);
    return found;
  }

  // ---------------------------------------------------------------- state
  var S = {
    mode: "loading",       // loading | error | list | part
    studio: null,          // /api/studio
    user: stored("name"),
    error: null,           // {text, kind}
    partId: null,
    detail: null,          // /api/part: {part, card}
    qty: null,             // /api/qty for the open part (null: counting)
    check: null,           // /api/check for the open part (null: checking)
    changed: undefined,    // /api/changed for a part on a sheet or cut (null: asking; undefined: not asked)
    asm: null,             // /api/assembly (the Assembly extension)
    asmSel: {},            // part key -> {on, qty}
    asmOpts: null,         // {tabs, nest, drafts} for the whole assembly
    asmResult: null,       // /api/send-many's answer
    shaftSel: {},          // shaft key -> {on, qty, ends, other, ops} (the assembly's library shafts)
    shaft: null,           // the shaft editor: {row, listedId, draft, error, saving}
    boughtSel: {},         // bought part key -> {on, qty, note} (the assembly's bought parts, for the shop's parts list)
    bought: null,          // the bought part editor: {row, listed, qty, note, error, saving}
    highlight: "",         // the row of the part clicked in the assembly
    form: null,
    editing: false,        // the form, making the open part's Drafts or sent-back card over
    sent: null,            // /api/send's answer
    notice: "",
    changing: null,        // {qty} while changing a card's quantity
    showOther: false,      // the list's parts in materials AutoCAM doesn't cut
    busy: false
  };

  function newForm(card) {
    var c = card && card.form;                 // a Drafts or sent-back card: start from what it says
    if (c) {
      return { qty: c.qty || 1, qtyBy: "you", asm: 0, tabs: c.tabs, nest: c.nest, face: c.face, scribe: c.scribe || "",
        fitKind: c.fit.kind, fitSize: c.fit.kind === "hole" ? String(c.fit.size) : "", rush: c.rush, drafts: false,
        smoked: c.smoked, sheet: c.sheet || "", tool18: c.tool_eighth, material: "", notes: c.notes || "", error: "", kept: c.kept || [] };
    }
    return fromAssembly({
      qty: card && card.qty ? card.qty : 1, qtyBy: card && card.qty ? "you" : "asm", asm: 0,
      tabs: S.studio ? S.studio.tabsDefault : true, nest: true, face: false, scribe: "", fitKind: "none", fitSize: "",
      rush: false, drafts: false, smoked: false, sheet: "", tool18: false, material: "", notes: "", error: ""
    });
  }
  function fromAssembly(f) {
    var q = S.qty;
    if (f && f.qtyBy === "asm" && q && q.assemblies.length) f.qty = q.assemblies[Math.min(f.asm, q.assemblies.length - 1)].count;
    return f;
  }

  // ---------------------------------------------------------------- loading
  function loadStudio(fresh) {
    if (!ctx.d || !ctx.w || !ctx.e || !ctx.key) {
      S.mode = "error";
      S.error = { text: "Open this panel from a Part Studio tab in Onshape." };
      render();
      return Promise.resolve();
    }
    refreshBtn.classList.add("spin");
    if (ctx.kind === "assembly") return loadAssembly(fresh);
    return api("GET", "/api/studio", where({ fresh: fresh ? "1" : "", u: ctx.u })).then(function (st) {
      S.studio = st;
      checkBuild(st.build);
      if (st.user) { S.user = st.user; stored("name", st.user); }
      if (S.mode === "loading" || S.mode === "error") S.mode = "list";
      startAuto(st.autoRefreshS);
      render();
    }, function (err) {
      S.mode = "error";
      S.error = { text: err.message, kind: err.data && err.data.kind };
      render();
    }).then(function () { refreshBtn.classList.remove("spin"); });
  }

  function loadAssembly(fresh) {
    return api("GET", "/api/assembly", where({ fresh: fresh ? "1" : "", u: ctx.u })).then(showAssembly, function (err) {
      S.mode = "error";
      S.error = { text: err.message, kind: err.data && err.data.kind };
      render();
    }).then(function () { refreshBtn.classList.remove("spin"); });
  }

  function showAssembly(a) {
    checkBuild(a.build);
    S.asm = a;
    if (a.user) { S.user = a.user; stored("name", a.user); }
    if (!S.asmOpts) S.asmOpts = { tabs: a.tabsDefault, nest: true, face: false, rush: false, drafts: false };
    var sel = {};
    a.items.forEach(function (i) {
      var old = S.asmSel[i.key];
      // ticked to start: this document's parts with no card (another document's are often bought parts)
      sel[i.key] = { on: i.sendable && (old ? old.on : i.here && i.chip.status === "none"), qty: old ? old.qty : Math.max(1, i.qty) };
    });
    S.asmSel = sel;
    var ssel = {};
    (a.shafts || []).forEach(function (s) {
      var old = S.shaftSel[s.key];
      // unticked to start (the user, 2026-11-02): sending an assembly's plates shouldn't put all its shafts on the list
      ssel[s.key] = old || { on: false, qty: s.qty, ends: s.ends.slice(), other: [false, false], note: "",
        ops: s.ops.map(function (o) { return Object.assign({}, o); }) };
      if (!s.sendable) ssel[s.key].on = false;
    });
    S.shaftSel = ssel;
    var bsel = {};
    (a.bought || []).forEach(function (b) {
      // unticked to start, like shafts: the team may have most of them already
      bsel[b.key] = S.boughtSel[b.key] || { on: false, qty: b.qty, note: "" };
      if (!b.sendable) bsel[b.key].on = false;
    });
    S.boughtSel = bsel;
    S.mode = "assembly";
    startAuto(a.autoRefreshS);
    render();
  }

  // ---------------------------------------------------------------- refreshing by itself
  // Every autoRefreshS seconds while the panel is open and someone's been using it in the last half hour: the
  // statuses from Trello. cached=1 means the server never calls Onshape for these (the parts list stays the last
  // one read; Refresh reads it again). Nothing changes while someone's typing, sending or editing.
  var autoTimer = null;
  var lastTouch = Date.now();
  var IDLE_MS = 30 * 60 * 1000;
  ["pointerdown", "pointermove", "keydown", "wheel"].forEach(function (ev) {
    document.addEventListener(ev, function () {
      var idle = Date.now() - lastTouch > IDLE_MS;
      lastTouch = Date.now();
      if (idle) autoRefresh();                     // back after a long break: catch up now
    }, { passive: true });
  });
  function startAuto(seconds) {
    if (autoTimer || !seconds) return;
    autoTimer = setInterval(autoRefresh, Math.max(5, seconds) * 1000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) autoRefresh(); });
  }
  function typing() {
    var a = document.activeElement;
    return !!a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT");
  }
  function leaveAlone() {
    return document.hidden || S.busy || typing() || Date.now() - lastTouch > IDLE_MS;
  }
  function autoRefresh() {
    if (!autoTimer || leaveAlone()) return;
    if (S.mode === "list") {
      api("GET", "/api/studio", where({ cached: "1", u: ctx.u })).then(function (st) {
        if (st.stale || S.mode !== "list" || leaveAlone()) return;
        S.studio = st;
        checkBuild(st.build);
        render();
      }, function () {});
    } else if (S.mode === "assembly") {
      api("GET", "/api/assembly", where({ cached: "1", u: ctx.u })).then(function (a) {
        if (!a.stale && S.mode === "assembly" && !leaveAlone()) showAssembly(a);
      }, function () {});
    } else if (S.mode === "part" && S.partId && S.detail && !S.editing && !S.changing && !S.sent) {
      var id = S.partId;
      api("GET", "/api/part", where({ p: id, cached: "1" })).then(function (r) {
        if (r.stale || S.partId !== id || S.mode !== "part" || S.editing || S.changing || S.sent || leaveAlone()) return;
        if (!S.detail || JSON.stringify(r.card) === JSON.stringify(S.detail.card)) return;
        S.detail = r;
        render();
      }, function () {});
    }
  }

  function openPart(id, keepNotice) {
    S.mode = "part"; S.partId = id; S.detail = null; S.qty = null; S.check = null; S.changed = undefined; S.sent = null;
    S.hint = "";
    S.editing = false;
    S.changing = null; S.form = null;
    if (!keepNotice) S.notice = "";
    var listed = (S.studio && S.studio.parts.filter(function (p) { return p.id === id; })[0]) || null;
    if (listed) S.detail = { part: { id: listed.id, name: listed.name, material: listed.material }, card: undefined };
    render();
    api("GET", "/api/part", where({ p: id })).then(function (r) {
      if (S.partId !== id) return;
      S.detail = r;
      S.form = newForm(null);
      if (r.card && (r.card.status === "sheet" || r.card.status === "cut")) loadChanged(id, r.card.card);
      render();
    }, function (err) {
      if (S.partId !== id) return;
      S.mode = "error"; S.error = { text: err.message, kind: err.data && err.data.kind };
      render();
    });
    loadQty(id, false);
    loadCheck(id, "");
  }

  function loadChanged(id, card) {
    S.changed = null;
    api("GET", "/api/changed", where({ p: id, card: card })).then(function (r) {
      if (S.partId !== id) return;
      S.changed = r;
      render();
    }, function () { if (S.partId === id) { S.changed = undefined; render(); } });
  }

  function loadCheck(id, material) {
    S.check = null;
    api("GET", "/api/check", where({ p: id, m: material || "" })).then(function (r) {
      if (S.partId !== id) return;
      S.check = r;
      render();
    }, function (err) {
      if (S.partId !== id) return;
      S.check = { unavailable: err.message };
      render();
    });
  }

  function loadQty(id, fresh) {
    S.qty = null;
    api("GET", "/api/qty", where({ p: id, fresh: fresh ? "1" : "" })).then(function (r) {
      if (S.partId !== id) return;
      S.qty = r;
      fromAssembly(S.form);
      render();
    }, function (err) {
      if (S.partId !== id) return;
      S.qty = { assemblies: [], note: err.message };
      render();
    });
  }

  function backToList() {
    S.mode = "list"; S.partId = null; S.detail = null; S.sent = null; S.editing = false; S.changing = null;
    S.notice = "";
    render();
  }

  refreshBtn.addEventListener("click", function () {
    var id = S.mode === "part" ? S.partId : null;
    loadStudio(true).then(function () {
      if (id && S.mode !== "error") { S.mode = "part"; openPart(id, false); loadQty(id, true); }
    });
  });

  // ---------------------------------------------------------------- pieces
  function chip(text, kind) { return h("span", { class: "chip" + (kind ? " " + kind : "") }, text); }
  function notice(kind, title, text, iconName) {
    return h("div", { class: "notice " + kind, role: kind === "bad" ? "alert" : null },
      icon(iconName || (kind === "good" ? "check" : kind === "bad" ? "alert" : "info")),
      h("div", null, title ? h("b", null, title) : null, text ? h("p", null, text) : null));
  }
  function switchLine(label, on, onChange, disabled) {
    var input = h("input", { type: "checkbox", role: "switch", checked: on, disabled: disabled,
      onchange: function () { onChange(input.checked); } });
    return h("label", { class: "line" }, h("span", { class: "name" }, label), h("span", { class: "switch" }, input, h("span")));
  }
  function group(label) {
    var g = h("div", { class: "group" }, h("div", { class: "group-label" }, label));
    for (var i = 1; i < arguments.length; i++) add(g, arguments[i]);
    return g;
  }
  function stepper(value, onChange, label) {
    var max = (S.studio && S.studio.maxQty) || 100;
    var input = h("input", { type: "number", min: "1", max: String(max), value: String(value), "aria-label": label || "Quantity",
      onchange: function () {
        var n = Math.max(1, Math.min(max, parseInt(input.value, 10) || 1));
        onChange(n);
      } });
    return h("div", { class: "stepper", role: "group", "aria-label": label || "Quantity" },
      h("button", { type: "button", "aria-label": "One fewer", onclick: function () { onChange(Math.max(1, value - 1)); } }, icon("minus")),
      input,
      h("button", { type: "button", "aria-label": "One more", onclick: function () { onChange(Math.min(max, value + 1)); } }, icon("plus")));
  }
  function partHead(part, chips) {
    var conf = S.studio && S.studio.configuration;               // a configured Part Studio: which one this is
    return h("div", { class: "part-head" }, h("div", { class: "part-name" }, part.name),
      h("div", { class: "chips" }, conf ? [chip(conf)].concat(chips) : chips));
  }
  function materialChip(m) {
    if (!m) return chip("No material", "bad");
    return m.key ? chip(m.label) : chip(m.onshape + " (not cut here)", "bad");
  }
  function linkBtn(text, href, kind) {
    return h("a", { class: "btn " + (kind || "btn-secondary"), href: href, target: "_blank", rel: "noopener noreferrer" }, text);
  }
  function backBtn() {
    return h("button", { class: "back-link", type: "button", onclick: backToList }, icon("back"), "All parts");
  }

  // ---------------------------------------------------------------- views
  function render() {
    bodyEl.textContent = "";
    actionEl.textContent = "";
    actionEl.hidden = true;
    if (S.mode === "loading") return renderLoading();
    if (S.mode === "error") return renderError();
    if (S.mode === "list") return renderList();
    if (S.mode === "assembly") return renderAssembly();
    if (S.mode === "shaft") return renderShaft();
    if (S.mode === "bought") return renderBought();
    return renderPart();
  }

  function renderLoading() {
    add(bodyEl, h("div", { class: "group" }, h("div", { class: "skeleton w60" }), h("div", { class: "skeleton w80" }),
      h("div", { class: "skeleton w40" })));
  }

  function renderError() {
    var e = S.error || {};
    if (e.kind === "noaccess") add(bodyEl, notice("bad", "AutoCAM can't open this document", e.text.replace(/^AutoCAM's Onshape account can't open this document\. /, ""), "lock"));
    else add(bodyEl, notice("bad", "Something's wrong", e.text));
    actionEl.hidden = false;
    add(actionEl, h("button", { class: "btn btn-secondary", type: "button", onclick: function () { S.mode = "loading"; render(); loadStudio(true); } }, "Try again"));
  }

  function minutes(n) {
    if (n < 60) return n + " min";
    var m = n % 60;
    return Math.floor(n / 60) + " h" + (m ? " " + (m < 10 ? "0" : "") + m : "");
  }
  function queueLine(q) {
    if (!q) return null;
    var text = q.sheets ? "Machine queue: " + q.sheets + " sheet" + (q.sheets === 1 ? "" : "s") + ", about " + minutes(q.minutes)
      : "Machine queue: empty";
    if (q.waiting) text += " · " + q.waiting + " card" + (q.waiting === 1 ? "" : "s") + " waiting to nest";
    return h("p", { class: "muted queue" }, text);
  }
  function markIcon(mark) {
    if (!mark) return null;
    return h("span", { class: "mark " + mark, title: mark === "ok" ? "AutoCAM can cut it" : "AutoCAM would send it back" },
      icon(mark === "ok" ? "check" : "alert"));
  }

  function renderList() {
    var st = S.studio;
    if (st.boardError) add(bodyEl, notice("info", null, st.boardError));
    add(bodyEl, queueLine(st.queue));
    if (st.configuration) add(bodyEl, h("p", { class: "muted" }, "Configuration: " + st.configuration));
    add(bodyEl, h("p", { class: "muted" }, "Select a part to send it."));
    if (S.hint) add(bodyEl, notice("info", null, S.hint));
    if (!st.parts.length) { add(bodyEl, h("p", { class: "muted" }, "This Part Studio has no parts yet.")); return; }
    // parts in a material AutoCAM doesn't cut (belts, PLA, steel) wait behind a link, unless they have a card
    var cut = st.parts.filter(function (p) { return !p.material || p.material.key || p.chip.status !== "none"; });
    var other = st.parts.filter(function (p) { return cut.indexOf(p) < 0; });
    function row(p) {
      return h("button", { class: "part-row", type: "button", onclick: function () { openPart(p.id); } },
        h("span", { class: "name" }, p.name), markIcon(p.mark), p.bent ? chip("Bent") : null,
        p.chip.changed ? chip("CAD changed", "back") : null,
        p.chip.label ? chip(p.chip.label, p.chip.status) : null);
    }
    var list = h("div", { class: "parts" }, cut.map(row), S.showOther ? other.map(row) : null);
    var more = other.length ? h("button", { class: "link-btn", type: "button", onclick: function () { S.showOther = !S.showOther; render(); } },
      S.showOther ? "Hide the parts AutoCAM doesn't cut" : other.length + " more AutoCAM doesn't cut") : null;
    add(bodyEl, group("In this Part Studio", list, more));
  }

  function renderAssembly() {
    var a = S.asm, o = S.asmOpts;
    if (a.boardError) add(bodyEl, notice("info", null, a.boardError));
    if (S.asmResult) {
      var r = S.asmResult, sent = r.results.filter(function (x) { return x.sent; }), skipped = r.results.filter(function (x) { return !x.sent; });
      add(bodyEl, h("div", { class: "notice " + (sent.length ? "good" : "info") }, icon(sent.length ? "check" : "info"), h("div", null,
        h("b", null, sent.length ? "Sent" : "Nothing sent"),
        skipped.length ? h("ul", { class: "problems" }, skipped.map(function (x) { return h("li", null, x.name + " " + x.why + "."); })) : null)));
    }
    add(bodyEl, queueLine(a.queue));
    // bought parts (a vendor's library) and the common parts library's prints aren't offered, whatever their material
    var ours = a.items.filter(function (i) { return !i.notOurs && ((i.material && i.material.key) || i.chip.status !== "none"); });
    var other = a.items.filter(function (i) { return ours.indexOf(i) < 0; });
    function row(i) {
      var sel = S.asmSel[i.key];
      var box = i.sendable ? h("input", { type: "checkbox", checked: sel.on, "aria-label": "Send " + i.name,
        onchange: function (ev) { sel.on = ev.target.checked; render(); } }) : h("span");
      var qty = i.sendable ? h("input", { class: "qty-in", type: "number", min: "1", max: String(a.maxQty), value: String(sel.qty),
        "aria-label": i.name + " quantity", onchange: function (ev) { sel.qty = Math.max(1, Math.min(a.maxQty, parseInt(ev.target.value, 10) || 1)); render(); } })
        : h("span", { class: "hint" }, "×" + i.qty);
      var tag = i.chip.status !== "none" && i.chip.label ? chip(i.chip.label, i.chip.status) : i.why ? chip(i.why, "bad") : null;
      return h("label", { class: "arow" + (S.highlight === i.key ? " hl" : ""), "data-key": i.key }, box,
        h("span", { class: "name locatable", title: "Show it in Onshape", onclick: function (ev) { locate(i.key, ev); } }, i.name), qty, tag);
    }
    var more = other.length ? h("button", { class: "link-btn", type: "button", onclick: function () { S.showOther = !S.showOther; render(); } },
      S.showOther ? "Hide the parts AutoCAM doesn't cut" : other.length + " more AutoCAM doesn't cut") : null;
    add(bodyEl, group(a.name ? "Parts in " + a.name : "Parts in this assembly",
      ours.length ? h("div", { class: "parts" }, ours.map(row), S.showOther ? other.map(row) : null) : h("p", { class: "muted" }, "No parts AutoCAM cuts."),
      more));
    if ((a.shafts && a.shafts.length) || (a.shaftsGone && a.shaftsGone.length)) {
      add(bodyEl, group("Shafts", h("div", { class: "parts" }, a.shafts.map(shaftRow), a.shaftsGone.map(goneRow))));
    }
    if ((a.bought && a.bought.length) || (a.boughtGone && a.boughtGone.length)) {
      add(bodyEl, group("Bought parts", h("div", { class: "parts" }, a.bought.map(boughtRow), a.boughtGone.map(boughtGoneRow))));
    }
    add(bodyEl, group("Options",
      switchLine("Hold them in with tabs", o.tabs, function (v) { o.tabs = v; render(); }),
      switchLine("Nest them with other parts", o.nest, function (v) { o.nest = v; render(); }),
      a.face ? switchLine("Face their tops", o.face, function (v) { o.face = v; render(); }) : null,
      switchLine("Rush", o.rush, function (v) { o.rush = v; render(); }),
      switchLine("Send plates to Drafts", o.drafts, function (v) { o.drafts = v; render(); }),
      h("span", { class: "hint" }, "A fit is set on one part, from its Part Studio.")));

    var chosen = a.items.filter(function (i) { return i.sendable && S.asmSel[i.key].on; });
    actionEl.hidden = false;
    var nameInput = null;
    if (!S.user) {
      nameInput = h("input", { class: "input wide", type: "text", placeholder: "Your name (it goes on the cards)", maxlength: "40", "aria-label": "Your name" });
      add(actionEl, nameInput);
    }
    var shafts = (a.shafts || []).filter(function (x) { return x.sendable && S.shaftSel[x.key] && S.shaftSel[x.key].on; });
    var stuck = shafts.filter(function (x) { var sel = S.shaftSel[x.key]; return shaftProblems(x, sel.ops, sel.ends, sel.other).length; });
    var bought = (a.bought || []).filter(function (x) { return x.sendable && S.boughtSel[x.key] && S.boughtSel[x.key].on; });
    var n = chosen.length, m = shafts.length, k = bought.length;
    function count(x, one) { return x + " " + one + (x === 1 ? "" : "s"); }
    var bits = [n ? count(n, "part") : "", m ? count(m, "shaft") : "", k ? count(k, "bought part") : ""].filter(Boolean);
    var words = !m && !k ? "Send " + count(n, "part") + " to " + (o.drafts ? "Drafts" : "Ready for CAM")
      : "Send " + (bits.length > 1 ? bits.slice(0, -1).join(", ") + " and " + bits[bits.length - 1] : bits[0]);
    if (stuck.length) add(actionEl, h("div", { class: "as" }, "Fix " + stuck.map(function (x) { return x.label; }).join(", ") + " first (the pencil)."));
    add(actionEl, h("button", { class: "btn btn-primary", type: "button", disabled: S.busy || !(n + m + k) || stuck.length > 0,
      onclick: function () { sendMany(chosen, shafts, bought, nameInput); } }, S.busy ? "Sending…" : words));
    if (S.user) {
      add(actionEl, h("div", { class: "as" }, "Sending as " + S.user + " · ",
        h("button", { class: "link-btn", type: "button", onclick: function () { S.user = ""; stored("name", ""); render(); } }, "change")));
    }
  }

  function sendMany(chosen, shafts, bought, nameInput) {
    if (nameInput) {
      var typed = nameInput.value.trim();
      if (!typed) { S.asmResult = { results: [{ name: "", sent: false, why: "Type your name first (it goes on the cards)" }], list: "" }; render(); return; }
      S.user = typed; stored("name", typed);
    }
    var o = S.asmOpts;
    S.busy = true; render();
    api("POST", "/api/send-many", where({
      items: chosen.map(function (i) { return { key: i.key, qty: S.asmSel[i.key].qty }; }),
      shafts: shafts.map(function (x) { var sel = S.shaftSel[x.key]; return { key: x.key, qty: sel.qty, ends: sel.ends, ops: sel.ops, note: sel.note || "" }; }),
      bought: bought.map(function (x) { var sel = S.boughtSel[x.key]; return { key: x.key, qty: sel.qty, note: sel.note || "" }; }),
      tabs: o.tabs, nest: o.nest, face: o.face, rush: o.rush, drafts: o.drafts, name: S.user, u: ctx.u, server: ctx.server
    })).then(function (r) {
      S.busy = false; S.asmResult = r;
      loadAssembly(false);
    }, function (err) {
      S.busy = false; S.asmResult = { results: [{ name: "", sent: false, why: err.message }], list: "" }; render();
    });
  }

  // ---------------------------------------------------------------- shafts (the shop's list; shafts.py)
  function fmt(x) { return Number(x || 0).toFixed(3); }
  var TAP_RE = /^[A-Za-z0-9./ x-]{1,12}$/;

  // What can't be made: the same rules as shafts.problems() on the server, so Send waits until they're fixed
  function shaftProblems(s, ops, ends, other) {
    var out = [], per = { 1: 0, 2: 0 };
    ops.forEach(function (o) {
      if (o.kind === "turn") {
        if (!(o.d > 0)) out.push("A turn-down needs a diameter.");
        else if (!o.auto && (s.shape === "hex" ? o.d > s.size + 1e-9 : o.d >= s.size - 1e-9)) out.push("Ø" + fmt(o.d) + " isn't smaller than the " + s.stock + ".");
        else if (s.bore && o.d < s.bore + 0.06) out.push("Ø" + fmt(o.d) + " cuts into the " + s.bore.toFixed(3) + " hole of the " + s.stock + ".");
        if (o.end) {
          if (!(o.l > 0 && o.l < s.length)) out.push("A turn-down must be shorter than the shaft (" + fmt(s.length) + ").");
          per[o.end] = Math.max(per[o.end], o.l || 0);
        }
      } else if (!(o.at > 0 && o.at < s.length)) {
        out.push("A groove " + fmt(o.at) + " from the end is off the " + fmt(s.length) + " shaft.");
      }
    });
    if (per[1] && per[2] && per[1] + per[2] > s.length) out.push("The turn-downs on the two ends overlap.");
    [0, 1].forEach(function (i) {
      if (other && other[i] && !ends[i]) out.push("Type a tap size for end " + (i + 1) + ", or pick None.");
      else if (ends[i] && !TAP_RE.test(ends[i])) out.push("\"" + ends[i] + "\" isn't a tap size (like 8-32 or M5).");
    });
    return out.filter(function (x, i) { return out.indexOf(x) === i; });
  }

  function editBtn(label, onclick) {
    return h("button", { class: "icon-btn row-btn", type: "button", "aria-label": "Lathe work and taps for " + label,
      title: "Lathe work and taps", onclick: onclick }, icon("edit"));
  }

  function shaftRow(s) {
    var L = s.listed, sel = S.shaftSel[s.key];
    if (L && (L.status === "waiting" || L.status === "started")) {           // on the list already
      var right = L.status === "waiting"
        ? [h("span", { class: "hint" }, "×" + L.qty), editBtn(s.label, function () { openShaft(s, L); })]
        : h("span", { class: "chip-cell" }, chip(L.chip.label || "In progress", L.chip.status || "queued"));
      return h("div", { class: "arow shaft locate-row" + (S.highlight === s.key ? " hl" : ""), "data-key": s.key, title: "Show it in Onshape",
        onclick: function (ev) { rowLocate(s.key, ev); } }, h("span", { class: "mark ok" }, icon("check")),
        h("span", { class: "name locatable" }, s.label), right);
    }
    var sub = s.problem ? h("span", { class: "sub bad-text" }, s.problem) : L && L.status === "done" ? h("span", { class: "sub" }, "Done") : null;
    return h("div", { class: "arow shaft locate-row" + (S.highlight === s.key ? " hl" : ""), "data-key": s.key, title: "Show it in Onshape",
      onclick: function (ev) { rowLocate(s.key, ev); } },
      s.sendable ? h("input", { type: "checkbox", checked: sel.on, "aria-label": "Send " + s.label,
        onchange: function (ev) { sel.on = ev.target.checked; render(); } }) : h("span"),
      h("span", { class: "name locatable" }, s.label, sub),
      s.sendable ? h("input", { class: "qty-in", type: "number", min: "1", max: String(S.asm.maxQty || 100), value: String(sel.qty),
        "aria-label": s.label + " quantity", onchange: function (ev) { sel.qty = Math.max(1, Math.min(S.asm.maxQty || 100, parseInt(ev.target.value, 10) || 1)); render(); } }) : h("span"),
      s.sendable ? editBtn(s.label, function () { openShaft(s, null); }) : h("span"));
  }

  function rowLocate(key, ev) {                // anywhere on the row but its checkbox, quantity and pencil
    if (ev.target.closest && ev.target.closest("input, button, a, select, textarea")) return;
    locate(key, ev);
  }

  function goneRow(x) {     // on the list, but not in the assembly any more (its length changed in CAD)
    return h("div", { class: "arow shaft" }, h("span"),
      h("span", { class: "name" }, x.label, h("span", { class: "sub bad-text" }, "Changed in CAD")),
      x.waiting ? h("button", { class: "icon-btn row-btn", type: "button", "aria-label": "Take " + x.label + " off the list",
        title: "Take it off the list", onclick: function () { removeShaft(x.id); } }, icon("x")) : h("span"), h("span"));
  }

  // ---------------------------------------------------------------- bought parts (the shop's parts list; bought.py)
  // A sent part shows its check and where it's at: ×4 (all still needed), "2 of 4 in bin", Ordered. One whose count in
  // the assembly changed since says "CAD has 6 now" with Make it 6. All in the bin: it can be sent again.
  function boughtRow(b) {
    var L = b.listed, sel = S.boughtSel[b.key], cls = "arow bought locate-row" + (S.highlight === b.key ? " hl" : "");
    function locateRow(ev) { rowLocate(b.key, ev); }
    var cad = L && L.cadNow ? h("span", { class: "sub bad-text" }, "CAD has " + L.cadNow + " now · ",
      h("button", { class: "link-btn", type: "button", disabled: S.busy, onclick: function () { saveBoughtQty(L, L.cadNow); } }, "Make it " + L.cadNow)) : null;
    var pencil = h("button", { class: "icon-btn row-btn", type: "button", "aria-label": "Quantity and note for " + b.name,
      title: "Quantity and note", onclick: function () { openBought(b); } }, icon("edit"));
    if (L && (L.status === "needed" || L.status === "ordered" || L.cadNow)) {   // on the list
      var right = L.chip.label && L.status !== "bin" ? chip(L.chip.label, L.chip.status)
        : L.status === "bin" ? chip("In bin", "good") : h("span", { class: "hint" }, "×" + L.qty);
      return h("div", { class: cls, "data-key": b.key, title: "Show it in Onshape", onclick: locateRow },
        h("span", { class: "mark ok" }, icon("check")), h("span", { class: "name locatable" }, b.name, cad), right, pencil);
    }
    var sub = L && L.status === "bin" ? h("span", { class: "sub" }, "In the bin")
      : sel.note ? h("span", { class: "sub" }, "Note: " + sel.note) : null;
    return h("div", { class: cls, "data-key": b.key, title: "Show it in Onshape", onclick: locateRow },
      b.sendable ? h("input", { type: "checkbox", checked: sel.on, "aria-label": "Send " + b.name,
        onchange: function (ev) { sel.on = ev.target.checked; render(); } }) : h("span"),
      h("span", { class: "name locatable" }, b.name, sub),
      b.sendable ? h("input", { class: "qty-in", type: "number", min: "1", max: String(S.asm.maxQty || 100), value: String(sel.qty),
        "aria-label": b.name + " quantity", onchange: function (ev) { sel.qty = Math.max(1, Math.min(S.asm.maxQty || 100, parseInt(ev.target.value, 10) || 1)); render(); } }) : h("span"),
      b.sendable ? pencil : h("span"));
  }

  // The pencil: quantity and a note (on the phone's card). Before sending it sets what Send sends; after, it changes
  // the part on the list.
  function openBought(b) {
    var L = b.listed && (b.listed.status !== "bin" || b.listed.cadNow) ? b.listed : null, sel = S.boughtSel[b.key];
    S.bought = { row: b, listed: L, qty: L ? L.qty : sel.qty, note: L ? L.note : sel.note || "", error: "", saving: false };
    S.mode = "bought";
    render();
  }
  function closeBought() { S.bought = null; S.mode = "assembly"; render(); }

  function renderBought() {
    var E = S.bought, L = E.listed, least = L ? Math.max(1, L.inBin) : 1;
    add(bodyEl, h("button", { class: "back-link", type: "button", onclick: closeBought }, icon("back"), (S.asm && S.asm.name) || "Assembly"));
    add(bodyEl, h("div", { class: "part-name" }, E.row.name));
    add(bodyEl, h("div", { class: "line" }, h("span", { class: "name" }, "Quantity"),
      stepper(E.qty, function (n) { E.qty = Math.max(least, n); render(); }, "Quantity")));
    if (L && L.inBin) add(bodyEl, h("span", { class: "hint" }, L.inBin + " in the bin already."));
    add(bodyEl, group("Note", h("textarea", { class: "textarea", maxlength: "200", rows: "2", "aria-label": "Note for the shop",
      placeholder: "Anything the shop should know", oninput: function (ev) { E.note = ev.target.value; } }, E.note)));
    if (E.error) add(bodyEl, notice("bad", null, E.error));
    actionEl.hidden = false;
    add(actionEl, h("button", { class: "btn btn-primary", type: "button", disabled: E.saving || S.busy, onclick: saveBought },
      E.saving ? "Saving…" : "Done"));
    if (L && !L.inBin && !L.onOrder) add(actionEl, h("button", { class: "btn btn-ghost", type: "button", disabled: E.saving || S.busy,
      onclick: function () { removePart(L.id); } }, "Take it off the list"));
  }

  function saveBought() {
    var E = S.bought, note = E.note.trim();
    if (!E.listed) {                         // not sent yet: what Send will send, and tick it
      S.boughtSel[E.row.key] = { on: true, qty: E.qty, note: note };
      return closeBought();
    }
    E.saving = true; render();
    api("POST", "/api/part", { id: E.listed.id, qty: E.qty, note: note }).then(function () {
      S.bought = null; S.mode = "assembly"; return reloadAssembly();
    }, function (err) { E.saving = false; E.error = err.message; render(); });
  }

  function saveBoughtQty(L, qty) {             // Make it 6: the CAD's count, keeping the note
    S.busy = true; render();
    api("POST", "/api/part", { id: L.id, qty: qty, note: L.note || "" }).then(function () {
      S.busy = false; return reloadAssembly();
    }, function (err) {
      S.busy = false; S.asmResult = { results: [{ name: "", sent: false, why: err.message }], list: "" }; render();
    });
  }

  function boughtGoneRow(x) {   // on the list, but not in the assembly any more
    return h("div", { class: "arow bought" }, h("span"),
      h("span", { class: "name" }, x.name, h("span", { class: "sub bad-text" }, "Changed in CAD")),
      x.waiting ? h("button", { class: "icon-btn row-btn", type: "button", "aria-label": "Take " + x.name + " off the list",
        title: "Take it off the list", disabled: S.busy, onclick: function () { removePart(x.id); } }, icon("x")) : h("span"), h("span"));
  }

  function removePart(id) {
    S.busy = true; render();
    api("POST", "/api/part-remove", { id: id }).then(function () {
      S.busy = false; S.bought = null; S.mode = "assembly"; return reloadAssembly();
    }, function (err) {
      S.busy = false;
      if (S.bought) S.bought.error = err.message;
      else S.asmResult = { results: [{ name: "", sent: false, why: err.message }], list: "" };
      render();
    });
  }

  function openShaft(s, listed) {
    var from = listed || S.shaftSel[s.key];
    var tap = (S.asm && S.asm.shaftTap) || "10-32";
    var ends = from.ends.slice();
    S.shaft = { row: s, listedId: listed ? listed.id : null, error: "", saving: false, draft: {
      qty: from.qty, ends: ends, note: from.note || "",
      other: from.other ? from.other.slice() : ends.map(function (t) { return !!t && t !== tap && t !== "10-32" && t !== "1/4-20"; }),
      ops: from.ops.map(function (o) { return Object.assign({}, o); }) } };
    S.mode = "shaft";
    render();
  }
  function closeShaft() { S.shaft = null; S.mode = "assembly"; render(); }

  function reloadAssembly() {
    return api("GET", "/api/assembly", where({ cached: "1", u: ctx.u })).then(function (a) {
      if (a.stale) return loadAssembly(false);
      showAssembly(a);
    }, function () { return loadAssembly(false); });
  }

  function saveShaft() {
    var E = S.shaft, d = E.draft;
    var ends = [0, 1].map(function (i) { return (d.ends[i] || "").trim(); });
    if (!E.listedId) {                         // not sent yet: it's what Send will send
      S.shaftSel[E.row.key] = { on: true, qty: d.qty, ends: ends, other: d.other, ops: d.ops, note: d.note.trim() };
      return closeShaft();
    }
    E.saving = true; E.error = ""; render();
    api("POST", "/api/shaft", { id: E.listedId, qty: d.qty, ends: ends, ops: d.ops, note: d.note.trim() }).then(function () {
      S.shaft = null; S.mode = "assembly"; return reloadAssembly();
    }, function (err) { E.saving = false; E.error = err.message; render(); });
  }

  function removeShaft(id) {
    S.busy = true; render();
    api("POST", "/api/shaft-remove", { id: id }).then(function () {
      S.busy = false; S.shaft = null; S.mode = "assembly"; return reloadAssembly();
    }, function (err) {
      S.busy = false;
      if (S.shaft) S.shaft.error = err.message;
      else S.asmResult = { results: [{ name: "", sent: false, why: err.message }], list: "" };
      render();
    });
  }

  function seg(options, current, set, name, wide) {
    return h("span", { class: "seg" + (wide ? " wide" : ""), role: "group", "aria-label": name }, options.map(function (o) {
      return h("button", { type: "button", "aria-pressed": String(current === o[0]), onclick: function () { set(o[0]); render(); } }, o[1]);
    }));
  }

  function renderShaft() {
    var E = S.shaft, s = E.row, d = E.draft;
    add(bodyEl, h("button", { class: "back-link", type: "button", onclick: closeShaft }, icon("back"), (S.asm && S.asm.name) || "Assembly"));
    add(bodyEl, h("div", { class: "part-name" }, s.label));
    add(bodyEl, h("div", { class: "line" }, h("span", { class: "name" }, "Quantity"), stepper(d.qty, function (n) { d.qty = n; render(); }, "Quantity")));
    add(bodyEl, group("Tap", [0, 1].map(function (i) {
      var pick = d.other[i] ? "other" : d.ends[i];
      return h("div", { class: "tap-end" },
        h("div", { class: "line" }, h("span", { class: "name" }, "End " + (i + 1)),
          seg([["10-32", "10-32"], ["1/4-20", "1/4-20"], ["other", "Other"], ["", "None"]], pick, function (v) {
            if (v === "other") { if (!d.other[i]) { d.other[i] = true; d.ends[i] = ""; } }
            else { d.other[i] = false; d.ends[i] = v; }
          }, "End " + (i + 1) + " tap", true)),
        d.other[i] ? h("input", { class: "input input-other", type: "text", maxlength: "12", value: d.ends[i],
          placeholder: "Size, like 8-32 or M5", "aria-label": "End " + (i + 1) + " tap size",
          oninput: function (ev) { d.ends[i] = ev.target.value.trim(); }, onchange: function () { render(); } }) : null);
    })));
    var ops = h("div", { class: "ops" });
    d.ops.forEach(function (o, idx) {
      var right = o.auto ? chip("From the model")
        : [seg([[1, "End 1"], [2, "End 2"]], o.end, function (v) { o.end = v; }, "Which end"),
           h("button", { class: "icon-btn", type: "button", "aria-label": "Remove", onclick: function () { d.ops.splice(idx, 1); render(); } }, icon("x"))];
      var fields = o.kind === "turn"
        ? h("div", { class: "fields" }, field("Diameter", o, "d", o.auto), o.end ? field("Length", o, "l") : null)
        : h("div", { class: "fields" }, field("From end", o, "at"),
            h("div", { class: "field" }, "Depth per side", h("span", { class: "fixed" }, fmt(S.asm.grooveDepth))));
      add(ops, h("div", { class: "op" }, h("div", { class: "op-top" },
        h("b", null, o.kind === "groove" ? "Groove" : o.auto ? "Turn down from " + s.stock : "Turn down"), right), fields));
    });
    add(ops, h("div", { class: "add-row" },
      h("button", { class: "btn btn-secondary btn-small", type: "button", onclick: function () {
        var used = d.ops.filter(function (o) { return o.kind === "turn" && o.end; }).map(function (o) { return o.end; });
        d.ops.push({ kind: "turn", end: used.indexOf(1) < 0 ? 1 : 2, d: s.shape === "hex" ? s.size : +(s.size - 0.125).toFixed(3), l: 0.5 });
        render();
      } }, icon("plus"), "Turn down"),
      h("button", { class: "btn btn-secondary btn-small", type: "button", onclick: function () {
        d.ops.push({ kind: "groove", end: 1, at: 0.5 }); render();
      } }, icon("plus"), "Groove")));
    add(bodyEl, group("Lathe", ops));
    add(bodyEl, group("Note", h("textarea", { class: "textarea", maxlength: "200", rows: "2", "aria-label": "Note for the shop",
      placeholder: "Anything the shop should know", oninput: function (ev) { d.note = ev.target.value; } }, d.note)));
    var bad = shaftProblems(s, d.ops, d.ends, d.other);
    if (E.error) add(bodyEl, notice("bad", null, E.error));
    if (bad.length) add(bodyEl, notice("bad", null, bad.join("\n")));
    actionEl.hidden = false;
    add(actionEl, h("button", { class: "btn btn-primary", type: "button", disabled: E.saving || S.busy || bad.length > 0, onclick: saveShaft },
      E.saving ? "Saving…" : "Done"));
    if (E.listedId) add(actionEl, h("button", { class: "btn btn-ghost", type: "button", disabled: E.saving || S.busy,
      onclick: function () { removeShaft(E.listedId); } }, "Take it off the list"));

    function field(name, o, key, readOnly) {
      return h("label", { class: "field" }, name,
        h("input", { class: "num", type: "text", inputmode: "decimal", value: fmt(o[key]), readonly: readOnly,
          onchange: function (ev) { var v = parseFloat(ev.target.value); o[key] = v > 0 ? +v.toFixed(4) : 0; render(); } }));
    }
  }

  function renderPart() {
    var d = S.detail;
    add(bodyEl, backBtn());
    if (!d) return renderLoading();
    var card = d.card;
    if (card === undefined) {                 // still asking AutoCAM about this part's card
      add(bodyEl, partHead(d.part, [materialChip(d.part.material)]));
      return add(bodyEl, h("div", { class: "group" }, h("div", { class: "skeleton w60" })));
    }
    if (S.sent) return renderSent();
    if (!card || card.status === "cut" || S.editing) return renderForm();
    if (card.status === "back") return renderBack();
    return renderStatus();
  }

  function renderStatus() {
    var d = S.detail, card = d.card;
    add(bodyEl, partHead(d.part, [chip(card.label, card.status), chip("Qty " + card.qty, "event")]));
    if (S.notice) add(bodyEl, notice("info", null, S.notice));
    var flagged = card.cadChanged;
    if (S.changed && S.changed.changed) {
      add(bodyEl, notice("bad", "Changed in Onshape since it was CAM'd" + (S.changed.run ? " (" + S.changed.run + ")" : ""),
        S.changed.what.join(", ") + "." + (card.status === "sheet" ? " Its card and sheet card are marked CAD changed." : "")));
    } else if (flagged && !(S.changed && S.changed.changed === false)) {
      add(bodyEl, notice("bad", "Changed in Onshape since it was CAM'd", (flagged.what || []).join(", ") + "." +
        (flagged.cut ? " What was cut is the old version." : "")));
    } else if (S.changed && S.changed.changed === false) {
      add(bodyEl, h("p", { class: "hint" }, "Onshape still matches what was CAM'd" + (S.changed.run ? " in " + S.changed.run : "") + "."));
    }
    add(bodyEl, group("Status", h("div", { class: "steps" }, card.steps.map(function (s) {
      return h("div", { class: "step " + s.state }, h("span", { class: "b" }, s.state === "done" ? icon("check") : null),
        h("span", { class: "t" }, s.t, s.w ? h("span", { class: "w" }, s.w) : null));
    }))));
    var sheets = card.sheets || [];
    var sheet = sheets[sheets.length - 1];
    if (sheet && sheet.id) {
      var img = h("img", { class: "thumb", alt: "The sheet " + sheet.label, src: "/api/preview?" + new URLSearchParams({ key: ctx.key, sheet: sheet.id }).toString() });
      img.addEventListener("error", function () { img.remove(); });
      add(bodyEl, img);
    }
    var ch = S.changed;
    actionEl.hidden = false;
    if (S.changing) return renderChangeQty();
    var open = sheet && sheet.url ? linkBtn("Open sheet", sheet.url) : linkBtn("Open card", card.url);
    if (((ch && ch.changed) || (card.cadChanged && !card.cadChanged.cut)) && card.status === "sheet") {
      add(actionEl, h("div", { class: "btn-row" },
        h("button", { class: "btn btn-primary", type: "button", disabled: S.busy, onclick: renest }, "Nest the new version"),
        card.cadChanged ? h("button", { class: "btn btn-ghost", type: "button", disabled: S.busy, onclick: acceptChange }, "Cut it as it is") : null,
        sheet && sheet.url ? linkBtn("Open sheet", sheet.url, "btn-ghost") : linkBtn("Open card", card.url, "btn-ghost")));
      return;
    }
    if (card.status === "drafts") {
      add(actionEl, h("div", { class: "btn-row" },
        h("button", { class: "btn btn-primary", type: "button", onclick: function () { startEdit(); } }, "Edit and send"),
        linkBtn("Open card", card.url, "btn-ghost")));
    } else {
      add(actionEl, h("div", { class: "btn-row" }, open,
        h("button", { class: "btn btn-ghost", type: "button", onclick: function () { S.changing = { qty: card.qty }; render(); } }, "Change qty")));
    }
  }

  function renest() {
    var card = S.detail.card;
    S.busy = true; render();
    api("POST", "/api/renest", where({ p: S.partId, card: card.card, name: S.user })).then(function (r) {
      S.busy = false; S.changed = undefined;
      if (r.card) S.detail.card = r.card;
      S.notice = "It's back in Ready for CAM: the next run nests the new version.";
      quietRefresh();
      render();
    }, function (err) { S.busy = false; S.notice = err.message; render(); });
  }

  function acceptChange() {
    var card = S.detail.card;
    S.busy = true; render();
    api("POST", "/api/accept-change", where({ p: S.partId, card: card.card, name: S.user })).then(function (r) {
      S.busy = false; S.changed = undefined;
      if (r.card) S.detail.card = r.card;
      S.notice = "OK: it's cut as it was CAM'd. The CAD changed marks are off its cards.";
      quietRefresh();
      render();
    }, function (err) { S.busy = false; S.notice = err.message; render(); });
  }

  function renderChangeQty() {
    var card = S.detail.card;
    var line = h("div", { class: "line" }, stepper(S.changing.qty, function (n) { S.changing.qty = n; render(); }, "New quantity"),
      h("span", { class: "hint" }, card.status === "sheet" ? "It goes back to Ready for CAM and is nested again." : "Was " + card.qty));
    if (S.changing.error) add(actionEl, notice("bad", null, S.changing.error));
    add(actionEl, line);
    add(actionEl, h("div", { class: "btn-row" },
      h("button", { class: "btn btn-primary", type: "button", disabled: S.busy || S.changing.qty === card.qty, onclick: saveQty }, "Save"),
      h("button", { class: "btn btn-ghost", type: "button", onclick: function () { S.changing = null; render(); } }, "Cancel")));
  }

  function saveQty() {
    var card = S.detail.card;
    S.busy = true; render();
    api("POST", "/api/qty", where({ p: S.partId, card: card.card, qty: S.changing.qty, name: S.user })).then(function (r) {
      S.busy = false; S.changing = null;
      if (r.card) S.detail.card = r.card;
      S.notice = r.renest ? "It's back in Ready for CAM: the next run nests the new quantity." : "Quantity changed.";
      quietRefresh();
      render();
    }, function (err) {
      S.busy = false; S.changing.error = err.message; render();
    });
  }

  function renderBack() {
    var d = S.detail, card = d.card;
    add(bodyEl, partHead(d.part, [chip("Sent back", "back")]));
    add(bodyEl, notice("bad", "Why it came back", card.reason || "Open the card to see the comment."));
    actionEl.hidden = false;
    add(actionEl, h("div", { class: "btn-row" },
      h("button", { class: "btn btn-primary", type: "button", onclick: function () { startEdit(); } }, "Send again"),
      linkBtn("Open card", card.url, "btn-ghost")));
  }

  function startEdit() {
    S.editing = true;
    S.form = newForm(S.detail.card);
    if (S.form.sheet) loadCheck(S.partId, S.form.sheet);
    render();
  }

  function renderSent() {
    var r = S.sent, f = S.form, d = S.detail;
    var drafts = r.list === "Drafts";
    add(bodyEl, notice("good", "Sent to " + r.list, r.own ? (f.placeMyself ? "It goes in Sheet review as it is, for the operator to place."
      : "AutoCAM picks your program a spot and puts it in Sheet review, checked.")
      : drafts ? "Move it to Ready for CAM when it's ready." : "It's nested about 2 minutes after the last card arrives."));
    if (r.material && r.material.line && r.material.missed) {
      add(bodyEl, notice("info", null, "Onshape didn't take the material (" + (r.material.why || "no reason given") + "), so the card says Material: " + r.material.line + "."));
    }
    var chips = r.own ? [chip("Own program", "event")].concat(f.qty > 1 ? [chip("Qty " + f.qty)] : []) : [chip("Qty " + f.qty, "event")];
    if (f.tabs && !r.own) chips.push(chip("Tabs"));
    if (!f.nest) chips.push(chip("Placed by hand"));
    if (f.face) chips.push(chip("Face"));
    if (f.scribe && !r.own) chips.push(chip("Scribed bends"));
    if (f.tool18 && !r.own) chips.push(chip("1/8 in endmill"));
    if (f.fitKind !== "none") chips.push(chip(f.fitKind === "outline" ? "Fit outline" : "Fit " + f.fitSize + " hole"));
    if (f.rush) chips.push(chip("Rush"));
    add(bodyEl, partHead(d.part, chips));
    actionEl.hidden = false;
    add(actionEl, h("div", { class: "btn-row" }, linkBtn("Open card", r.card.url),
      h("button", { class: "btn btn-ghost", type: "button", onclick: backToList }, "Send another")));
  }

  function renderForm() {
    var d = S.detail, f = S.form, st = S.studio, card = d.card;
    var m = d.part.material;
    var pickMaterial = !m || !m.key;
    var chips = [materialChip(m)];
    if (d.part.bent) chips.push(chip("Bent"));
    if (card && card.status === "cut") chips.push(chip("Cut before", "cut"));
    if (S.editing && card) chips.push(chip(card.label, card.status));
    add(bodyEl, partHead(d.part, chips));
    add(bodyEl, checkBlock());
    var ch = S.changed;
    if (card && card.status === "cut" && ch && ch.changed) {
      add(bodyEl, notice("info", "Changed since it was cut" + (ch.run ? " (" + ch.run + ")" : ""), ch.what.join(", ") + "."));
    } else if (card && card.status === "cut" && ch && ch.changed === false) {
      add(bodyEl, h("p", { class: "hint" }, "The same as when it was cut" + (ch.run ? " in " + ch.run : "") + "."));
    }

    if (pickMaterial) {
      var sel = h("select", { class: "select wide", "aria-label": "Material", onchange: function () {
        f.material = sel.value; loadCheck(S.partId, f.material); render(); } },
        h("option", { value: "" }, "Choose a material"),
        st.materials.map(function (o) { return h("option", { value: o.value, selected: f.material === o.value ? "selected" : null }, o.label); }));
      add(bodyEl, group("Material", sel, h("span", { class: "hint" },
        st.writable ? "Set on the part in Onshape when you send." : "Goes on the card (a version can't be changed).")));
    }

    // quantity: from the assemblies, still changeable
    var q = S.qty, from;
    if (f.qtyBy === "you") {
      var best = q && q.assemblies.length ? q.assemblies[Math.min(f.asm, q.assemblies.length - 1)].count : null;
      from = h("div", { class: "from" }, h("span", null, best ? "set by you ·" : "set by you"),
        best ? h("button", { class: "link-btn", type: "button", onclick: function () { f.qtyBy = "asm"; f.qty = best; render(); } }, "reset to " + best) : null);
    } else if (!q) {
      from = h("div", { class: "from" }, "counting in the assemblies…");
    } else if (q.assemblies.length) {
      var asel = h("select", { "aria-label": "Count from which assembly", onchange: function () {
        f.asm = asel.selectedIndex; f.qty = q.assemblies[f.asm].count; render();
      } }, q.assemblies.map(function (a, i) { return h("option", { selected: i === f.asm ? "selected" : null }, a.name + " (" + a.count + ")"); }));
      from = h("div", { class: "from" }, h("span", null, "from"), asel);
    } else {
      from = h("div", { class: "from" }, q.note ? q.note : "no assembly here uses it");
    }
    // own programs too: AutoCAM cuts the program once per copy (2026-11-03)
    add(bodyEl, group("Quantity", h("div", { class: "line" }, stepper(f.qty, function (n) { f.qty = n; f.qtyBy = "you"; render(); }), from),
      q && q.assemblies.length && q.note && f.qtyBy !== "you" ? h("span", { class: "hint" }, q.note) : null));
    if (f.own) add(bodyEl, ownProgramGroup(f, m));

    // options, all in one list
    var fitted = f.fitKind !== "none";
    var poly = (m && m.key && m.family === "polycarbonate");
    var ownSwitch = switchLine("I CAM'd it myself", f.own, function (v) { f.own = v; f.error = ""; render(); });
    var opts = f.own ? [ownSwitch] : [
      switchLine("Hold it in with tabs", f.tabs || fitted, function (v) { f.tabs = v; render(); }, fitted),
      switchLine("Nest it with other parts", f.nest || fitted, function (v) { f.nest = v; render(); }, fitted)
    ];
    if (st.face && !f.own) opts.push(switchLine("Face the top", f.face, function (v) { f.face = v; render(); }));
    if (!f.own) opts.push(switchLine("Cut all of it with the 1/8 in endmill", f.tool18, function (v) { f.tool18 = v; render(); }));
    if (st.scribe && d.part.bent && !f.own) {
      var ssel = h("select", { class: "select", id: "scribe", onchange: function () { f.scribe = ssel.value; render(); } },
        [["", "No"], ["endmill", "With the endmill"], ["chamfer", "With the chamfer mill"]].map(function (o) {
          return h("option", { value: o[0], selected: (f.scribe || "") === o[0] ? "selected" : null }, o[1]);
        }));
      opts.push(h("div", { class: "line" }, h("label", { class: "name", for: "scribe" }, "Scribe the bend lines"), ssel));
    }
    if (st.fit && !f.own) {
      var fsel = h("select", { class: "select", id: "fit", onchange: function () { f.fitKind = fsel.value; render(); } },
        [["none", "None"], ["hole", "A hole"], ["outline", "Outline"]].map(function (o) {
          return h("option", { value: o[0], selected: f.fitKind === o[0] ? "selected" : null }, o[1]);
        }));
      opts.push(h("div", { class: "line" }, h("label", { class: "name", for: "fit" }, "Fit"), fsel));
      if (f.picked) opts.push(h("span", { class: "hint" }, f.picked));
      if (f.fitKind === "hole") {
        var size = h("input", { class: "input size", type: "text", inputmode: "decimal", placeholder: "1.125", value: f.fitSize,
          "aria-label": "Hole size", oninput: function () { f.fitSize = size.value; } });
        opts.push(h("div", { class: "line" }, h("span", { class: "name" }, "Hole size (in, or mm)"), size));
      }
    }
    if (!f.own) opts.push(switchLine("Rush", f.rush, function (v) { f.rush = v; render(); }));
    if (poly && !f.own) {
      // which sheet: Onshape's polycarbonate is also smoked PC and SRPP (each kind its own stock)
      var kinds = (st.materials || []).filter(function (o) { return o.onshape.toLowerCase() === m.onshape.toLowerCase(); });
      if (kinds.length > 1) {
        var shsel = h("select", { class: "select", id: "sheet", onchange: function () {
          var picked = kinds.filter(function (o) { return o.value === shsel.value; })[0];
          f.sheet = shsel.value === m.key ? "" : shsel.value; f.smoked = !!(picked && picked.smoked);
          loadCheck(S.partId, f.sheet); render(); } },
          kinds.map(function (o) { return h("option", { value: o.value, selected: (f.sheet || m.key) === o.value ? "selected" : null }, o.label); }));
        opts.push(h("div", { class: "line" }, h("label", { class: "name", for: "sheet" }, "Sheet"), shsel));
      }
    }
    if (!f.own) {
      opts.push(switchLine("Send to Drafts", f.drafts, function (v) { f.drafts = v; render(); }));
      opts.push(ownSwitch);
    }
    var notes = h("textarea", { class: "textarea", placeholder: "Notes for the CAM person", "aria-label": "Notes", maxlength: "300",
      oninput: function () { f.notes = notes.value; } });
    notes.value = f.notes;
    opts.push(notes);
    if (f.kept && f.kept.length && !f.own) {
      opts.push(h("span", { class: "hint" }, "Also kept from the card: " + f.kept.join(" / ")));
    }
    add(bodyEl, group("Options", opts));

    // action: who's sending, and Send
    actionEl.hidden = false;
    if (f.error) add(actionEl, notice("bad", null, f.error));
    var nameInput = null;
    if (!S.user) {
      nameInput = h("input", { class: "input wide", type: "text", placeholder: "Your name (it goes on the card)", maxlength: "40", "aria-label": "Your name" });
      add(actionEl, nameInput);
    }
    var label = f.own ? "Send my program" : S.editing ? (f.drafts ? "Save in Drafts" : "Send again") : (f.drafts ? "Send to Drafts" : "Send to Ready for CAM");
    add(actionEl, h("button", { class: "btn btn-primary", type: "button", disabled: S.busy || (pickMaterial && !f.material) || (f.own && !f.program),
      onclick: function () { send(nameInput); } }, S.busy ? "Sending…" : label));
    if (S.user) {
      add(actionEl, h("div", { class: "as" }, "Sending as " + S.user + " · ",
        h("button", { class: "link-btn", type: "button", onclick: function () { S.user = ""; stored("name", ""); render(); } }, "change")));
    }
  }

  // "I CAM'd it myself": the program, the plate's thickness, and the cutter if it's over 1/4 in
  function ownProgramGroup(f, m) {
    var key = (m && m.key) || f.material;
    var mat = (S.studio.materials || []).filter(function (o) { return o.value === key; })[0];
    var sizes = mat ? mat.thicknesses : [];
    if (f.thickness === undefined && S.check && S.check.stock_in) f.thickness = S.check.stock_in;
    var file = h("input", { type: "file", accept: ".tap,.nc", "aria-label": "Your program", class: "file-in",
      onchange: function () {
        var pick = file.files && file.files[0];
        if (!pick) return;
        var reader = new FileReader();
        reader.onload = function () { f.program = { filename: pick.name, text: String(reader.result || ""), size: pick.size }; render(); };
        reader.readAsText(pick);
      } });
    var thick = h("select", { class: "select", "aria-label": "Thickness", onchange: function () { f.thickness = parseFloat(thick.value); render(); } },
      h("option", { value: "" }, "Pick"),
      sizes.map(function (s) { return h("option", { value: String(s), selected: Math.abs((f.thickness || 0) - s) < 1e-6 ? "selected" : null }, fraction(s)); }));
    var cutter = h("select", { class: "select", "aria-label": "Cutter", onchange: function () { f.cutter = cutter.value; } },
      [["", "1/4 in or smaller"], ["3/8", "3/8 in"], ["1/2", "1/2 in"]].map(function (o) {
        return h("option", { value: o[0], selected: (f.cutter || "") === o[0] ? "selected" : null }, o[1]); }));
    return group("Your program",
      h("div", { class: "line" }, f.program ? h("span", { class: "name" }, f.program.filename,
        h("span", { class: "sub" }, Math.max(1, Math.round(f.program.size / 1024)) + " KB")) : h("span", { class: "name" }, "Pick the .tap you made"),
        h("label", { class: "btn btn-secondary file-btn" }, f.program ? "Change" : "Choose", file)),
      h("div", { class: "line" }, h("span", { class: "name" }, "Plate thickness"), thick),
      h("div", { class: "line" }, h("span", { class: "name" }, "Cutter"), cutter),
      switchLine("I'll place it myself", f.placeMyself, function (v) { f.placeMyself = v; render(); }),
      h("span", { class: "hint" }, f.placeMyself
        ? "It goes in Sheet review as it is: the operator zeros it on a piece big enough. Z0 on the spoilboard."
        : "Zero X/Y at the part's front-left corner and Z0 on the spoilboard. AutoCAM picks a spot, moves your program there and checks it."));
  }
  function fraction(x) {
    var names = { "0.0625": "1/16 in", "0.125": "1/8 in", "0.1875": "3/16 in", "0.25": "1/4 in" };
    return names[String(x)] || x + " in";
  }

  // the design checks: AutoCAM's own rules on the part's shape (panel/checks.py)
  function checkBlock() {
    var c = S.check;
    if (c === null) return h("div", { class: "notice info" }, icon("info"), h("div", null, "Checking the part…"));
    if (c.unavailable) return notice("info", null, "Couldn't check the part: " + c.unavailable);
    var cutter = c.cutter ? c.cutter + (c.cutterWhy ? " (" + c.cutterWhy + ")" : "") : "";
    var facts = [c.thickness, cutter, c.features].filter(Boolean).join(" · ");
    var block = h("div", { class: "group check" });
    if (!c.ok) {
      add(block, h("div", { class: "notice bad", role: "alert" }, icon("alert"), h("div", null,
        h("b", null, "AutoCAM would send this back"),
        h("ul", { class: "problems" }, c.problems.map(function (p) {
          return h("li", null, p.title, p.fix ? h("span", null, p.fix) : null);
        })))));
    } else if (c.needMaterial) {
      add(block, notice("info", "The shape can be cut", facts + ". Pick a material to check the thickness and cutter."));
    } else {
      add(block, notice("good", "AutoCAM can cut this", facts));
    }
    if (c.bent) add(block, h("p", { class: "hint check-note" }, c.bent));
    (c.notes || []).forEach(function (n) { add(block, h("p", { class: "hint check-note" }, n)); });
    if (c.use && S.form) {
      var q = S.form.qty, share = Math.round(c.use.sheet_share * q * 100);
      var money = c.use.each ? "about $" + (c.use.each * q).toFixed(2) + (q > 1 ? " for " + q : "") + " · " : "";
      add(block, h("p", { class: "hint check-note" }, "Cost: " + money + (share < 1 ? "under 1" : share) + "% of a sheet"));
    }
    return block;
  }

  function send(nameInput) {
    var f = S.form, d = S.detail;
    if (nameInput) {
      var typed = nameInput.value.trim();
      if (!typed) { f.error = "Type your name first (it goes on the card)."; render(); return; }
      S.user = typed; stored("name", typed);
    }
    if (f.own) return sendProgram();
    var fitted = f.fitKind !== "none";
    var body = where({
      p: S.partId, qty: f.qty, nest: f.nest || fitted, tabs: f.tabs || fitted, face: f.face, rush: f.rush, tool_eighth: !!f.tool18,
      scribe: d.part.bent ? (f.scribe || "") : "",
      drafts: f.drafts, smoked: f.smoked, sheet: f.sheet || "", material: f.material, fit: { kind: f.fitKind, size: f.fitSize },
      notes: f.notes, u: ctx.u, name: S.user, server: ctx.server, card: S.editing && d.card ? d.card.card : ""
    });
    f.error = ""; S.busy = true; render();
    api("POST", "/api/send", body).then(function (r) {
      S.busy = false; S.sent = r; S.editing = false; d.card = r.card;
      quietRefresh();
      render();
    }, function (err) {
      S.busy = false;
      if (err.data && err.data.kind === "duplicate" && err.data.card) {
        d.card = err.data.card; S.editing = false; S.notice = err.message;
      } else {
        f.error = err.message;
      }
      render();
    });
  }

  function sendProgram() {
    var f = S.form, d = S.detail;
    if (!f.thickness) { f.error = "Pick the plate's thickness."; render(); return; }
    f.error = ""; S.busy = true; render();
    api("POST", "/api/send-program", where({
      p: S.partId, qty: f.qty, thickness: f.thickness, cutter: f.cutter || "", material: f.material, notes: f.notes, place_myself: !!f.placeMyself,
      program: { filename: f.program.filename, text: f.program.text }, u: ctx.u, name: S.user, server: ctx.server
    })).then(function (r) {
      S.busy = false; S.sent = r; d.card = r.card;
      quietRefresh();
      render();
    }, function (err) {
      S.busy = false;
      if (err.data && err.data.kind === "duplicate" && err.data.card) { d.card = err.data.card; S.notice = err.message; }
      else f.error = err.message;
      render();
    });
  }

  function quietRefresh() {
    api("GET", "/api/studio", where({ u: ctx.u })).then(function (st) { S.studio = st; if (S.mode === "list") render(); }, function () {});
  }

  // ---------------------------------------------------------------- start
  tellOnshape("applicationInit");
  render();
  loadStudio(false);
})();
