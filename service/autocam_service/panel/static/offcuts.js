// The shop page's Offcuts tab (offcut_photos.py, 2026-11-09; the mockup the user approved, and docs/offcut-photos.md).
//   The rack: each offcut's number, stock, size and a picture of what's used of it; Re-photo, Gone (with Undo).
//   Add an offcut / Re-photo, three steps: Photo (the page's own camera, with a level and a frame to line the piece up
//   with, and a check for blurry or dark photos; or the phone's camera app; and what it is: material, thickness, length
//   taped; the width is the sheet's), Corners (suggested when they can be found; drag them, the front is the end at the bottom of the photo),
//   Used areas (the piece straightened, a box over each hole found and dotted maybes, kept with a tap; tap a missed
//   hole and the laptop boxes it; move, resize, delete, drag to add). Save writes the
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
  var CAM = null;               // the camera while it's open: outside the page's render, so the video keeps playing
  var LEVEL_OK = 5, LEVEL_NEAR = 12;     // degrees off flat: the level goes green, then orange
  var STILL = 4;                // degrees a second: still enough for a sharp photo (the shutter waits for it)
  var SHARP_MIN = 0.5;          // the photo check: below this the sharpest edges are smeared (photoCheck below)
  var DARK_MIN = 40;            // mean brightness, 0-255

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
          flat: null, items: [], sel: -1, busy: false };
    var m = mat(F.material);
    F.thickness = target ? target.thickness : (m && m.thicknesses.length ? m.thicknesses[0] : "");
    C.render();
    window.scrollTo(0, 0);
  }
  function close() { closeCamera(); F = null; C.render(); C.load(); }
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

  // ------------------------------------------------------------------ the camera (2026-11-09)
  // The page's own camera: a frame shaped like the piece (the front at the bottom) to line it up with, a level that goes
  // green when the phone is flat over it, and a shutter that waits until the phone is still. Each photo, from here or
  // the camera app, is checked for blur and darkness before it's sent; what the check measured goes with it, so the
  // laptop can tune these numbers on the team's real photos (offcut-photo-report).
  var motion = { listening: false, on: false, tilt: null, rate: null, x: 0, y: 0, last: null };
  function onOrient(e) {
    if (e.beta == null || e.gamma == null) return;
    var now = performance.now(), b = e.beta, g = e.gamma, a = e.alpha, R = Math.PI / 180;
    var tilt = Math.acos(Math.max(-1, Math.min(1, Math.cos(b * R) * Math.cos(g * R)))) / R;
    var L = motion.last;
    if (L && now > L.t) {
      var da = a != null && L.a != null ? Math.abs(((a - L.a + 540) % 360) - 180) : 0;
      var r = Math.sqrt((b - L.b) * (b - L.b) + (g - L.g) * (g - L.g) + da * da) / ((now - L.t) / 1000);
      motion.rate = motion.rate == null ? r : 0.7 * motion.rate + 0.3 * r;
    }
    motion.last = { t: now, a: a, b: b, g: g };
    motion.tilt = motion.tilt == null ? tilt : 0.6 * motion.tilt + 0.4 * tilt;
    // the bubble floats to the high side, turned to how the screen is
    var ang = ((screen.orientation && screen.orientation.angle) || window.orientation || 0) * R;
    var dx = -g, dy = -b;
    motion.x = dx * Math.cos(ang) + dy * Math.sin(ang);
    motion.y = -dx * Math.sin(ang) + dy * Math.cos(ang);
    motion.on = true;
  }
  function listenMotion() {
    if (motion.listening) return;
    motion.listening = true;
    window.addEventListener("deviceorientation", onOrient);
  }
  function stopMotion() {
    window.removeEventListener("deviceorientation", onOrient);
    motion = { listening: false, on: false, tilt: null, rate: null, x: 0, y: 0, last: null };
  }
  // iPhones ask first, and only from a tap: this runs in the tap that opens the camera
  function askMotion() {
    var D = window.DeviceOrientationEvent;
    if (D && typeof D.requestPermission === "function") {
      D.requestPermission().then(function (st) { if (st === "granted") listenMotion(); }, function () { /* no level */ });
    } else if (D) listenMotion();
  }

  function cameraWorks() {
    return !F.noCam && !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }
  function guideLength() {
    var n = parseFloat(F.length), m = mat(F.material);
    if (n > 0) return n;
    return F.target ? F.target.length : (m ? m.length : 48);
  }

  function openCamera() {
    if (CAM) return;
    askMotion();
    navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: "environment" },
                                                                 width: { ideal: 3840 }, height: { ideal: 2160 } } })
      .then(showCamera, function (e) {
        stopMotion();
        if (!F) return;
        F.noCam = true;
        C.toast(e && e.name === "NotAllowedError" ? "The camera isn't allowed here. Use the camera app."
                                                   : "The camera didn't open. Use the camera app.");
        C.render();
      });
  }

  function showCamera(stream) {
    if (!F) { stream.getTracks().forEach(function (t) { t.stop(); }); stopMotion(); return; }
    var h = C.h;
    var video = h("video", { class: "cam-video", autoplay: true, playsinline: true, muted: true });
    video.muted = true; video.playsInline = true;
    var guide = svg("svg", { class: "cam-guide", "aria-hidden": "true" });
    var bubble = h("i", { class: "bubble" });
    var level = h("div", { class: "cam-level", hidden: true }, h("i", { class: "mid" }), bubble);
    var hint = h("div", { class: "cam-hint" }, "Line the piece up with the frame");
    var flash = h("div", { class: "cam-flash" });
    var view = h("div", { class: "cam-view" }, video, level, hint, flash);
    view.insertBefore(guide, level);
    var shut = h("button", { class: "cam-shutter", type: "button", "aria-label": "Take the photo", disabled: true,
                             onclick: function () { shutter(); } });
    var el = h("div", { class: "cam", role: "dialog", "aria-modal": "true", "aria-label": "Camera" }, view,
      h("div", { class: "cam-bar" },
        h("button", { class: "cam-x", type: "button", "aria-label": "Close the camera", onclick: function () { closeCamera(); } },
          C.icon("x")),
        shut, h("span", { class: "cam-state" })));
    document.body.appendChild(el);
    document.body.classList.add("cam-open");
    CAM = { el: el, view: view, video: video, guide: guide, level: level, bubble: bubble, hint: hint, flash: flash,
            shut: shut, state: el.querySelector(".cam-state"), stream: stream, raf: 0, waiting: 0, check: null };
    video.srcObject = stream;
    video.addEventListener("loadedmetadata", function () { placeGuide(); shut.disabled = false; });
    var p = video.play();
    if (p && p.catch) p.catch(function () { /* autoplay with sound off is allowed; the next tap plays it */ });
    window.addEventListener("resize", placeGuide);
    document.addEventListener("visibilitychange", hiddenClose);
    document.addEventListener("keydown", camKey);
    tick();
  }
  function hiddenClose() { if (document.hidden) closeCamera(); }
  function camKey(e) { if (e.key === "Escape") closeCamera(); }

  function closeCamera() {
    if (!CAM) return;
    cancelAnimationFrame(CAM.raf);
    CAM.stream.getTracks().forEach(function (t) { t.stop(); });
    CAM.el.parentNode && CAM.el.parentNode.removeChild(CAM.el);
    document.body.classList.remove("cam-open");
    window.removeEventListener("resize", placeGuide);
    document.removeEventListener("visibilitychange", hiddenClose);
    document.removeEventListener("keydown", camKey);
    CAM = null;
    stopMotion();
  }

  // the frame: the piece's shape (across x along, the front at the bottom) over the picture, a fifth of it floor
  function placeGuide() {
    if (!CAM || !CAM.video.videoWidth) return;
    var vw = CAM.video.videoWidth, vh = CAM.video.videoHeight, b = CAM.view.getBoundingClientRect();
    var k = Math.min(b.width / vw, b.height / vh), g = CAM.guide;
    g.style.width = vw * k + "px"; g.style.height = vh * k + "px";
    g.style.left = (b.width - vw * k) / 2 + "px"; g.style.top = (b.height - vh * k) / 2 + "px";
    g.setAttribute("viewBox", "0 0 " + vw + " " + vh);
    while (g.firstChild) g.removeChild(g.firstChild);
    var W = width(), L = guideLength(), f = 0.8 * Math.min(vw / W, vh / L);
    var rw = W * f, rh = L * f, x = (vw - rw) / 2, y = (vh - rh) / 2, sw = Math.max(vw, vh) / 260;
    CAM.frame = [[x, y + rh], [x + rw, y + rh], [x + rw, y], [x, y]];    // front-left, front-right, back-right, back-left
    var c = Math.min(rw, rh) * 0.14;
    g.appendChild(svg("rect", { x: x, y: y, width: rw, height: rh, class: "cam-frame", "stroke-width": sw / 2 }));
    g.appendChild(svg("path", { class: "cam-corners", "stroke-width": sw * 1.6, d:
      "M" + x + "," + (y + c) + "V" + y + "H" + (x + c) + "M" + (x + rw - c) + "," + y + "H" + (x + rw) + "V" + (y + c) +
      "M" + (x + rw) + "," + (y + rh - c) + "V" + (y + rh) + "H" + (x + rw - c) + "M" + (x + c) + "," + (y + rh) + "H" + x + "V" + (y + rh - c) }));
    g.appendChild(svg("line", { x1: x, y1: y + rh, x2: x + rw, y2: y + rh, class: "cam-front", "stroke-width": sw * 1.6 }));
    var t = svg("text", { x: vw / 2, y: Math.min(vh - sw * 2, y + rh + sw * 9), "font-size": sw * 7, "text-anchor": "middle", class: "cam-front-tag" });
    t.textContent = "FRONT";
    g.appendChild(t);
  }

  // every frame: the level, the hint, and a shutter that's waiting for the phone to be still
  function tick() {
    if (!CAM) return;
    CAM.raf = requestAnimationFrame(tick);
    if (CAM.check) return;
    var flat = null, still = motion.rate == null || motion.rate <= STILL;
    if (motion.on && motion.tilt != null) {
      flat = motion.tilt <= LEVEL_OK ? "ok" : motion.tilt <= LEVEL_NEAR ? "near" : "off";
      CAM.level.hidden = false;
      CAM.level.className = "cam-level " + flat;
      var m = 31 / 20, bx = Math.max(-20, Math.min(20, motion.x)) * m, by = Math.max(-20, Math.min(20, motion.y)) * m;
      CAM.bubble.style.transform = "translate(" + bx.toFixed(1) + "px," + by.toFixed(1) + "px)";
    }
    CAM.el.classList.toggle("flat", flat === "ok");
    var text = CAM.waiting ? "Hold still…" : flat === "off" ? "Hold the phone flat over the piece"
      : flat === "near" ? "Almost flat" : flat === "ok" && !still ? "Hold still" : flat === "ok" ? "Flat. Take it"
      : "Line the piece up with the frame";
    if (CAM.hint.textContent !== text) CAM.hint.textContent = text;
    if (CAM.waiting && (still || performance.now() > CAM.waiting)) { CAM.waiting = 0; take(); }
  }

  function shutter() {
    if (!CAM || CAM.check || CAM.waiting || !CAM.video.videoWidth) return;
    if (motion.on && motion.rate != null && motion.rate > STILL) { CAM.waiting = performance.now() + 2000; return; }
    take();
  }

  function take() {
    var v = CAM.video, vw = v.videoWidth, vh = v.videoHeight;
    var s = Math.min(1, MAX_SIDE / Math.max(vw, vh));
    var c = document.createElement("canvas");
    c.width = Math.round(vw * s); c.height = Math.round(vh * s);
    var x = c.getContext("2d");
    x.imageSmoothingQuality = "high";
    x.drawImage(v, 0, 0, c.width, c.height);
    CAM.flash.classList.add("on");
    setTimeout(function () { if (CAM) CAM.flash.classList.remove("on"); }, 120);
    var q = photoCheck(c);
    q.via = "camera";
    if (motion.on && motion.tilt != null) q.tilt = Math.round(motion.tilt * 10) / 10;
    var data = c.toDataURL("image/jpeg", 0.9);
    // where the piece was lined up: where its corners start if they can't be found
    var guide = CAM.frame ? CAM.frame.map(function (p) { return [Math.round(p[0] * s), Math.round(p[1] * s)]; }) : null;
    if (!q.problem) { closeCamera(); usePhoto(data, q, guide); return; }
    showCheck(data, q, guide);
  }

  // a photo that's blurry or dark: take it again (the camera's still open), or use it anyway
  function showCheck(data, q, guide) {
    var h = C.h, still = h("img", { class: "cam-still", src: data, alt: "The photo just taken" });
    var card = h("div", { class: "cam-check", role: "alertdialog", "aria-label": checkTitle(q) },
      h("div", { class: "cam-check-title" }, checkTitle(q)),
      h("p", null, checkLine(q)),
      h("div", { class: "foot-two" },
        h("button", { class: "btn btn-ghost", type: "button", onclick: function () {
          q.anyway = true; closeCamera(); usePhoto(data, q, guide);
        } }, "Use it anyway"),
        h("button", { class: "btn btn-primary", type: "button", onclick: function () {
          CAM.view.removeChild(still); CAM.view.removeChild(card); CAM.check = null; CAM.hint.hidden = false;
        } }, C.icon("camera"), "Take it again")));
    CAM.check = q;
    CAM.hint.hidden = true; CAM.level.hidden = true;
    CAM.view.appendChild(still);
    CAM.view.appendChild(card);
  }
  function checkTitle(q) { return q.problem === "dark" ? "Too dark" : "Too blurry"; }
  function checkLine(q) {
    return q.problem === "dark" ? "Turn on more light or move out of the shadow, then take it again."
                                : "Hold the phone still over the piece and take it again.";
  }

  // The check, on the photo shrunk to 1000 px: its mean brightness, and how sharp its sharpest edges are. At each
  // pixel step that's the steepest of the 9 around it, the step against the whole change over those 9 px: a sharp edge
  // jumps in a pixel or two (near 1), a blurred one is spread out (about 0.4 / its blur in px). Each way (along rows,
  // down columns) takes its strongest 2% of edges and the 90th percentile; the lower way counts, so blur from the phone
  // moving one way shows. On the team's 5 real photos (2026-11-09): sharp 0.75-0.90, a 1080p video frame ~0.65-0.73;
  // hole finding starts losing holes (film-filled and small ones first) at about 0.5 (1 px of blur, or a 5 px shake).
  function photoCheck(canvas) {
    var M = 1000, s = Math.min(1, M / Math.max(canvas.width, canvas.height));
    var w = Math.max(1, Math.round(canvas.width * s)), hh = Math.max(1, Math.round(canvas.height * s));
    var c = document.createElement("canvas");
    c.width = w; c.height = hh;
    var x = c.getContext("2d", { willReadFrequently: true });
    x.imageSmoothingQuality = "high";
    x.drawImage(canvas, 0, 0, w, hh);
    var px = x.getImageData(0, 0, w, hh).data, g = new Float32Array(w * hh), sum = 0;
    for (var i = 0, j = 0; i < g.length; i++, j += 4) { g[i] = 0.299 * px[j] + 0.587 * px[j + 1] + 0.114 * px[j + 2]; sum += g[i]; }
    var bright = sum / g.length;
    var a = edgeSharpness(g, w, hh, 1, w), b = edgeSharpness(g, hh, w, w, 1);
    var sharp = a == null ? b : b == null ? a : Math.min(a, b);
    var q = { bright: Math.round(bright * 10) / 10, sharp: sharp == null ? null : Math.round(sharp * 1000) / 1000, problem: null };
    if (bright < DARK_MIN) q.problem = "dark";
    else if (sharp != null && sharp < SHARP_MIN) q.problem = "blurry";
    return q;
  }
  // along `lines` lines `ls` apart, each `n` px long with `st` between px
  function edgeSharpness(g, n, lines, st, ls) {
    var rs = [], ss = [];
    for (var L = 0; L < lines; L++) {
      var o = L * ls;
      for (var k = 4; k < n - 5; k++) {
        var p = o + k * st, S = g[p + 5 * st] - g[p - 4 * st], aS = S < 0 ? -S : S;
        if (aS < 12) continue;
        var d = g[p + st] - g[p];
        if ((d > 0) !== (S > 0)) continue;
        var ad = d < 0 ? -d : d, steep = true;
        for (var m = -4; m <= 4 && steep; m++) {
          if (m === 0) continue;
          var e = g[p + (m + 1) * st] - g[p + m * st];
          if ((e < 0 ? -e : e) > ad) steep = false;
        }
        if (steep) { rs.push(ad / aS); ss.push(aS); }
      }
    }
    if (rs.length < 100) return null;
    var cut = Float32Array.from(ss).sort()[Math.floor(0.98 * (ss.length - 1))];
    var top = [];
    for (var t = 0; t < rs.length; t++) if (ss[t] >= cut) top.push(rs[t]);
    top.sort(function (u, v) { return u - v; });
    return top[Math.floor(0.9 * (top.length - 1))];
  }

  // step 1: the photo, and what the piece is
  // a photo from the camera app is read as a data: URL (the page's policy allows data: images, not blob: ones),
  // shrunk, checked and sent as a JPEG
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
        var q = photoCheck(c);
        q.via = "app";
        usePhoto(c.toDataURL("image/jpeg", 0.88), q);
      };
      img.onerror = function () { C.toast("That isn't a photo."); };
      img.src = reader.result;
    };
    reader.onerror = function () { C.toast("Couldn't read that photo."); };
    reader.readAsDataURL(file);
  }
  function usePhoto(data, q, guide) {
    if (!F) return;
    F.data = data; F.quality = q; F.warn = q.problem && !q.anyway ? q.problem : ""; F.guide = guide || null;
    F.photo = ""; F.corners = null; F.flat = null; F.items = [];
    var shown = new Image();
    shown.onload = function () { if (F && F.data === data) { F.img = shown; C.render(); } };
    shown.src = data;
  }

  function stepPhoto() {
    var h = C.h, add = C.add, listEl = C.listEl;
    var input = h("input", { type: "file", accept: "image/*", capture: "environment", class: "hidden-file",
                             onchange: function () { pickPhoto(input.files && input.files[0]); } });
    var cam = cameraWorks();
    var shot = cam ? h("button", { type: "button", class: "shot" + (F.data ? " has" : ""), onclick: function () { openCamera(); } })
                   : h("label", { class: "shot" + (F.data ? " has" : "") }, input);
    if (F.data) {
      add(shot, h("img", { src: F.data, alt: "The photo" }));
      add(shot, h("span", { class: "again" }, C.icon("camera"), "Again"));
    } else {
      add(shot, h("span", { class: "shot-in" }, C.icon("camera"), h("span", null, "Take a photo"),
        cam ? h("span", { class: "shot-sub" }, "Phone flat over the piece, all of it in the frame") : null));
    }
    add(listEl, shot);
    if (F.warn) {
      add(listEl, h("div", { class: "notice photo-warn" },
        h("span", null, (F.warn === "dark" ? "This photo looks too dark. " : "This photo looks blurry. ") +
          "Holes can be missed. Tap Again to retake it, or "),
        h("button", { class: "linkish", type: "button", onclick: function () {
          F.warn = ""; if (F.quality) F.quality.anyway = true; C.render();
        } }, "use it anyway")));
    }
    if (cam) add(listEl, h("div", { class: "tools" }, h("label", { class: "tool" }, input, C.icon("camera"), "Use the camera app")));
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
                             oninput: function () { F.length = len.value; nextBtn.disabled = !(F.data && !F.warn && lenOk()) || null; } });
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
    foot("Cancel", "Next", F.data && !F.warn && lenOk(), function () {
      if (F.photo) { F.step = 2; C.render(); return; }
      F.busy = true; C.render();
      C.api("POST", "/api/shop/offcut-photo", { device: C.DEVICE, data: F.data, material: F.material, length: F.length,
                                                target: F.target ? F.target.id : "", quality: F.quality || null }).then(function (r) {
        F.busy = false; F.photo = r.photo; F.found = !!r.corners;
        var w = F.img.naturalWidth, hh = F.img.naturalHeight;
        F.corners = r.corners || (F.guide && F.guide.map(function (p) { return p.slice(); }))
          || [[0.15 * w, 0.85 * hh], [0.85 * w, 0.85 * hh], [0.85 * w, 0.15 * hh], [0.15 * w, 0.15 * hh]];
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
        F.busy = false; F.flat = r; F.items = itemsFrom(r.boxes, r.groups, "found"); F.sel = -1;
        F.maybes = (r.maybes || []).map(function (b) { return b.slice(); });
        F.step = 3; C.render();
      }, function (e) {
        F.busy = false; C.toast(e.message);
        if (e.data && e.data.kind === "gone") { F.photo = ""; F.step = 1; }
        C.render();
      });
    });
  }

  // step 3: the straightened piece and the used areas on it. Each item is one hole: one box, or a few boxes stepped
  // along a curved or slanted hole (drawn as one outline; moved and deleted as one; resizing makes it one box again).
  // Maybes dotted (a tap keeps one); a tap on a missed hole asks the laptop for its boxes (photo_find.grow_at); a drag
  // on the piece draws a box.
  function bbox(rects) {
    return [Math.min.apply(null, rects.map(function (r) { return r[0]; })), Math.min.apply(null, rects.map(function (r) { return r[1]; })),
            Math.max.apply(null, rects.map(function (r) { return r[2]; })), Math.max.apply(null, rects.map(function (r) { return r[3]; }))];
  }
  // a stepped hole's outline, inches: its boxes stacked along the piece, or across it (photo_find._steps)
  function outline(rects) {
    var eq = function (a, b) { return Math.abs(a - b) < 1e-6; };
    var r = rects.slice().sort(function (a, b) { return a[1] - b[1]; });
    if (r.every(function (b, i) { return i === 0 || eq(b[1], r[i - 1][3]); })) {
      var p = [];
      r.forEach(function (b) { p.push([b[2], b[1]], [b[2], b[3]]); });                          // up the right side
      for (var i = r.length - 1; i >= 0; i--) p.push([r[i][0], r[i][3]], [r[i][0], r[i][1]]);  // down the left
      return p;
    }
    var c = rects.slice().sort(function (a, b) { return a[0] - b[0]; });
    if (c.every(function (b, i) { return i === 0 || eq(b[0], c[i - 1][2]); })) {
      var q = [];
      c.forEach(function (b) { q.push([b[0], b[1]], [b[2], b[1]]); });                          // along the front
      for (var j = c.length - 1; j >= 0; j--) q.push([c[j][2], c[j][3]], [c[j][0], c[j][3]]);  // back along the back
      return q;
    }
    return null;
  }
  function addItem(rects, kind) { F.items.push({ rects: rects.map(function (b) { return b.slice(); }), kind: kind }); F.sel = F.items.length - 1; }
  function itemsFrom(boxes, groups, kind) {
    var by = {}, order = [];
    (boxes || []).forEach(function (b, i) {
      var g = groups && groups[i] != null ? "g" + groups[i] : "b" + i;
      if (!by[g]) { by[g] = []; order.push(g); }
      by[g].push(b.slice());
    });
    return order.map(function (g) { return { rects: by[g], kind: kind }; });
  }
  function stepUsed() {
    F.items = F.items || [];
    F.maybes = F.maybes || [];
    var h = C.h, add = C.add, listEl = C.listEl;
    var w = F.flat.width, len = F.flat.length;
    var avail = Math.max(240, Math.min(528, (listEl.clientWidth || 392) - 32));   // the page's side padding
    var s = Math.min(avail / w, 560 / len);           // px per inch on screen
    var tools = h("div", { class: "tools" },
      h("button", { class: "tool", type: "button", onclick: function () {
        addItem([[w / 2 - 2, len / 2 - 2, w / 2 + 2, len / 2 + 2]], "drawn"); draw();
      } }, C.icon("plus"), "Box"),
      h("span", { class: "muted small" }, "or tap a hole it missed, or drag round it"));
    add(listEl, tools);
    var flat = h("div", { class: "flat" });
    flat.style.width = (w * s) + "px"; flat.style.height = (len * s) + "px";
    if (F.flat.image) add(flat, h("img", { src: F.flat.image, alt: "The piece from above", class: "flat-img", draggable: "false" }));
    else add(flat, h("div", { class: "flat-blank" }));
    var shapes = svg("svg", { class: "shapes", viewBox: "0 0 " + (w * s) + " " + (len * s), "aria-hidden": "true" });
    flat.appendChild(shapes);
    var dims = h("div", { class: "dims", "aria-live": "polite" });
    add(listEl, h("div", { class: "flat-wrap" }, h("div", { class: "end-tag" }, "Back"), flat, h("div", { class: "end-tag" }, "Front"), dims));
    add(listEl, h("p", { class: "muted small" }, (F.flat.how === "outline" || !F.flat.how
      ? "Box every hole: on bare aluminum some aren't found." : "Box every hole it missed.")
      + (F.maybes.length ? " Dotted: maybe a hole. Tap one to keep it." : "")));
    var known = F.target ? (F.target.used || []) : [];

    function place(el, b) {
      el.style.left = (b[0] * s) + "px"; el.style.top = ((len - b[3]) * s) + "px";
      el.style.width = ((b[2] - b[0]) * s) + "px"; el.style.height = ((b[3] - b[1]) * s) + "px";
    }
    function handles(el) {
      add(el, h("span", { class: "h", "data-h": "1" }));
      add(el, h("button", { class: "x", type: "button", "data-x": "1", "aria-label": "Remove this box" }, C.icon("x")));
    }
    function draw() {
      Array.prototype.slice.call(flat.querySelectorAll(".box,.known,.maybe")).forEach(function (e) { flat.removeChild(e); });
      while (shapes.firstChild) shapes.removeChild(shapes.firstChild);
      known.forEach(function (k) { var e = h("div", { class: "known" }); place(e, k); flat.appendChild(e); });
      F.maybes.forEach(function (b, i) {
        var e = h("div", { class: "maybe", "data-m": String(i), role: "button", "aria-label": "Maybe a hole: tap to box it" }, "?");
        place(e, b); flat.appendChild(e);
      });
      F.items.forEach(function (it, i) {
        var sel = i === F.sel;
        if (it.rects.length === 1) {
          var e = h("div", { class: "box" + (sel ? " sel" : ""), "data-i": String(i) });
          place(e, it.rects[0]);
          if (sel) handles(e);
          flat.appendChild(e);
          return;
        }
        var pts = outline(it.rects), cls = "shape" + (sel ? " sel" : "");
        var draws = pts ? [pts] : it.rects.map(function (b) { return [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]; });
        draws.forEach(function (pp) {
          shapes.appendChild(svg("polygon", { class: cls, "data-i": String(i),
            points: pp.map(function (p) { return (p[0] * s).toFixed(1) + "," + ((len - p[1]) * s).toFixed(1); }).join(" ") }));
        });
        if (sel) {                                     // its frame: drag to move it, the dot to make it one box
          var f = h("div", { class: "box sel frame", "data-i": String(i) });
          place(f, bbox(it.rects)); handles(f); flat.appendChild(f);
        }
      });
      var it = F.items[F.sel], b = it && bbox(it.rects);
      dims.textContent = b ? fmt(b[1]) + " to " + fmt(b[3]) + " along · " + fmt(b[0]) + " to " + fmt(b[2]) + " across"
        + (it.rects.length > 1 ? " · stepped, " + it.rects.length + " boxes" : "") : "";
    }
    draw();
    var mode = null, start = null, orig = null;
    function pt(e) { var r = flat.getBoundingClientRect(); return [(e.clientX - r.left) / s, len - (e.clientY - r.top) / s]; }
    // a tap (not a drag) on the piece: the laptop finds the hole there
    function tapAt(p) {
      var dot = h("div", { class: "tap-wait", "aria-hidden": "true" });
      dot.style.left = (p[0] * s) + "px"; dot.style.top = ((len - p[1]) * s) + "px";
      flat.appendChild(dot);
      C.api("POST", "/api/shop/offcut-grow", { device: C.DEVICE, photo: F.photo, corners: F.corners, at: p,
                                               material: F.material, length: F.length,
                                               target: F.target ? F.target.id : "" }).then(function (r) {
        if (dot.parentNode) dot.parentNode.removeChild(dot);
        if (!F || F.step !== 3) return;
        var got = r.boxes && r.boxes.length ? r.boxes : r.box ? [r.box] : null;
        if (got) { addItem(got, "tap"); draw(); }
        else C.toast("Couldn't find a hole there. Drag a box round it.");
      }, function (e) { if (dot.parentNode) dot.parentNode.removeChild(dot); C.toast(e.message); });
    }
    flat.addEventListener("pointerdown", function (e) {
      var t = e.target;
      if (t.closest("[data-x]")) { F.items.splice(F.sel, 1); F.sel = -1; draw(); return; }
      var me = t.closest("[data-m]");
      if (me) {                                        // a maybe kept: a box like any other
        var mi = +me.getAttribute("data-m");
        addItem([F.maybes[mi]], "maybe"); F.maybes.splice(mi, 1); draw(); e.preventDefault(); return;
      }
      var be = t.closest("[data-i]");
      start = pt(e);
      if (!be) { addItem([[start[0], start[1], start[0], start[1]]], "drawn"); mode = "draw"; }
      else {
        F.sel = +be.getAttribute("data-i"); mode = t.closest("[data-h]") ? "size" : "move";
        if (mode === "size") F.items[F.sel].rects = [bbox(F.items[F.sel].rects)];   // resizing makes it one box
      }
      orig = F.items[F.sel].rects.map(function (b) { return b.slice(); });
      flat.setPointerCapture(e.pointerId); e.preventDefault();
      draw();
    });
    flat.addEventListener("pointermove", function (e) {
      if (!mode) return;
      var p = pt(e), dx = p[0] - start[0], dy = p[1] - start[1], it = F.items[F.sel];
      if (mode === "draw") {
        var q = [Math.max(0, Math.min(w, p[0])), Math.max(0, Math.min(len, p[1]))];
        it.rects = [[Math.min(start[0], q[0]), Math.min(start[1], q[1]), Math.max(start[0], q[0]), Math.max(start[1], q[1])]];
      } else if (mode === "move") {
        var bb = bbox(orig);
        dx = Math.max(-bb[0], Math.min(w - bb[2], dx)); dy = Math.max(-bb[1], Math.min(len - bb[3], dy));
        it.rects = orig.map(function (b) { return [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy]; });
      } else {
        var b = orig[0].slice();
        b[2] = Math.max(b[0] + 0.5, Math.min(w, b[2] + dx)); b[1] = Math.min(b[3] - 0.5, Math.max(0, b[1] + dy));
        it.rects = [b];
      }
      draw();
    });
    function end() {
      if (mode === "draw") {
        var b = F.items[F.sel].rects[0];
        if (b[2] - b[0] < 0.5 || b[3] - b[1] < 0.5) {
          F.items.splice(F.sel, 1); F.sel = -1;
          var still = 10 / s;                          // under 10 px on screen: a tap, not a drag
          if (b[2] - b[0] < still && b[3] - b[1] < still && F.photo && F.flat.image) tapAt(start);
        }
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
    var boxes = [], kinds = [];
    F.items.forEach(function (it) { it.rects.forEach(function (b) { boxes.push(b); kinds.push(it.kind); }); });
    C.api("POST", "/api/shop/offcut-save", { device: C.DEVICE, photo: F.photo, boxes: boxes, kinds: kinds, material: F.material,
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
