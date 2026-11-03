// The shop's phone page (panel/api.py: /api/shop). Two tabs:
//   Shafts (shafts.py): one card per shaft: Start, Done.
//   Parts (bought.py): bought parts sent from an assembly: In the bin (the team has it), or Order. Order opens the
//   team's order sheet in a new tab and copies the part's name; people write the row themselves (AutoCAM never
//   writes to the sheet), then say "It's on the sheet".
// "Yours" is what this phone started (a random id kept on the phone; no names). Plain JavaScript, no build step;
// everything from the server goes in as text, never as HTML.
(function () {
  "use strict";

  var KEY = new URLSearchParams(location.search).get("key") || "";
  var listEl = document.getElementById("list");
  var toastEl = document.getElementById("toast");
  var tabsEl = document.getElementById("tabs");
  var titleEl = document.getElementById("title");
  var SHEET_RE = /^https:\/\/docs\.google\.com\/spreadsheets\//;
  var ICONS = {
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    undo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/></svg>',
    open: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>',
    bin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7h18"/><path d="M5 7l1.5 13h11L19 7"/><path d="M9 7V4h6v3"/></svg>',
    minus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
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
  var S = { data: null, error: "", busy: false, tab: location.hash === "#parts" ? "parts" : location.hash === "#shafts" ? "shafts" : remember("tab") || "shafts",
            ask: null, copied: false };        // ask: a part's Order or In the bin question, {id, kind, n}
  var toastTimer = 0;
  function toast(text) {
    toastEl.textContent = text; toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, 3000);
  }

  function load() {
    if (S.busy) return Promise.resolve();
    return api("GET", "/api/shop?device=" + encodeURIComponent(DEVICE)).then(function (d) {
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
    });
  });

  // ---------------------------------------------------------------- the page
  function render() {
    listEl.textContent = "";
    var d = S.data;
    var both = !!(d && d.shaftsOn && d.partsOn);
    var tab = d && !both ? (d.partsOn ? "parts" : "shafts") : S.tab;
    tabsEl.hidden = !both; titleEl.hidden = both;
    titleEl.textContent = !d ? "Shop" : tab === "parts" ? "Parts" : "Shafts";
    Array.prototype.forEach.call(tabsEl.querySelectorAll("button"), function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-tab") === tab));
    });
    document.title = tab === "parts" ? "Parts" : "Shafts";
    if (S.error) add(listEl, h("div", { class: "notice" }, S.error));
    if (!d) { if (!S.error) add(listEl, h("p", { class: "muted" }, "Loading…")); return; }
    if (tab === "parts") renderParts(d); else renderShafts(d);
  }

  function section(title, items, draw) {
    if (!items.length) return;
    add(listEl, h("section", { class: "group" }, h("div", { class: "group-label" }, title), items.map(draw)));
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
    section("Yours", all.filter(function (s) { return s.started && s.mine; }).sort(by), cards("yours"));
    section("Rush", all.filter(function (s) { return s.rush && !s.started; }).sort(byStock), cards("rush"));
    d.order.forEach(function (stock) {
      section(stock, all.filter(function (s) { return s.stock === stock && !s.started && !s.rush; }).sort(byAsm), cards("stock"));
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
          h("span", { class: "from" }, ctx === "stock" ? s.assembly : s.stock + " · " + s.assembly)),
        s.changed ? h("span", { class: "chip bad" }, "Changed in CAD") : null,
        s.rush && ctx !== "rush" ? h("span", { class: "chip rush" }, "Rush") : null,
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
      h("span", { class: "name" }, s.stock + ", " + len(s.length) + " ×" + s.qty, h("span", { class: "sub" }, s.assembly + " · " + s.when)),
      s.undo ? h("button", { class: "btn btn-ghost btn-small", type: "button", disabled: S.busy, onclick: function () { post("undo", s); } },
        icon("undo"), "Undo") : null);
  }

  // ---------------------------------------------------------------- bought parts
  // A part needs qty: inBin are in the bin, onOrder are on the order sheet, left are still needed (bought.py).
  function renderParts(d) {
    function byName(a, b) { return a.name.localeCompare(b.name, undefined, { numeric: true }); }
    var needed = d.parts.filter(function (p) { return p.left > 0; });
    var asms = needed.map(function (p) { return p.assembly; }).filter(function (a, i, all) { return all.indexOf(a) === i; }).sort();
    asms.forEach(function (a) {          // each subsystem's parts together: they go in its bin
      section(a ? a + " bin" : "Bin", needed.filter(function (p) { return p.assembly === a; }).sort(byName),
        function (p) { return partCard(p, "need"); });
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

  // ---------------------------------------------------------------- staying current
  // Every 10 seconds while the page is showing (only the lists on the laptop: no Onshape or Trello calls).
  load();
  setInterval(function () { if (!document.hidden) load(); }, 10000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
})();
