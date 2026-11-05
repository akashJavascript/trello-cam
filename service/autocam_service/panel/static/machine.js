// The machine page (machine.py, the mockup the user approved 2026-11-05): the operator's screen on the ShopSabre's
// PC. The list (in progress, ready to cut, placed by hand, checked programs, cut today), then one card for the step
// to do now. Each button is something Trello already understands (a box on the sheet card, or the card moving), so
// the service does the rest. Download saves the program to this PC's Downloads; WinCNC opens it from there.
// Plain JavaScript, no build step; everything from the server goes in as text, never as HTML.
(function () {
  "use strict";

  var KEY = new URLSearchParams(location.search).get("key") || "";
  var headEl = document.getElementById("head");
  var bodyEl = document.getElementById("body");
  var overEl = document.getElementById("over");
  var ICONS = {
    back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4l13 8-13 8z"/></svg>',
    download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12M7 10l5 5 5-5M4 21h16"/></svg>',
    undo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/></svg>',
    logo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 15c2-6 4 2 6-4s3 3 4 1"/></svg>'
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
  function stem(file) { return String(file || "").replace(/\.(tap|nc)$/i, ""); }

  // ---------------------------------------------------------------- talking to AutoCAM
  function api(method, path, body) {
    return fetch(path, {
      method: method, cache: "no-store",
      headers: Object.assign({ "X-AutoCAM-Key": KEY }, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) { var e = new Error(d.error || "AutoCAM didn't answer. Try again."); e.status = r.status; throw e; }
        return d;
      });
    });
  }
  function link(path, params) {
    var q = new URLSearchParams(Object.assign({}, params, { key: KEY }));
    return path + "?" + q.toString();
  }

  var S = { open: null, sheet: null, list: null, at: null, busy: false, got: {}, problem: false, confirm: false, toast: "", error: "" };
  var toastTimer = null;
  function say(text) {
    S.toast = text; render();
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { S.toast = ""; render(); }, 3500);
  }
  function fail(e) {
    S.busy = false;
    // the sheet's gone (moved on Trello, or archived): back to the list; anything else stays on the step
    if (e && (e.status === 404 || e.status === 409) && S.open) { S.open = null; S.sheet = null; loadList(); }
    say(e && e.message ? e.message : "Something went wrong. Try again.");
  }

  function loadList() {
    return api("GET", "/api/machine").then(function (d) { S.list = d; S.error = ""; render(); },
      function (e) { S.error = e.message; render(); });
  }
  function loadSheet(id, keepAt) {
    return api("GET", "/api/machine/sheet?id=" + encodeURIComponent(id)).then(function (d) {
      if (S.open !== id) return;
      S.sheet = d;
      if (!keepAt || !S.at) S.at = d.at;
      render();
    }, fail);
  }
  function act(body) {
    S.busy = true; render();
    return api("POST", "/api/machine/act", Object.assign({ id: S.open }, body)).then(function (d) { S.busy = false; return d; },
      function (e) { fail(e); throw e; });
  }
  function save() {
    if (!S.open || !S.at) return;
    api("POST", "/api/machine/act", { id: S.open, do: "progress", step: S.at.step, rep: S.at.rep, lap: S.at.lap,
      lapRan: S.at.lapRan }).catch(function () {});
  }
  function go(step) {
    S.at = { step: step, rep: 1, lap: 0, lapRan: false };
    save(); render(); window.scrollTo(0, 0);
  }
  function next() { go(Math.min(S.at.step + 1, S.sheet.steps.length - 1)); }

  // ---------------------------------------------------------------- the list
  function listCard(s) {
    var button = s.progress ? "Continue" : "Start";
    return h("article", { class: "item" },
      h("div", { class: "item-top" },
        h("div", { class: "item-name part-name" }, s.title, h("span", { class: "from" }, s.sub)),
        s.progress ? h("span", { class: "chip now" }, "Step " + s.progress.step + " of " + s.progress.of) : null,
        s.rush ? h("span", { class: "chip rush" }, "Rush") : null,
        s.group === "check" ? h("span", { class: "chip good" }, "Passed") : null),
      s.plates.length ? h("div", { class: "plates" }, s.plates.map(function (p) {
        return h("div", { class: "plate" }, h("span", { class: "n" }, p.name), p.qty ? h("span", { class: "q" }, "×" + p.qty) : null);
      })) : null,
      h("div", { class: "item-btns one" }, h("button", { class: "btn btn-primary", type: "button",
        onclick: function () { open(s.id); } }, icon("play"), button)));
  }
  function group(label, list) {
    return list.length ? h("section", { class: "group" }, h("span", { class: "group-label" }, label), list.map(listCard)) : null;
  }
  function listView() {
    if (S.error && !S.list) return h("div", { class: "notice" }, S.error);
    if (!S.list) return h("p", { class: "muted" }, "Loading…");
    var all = S.list.sheets;
    var going = all.filter(function (s) { return s.progress; });
    var rest = all.filter(function (s) { return !s.progress; });
    var by = function (g) { return rest.filter(function (s) { return s.group === g; }); };
    var ready = by("ready").sort(function (a, b) { return (b.rush ? 1 : 0) - (a.rush ? 1 : 0); });
    var out = [S.error ? h("div", { class: "notice" }, S.error) : null,
      group("In progress", going), group("Ready to cut", ready), group("Placed by hand", by("hand")),
      group("Checked programs", by("check"))];
    if (!all.length) out.push(h("p", { class: "muted" }, "Nothing ready to cut."));
    if (S.list.cut.length) {
      out.push(h("section", { class: "group" }, h("span", { class: "group-label" }, "Cut today"),
        h("div", { class: "item list" }, S.list.cut.map(function (c) {
          return h("div", { class: "done-row" }, h("span", { class: "mark" }, icon("check")),
            h("span", { class: "name" }, c.title, h("span", { class: "sub" }, c.sub)),
            c.undo > 0 ? h("button", { class: "btn btn-ghost btn-small", type: "button", onclick: function () { undo(c.id); } },
              icon("undo"), "Undo") : null);
        }))));
    }
    return out;
  }
  function open(id) {
    S.open = id; S.sheet = null; S.at = null; S.got = {}; render(); window.scrollTo(0, 0);
    loadSheet(id, false);
  }
  function home() { S.open = null; S.sheet = null; S.at = null; S.problem = false; S.confirm = false; render(); loadList(); }
  function undo(id) {
    api("POST", "/api/machine/act", { id: id, do: "undo" }).then(function () { say("Undone: it stays in Ready to cut."); loadList(); },
      function (e) { say(e.message); loadList(); });
  }

  // ---------------------------------------------------------------- one sheet: the step to do now
  function card(k, v, sub, word, extra, btns) {
    return h("article", { class: "item started" },
      h("div", { class: "len" }, h("span", { class: "k" }, k), h("span", { class: "v" + (word ? " word" : "") }, v),
        sub ? h("span", { class: "from" }, sub) : null),
      extra, btns);
  }
  function todo(items) {
    items = items.filter(Boolean);
    return items.length ? h("ul", { class: "todo" }, items.map(function (t) { return h("li", null, t); })) : null;
  }
  function one(label, onclick, cls) {
    return h("div", { class: "item-btns one" }, h("button", { class: "btn " + (cls || "btn-primary"), type: "button",
      disabled: S.busy, onclick: onclick }, icon("check"), label));
  }
  function picture(what) {
    var img = h("img", { src: link("/api/machine/picture", { id: S.open, what: what }), alt: what === "offcut" ? "What the offcut looks like" : "The sheet" });
    var box = h("div", { class: "pic" }, img);
    img.addEventListener("error", function () { box.hidden = true; });
    return box;
  }
  function download(file, onDone, doneLabel) {
    var ready = !!file;
    var got = !!S.got[file];
    var dl = h("a", { class: "btn btn-secondary", href: ready ? link("/api/machine/file", { id: S.open, name: file }) : "#",
      download: ready ? file : null, "aria-disabled": ready ? null : "true",
      onclick: function () { if (ready) { S.got[file] = true; setTimeout(render, 50); say(file + " is in Downloads. In WinCNC: the yellow folder, then Downloads."); } } },
      icon(got ? "check" : "download"), got ? "Downloaded" : "Download");
    return h("div", { class: "item-btns" }, dl, h("button", { class: "btn btn-primary", type: "button", disabled: S.busy || !ready,
      onclick: onDone }, icon("check"), doneLabel || "Done"));
  }

  function stepCard(st) {
    var sh = S.sheet, at = S.at;
    switch (st.t) {
      case "find":
        if (st.asked) {
          return card("Can’t find #" + st.n, "Pick one", "No other piece fits this program as it is", true, null,
            h("div", { class: "group" },
              h("button", { class: "opt", type: "button", disabled: S.busy, onclick: function () { stock("renest"); } },
                h("b", null, "Nest it again"), h("span", null, "On whatever stock there is. It comes back for review")),
              h("button", { class: "opt", type: "button", disabled: S.busy, onclick: function () { stock("newsheet"); } },
                h("b", null, "Cut it on a new sheet"), h("span", null, "The same program, no new review"))));
        }
        if (st.missing) return card("Find the offcut", "#" + st.n, "Looking for another piece…", true, h("p", { class: "waiting" }, "One moment"), null);
        return card("Find the offcut", "#" + st.n, [sh.title, st.size].filter(Boolean).join(" · "), true,
          [st.instead ? h("p", { class: "note" }, "#" + st.instead + " wasn’t on the rack: this one fits the same program") : null,
            picture("offcut")],
          st.canMiss ? h("div", { class: "item-btns" },
            h("button", { class: "btn btn-ghost", type: "button", disabled: S.busy, onclick: function () { S.confirm = true; render(); } }, "Can’t find it"),
            h("button", { class: "btn btn-primary", type: "button", disabled: S.busy, onclick: next }, icon("check"), "Found it"))
            : one("Found it", next));
      case "load":
        return card("Load", st.stock, [st.where, "clamps " + st.clamps, "mist " + (st.mist ? "on" : "off")].join(" · "), true,
          [st.picture ? picture("sheet") : null,
            todo([st.how, st.cutter ? "Cutter: " + st.cutter : "", "Zero X/Y at the front-left corner", "Z from the tool setter"])],
          one("Loaded", loaded));
      case "piece":
        return card("Get a piece", st.stock, st.size ? "Any piece at least " + st.size : "Any piece big enough for the part", true, null,
          one("Got one", next));
      case "loadhand":
        return card("Load", "Anywhere on the bed", "Clamps at least 1/2 in clear of the part · mist " + (st.mist ? "on" : "off"), true,
          todo([st.cutter ? "Cutter: " + st.cutter : "", "Zero X/Y at the part’s front-left corner", "Z from the tool setter"]),
          one("Loaded", loaded));
      case "cutter":
        return card("Change the cutter", st.cutter + (st.tool ? " · " + st.tool : ""), "Measure it on the tool setter", true, null,
          one("Done", next));
      case "air":
        return card("Air test", st.file ? stem(st.file) : "Making it…", "Nothing is cut: it runs " + st.gap + " above the sheet", false,
          todo(["Check it misses the clamps"]), download(st.file, next));
      case "run":
        var file = st.file, extra = [];
        var stops = st.nostop ? !st.nostop.on : !!st.stops;
        if (st.nostop) {
          if (st.nostop.on) file = st.nostop.ready ? st.nostop.file : "";
          extra.push(h("button", { class: "tog", type: "button", "aria-pressed": String(stops), disabled: S.busy,
            onclick: function () { switchStops(!stops); } }, h("span", { class: "knob" }), "Stop after each part"));
          extra.push(todo([stops ? "Take each part out, then press ENTER" : "Cuts the whole sheet in one go. Hands off until it’s done"]));
        }
        var repeat = st.repeat > 1;
        var k = repeat ? "Run " + at.rep + " of " + st.repeat : "Run";
        var sub = [repeat && at.rep > 1 ? "Zero at a fresh spot first" : "", st.note || "", st.min ? "about " + st.min + " min" : ""]
          .filter(Boolean).join(" · ");
        return card(k, file ? stem(file) : "Making the no-stop version…", sub, false, extra,
          download(file, function () { if (repeat && at.rep < st.repeat) { at.rep += 1; save(); render(); } else next(); }));
      case "fit":
        if (st.answered) return card("Try the fit", st.feature, "Saved: " + st.answered, true, null, one("Next", next));
        if (at.lap > 0 && !at.lapRan) {
          var lap = st.laps[at.lap - 1];
          return card("Run a lap", stem(lap.file), "Opens it a little · same cutter and zero", false, null,
            download(lap.file, function () { at.lapRan = true; save(); render(); }));
        }
        var last = at.lap >= st.laps.length;
        return card("Try the fit", st.feature, st.part, true,
          [at.lap === 0 && st.cutout ? todo(["Break the slug out first"]) : null, h("p", { class: "muted" }, "Does it fit?")],
          h("div", { class: "item-btns" },
            h("button", { class: "btn btn-ghost", type: "button", disabled: S.busy, onclick: function () {
              if (last) fitAnswer(st, st.items.length - 1); else { at.lap += 1; at.lapRan = false; save(); render(); }
            } }, last ? "Still tight" : "Not yet"),
            h("button", { class: "btn btn-good", type: "button", disabled: S.busy, onclick: function () { fitAnswer(st, at.lap); } },
              icon("check"), "Yes")));
      case "checkrun":
        return card("Run your program", stem(st.file), "Zero at " + st.zero, false, null, download(st.file, finish, "Done"));
      case "finishhand":
        return card("Done cutting", st.what, "", true, null, one("Move to Cut", finish));
    }
    // finish: AutoCAM says whether what's left is worth keeping; one link changes its mind
    if (st.kind === "offcut") {
      return card("Done cutting", st.usedUp ? "#" + st.n + " is used up" : "Put #" + st.n + " back on the rack",
        st.usedUp ? "Scrap what’s left" : (st.size ? st.size + " free after this" : ""), true, null, one("Move to Cut", finish));
    }
    var v = st.size ? "What’s left: " + st.size : "Done cutting";
    if (!st.can) return card("Done cutting", v, "Too small to keep: scrap it", true, null, one("Move to Cut", finish));
    return card("Done cutting", v, st.keep ? "AutoCAM keeps it as an offcut" : "AutoCAM scraps it", true,
      h("button", { class: "link-btn quiet", type: "button", disabled: S.busy, onclick: function () { keep(!st.keep); } },
        st.keep ? "Scrap it instead" : "Keep it after all"),
      one("Move to Cut", finish));
  }

  // ---------------------------------------------------------------- what the buttons do
  function loaded() { act({ do: "loaded" }).then(next, function () {}); }
  function refresh() { return loadSheet(S.open, true); }
  function switchStops(on) { act({ do: "stops", on: on }).then(refresh, function () {}); }
  function keep(on) { act({ do: "keep", on: on }).then(refresh, function () {}); }
  function stock(choice) {
    act({ do: "stock", choice: choice }).then(function () {
      say(choice === "renest" ? "Its parts go back to be nested again." : "Cut it on a new sheet: the same program.");
      if (choice === "renest") home(); else refresh();
    }, function () {});
  }
  function cantFind() {
    S.confirm = false;
    act({ do: "cantfind" }).then(function () { say("Marked missing. Looking for another piece…"); refresh(); }, function () {});
  }
  function fitAnswer(st, at) {
    act({ do: "fit", series: st.series, at: at }).then(function () { say("Saved: the next sheet starts there."); next(); }, function () {});
  }
  function finish() {
    act({ do: "finish" }).then(function (d) {
      home();
      say("Moving to Cut in " + Math.round((d.undo || 180) / 60) + " minutes. Undo it below if that was a mistake.");
    }, function () {});
  }
  function problem(what) {
    act({ do: "problem", what: what }).then(function () { home(); say("Sent back to Sheet review."); }, function () {});
  }

  function problemSheet() {
    var picked = {};
    var choices = ["Bit broke", "Sheet moved", "Part came loose", "Didn’t cut through"];
    return h("div", { class: "sheet", onclick: function (e) { if (e.target === e.currentTarget) { S.problem = false; render(); } } },
      h("div", { class: "sheet-card" }, h("h3", null, "What happened?"),
        h("div", { class: "names" }, choices.map(function (t) {
          var b = h("button", { type: "button", "aria-pressed": "false", onclick: function () {
            picked[t] = !picked[t]; b.setAttribute("aria-pressed", String(!!picked[t]));
          } }, t);
          return b;
        })),
        h("div", { class: "item-btns" },
          h("button", { class: "btn btn-ghost", type: "button", onclick: function () { S.problem = false; render(); } }, "Cancel"),
          h("button", { class: "btn btn-primary", type: "button", onclick: function () {
            S.problem = false; problem(choices.filter(function (t) { return picked[t]; }));
          } }, S.sheet && S.sheet.label === "Check" ? "Save" : "Send back to review"))));
  }
  function confirmSheet(st) {
    return h("div", { class: "sheet", onclick: function (e) { if (e.target === e.currentTarget) { S.confirm = false; render(); } } },
      h("div", { class: "sheet-card" }, h("h3", null, "Can’t find #" + st.n + "?"),
        h("p", { class: "muted" }, "AutoCAM marks it missing and looks for another piece the same program fits."),
        h("div", { class: "item-btns" },
          h("button", { class: "btn btn-ghost", type: "button", onclick: function () { S.confirm = false; render(); } }, "Cancel"),
          h("button", { class: "btn btn-primary", type: "button", onclick: cantFind }, "It’s not there"))));
  }

  function sheetView() {
    var sh = S.sheet;
    if (!sh) return h("p", { class: "muted" }, "Loading…");
    var at = S.at, st = sh.steps[at.step];
    var bars = sh.steps.length > 1 ? h("div", { class: "steps", role: "img", "aria-label": "Step " + (at.step + 1) + " of " + sh.steps.length },
      sh.steps.map(function (x, i) { return h("i", { class: i < at.step ? "done" : i === at.step ? "on" : null }); })) : null;
    var canBack = at.step > 0 || at.rep > 1 || at.lap > 0;
    return [bars, stepCard(st), h("div", { class: "links" },
      canBack ? h("button", { class: "link-btn quiet", type: "button", onclick: function () {
        if (at.rep > 1) at.rep -= 1;
        else if (at.lap > 0) { if (at.lapRan) at.lapRan = false; else at.lap -= 1; }
        else { at.step -= 1; at.rep = 1; at.lap = 0; at.lapRan = false; }
        save(); render();
      } }, "Back a step") : null,
      h("button", { class: "link-btn", type: "button", onclick: function () { S.problem = true; render(); } }, "Something went wrong"))];
  }

  // ---------------------------------------------------------------- render
  function render() {
    headEl.textContent = "";
    if (S.open) {
      add(headEl, [h("button", { class: "back", type: "button", "aria-label": "Back to the list", onclick: home }, icon("back")),
        h("span", { class: "title" }, S.sheet ? [S.sheet.title, S.sheet.label].filter(Boolean).join(" · ") : "")]);
    } else {
      add(headEl, [h("span", { class: "logo", "aria-hidden": "true" }, icon("logo")), h("span", { class: "title" }, "At the machine")]);
    }
    bodyEl.textContent = "";
    add(bodyEl, S.open ? sheetView() : listView());
    overEl.textContent = "";
    if (S.open && S.problem) add(overEl, problemSheet());
    if (S.open && S.confirm && S.sheet) add(overEl, confirmSheet(S.sheet.steps[S.at.step]));
    if (S.toast) add(overEl, h("div", { class: "toast", role: "status" }, S.toast));
  }

  // keep up with the board every 8 s (Trello is read at most every 15 s): a no-stop version or another offcut turns
  // up a pass after it's asked for
  setInterval(function () {
    if (document.hidden) return;
    if (S.open) { if (!S.busy) refresh(); } else loadList();
  }, 8000);
  render();
  loadList();
})();
