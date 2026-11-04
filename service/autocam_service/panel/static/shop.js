// The shop's phone page (panel/api.py: /api/shop). Three tabs:
//   Shafts (shafts.py): one card per shaft: Start, Done.
//   Parts (bought.py): bought parts sent from an assembly: In the bin (the team has it), or Order. Order opens the
//   team's order sheet in a new tab and copies the part's name; people write the row themselves (AutoCAM never
//   writes to the sheet), then say "It's on the sheet".
//   Plates (panel/plates.py): the sheets in Ready to cut (and cut today), who cuts each, and who finishes each plate.
//   Its Spares section (panel/spares.py, 2026-11-04): spares on the way, and Send spares: a count for any plate
//   AutoCAM has nested, each plate's spares a card of their own (nested after the robot parts).
// Who does what (people.py, the mockup approved 2026-11-04): a name tag on every item (+ when nobody), "All" on a
// group, a picker with the shop's names ("Me" first), and this phone's name at the top right. Soft: anyone can still
// start anything. "Yours" is what this phone started, or what's assigned to its name. Plain JavaScript, no build step;
// everything from the server goes in as text, never as HTML.
(function () {
  "use strict";

  var KEY = new URLSearchParams(location.search).get("key") || "";
  var listEl = document.getElementById("list");
  var toastEl = document.getElementById("toast");
  var tabsEl = document.getElementById("tabs");
  var titleEl = document.getElementById("title");
  var meEl = document.getElementById("me");
  var pickerEl = document.getElementById("picker");
  var backEl = document.getElementById("back");
  var logoEl = document.getElementById("logo");
  var footEl = document.getElementById("foot");
  var SHEET_RE = /^https:\/\/docs\.google\.com\/spreadsheets\//;
  var ICONS = {
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    undo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/></svg>',
    open: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>',
    bin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7h18"/><path d="M5 7l1.5 13h11L19 7"/><path d="M9 7V4h6v3"/></svg>',
    minus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
    cart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.5 12h12l2-8H6.2"/></svg>'
  };
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
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    });
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function remember(name, value) {
    try { if (value === undefined) return localStorage.getItem("autocam.shop." + name) || ""; localStorage.setItem("autocam.shop." + name, value); }
    catch (e) { /* private mode: this visit only */ }
    return "";
  }

  // This phone's id: what it started is "Yours". Kept on the phone; a new one if storage is blocked.
  var DEVICE = (function () {
    var id = remember("device");
    if (!/^[A-Za-z0-9_-]{8,64}$/.test(id)) {
      var b = new Uint8Array(12);
      crypto.getRandomValues(b);
      id = Array.prototype.map.call(b, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
      remember("device", id);
    }
    return id;
  })();

  function api(method, path, data) {
    var opts = { method: method, headers: { "X-AutoCAM-Key": KEY } };
    if (data) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(data); }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.error || "AutoCAM didn't answer."); e.data = j; e.status = r.status; throw e; }
        return j;
      });
    }, function () { throw new Error("Can't reach AutoCAM. Check the Wi-Fi."); });
  }

  // tab: "#parts" in the link picks Parts (for a QR code at the bins); otherwise the last one used on this phone
  var HASH_TAB = { "#parts": "parts", "#shafts": "shafts", "#plates": "plates" }[location.hash];
  var S = { data: null, error: "", busy: false, tab: HASH_TAB || remember("tab") || "shafts",
            ask: null, copied: false,          // ask: a part's Order or In the bin question, {id, kind, n}
            picker: null,                      // the name picker: {title, current, pick(name), whoAmI}
            spares: null };                    // Send spares: {choices (null: loading), counts: {card id: n}, q, error}
  var toastTimer = 0;
  function toast(text) {
    toastEl.textContent = text; toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, 3000);
  }

  function load() {
    if (S.busy || S.spares) return Promise.resolve();          // not while Send spares is open (it'd lose the counts)
    var plates = S.tab === "plates" ? "&plates=1" : "";        // Trello is read only while Plates shows
    return api("GET", "/api/shop?device=" + encodeURIComponent(DEVICE) + plates).then(function (d) {
      S.data = d; S.error = ""; render();
    }, function (e) { S.error = e.message; render(); });
  }

  function post(action, item, extra) {
    S.busy = true; render();
    return api("POST", "/api/shop/" + action, Object.assign({ id: item.id, device: DEVICE }, extra || {})).then(function () {
      S.busy = false; return load();
    }, function (e) {
      S.busy = false;
      toast(e.message);
      return load();
    });
  }

  Array.prototype.forEach.call(tabsEl.querySelectorAll("button"), function (b) {
    b.addEventListener("click", function () {
      S.tab = b.getAttribute("data-tab"); S.ask = null;
      remember("tab", S.tab);
      try { history.replaceState(null, "", "#" + S.tab); } catch (e) { /* fine */ }
      render(); window.scrollTo(0, 0);
      if (S.tab === "plates") load();
    });
  });

  // ---------------------------------------------------------------- the page
  var TITLES = { shafts: "Shafts", parts: "Parts", plates: "Plates" };
  function render() {
    listEl.textContent = "";
    footEl.textContent = ""; footEl.hidden = true;
    backEl.hidden = !S.spares; logoEl.hidden = !!S.spares;
    if (S.spares) {
      tabsEl.hidden = true; meEl.hidden = true; titleEl.hidden = false; titleEl.textContent = "Send spares";
      document.title = "Send spares";
      renderPicker();
      return renderSendSpares();
    }
    var d = S.data;
    var on = !d ? [] : ["shafts", "parts", "plates"].filter(function (t) { return d[t + "On"]; });
    var tab = on.indexOf(S.tab) >= 0 ? S.tab : on[0] || "shafts";
    tabsEl.hidden = on.length < 2; titleEl.hidden = on.length >= 2;
    titleEl.textContent = !d ? "Shop" : TITLES[tab];
    Array.prototype.forEach.call(tabsEl.querySelectorAll("button"), function (b) {
      b.hidden = on.indexOf(b.getAttribute("data-tab")) < 0;
      b.setAttribute("aria-pressed", String(b.getAttribute("data-tab") === tab));
    });
    document.title = TITLES[tab];
    renderMe(d);
    renderPicker();
    if (S.error) add(listEl, h("div", { class: "notice" }, S.error));
    if (!d) { if (!S.error) add(listEl, h("p", { class: "muted" }, "Loading…")); return; }
    if (tab === "parts") renderParts(d);
    else if (tab === "plates") renderPlates(d);
    else renderShafts(d);
  }

  // all: {kind, ids} for an "All" link that assigns the whole group
  function section(title, items, draw, all) {
    if (!items.length) return;
    var head = all ? h("div", { class: "group-head" }, h("span", { class: "group-label" }, title),
      h("button", { class: "link-btn", type: "button", disabled: S.busy, onclick: function () {
        openPicker("All " + title + " to", "", function (n) { assign(all.kind, items.map(function (x) { return x.id; }), n); });
      } }, "All")) : h("div", { class: "group-label" }, title);
    add(listEl, h("section", { class: "group" }, head, items.map(draw)));
  }

  // ---------------------------------------------------------------- who does what
  function me() { return (S.data && S.data.me) || ""; }

  function whoTag(current, label, onPick) {
    return h("button", { class: "who" + (current ? "" : " none"), type: "button", disabled: S.busy,
      "aria-label": label + ": " + (current || "nobody"), onclick: function () { openPicker(label, current, onPick); } },
      current ? [icon("user"), current] : icon("plus"));
  }

  function openPicker(title, current, pick, whoAmI) {
    S.picker = { title: title, current: current || "", pick: pick, whoAmI: !!whoAmI };
    render();
  }

  function closePicker() { S.picker = null; render(); }

  function renderMe(d) {
    meEl.hidden = !d || !d.platesOn;           // names are on with the Plates tab (people.py)
    meEl.textContent = "";
    var name = me();
    if (name) add(meEl, name.charAt(0).toUpperCase()); else add(meEl, icon("user"));
    meEl.setAttribute("aria-label", name ? "This phone is " + name + ". Change" : "Who's using this phone?");
    meEl.title = name || "Who are you?";
  }
  meEl.addEventListener("click", function () {
    openPicker("Who are you?", me(), function (n) { if (n) setMe(n); }, true);
  });

  function renderPicker() {
    var p = S.picker;
    pickerEl.textContent = "";
    pickerEl.hidden = !p;
    if (!p) return;
    var mine = me(), all = (S.data && S.data.people) || [];
    var names = (mine ? [mine] : []).concat(all.filter(function (n) { return n !== mine; }));   // you first
    function choose(n) { var pick = p.pick; S.picker = null; render(); pick(n); }
    var input = h("input", { type: "text", placeholder: "New name", maxlength: "24", "aria-label": "New name", autocomplete: "off" });
    function addName() { var n = input.value.trim().replace(/\s+/g, " "); if (n) choose(n); }
    input.addEventListener("keydown", function (ev) { if (ev.key === "Enter") addName(); });
    add(pickerEl, h("div", { class: "sheet-card", role: "dialog", "aria-modal": "true", "aria-label": p.title },
      h("h3", null, p.title),
      h("div", { class: "names" },
        names.map(function (n) {
          return h("button", { type: "button", "aria-pressed": String(p.current === n), onclick: function () { choose(n); } },
            n === mine && !p.whoAmI ? "Me" : n);
        }),
        p.whoAmI ? null : h("button", { type: "button", "aria-pressed": String(!p.current), onclick: function () { choose(""); } }, "Nobody")),
      h("div", { class: "add" }, input, h("button", { class: "btn btn-secondary btn-small", type: "button", onclick: addName }, "Add"))));
  }
  pickerEl.addEventListener("click", function (ev) { if (ev.target === pickerEl) closePicker(); });
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && S.picker) closePicker(); });

  function assign(kind, ids, who) {
    S.busy = true; render();
    return api("POST", "/api/shop/assign", { device: DEVICE, kind: kind, ids: ids, who: who }).then(function () {
      S.busy = false; return load();
    }, function (e) { S.busy = false; toast(e.message); return load(); });
  }

  function setMe(name) {
    S.busy = true; render();
    return api("POST", "/api/shop/me", { device: DEVICE, name: name }).then(function () {
      S.busy = false; return load();
    }, function (e) { S.busy = false; toast(e.message); return load(); });
  }

  function openLink(url) {
    return /^https:\/\/[a-z0-9.-]*onshape\.com\//.test(url || "") ? h("a", { class: "open", href: url, target: "_blank",
      rel: "noopener noreferrer", "aria-label": "Open the assembly in Onshape", title: "Open in Onshape" }, icon("open")) : null;
  }

  // ---------------------------------------------------------------- shafts
  function renderShafts(d) {
    var all = d.shafts;
    function by(a, b) { return (a.startedAt || "").localeCompare(b.startedAt || ""); }
    // one subsystem's shafts together: by assembly, then length (Rush: by stock first)
    function byAsm(a, b) { return a.assembly.localeCompare(b.assembly) || a.length - b.length; }
    function byStock(a, b) { return d.order.indexOf(a.stock) - d.order.indexOf(b.stock) || byAsm(a, b); }
    function cards(ctx) { return function (s) { return card(s, ctx); }; }
    var name = d.me;
    // yours: what this phone started, and what's assigned to this phone's name that nobody started yet
    function yours(s) { return (s.started && s.mine) || (!s.started && name && s.who === name); }
    section("Yours", all.filter(yours).sort(by), cards("yours"));
    section("Rush", all.filter(function (s) { return s.rush && !s.started && !yours(s); }).sort(byStock), cards("rush"), { kind: "shaft" });
    d.order.forEach(function (stock) {
      section(stock, all.filter(function (s) { return s.stock === stock && !s.started && !s.rush && !yours(s); }).sort(byAsm),
        cards("stock"), { kind: "shaft" });
    });
    section("In progress", all.filter(function (s) { return s.started && !s.mine; }).sort(by), cards("others"));
    if (!all.length) add(listEl, h("p", { class: "muted" }, "Nothing to make."));
    add(listEl, h("section", { class: "group" }, h("div", { class: "group-label" }, "Done today"),
      d.done.length ? h("div", null, d.done.map(doneRow)) : h("p", { class: "muted" }, "Nothing yet.")));
  }

  function len(x) { return Number(x).toFixed(3); }

  function card(s, ctx) {
    var off = S.busy;
    return h("article", { class: "item" + (s.started ? " started" : "") },
      h("div", { class: "item-top" },
        h("div", { class: "item-name" },
          h("div", { class: "len" }, h("span", { class: "k" }, "Length"), h("span", { class: "v" }, len(s.length))),
          h("span", { class: "from" }, (ctx === "stock" ? s.assembly : s.stock + " · " + s.assembly)
            + (s.started && !s.mine && s.startedBy ? " · " + s.startedBy + " started it" : ""))),
        s.changed ? h("span", { class: "chip bad" }, "Changed in CAD") : null,
        s.rush && ctx !== "rush" ? h("span", { class: "chip rush" }, "Rush") : null,
        whoTag(s.who, "Assign to", function (n) { assign("shaft", [s.id], n); }),
        h("span", { class: "qty", "aria-label": "Quantity " + s.qty }, "×" + s.qty),
        openLink(s.url)),
      s.steps.length ? h("ul", { class: "todo" }, s.steps.map(function (x) { return h("li", null, x); })) : null,
      s.note ? h("p", { class: "note" }, s.note) : null,
      h("div", { class: "item-btns" },
        s.started && s.mine
          ? h("button", { class: "btn btn-ghost", type: "button", disabled: off, onclick: function () { post("stop", s); } }, "Stop")
          : s.started ? h("span")
          : h("button", { class: "btn btn-secondary", type: "button", disabled: off, onclick: function () { post("start", s); } }, "Start"),
        h("button", { class: "btn btn-primary", type: "button", disabled: off, onclick: function () { post("done", s); } }, icon("check"), "Done")));
  }

  function doneRow(s) {
    return h("div", { class: "done-row" }, h("span", { class: "mark" }, icon("check")),
      h("span", { class: "name" }, s.stock + ", " + len(s.length) + " ×" + s.qty,
        h("span", { class: "sub" }, s.assembly + " · " + s.when + (s.by ? " · " + s.by : ""))),
      s.undo ? h("button", { class: "btn btn-ghost btn-small", type: "button", disabled: S.busy, onclick: function () { post("undo", s); } },
        icon("undo"), "Undo") : null);
  }

  // ---------------------------------------------------------------- bought parts
  // A part needs qty: inBin are in the bin, onOrder are on the order sheet, left are still needed (bought.py).
  function renderParts(d) {
    function byName(a, b) { return a.name.localeCompare(b.name, undefined, { numeric: true }); }
    var needed = d.parts.filter(function (p) { return p.left > 0; });
    var name = d.me;
    function yours(p) { return name && p.who === name; }
    section("Yours", needed.filter(yours).sort(byName), function (p) { return partCard(p, "need"); });
    var others = needed.filter(function (p) { return !yours(p); });
    var asms = others.map(function (p) { return p.assembly; }).filter(function (a, i, all) { return all.indexOf(a) === i; }).sort();
    asms.forEach(function (a) {          // each subsystem's parts together: they go in its bin
      section(a ? a + " bin" : "Bin", others.filter(function (p) { return p.assembly === a; }).sort(byName),
        function (p) { return partCard(p, "need"); }, { kind: "part" });
    });
    if (!needed.length) add(listEl, h("p", { class: "muted" }, "Nothing needed."));
    section("On the order sheet", d.parts.filter(function (p) { return p.left <= 0 && p.onOrder > 0; })
      .sort(function (a, b) { return a.assembly.localeCompare(b.assembly) || byName(a, b); }), function (p) { return partCard(p, "ordered"); });
    add(listEl, h("section", { class: "group" }, h("div", { class: "group-label" }, "In the bin today"),
      d.bin.length ? h("div", null, d.bin.map(binRow)) : h("p", { class: "muted" }, "Nothing yet.")));
  }

  function ago(days) { return days === 0 ? "ordered today" : days === 1 ? "ordered yesterday" : "ordered " + days + " days ago"; }

  function partCard(p, where) {
    var off = S.busy, sheet = SHEET_RE.test(S.data.orderSheet || ""), ask = S.ask && S.ask.id === p.id ? S.ask : null;
    var bits = [];
    if (where === "need" && p.vendor) bits.push(p.vendor);
    if (where === "ordered" && p.assembly) bits.push(p.assembly);
    if (p.inBin) bits.push(p.inBin + " of " + p.qty + " in the bin");
    if (where === "need" && p.onOrder) bits.push(p.onOrder + " on the order sheet");
    if (where === "ordered" && p.orderedDays !== null && p.orderedDays !== undefined) bits.push(ago(p.orderedDays));
    var most = p.qty - p.inBin;
    return h("article", { class: "item" + (where === "ordered" ? " ordered" : "") },
      h("div", { class: "item-top" },
        h("div", { class: "item-name part-name" }, p.name,
          bits.length ? h("span", { class: "from" + (where === "ordered" && p.late ? " late" : "") }, bits.join(" · ")) : null),
        p.changed ? h("span", { class: "chip bad" }, "Changed in CAD")
          : p.cadNow ? h("span", { class: "chip bad" }, "CAD has " + p.cadNow) : null,
        whoTag(p.who, "Assign to", function (n) { assign("part", [p.id], n); }),
        h("span", { class: "qty", "aria-label": "Quantity " + (where === "need" ? p.left : p.onOrder) }, "×" + (where === "need" ? p.left : p.onOrder)),
        openLink(p.url)),
      p.note ? h("p", { class: "note" }, p.note) : null,
      ask && ask.kind === "order" ? h("div", { class: "order-box" },
        h("p", null, (S.copied ? "Row copied: paste it in Description. " : "") + "Added " + p.left + " to the sheet?"),
        h("div", { class: "row" },
          h("button", { class: "btn btn-ghost btn-small", type: "button", onclick: function () { S.ask = null; render(); } }, "Not yet"),
          h("button", { class: "btn btn-primary btn-small", type: "button", disabled: off,
            onclick: function () { S.ask = null; post("part-ordered", p); } }, "It's on the sheet"))) : null,
      ask && ask.kind === "bin" ? h("div", { class: "order-box" },
        h("div", { class: "count" }, h("p", null, "How many went in the bin?"),
          h("span", { class: "stepper" },
            h("button", { type: "button", "aria-label": "One fewer", onclick: function () { ask.n = Math.max(1, ask.n - 1); render(); } }, icon("minus")),
            h("span", { "aria-live": "polite" }, String(ask.n)),
            h("button", { type: "button", "aria-label": "One more", onclick: function () { ask.n = Math.min(most, ask.n + 1); render(); } }, icon("plus")))),
        h("div", { class: "row" },
          h("button", { class: "btn btn-ghost btn-small", type: "button", onclick: function () { S.ask = null; render(); } }, "Cancel"),
          h("button", { class: "btn btn-primary btn-small", type: "button", disabled: off,
            onclick: function () { var n = ask.n; S.ask = null; post("part-bin", p, { n: n }); } }, "Put " + ask.n + " in the bin"))) : null,
      ask ? null : h("div", { class: "item-btns" + (where === "need" && !sheet ? " one" : "") },
        where === "ordered" ? h("span") : sheet ? h("button", { class: "btn btn-secondary", type: "button", disabled: off,
          onclick: function () { order(p); } }, icon("cart"), "Order") : null,
        h("button", { class: "btn btn-primary", type: "button", disabled: off, onclick: function () {
          // more than one: ask how many (all the rest to start; on the order sheet: the ones ordered)
          if (most > 1) { S.ask = { id: p.id, kind: "bin", n: where === "ordered" ? p.onOrder : most }; render(); }
          else post("part-bin", p, { n: most });
        } }, icon("bin"), "In the bin")));
  }

  // The order sheet's columns E to I, tab-separated so pasting at Description fills the row: Description, Link,
  // Supplier, Part Number, Units (the team's sheet, 2026-11-02). Name, why, subteam and project are the person's.
  function sheetRow(p) {
    return [p.name, p.link || "", p.vendor || "", p.number || "", String(p.left)].map(function (x) {
      return String(x).replace(/[\t\r\n]+/g, " ");
    }).join("\t");
  }

  // Order: copy the part's row, open the order sheet in a new tab, then ask whether they added it. The copy goes
  // first, while this page still has the focus (a phone switches to the new tab).
  function order(p) {
    var url = S.data && S.data.orderSheet;
    if (!SHEET_RE.test(url || "")) return;
    var row = sheetRow(p);
    var copied = copyNow(row);
    if (!copied && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(row).then(function () { if (S.ask && S.ask.id === p.id) { S.copied = true; render(); } }, function () {});
    }
    window.open(url, "_blank", "noopener,noreferrer");
    S.ask = { id: p.id, kind: "order" }; S.copied = copied;
    render();
  }

  function copyNow(text) {
    var ta = h("textarea", { class: "copy-buffer", readonly: true, "aria-hidden": "true", tabindex: "-1" });
    ta.value = text;
    document.body.appendChild(ta);
    var ok = false;
    try { ta.select(); ta.setSelectionRange(0, text.length); ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return !!ok;
  }

  function binRow(p) {
    return h("div", { class: "done-row" }, h("span", { class: "mark" }, icon("check")),
      h("span", { class: "name wrap" }, p.name, h("span", { class: "sub" },
        (p.assembly ? p.assembly + " · " : "") + "×" + p.n + (p.n < p.qty ? " of " + p.qty : "") + " · " + p.when)),
      p.undo ? h("button", { class: "btn btn-ghost btn-small", type: "button", disabled: S.busy, onclick: function () { post("part-undo", p); } },
        icon("undo"), "Undo") : null);
  }

  // ---------------------------------------------------------------- plates (the sheets at the machine)
  function renderPlates(d) {
    if (d.platesError) add(listEl, h("div", { class: "notice" }, d.platesError));
    if (!d.sheets) { add(listEl, h("p", { class: "muted" }, "Loading…")); return; }
    var name = d.me;
    function yours(s) { return !!name && (s.who === name || s.plates.some(function (p) { return p.who === name; })); }
    renderSpares(d);
    section("Yours", d.sheets.filter(yours), sheetCard);
    section("Ready to cut", d.sheets.filter(function (s) { return !s.cut && !yours(s); }), sheetCard);
    section("Cut today", d.sheets.filter(function (s) { return s.cut && !yours(s); }), sheetCard);
    if (!d.sheets.length) add(listEl, h("p", { class: "muted" }, "Nothing ready to cut."));
  }

  function sheetCard(s) {
    return h("article", { class: "item" + (s.cut ? " cut" : "") },
      h("div", { class: "item-top" },
        h("div", { class: "item-name part-name" }, s.title, s.sub ? h("span", { class: "from" }, s.sub) : null),
        s.rush && !s.cut ? h("span", { class: "chip rush" }, "Rush") : null,
        s.cut ? h("span", { class: "chip" }, "Cut") : whoTag(s.who, "Who cuts it", function (n) { assign("sheet", [s.id], n); })),
      s.plates.length ? h("div", { class: "plates" }, s.plates.map(function (p) {
        return h("div", { class: "plate" }, h("span", { class: "n" }, p.name), p.spare ? h("span", { class: "chip spare" }, "Spare") : null,
          p.qty ? h("span", { class: "q" }, "×" + p.qty) : null,
          whoTag(p.who, "Who finishes it", function (n) { assign("plate", [p.id], n); }));
      })) : null);
  }

  // ---------------------------------------------------------------- spares (panel/spares.py)
  function renderSpares(d) {
    var rows = (d.spares || []).map(function (s) {
      return h("div", { class: "done-row" }, h("span", { class: "name wrap" }, s.name, h("span", { class: "sub" }, s.where)),
        h("span", { class: "qty" }, "×" + s.qty));
    });
    add(listEl, h("section", { class: "group" },
      h("div", { class: "group-head" }, h("span", { class: "group-label" }, "Spares"),
        h("button", { class: "btn btn-secondary btn-small", type: "button", onclick: openSpares }, icon("plus"), "Send spares")),
      rows.length ? h("div", { class: "item spare-list" }, rows) : null));
  }

  function openSpares() {
    S.spares = { choices: null, counts: {}, q: "", error: "" };
    render(); window.scrollTo(0, 0);
    api("GET", "/api/shop?device=" + encodeURIComponent(DEVICE) + "&choices=1").then(function (d) {
      if (!S.spares) return;
      S.spares.choices = d.choices || []; S.spares.error = d.platesError || ""; render();
    }, function (e) { if (S.spares) { S.spares.choices = []; S.spares.error = e.message; render(); } });
  }
  function closeSpares() { S.spares = null; render(); load(); }
  backEl.addEventListener("click", closeSpares);

  function renderSendSpares() {
    var sp = S.spares;
    if (sp.error) add(listEl, h("div", { class: "notice" }, sp.error));
    if (!sp.choices) { add(listEl, h("p", { class: "muted" }, "Loading…")); return; }
    if (!sp.choices.length) { add(listEl, h("p", { class: "muted" }, "No plates yet: AutoCAM offers the ones it has nested.")); return; }
    var search = h("input", { class: "search", type: "search", placeholder: "Find a plate", "aria-label": "Find a plate", value: sp.q,
      oninput: function () { sp.q = search.value; draw(); } });
    var rowsEl = h("div", { class: "group-list" });
    add(listEl, h("label", { class: "search-box" }, search));
    add(listEl, rowsEl);
    function draw() {
      rowsEl.textContent = "";
      var q = sp.q.trim().toLowerCase();
      var shown = 0;
      sp.choices.forEach(function (g) {
        var rows = g.rows.filter(function (r) { return !q || r.name.toLowerCase().indexOf(q) >= 0; });
        if (!rows.length) return;
        shown += rows.length;
        add(rowsEl, h("section", { class: "group" }, h("span", { class: "group-label" }, g.title),
          h("div", { class: "item spare-list" }, rows.map(spareRow))));
      });
      if (!shown) add(rowsEl, h("p", { class: "muted" }, "No plate called that."));
    }
    draw();
    var ids = Object.keys(sp.counts).filter(function (k) { return sp.counts[k] > 0; });
    var total = ids.reduce(function (t, k) { return t + sp.counts[k]; }, 0);
    function count(x, one) { return x + " " + one + (x === 1 ? "" : "s"); }
    footEl.hidden = false;
    add(footEl, h("button", { class: "btn btn-primary", type: "button", disabled: S.busy || !total, onclick: function () { sendSpares(ids); } },
      S.busy ? "Sending…" : total ? "Send " + count(total, "spare") + " of " + count(ids.length, "plate") : "Pick how many spares"));
  }

  function spareRow(r) {
    var sp = S.spares, n = sp.counts[r.id] || 0;
    function set(v) { sp.counts[r.id] = Math.max(0, Math.min(100, v)); render(); }
    return h("div", { class: "done-row spare-row" },
      h("span", { class: "name wrap" }, r.name, r.sub ? h("span", { class: "sub" }, r.sub) : null),
      h("div", { class: "stepper", role: "group", "aria-label": r.name + " spares" },
        h("button", { type: "button", "aria-label": "One fewer", onclick: function () { set(n - 1); } }, icon("minus")),
        h("span", { class: n ? "on" : "" }, String(n)),
        h("button", { type: "button", "aria-label": "One more", onclick: function () { set(n + 1); } }, icon("plus"))));
  }

  function sendSpares(ids) {
    var sp = S.spares;
    S.busy = true; render();
    api("POST", "/api/shop/spares", { device: DEVICE, items: ids.map(function (k) { return { id: k, n: sp.counts[k] }; }) }).then(function (r) {
      S.busy = false;
      var missed = (r.results || []).filter(function (x) { return !x.sent; }).length;
      toast("Sent " + r.sent + " spare" + (r.sent === 1 ? "" : "s") + (missed ? " (" + missed + " plate" + (missed === 1 ? "" : "s") + " no longer on the board)" : ""));
      closeSpares();
    }, function (e) { S.busy = false; toast(e.message); render(); });
  }

  // ---------------------------------------------------------------- staying current
  // Every 10 seconds while the page is showing (only the lists on the laptop: no Onshape or Trello calls).
  load();
  setInterval(function () { if (!document.hidden) load(); }, 10000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
})();
