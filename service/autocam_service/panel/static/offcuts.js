// The shop page's Offcuts tab (offcut_photos.py, 2026-11-09; the mockup the user approved, and docs/offcut-photos.md).
//   The rack: each offcut's number, stock, size and a picture of what's used of it; Re-photo, Gone (with Undo).
//   Add an offcut / Re-photo, three steps: Photo (and what it is: material, thickness, length taped; the width is the
//   sheet's), Corners (suggested when they can be found; drag them, the front is the end at the bottom of the photo),
//   Used areas (the piece straightened, a box over each hole found; move, resize, delete, drag to add). Save writes the
//   offcut's card as a person would (AutoCAM's offcut pass numbers it); the number shows here a few seconds later.
// shop.js calls ShopOffcuts.init(ctx) with its helpers, renderList(d) for the tab and renderFlow() while a flow is
// open. Plain JavaScript, no build step; nothing from the server goes in as HTML.
(function () {
  "use strict";
  var C = null;                 // shop.js: {h, add, icon, api, toast, render, load, listEl, footEl, DEVICE}
  var F = null;                 // the open flow
  var pending = null;           // a new piece's card, until AutoCAM numbers it: {card, since}
  var gone = null;              // an offcut going (Undo for a few seconds): {id, number, timer}
  var NS = "http://www.w3.org/2000/svg";
  var MAX_SIDE = 1600;          // the photo is shrunk to this before it's sent

  function svg(tag, attrs) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function fmt(n) { var r = Math.round(n * 4) / 4; return r % 1 === 0 ? String(r) : String(r); }
  function mats() { return (C.data() && C.data().offcutMaterials) || []; }
  function mat(key) { return mats().filter(function (m) { return m.key === key; })[0] || null; }

  // ------------------------------------------------------------------ the rack
  // its little picture: along runs left to right on a full sheet's scale, the front at the left (a dark bar)
  function picture(o) {
    var k = 7, W = o.length * k, H = o.width * k, full = Math.max(o.sheet, o.length) * k;
    var s = svg("svg", { viewBox: "-2 -2 " + (full + 4) + " " + (H + 4), class: "mini", role: "img", preserveAspectRatio: "xMinYMid meet",
                         "aria-label": "#" + (o.number || "") + ", what's used of it shaded" });
    s.appendChild(svg("rect", { x: 0, y: 0, width: W, height: H, rx: 3, class: "mini-piece" }));
    (o.used || []).forEach(function (u) {
      s.appendChild(svg("rect", { x: u[1] * k, y: (o.width - u[2]) * k, width: (u[3] - u[1]) * k, height: (u[2] - u[0]) * k,
                                 rx: 2, class: "mini-used" }));
    });
    s.appendChild(svg("rect", { x: 0, y: 0, width: 5, height: H, rx: 2, class: "mini-front" }));
    return s;
  }

  function renderList(d) {
    var h = C.h, add = C.add, listEl = C.listEl;
    var list = (d.offcuts || []).filter(function (o) { return !gone || o.id !== gone.id; });
    if (pending) {
      var got = list.filter(function (o) { return o.id === pending.card; })[0];
      if (got && got.number) {
        C.toast("Write #" + got.number + " on the front end");
        pending = null;
      } else if (Date.now() - pending.since > 120000) {
        pending = null;
      }
    }
    if (d.offcutBoard === false) add(listEl, h("div", { class: "notice" }, "Couldn't read the Trello board just now."));
    if (pending) {
      add(listEl, h("article", { class: "item started" }, h("div", { class: "item-top" },
        h("span", { class: "num" }, "#…"), h("span", { class: "muted" }, "Numbering it…"))));
    }
    var groups = {};
    list.forEach(function (o) { (groups[o.label] = groups[o.label] || []).push(o); });
    Object.keys(groups).forEach(function (label) {
      add(listEl, h("section", { class: "group" }, h("div", { class: "group-label" }, label),
        groups[label].map(function (o) {
          return h("article", { class: "item" + (o.inUse ? " started" : "") },
            h("div", { class: "item-top offcut-top" },
              h("span", { class: "num" }, o.number ? "#" + o.number : "#…"),
              h("span", { class: "chip" }, o.thickness),
              o.inUse ? h("span", { class: "chip rush" }, "On a sheet") : null,
              h("span", { class: "size" }, fmt(o.length) + " × " + fmt(o.width))),
            picture(o),
            h("div", { class: "item-btns" },
              h("button", { class: "btn btn-secondary", type: "button", onclick: function () { open(o); } },
                C.icon("camera"), "Re-photo"),
              h("button", { class: "btn btn-ghost", type: "button", disabled: o.inUse || null,
                            onclick: function () { goneLater(o); } }, C.icon("bin"), "Gone")));
        })));
    });
    if (!list.length && !pending) add(listEl, h("p", { class: "muted" }, "Nothing on the rack."));
    C.footEl.hidden = false;
    add(C.footEl, h("button", { class: "btn btn-secondary", type: "button", onclick: function () { open(null); } },
      C.icon("camera"), "Add an offcut"));
  }

  // Gone waits a few seconds so Undo needs nothing from the server
  function goneLater(o) {
    if (gone) { clearTimeout(gone.timer); send(gone); }
    gone = { id: o.id, number: o.number };
    gone.timer = setTimeout(function () { var g = gone; gone = null; send(g); }, 5000);
    C.toastUndo("#" + (o.number || "") + " gone", function () { clearTimeout(gone.timer); gone = null; C.render(); });
    C.render();
  }
  function send(g) {
    C.api("POST", "/api/shop/offcut-gone", { device: C.DEVICE, id: g.id }).then(function () { C.load(); },
      function (e) { C.toast(e.message); C.load(); });
  }

  // ------------------------------------------------------------------ the flow
  function open(target) {
    var m0 = mats()[0];
    F = { target: target, step: 1, data: "", img: null, photo: "", corners: null, found: false,
          material: target ? target.material : (m0 ? m0.key : ""), thickness: "", length: target ? String(target.length) : "",
          flat: null, boxes: [], sel: -1, busy: false };
    var m = mat(F.material);
    F.thickness = target ? target.thickness : (m && m.thicknesses.length ? m.thicknesses[0] : "");
    C.render();
    window.scrollTo(0, 0);
  }
  function close() { F = null; C.render(); C.load(); }
  function back() { if (F && F.step > 1 && !F.busy) { F.step -= 1; C.render(); } else close(); }
  function isOpen() { return !!F; }
  function title() { return !F ? "" : F.target ? "#" + (F.target.number || "") : "New offcut"; }
  function width() { return F.target ? F.target.width : (mat(F.material) || { width: 24 }).width; }
  function lenOk() {
    var n = parseFloat(F.length), m = mat(F.material);
    return F.target ? true : !!m && n >= 1 && n <= m.length && !!F.thickness;
  }

  function steps() {
    var h = C.h;
    var names = [["Photo", 1], ["Corners", 2], ["Used areas", 3]];
    return h("nav", { class: "steps", "aria-label": "Steps" }, names.map(function (s) {
      var ok = s[1] === 1 || (s[1] === 2 && F.photo) || (s[1] === 3 && F.flat);
      return h("button", { type: "button", "aria-current": F.step === s[1] ? "step" : null,
                           class: F.step > s[1] ? "past" : null, disabled: !ok || F.busy || null,
                           onclick: function () { F.step = s[1]; C.render(); } }, h("i"), s[0]);
    }));
  }
  function foot(backText, nextText, ok, onNext) {
    var h = C.h;
    C.footEl.hidden = false;
    C.add(C.footEl, h("div", { class: "foot-two" },
      h("button", { class: "btn btn-ghost", type: "button", disabled: F.busy || null, onclick: back }, backText),
      h("button", { class: "btn btn-primary", type: "button", disabled: !ok || F.busy || null, onclick: onNext },
        F.busy ? "…" : nextText)));
  }

  function renderFlow() {
    C.add(C.listEl, steps());
    if (F.step === 1) stepPhoto();
    else if (F.step === 2) stepCorners();
    else stepUsed();
  }

  // step 1: the photo, and what the piece is
  // read as a data: URL (the page's policy allows data: images, not blob: ones), shrunk, sent as a JPEG
  function pickPhoto(file) {
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      var img = new Image();
      img.onload = function () {
        var s = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
        var c = document.createElement("canvas");
        c.width = Math.round(img.naturalWidth * s); c.height = Math.round(img.naturalHeight * s);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        F.data = c.toDataURL("image/jpeg", 0.88);
        F.photo = ""; F.corners = null; F.flat = null; F.boxes = [];
        var shown = new Image();
        shown.onload = function () { F.img = shown; C.render(); };
        shown.src = F.data;
      };
      img.onerror = function () { C.toast("That isn't a photo."); };
      img.src = reader.result;
    };
    reader.onerror = function () { C.toast("Couldn't read that photo."); };
    reader.readAsDataURL(file);
  }

  function stepPhoto() {
    var h = C.h, add = C.add, listEl = C.listEl;
    var input = h("input", { type: "file", accept: "image/*", capture: "environment", class: "hidden-file",
                             onchange: function () { pickPhoto(input.files && input.files[0]); } });
    var shot = h("label", { class: "shot" + (F.data ? " has" : "") }, input);
    if (F.data) {
      add(shot, h("img", { src: F.data, alt: "The photo" }));
      add(shot, h("span", { class: "again" }, C.icon("camera"), "Again"));
    } else {
      add(shot, h("span", { class: "shot-in" }, C.icon("camera"), h("span", null, "Take a photo")));
    }
    add(listEl, shot);
    if (F.target) {
      add(listEl, h("div", { class: "chips" }, h("span", { class: "chip" }, F.target.label),
        h("span", { class: "chip" }, F.target.thickness), h("span", { class: "chip" }, fmt(F.target.length) + " × " + fmt(F.target.width))));
    } else {
      var m = mat(F.material);
      var sel = h("select", { class: "input", id: "offcut-material", onchange: function () {
        F.material = sel.value;
        var mm = mat(F.material);
        if (!mm || mm.thicknesses.indexOf(F.thickness) < 0) F.thickness = mm && mm.thicknesses.length ? mm.thicknesses[0] : "";
        C.render();
      } }, mats().map(function (x) { return h("option", { value: x.key, selected: x.key === F.material || null }, x.label); }));
      var len = h("input", { class: "input mono", id: "offcut-length", type: "number", inputmode: "decimal", min: "1",
                             max: m ? String(m.length) : "48", step: "0.25", placeholder: "Tape it", value: F.length,
                             oninput: function () { F.length = len.value; nextBtn.disabled = !(F.data && lenOk()) || null; } });
      add(listEl, h("div", { class: "fields" },
        h("label", { class: "field wide" }, "Material", sel),
        h("div", { class: "field wide" }, "Thickness", h("div", { class: "seg", role: "group", "aria-label": "Thickness" },
          (m ? m.thicknesses : []).map(function (t) {
            return h("button", { type: "button", "aria-pressed": String(t === F.thickness),
                                 onclick: function () { F.thickness = t; C.render(); } }, t);
          }))),
        h("label", { class: "field wide" }, "Length", h("div", { class: "lenrow" }, len,
          h("span", { class: "mono" }, "× " + fmt(m ? m.width : 24) + " in")))));
    }
    foot("Cancel", "Next", F.data && lenOk(), function () {
      if (F.photo) { F.step = 2; C.render(); return; }
      F.busy = true; C.render();
      C.api("POST", "/api/shop/offcut-photo", { device: C.DEVICE, data: F.data, material: F.material, length: F.length,
                                                target: F.target ? F.target.id : "" }).then(function (r) {
        F.busy = false; F.photo = r.photo; F.found = !!r.corners;
        var w = F.img.naturalWidth, hh = F.img.naturalHeight;
        F.corners = r.corners || [[0.15 * w, 0.85 * hh], [0.85 * w, 0.85 * hh], [0.85 * w, 0.15 * hh], [0.15 * w, 0.15 * hh]];
        F.step = 2; C.render();
      }, function (e) { F.busy = false; C.toast(e.message); C.render(); });
    });
    var nextBtn = C.footEl.querySelector(".btn-primary");
  }

  // step 2: the corners (dragged, with a magnifier over the finger); the dark side is the front
  function stepCorners() {
    var h = C.h, add = C.add, listEl = C.listEl;
    var W = F.img.naturalWidth, H = F.img.naturalHeight;
    var stage = h("div", { class: "stage" }, h("img", { src: F.data, alt: "The photo", class: "photo" }));
    var over = svg("svg", { class: "over", viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" });
    stage.appendChild(over);
    var lc = h("canvas", { width: "112", height: "112" });
    var loupe = h("div", { class: "loupe", hidden: true }, lc);
    add(stage, loupe);
    if (!F.found) add(listEl, h("p", { class: "muted" }, "Drag each dot onto a corner of the piece."));
    add(listEl, stage);
    add(listEl, h("div", { class: "tools" }, h("button", { class: "tool", type: "button", onclick: function () {
      var k = F.corners; F.corners = [k[2], k[3], k[0], k[1]]; F.flat = null; draw();
    } }, C.icon("flip"), "Other end is the front")));
    var r = Math.max(W, H) / 90;
    function draw() {
      while (over.firstChild) over.removeChild(over.firstChild);
      var k = F.corners;
      over.appendChild(svg("polygon", { points: k.map(function (p) { return p.join(","); }).join(" "), class: "quad", "stroke-width": r / 4 }));
      over.appendChild(svg("line", { x1: k[0][0], y1: k[0][1], x2: k[1][0], y2: k[1][1], class: "front-edge", "stroke-width": r / 1.5 }));
      var t = svg("text", { x: (k[0][0] + k[1][0]) / 2, y: (k[0][1] + k[1][1]) / 2 - r * 1.2, "font-size": r * 1.6, class: "front-tag",
                            "text-anchor": "middle" });
      t.textContent = "FRONT";
      over.appendChild(t);
      k.forEach(function (p) {
        over.appendChild(svg("circle", { cx: p[0], cy: p[1], r: r * 1.1, class: "handle", "stroke-width": r / 3 }));
        over.appendChild(svg("circle", { cx: p[0], cy: p[1], r: r / 3, class: "handle-dot" }));
      });
    }
    draw();
    var drag = -1;
    function at(e) { var b = over.getBoundingClientRect(); return [(e.clientX - b.left) * W / b.width, (e.clientY - b.top) * H / b.height]; }
    function showLoupe(p) {
      var b = over.getBoundingClientRect(), sx = b.width / W, sy = b.height / H;
      var lx = p[0] * sx - 56, ly = p[1] * sy - 140;
      if (ly < -20) ly = p[1] * sy + 30;
      loupe.style.left = Math.max(-10, Math.min(b.width - 102, lx)) + "px";
      loupe.style.top = ly + "px";
      var x = lc.getContext("2d"), z = 14 / sx;
      x.imageSmoothingEnabled = false;
      x.clearRect(0, 0, 112, 112);
      x.drawImage(F.img, p[0] - z, p[1] - z, 2 * z, 2 * z, 0, 0, 112, 112);
      x.strokeStyle = "#692c00"; x.lineWidth = 2;
      x.beginPath(); x.moveTo(56, 40); x.lineTo(56, 72); x.moveTo(40, 56); x.lineTo(72, 56); x.stroke();
      loupe.hidden = false;
    }
    over.addEventListener("pointerdown", function (e) {
      var p = at(e), best = -1, bd = Infinity;
      F.corners.forEach(function (k, i) { var d = (k[0] - p[0]) * (k[0] - p[0]) + (k[1] - p[1]) * (k[1] - p[1]); if (d < bd) { bd = d; best = i; } });
      if (best < 0 || bd > Math.pow(Math.max(W, H) / 8, 2)) return;
      drag = best; over.setPointerCapture(e.pointerId); showLoupe(F.corners[drag]); e.preventDefault();
    });
    over.addEventListener("pointermove", function (e) {
      if (drag < 0) return;
      var p = at(e);
      F.corners[drag] = [Math.max(0, Math.min(W, p[0])), Math.max(0, Math.min(H, p[1]))];
      F.flat = null; draw(); showLoupe(F.corners[drag]);
    });
    function end() { drag = -1; loupe.hidden = true; }
    over.addEventListener("pointerup", end);
    over.addEventListener("pointercancel", end);
    foot("Back", "Next", true, function () {
      if (F.flat) { F.step = 3; C.render(); return; }
      F.busy = true; C.render();
      C.api("POST", "/api/shop/offcut-flat", { device: C.DEVICE, photo: F.photo, corners: F.corners, material: F.material,
                                               length: F.length, target: F.target ? F.target.id : "" }).then(function (r) {
        F.busy = false; F.flat = r; F.boxes = (r.boxes || []).map(function (b) { return b.slice(); }); F.sel = -1;
        F.step = 3; C.render();
      }, function (e) {
        F.busy = false; C.toast(e.message);
        if (e.data && e.data.kind === "gone") { F.photo = ""; F.step = 1; }
        C.render();
      });
    });
  }

  // step 3: the straightened piece, a box over each hole (move, resize, delete; drag on the piece to add one)
  function stepUsed() {
    var h = C.h, add = C.add, listEl = C.listEl;
    var w = F.flat.width, len = F.flat.length;
    var avail = Math.max(240, Math.min(528, (listEl.clientWidth || 392) - 32));   // the page's side padding
    var s = Math.min(avail / w, 560 / len);           // px per inch on screen
    var tools = h("div", { class: "tools" },
      h("button", { class: "tool", type: "button", onclick: function () {
        F.boxes.push([w / 2 - 2, len / 2 - 2, w / 2 + 2, len / 2 + 2]); F.sel = F.boxes.length - 1; draw();
      } }, C.icon("plus"), "Box"),
      h("span", { class: "muted small" }, "or drag around a hole"));
    add(listEl, tools);
    var flat = h("div", { class: "flat" });
    flat.style.width = (w * s) + "px"; flat.style.height = (len * s) + "px";
    if (F.flat.image) add(flat, h("img", { src: F.flat.image, alt: "The piece from above", class: "flat-img", draggable: "false" }));
    else add(flat, h("div", { class: "flat-blank" }));
    var dims = h("div", { class: "dims", "aria-live": "polite" });
    add(listEl, h("div", { class: "flat-wrap" }, h("div", { class: "end-tag" }, "Back"), flat, h("div", { class: "end-tag" }, "Front"), dims));
    add(listEl, h("p", { class: "muted small" }, F.flat.how === "outline" || !F.flat.how
      ? "Box every hole: on bare aluminum some aren't found." : "Box every hole it missed."));
    var known = F.target ? (F.target.used || []) : [];

    function place(el, b) {
      el.style.left = (b[0] * s) + "px"; el.style.top = ((len - b[3]) * s) + "px";
      el.style.width = ((b[2] - b[0]) * s) + "px"; el.style.height = ((b[3] - b[1]) * s) + "px";
    }
    function draw() {
      Array.prototype.slice.call(flat.querySelectorAll(".box,.known")).forEach(function (e) { flat.removeChild(e); });
      known.forEach(function (k) { var e = h("div", { class: "known" }); place(e, k); flat.appendChild(e); });
      F.boxes.forEach(function (b, i) {
        var e = h("div", { class: "box" + (i === F.sel ? " sel" : ""), "data-i": String(i) });
        place(e, b);
        if (i === F.sel) {
          add(e, h("span", { class: "h", "data-h": "1" }));
          add(e, h("button", { class: "x", type: "button", "data-x": "1", "aria-label": "Remove this box" }, C.icon("x")));
        }
        flat.appendChild(e);
      });
      var b = F.boxes[F.sel];
      dims.textContent = b ? fmt(b[1]) + " to " + fmt(b[3]) + " along · " + fmt(b[0]) + " to " + fmt(b[2]) + " across" : "";
    }
    draw();
    var mode = null, start = null, orig = null;
    function pt(e) { var r = flat.getBoundingClientRect(); return [(e.clientX - r.left) / s, len - (e.clientY - r.top) / s]; }
    flat.addEventListener("pointerdown", function (e) {
      var t = e.target;
      if (t.closest("[data-x]")) { F.boxes.splice(F.sel, 1); F.sel = -1; draw(); return; }
      var be = t.closest("[data-i]");
      start = pt(e);
      if (!be) { F.boxes.push([start[0], start[1], start[0], start[1]]); F.sel = F.boxes.length - 1; mode = "draw"; }
      else { F.sel = +be.getAttribute("data-i"); mode = t.closest("[data-h]") ? "size" : "move"; }
      orig = F.boxes[F.sel].slice();
      flat.setPointerCapture(e.pointerId); e.preventDefault();
      draw();
    });
    flat.addEventListener("pointermove", function (e) {
      if (!mode) return;
      var p = pt(e), dx = p[0] - start[0], dy = p[1] - start[1], b = orig.slice();
      if (mode === "draw") {
        var q = [Math.max(0, Math.min(w, p[0])), Math.max(0, Math.min(len, p[1]))];
        b = [Math.min(start[0], q[0]), Math.min(start[1], q[1]), Math.max(start[0], q[0]), Math.max(start[1], q[1])];
      } else if (mode === "move") {
        dx = Math.max(-b[0], Math.min(w - b[2], dx)); dy = Math.max(-b[1], Math.min(len - b[3], dy));
        b = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy];
      } else {
        b[2] = Math.max(b[0] + 0.5, Math.min(w, b[2] + dx)); b[1] = Math.min(b[3] - 0.5, Math.max(0, b[1] + dy));
      }
      F.boxes[F.sel] = b; draw();
    });
    function end() {
      if (mode === "draw") {
        var b = F.boxes[F.sel];
        if (b[2] - b[0] < 0.5 || b[3] - b[1] < 0.5) { F.boxes.splice(F.sel, 1); F.sel = -1; }
        draw();
      }
      mode = null;
    }
    flat.addEventListener("pointerup", end);
    flat.addEventListener("pointercancel", end);
    foot("Back", F.target ? "Save #" + (F.target.number || "") : "Add it", true, save);
  }

  function save() {
    F.busy = true; C.render();
    C.api("POST", "/api/shop/offcut-save", { device: C.DEVICE, photo: F.photo, boxes: F.boxes, material: F.material,
                                             thickness: F.thickness, length: F.length,
                                             target: F.target ? F.target.id : "" }).then(function (r) {
      var t = F.target;
      F = null;
      if (t) C.toast("#" + (t.number || "") + " saved");
      else { pending = { card: r.card, since: Date.now() }; C.toast("Added. Its number shows here in a few seconds."); }
      C.render(); C.load();
    }, function (e) {
      F.busy = false; C.toast(e.message);
      if (e.data && e.data.kind === "gone") { F.photo = ""; F.flat = null; F.step = 1; }
      C.render();
    });
  }

  window.ShopOffcuts = {
    init: function (ctx) { C = ctx; },
    renderList: renderList, renderFlow: renderFlow,
    isOpen: isOpen, title: title, back: back,
    waiting: function () { return !!pending; }
  };
})();
