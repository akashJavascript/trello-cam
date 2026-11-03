// The shop's shaft list on a phone (panel/api.py: /api/shop; shafts.py). One card per shaft: Start, Done.
// "Yours" is what this phone started (a random id kept on the phone; no names). Plain JavaScript, no build step;
// everything from the server goes in as text, never as HTML.
(function () {
  "use strict";

  var KEY = new URLSearchParams(location.search).get("key") || "";
  var listEl = document.getElementById("list");
  var toastEl = document.getElementById("toast");
  var ICONS = {
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    undo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/></svg>',
    open: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>'
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

  // This phone's id: what it started is "Yours". Kept on the phone; a new one if storage is blocked.
  var DEVICE = (function () {
    var id = "";
    try { id = localStorage.getItem("autocam.shop.device") || ""; } catch (e) { /* private mode */ }
    if (!/^[A-Za-z0-9_-]{8,64}$/.test(id)) {
      var b = new Uint8Array(12);
      crypto.getRandomValues(b);
      id = Array.prototype.map.call(b, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
      try { localStorage.setItem("autocam.shop.device", id); } catch (e) { /* fine: this visit only */ }
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

  var S = { data: null, error: "", busy: false };
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

  function act(action, item) {
    S.busy = true; render();
    api("POST", "/api/shop/" + action, { id: item.id, device: DEVICE }).then(function () {
      S.busy = false; return load();
    }, function (e) {
      S.busy = false;
      toast(e.message);
      return load();
    });
  }

  // ---------------------------------------------------------------- the list
  function render() {
    listEl.textContent = "";
    if (S.error) add(listEl, h("div", { class: "notice" }, S.error));
    var d = S.data;
    if (!d) { if (!S.error) add(listEl, h("p", { class: "muted" }, "Loading…")); return; }
    var all = d.shafts;
    function by(a, b) { return (a.startedAt || "").localeCompare(b.startedAt || ""); }
    section("Yours", all.filter(function (s) { return s.started && s.mine; }).sort(by), "yours");
    section("Rush", all.filter(function (s) { return s.rush && !s.started; }), "rush");
    d.order.forEach(function (stock) {
      section(stock, all.filter(function (s) { return s.stock === stock && !s.started && !s.rush; }), "stock");
    });
    section("In progress", all.filter(function (s) { return s.started && !s.mine; }).sort(by), "others");
    if (!all.length) add(listEl, h("p", { class: "muted" }, "Nothing to make."));
    add(listEl, h("section", { class: "group" }, h("div", { class: "group-label" }, "Done today"),
      d.done.length ? h("div", null, d.done.map(doneRow)) : h("p", { class: "muted" }, "Nothing yet.")));
  }

  function section(title, items, ctx) {
    if (!items.length) return;
    add(listEl, h("section", { class: "group" }, h("div", { class: "group-label" }, title),
      items.map(function (s) { return card(s, ctx); })));
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
        /^https:\/\/[a-z0-9.-]*onshape\.com\//.test(s.url) ? h("a", { class: "open", href: s.url, target: "_blank",
          rel: "noopener noreferrer", "aria-label": "Open the assembly in Onshape", title: "Open in Onshape" }, icon("open")) : null),
      s.steps.length ? h("ul", { class: "todo" }, s.steps.map(function (x) { return h("li", null, x); })) : null,
      s.note ? h("p", { class: "note" }, s.note) : null,
      h("div", { class: "item-btns" },
        s.started && s.mine
          ? h("button", { class: "btn btn-ghost", type: "button", disabled: off, onclick: function () { act("stop", s); } }, "Stop")
          : s.started ? h("span")
          : h("button", { class: "btn btn-secondary", type: "button", disabled: off, onclick: function () { act("start", s); } }, "Start"),
        h("button", { class: "btn btn-primary", type: "button", disabled: off, onclick: function () { act("done", s); } }, icon("check"), "Done")));
  }

  function doneRow(s) {
    return h("div", { class: "done-row" }, h("span", { class: "mark" }, icon("check")),
      h("span", { class: "name" }, s.stock + ", " + len(s.length) + " ×" + s.qty, h("span", { class: "sub" }, s.assembly + " · " + s.when)),
      s.undo ? h("button", { class: "btn btn-ghost btn-small", type: "button", disabled: S.busy, onclick: function () { act("undo", s); } },
        icon("undo"), "Undo") : null);
  }

  // ---------------------------------------------------------------- staying current
  // Every 10 seconds while the page is showing (only the list on the laptop: no Onshape or Trello calls).
  load();
  setInterval(function () { if (!document.hidden) load(); }, 10000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
})();
