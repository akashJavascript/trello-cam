// AutoCAM's Onshape panel: pick a part, set the quantity and options, Send. The requests are in panel/api.py.
// Plain JavaScript, no build step. Everything from Onshape or Trello goes in as text, never as HTML.
(function () {
  "use strict";

  var P = new URLSearchParams(location.search);
  var wvm = (P.get("workspaceOrVersion") || "w").charAt(0).toLowerCase();
  var ctx = {
    key: P.get("key") || "",
    d: P.get("documentId") || "",
    wvm: wvm === "v" ? "v" : "w",
    w: P.get("workspaceOrVersionId") || P.get("workspaceId") || "",
    e: P.get("elementId") || "",
    u: P.get("userId") || "",
    server: P.get("server") || ""
  };
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
    back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>'
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

  var selectionsLogged = 0;
  window.addEventListener("message", function (ev) {
    if (ctx.server && ev.origin !== ctx.server) return;      // only Onshape's own messages
    var m = ev.data;
    if (!m || typeof m !== "object" || m.messageName !== "SELECTION") return;
    if (selectionsLogged < 5) {                               // what they look like goes in AutoCAM's log
      selectionsLogged++;
      api("POST", "/api/selection", { message: m }).catch(function () {});
    }
    var id = selectedPart(m);
    if (id && (S.partId !== id || S.mode !== "part")) openPart(id);
  });
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
    form: null,
    editing: false,        // the form, making the open part's Drafts or sent-back card over
    sent: null,            // /api/send's answer
    notice: "",
    changing: null,        // {qty} while changing a card's quantity
    busy: false
  };

  function newForm(card) {
    return fromAssembly({
      qty: card && card.qty ? card.qty : 1, qtyBy: card && card.qty ? "you" : "asm", asm: 0,
      tabs: S.studio ? S.studio.tabsDefault : true, nest: true, face: false, fitKind: "none", fitSize: "",
      rush: false, drafts: false, smoked: false, material: "", notes: "", error: ""
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
    return api("GET", "/api/studio", where({ fresh: fresh ? "1" : "", u: ctx.u })).then(function (st) {
      S.studio = st;
      if (st.user) { S.user = st.user; stored("name", st.user); }
      if (S.mode === "loading" || S.mode === "error") S.mode = "list";
      render();
    }, function (err) {
      S.mode = "error";
      S.error = { text: err.message, kind: err.data && err.data.kind };
      render();
    }).then(function () { refreshBtn.classList.remove("spin"); });
  }

  function openPart(id, keepNotice) {
    S.mode = "part"; S.partId = id; S.detail = null; S.qty = null; S.sent = null; S.editing = false;
    S.changing = null; S.form = null;
    if (!keepNotice) S.notice = "";
    var listed = (S.studio && S.studio.parts.filter(function (p) { return p.id === id; })[0]) || null;
    if (listed) S.detail = { part: { id: listed.id, name: listed.name, material: listed.material }, card: undefined };
    render();
    api("GET", "/api/part", where({ p: id })).then(function (r) {
      if (S.partId !== id) return;
      S.detail = r;
      S.form = newForm(null);
      render();
    }, function (err) {
      if (S.partId !== id) return;
      S.mode = "error"; S.error = { text: err.message, kind: err.data && err.data.kind };
      render();
    });
    loadQty(id, false);
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
    return h("div", { class: "part-head" }, h("div", { class: "part-name" }, part.name), h("div", { class: "chips" }, chips));
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

  function renderList() {
    var st = S.studio;
    if (st.boardError) add(bodyEl, notice("info", null, st.boardError));
    add(bodyEl, h("p", { class: "muted" }, "Select a part to send it."));
    if (!st.parts.length) { add(bodyEl, h("p", { class: "muted" }, "This Part Studio has no parts yet.")); return; }
    add(bodyEl, group("In this Part Studio", h("div", { class: "parts" }, st.parts.map(function (p) {
      return h("button", { class: "part-row", type: "button", onclick: function () { openPart(p.id); } },
        h("span", { class: "name" }, p.name),
        p.chip.label ? chip(p.chip.label, p.chip.status) : null);
    }))));
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
    actionEl.hidden = false;
    if (S.changing) return renderChangeQty();
    var open = sheet && sheet.url ? linkBtn("Open sheet", sheet.url) : linkBtn("Open card", card.url);
    if (card.status === "drafts") {
      add(actionEl, h("div", { class: "btn-row" },
        h("button", { class: "btn btn-primary", type: "button", onclick: function () { startEdit(); } }, "Edit and send"),
        linkBtn("Open card", card.url, "btn-ghost")));
    } else {
      add(actionEl, h("div", { class: "btn-row" }, open,
        h("button", { class: "btn btn-ghost", type: "button", onclick: function () { S.changing = { qty: card.qty }; render(); } }, "Change qty")));
    }
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
    render();
  }

  function renderSent() {
    var r = S.sent, f = S.form, d = S.detail;
    var drafts = r.list === "Drafts";
    add(bodyEl, notice("good", "Sent to " + r.list, drafts ? "Move it to Ready for CAM when it's ready." : "It's nested about 2 minutes after the last card arrives."));
    if (r.material && r.material.line) {
      add(bodyEl, notice("info", null, "Onshape didn't take the material (" + (r.material.why || "no reason given") + "), so the card says Material: " + r.material.line + "."));
    }
    var chips = [chip("Qty " + f.qty, "event")];
    if (f.tabs) chips.push(chip("Tabs"));
    if (!f.nest) chips.push(chip("Placed by hand"));
    if (f.face) chips.push(chip("Face"));
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
    if (card && card.status === "cut") chips.push(chip("Cut before", "cut"));
    if (S.editing && card) chips.push(chip(card.label, card.status));
    add(bodyEl, partHead(d.part, chips));

    if (pickMaterial) {
      var sel = h("select", { class: "select wide", "aria-label": "Material", onchange: function () { f.material = sel.value; render(); } },
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
    add(bodyEl, group("Quantity", h("div", { class: "line" }, stepper(f.qty, function (n) { f.qty = n; f.qtyBy = "you"; render(); }), from),
      q && q.assemblies.length && q.note && f.qtyBy !== "you" ? h("span", { class: "hint" }, q.note) : null));

    // options, all in one list
    var fitted = f.fitKind !== "none";
    var poly = (m && m.key && m.family === "polycarbonate");
    var opts = [
      switchLine("Hold it in with tabs", f.tabs || fitted, function (v) { f.tabs = v; render(); }, fitted),
      switchLine("Nest it with other parts", f.nest || fitted, function (v) { f.nest = v; render(); }, fitted)
    ];
    if (st.face) opts.push(switchLine("Face the top", f.face, function (v) { f.face = v; render(); }));
    if (st.fit) {
      var fsel = h("select", { class: "select", id: "fit", onchange: function () { f.fitKind = fsel.value; render(); } },
        [["none", "None"], ["hole", "A hole"], ["outline", "Outline"]].map(function (o) {
          return h("option", { value: o[0], selected: f.fitKind === o[0] ? "selected" : null }, o[1]);
        }));
      opts.push(h("div", { class: "line" }, h("label", { class: "name", for: "fit" }, "Fit"), fsel));
      if (f.fitKind === "hole") {
        var size = h("input", { class: "input size", type: "text", inputmode: "decimal", placeholder: "1.125", value: f.fitSize,
          "aria-label": "Hole size", oninput: function () { f.fitSize = size.value; } });
        opts.push(h("div", { class: "line" }, h("span", { class: "name" }, "Hole size (in, or mm)"), size));
      }
    }
    opts.push(switchLine("Rush", f.rush, function (v) { f.rush = v; render(); }));
    if (poly) opts.push(switchLine("Smoked", f.smoked, function (v) { f.smoked = v; render(); }));
    opts.push(switchLine("Send to Drafts", f.drafts, function (v) { f.drafts = v; render(); }));
    var notes = h("textarea", { class: "textarea", placeholder: "Notes for the CAM person", "aria-label": "Notes", maxlength: "300",
      oninput: function () { f.notes = notes.value; } });
    notes.value = f.notes;
    opts.push(notes);
    add(bodyEl, group("Options", opts));

    // action: who's sending, and Send
    actionEl.hidden = false;
    if (f.error) add(actionEl, notice("bad", null, f.error));
    var nameInput = null;
    if (!S.user) {
      nameInput = h("input", { class: "input wide", type: "text", placeholder: "Your name (it goes on the card)", maxlength: "40", "aria-label": "Your name" });
      add(actionEl, nameInput);
    }
    var label = S.editing ? (f.drafts ? "Save in Drafts" : "Send again") : (f.drafts ? "Send to Drafts" : "Send to Ready for CAM");
    add(actionEl, h("button", { class: "btn btn-primary", type: "button", disabled: S.busy || (pickMaterial && !f.material),
      onclick: function () { send(nameInput); } }, S.busy ? "Sending…" : label));
    if (S.user) {
      add(actionEl, h("div", { class: "as" }, "Sending as " + S.user + " · ",
        h("button", { class: "link-btn", type: "button", onclick: function () { S.user = ""; stored("name", ""); render(); } }, "change")));
    }
  }

  function send(nameInput) {
    var f = S.form, d = S.detail;
    if (nameInput) {
      var typed = nameInput.value.trim();
      if (!typed) { f.error = "Type your name first (it goes on the card)."; render(); return; }
      S.user = typed; stored("name", typed);
    }
    var fitted = f.fitKind !== "none";
    var body = where({
      p: S.partId, qty: f.qty, nest: f.nest || fitted, tabs: f.tabs || fitted, face: f.face, rush: f.rush,
      drafts: f.drafts, smoked: f.smoked, material: f.material, fit: { kind: f.fitKind, size: f.fitSize },
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

  function quietRefresh() {
    api("GET", "/api/studio", where({ u: ctx.u })).then(function (st) { S.studio = st; if (S.mode === "list") render(); }, function () {});
  }

  // ---------------------------------------------------------------- start
  tellOnshape("applicationInit");
  render();
  loadStudio(false);
})();
