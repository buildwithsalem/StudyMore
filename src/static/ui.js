/* StudyMore UI layer: nav avatar, cursor, help dock, spotlight tour, customizer,
   availability tools. Needs window.SM = {user, campus} (set in _ui_foot.html). */
(function () {
  "use strict";
  var SM = window.SM || {};
  var root = document.documentElement;
  var KEY = "sm_theme", SEEN = "studymore_tutorial_seen";

  function $(s, c) { return (c || document).querySelector(s); }
  function $$(s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); }
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) {} return null; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  var reduceMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- theme state ---------- */
  var DEF = {
    light: { bg: "#e6e9fb", card: "#ffffff", ink: "#12123a", blue: "#2d46ff", orange: "#ff6a1f", lime: "#c9f13b" },
    dark: { bg: "#0e0f2b", card: "#1a1c47", ink: "#f1f2ff", blue: "#7b8cff", orange: "#ff8a4d", lime: "#d4f55a" }
  };
  var PRESETS = [
    { id: "zine", name: "Zine" },
    { id: "sunset", name: "Sunset", blue: "#d6336c", orange: "#ff8a1f", lime: "#ffd43b", bg: "#ffe9d6" },
    { id: "forest", name: "Forest", blue: "#1f7a4d", orange: "#f08a24", lime: "#d9f26b", bg: "#e3efe0" },
    { id: "bubble", name: "Bubblegum", blue: "#8a3ffc", orange: "#ff5fa2", lime: "#7cf0d4", bg: "#fde4f2" },
    { id: "mono", name: "Mono", blue: "#111111", orange: "#ff3b30", lime: "#ffe600", bg: "#f2f2f2" },
    { id: "ocean", name: "Ocean", blue: "#0a6ea8", orange: "#ff7a45", lime: "#8be9d0", bg: "#dff1f7" }
  ];
  var COLOR_LABELS = [["bg", "Background"], ["card", "Cards"], ["ink", "Text and lines"], ["blue", "Main color"], ["orange", "Accent"], ["lime", "Highlight"]];

  function hex(c) { c = c.replace("#", ""); return [0, 2, 4].map(function (i) { return parseInt(c.substr(i, 2), 16); }); }
  function toHex(a) { return "#" + a.map(function (n) { return ("0" + Math.round(n).toString(16)).slice(-2); }).join(""); }
  function mix(a, b, t) { var x = hex(a), y = hex(b); return toHex(x.map(function (v, i) { return v * (1 - t) + y[i] * t; })); }
  function lum(c) { var a = hex(c).map(function (v) { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); }); return .2126 * a[0] + .7152 * a[1] + .0722 * a[2]; }

  var state;
  function effMode() {
    return state.theme || (window.matchMedia && matchMedia("(prefers-color-scheme:dark)").matches ? "dark" : "light");
  }
  function loadState() {
    try { var raw = JSON.parse(store(KEY) || "null"); state = raw && raw.state ? raw.state : {}; } catch (e) { state = {}; }
    state.c = state.c || { light: {}, dark: {} };
    state.c.light = state.c.light || {}; state.c.dark = state.c.dark || {};
    return state;
  }
  function currentColors(mode) {
    var c = {}; Object.keys(DEF[mode]).forEach(function (k) { c[k] = state.c[mode][k] || DEF[mode][k]; });
    return c;
  }
  var VARS = ["--bg", "--card", "--ink", "--blue", "--orange", "--lime", "--muted", "--tint", "--pink", "--on", "--on-blue"];
  function paint() {
    var mode = effMode(), custom = Object.keys(state.c[mode]).length > 0, vars = { "--r": (state.r == null ? 16 : state.r) + "px" };
    if (custom) {
      var c = currentColors(mode);
      vars["--bg"] = c.bg; vars["--card"] = c.card; vars["--ink"] = c.ink; vars["--blue"] = c.blue; vars["--orange"] = c.orange; vars["--lime"] = c.lime;
      vars["--muted"] = mix(c.ink, c.card, .35); vars["--tint"] = mix(c.card, c.blue, mode === "dark" ? .22 : .16);
      vars["--pink"] = mix(c.card, c.orange, mode === "dark" ? .65 : .3);
      vars["--on"] = lum(c.lime) > .35 ? "#12123a" : "#ffffff"; vars["--on-blue"] = lum(c.blue) > .35 ? "#12123a" : "#ffffff";
    }
    var attrs = {};
    if (state.theme) attrs["data-theme"] = state.theme;
    if (state.font && state.font !== "zine") attrs["data-font"] = state.font;
    if (state.shadow && state.shadow !== "hard") attrs["data-shadow"] = state.shadow;
    if (state.motion === "off") attrs["data-motion"] = "off";
    ["data-theme", "data-font", "data-shadow", "data-motion"].forEach(function (a) { root.removeAttribute(a); });
    Object.keys(attrs).forEach(function (a) { root.setAttribute(a, attrs[a]); });
    VARS.forEach(function (k) { root.style.removeProperty(k); });
    Object.keys(vars).forEach(function (k) { root.style.setProperty(k, vars[k]); });
    root.style.fontSize = (state.size || 100) + "%";
    var bootVars = { light: null, dark: null };
    ["light", "dark"].forEach(function (m) {
      var v = { "--r": vars["--r"] };
      if (Object.keys(state.c[m]).length) {
        var cc = currentColors(m);
        v["--bg"] = cc.bg; v["--card"] = cc.card; v["--ink"] = cc.ink; v["--blue"] = cc.blue; v["--orange"] = cc.orange; v["--lime"] = cc.lime;
        v["--muted"] = mix(cc.ink, cc.card, .35); v["--tint"] = mix(cc.card, cc.blue, m === "dark" ? .22 : .16);
        v["--pink"] = mix(cc.card, cc.orange, m === "dark" ? .65 : .3);
        v["--on"] = lum(cc.lime) > .35 ? "#12123a" : "#ffffff"; v["--on-blue"] = lum(cc.blue) > .35 ? "#12123a" : "#ffffff";
      }
      bootVars[m] = v;
    });
    store(KEY, JSON.stringify({ theme: state.theme || "", vars: bootVars, attrs: attrs, size: state.size || 100, state: state }));
    cursorFlag();
    var tb = $(".sm-theme"); if (tb) tb.innerHTML = '<span aria-hidden="true">' + (mode === "dark" ? "\u2600" : "\u263E") + '</span><span class="lbl">' + (mode === "dark" ? "Light" : "Dark") + "</span>";
  }
  function toggleTheme() { state.theme = effMode() === "dark" ? "light" : "dark"; paint(); if (drawer) render(); }
  function themeButton() {
    var bar = $(".topbar"); if (!bar) return;
    var b = el("button", "sm-theme"); b.type = "button"; b.setAttribute("aria-label", "Switch between light and dark mode");
    b.onclick = toggleTheme;
    var tgl = $(".menu-toggle", bar);
    bar.insertBefore(b, tgl || null);
    paint();
  }

  /* ---------- nav profile picture ---------- */
  function navAvatar() {
    var a = $('#site-nav a[href$="/profile"]'); if (!a || !SM.user) return;
    a.classList.add("nav-avatar"); a.setAttribute("aria-label", "Your profile"); a.title = "Your profile";
    var av = el("span", "av");
    if (SM.user.photo) { var img = el("img"); img.src = SM.user.photo; img.alt = ""; av.appendChild(img); }
    else { av.textContent = SM.user.initials || "?"; if (SM.user.color) { av.style.background = SM.user.color; av.style.color = "#fff"; } }
    a.textContent = ""; a.appendChild(av); a.appendChild(el("span", "av-text", "Profile"));
  }

  /* ---------- cursor (same behavior as the design preview) ---------- */
  var dot, ring, mx = 0, my = 0, rx = 0, ry = 0, raf = null;
  function cursorWanted() {
    return window.matchMedia && matchMedia("(hover:hover) and (pointer:fine)").matches && !reduceMotion && state.cursor !== "off";
  }
  function cursorFlag() {
    var want = cursorWanted();
    root.classList.toggle("sm-cursor", want);
    if (dot) { dot.style.display = ring.style.display = want ? "" : "none"; }
  }
  function cursor() {
    dot = el("div"); dot.id = "sm-dot"; ring = el("div"); ring.id = "sm-ring";
    document.body.appendChild(ring); document.body.appendChild(dot);
    cursorFlag();
    function frame() {
      rx += (mx - rx) * .22; ry += (my - ry) * .22;
      ring.style.transform = "translate(" + rx + "px," + ry + "px)";
      raf = requestAnimationFrame(frame);
    }
    addEventListener("pointermove", function (e) {
      if (e.pointerType !== "mouse" || !cursorWanted()) return;
      mx = e.clientX; my = e.clientY;
      if (!raf) { rx = mx; ry = my; frame(); }
      dot.style.opacity = ring.style.opacity = 1;
      dot.style.transform = "translate(" + mx + "px," + my + "px)";
      var t = e.target.closest ? e.target.closest("[data-cur],a,button,summary,select,label,.availability-slot,input,textarea,[role=button]") : null;
      ring.classList.remove("big", "paint", "text", "warn"); ring.textContent = "";
      if (!t) return;
      if (t.matches(".availability-slot,.availability-slot *")) ring.classList.add("paint");
      else if (t.matches("input:not([type=checkbox]):not([type=radio]):not([type=color]):not([type=range]),textarea")) ring.classList.add("text");
      else if (t.matches(".danger-button")) { ring.classList.add("warn"); ring.textContent = "Sure?"; }
      else { ring.classList.add("big"); ring.textContent = t.dataset.cur || (t.matches(".primary,.btn") ? "Go" : t.matches("a") ? "Go" : "Tap"); }
    }, { passive: true });
    addEventListener("pointerdown", function (e) {
      if (e.pointerType !== "mouse" || !cursorWanted()) return;
      for (var i = 0; i < 7; i++) {
        var p = el("i", "sm-splat"); p.style.left = (e.clientX - 6) + "px"; p.style.top = (e.clientY - 6) + "px";
        var a = Math.random() * 6.28, r = 20 + Math.random() * 26;
        p.style.setProperty("--dx", Math.cos(a) * r + "px"); p.style.setProperty("--dy", Math.sin(a) * r + "px");
        document.body.appendChild(p); setTimeout(function (n) { n.remove(); }, 650, p);
      }
    });
  }

  /* ---------- tour ---------- */
  var tour = null;
  function visible(e) { if (!e) return false; var r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }
  var GENERAL = [
    { t: "Welcome to StudyMore", d: "Find study groups for your classes, match with people who share your schedule, and plan sessions together. This quick tour takes under a minute.", sel: null },
    { t: "Groups", d: "Search for study groups by course, create your own, see the ones you joined, and answer invitations.", sel: "#site-nav details:nth-of-type(1) > summary", menu: 1 },
    { t: "Plan", d: "Find My Match, your weekly availability, schedule, semesters with syllabus upload, and walking directions around campus.", sel: "#site-nav details:nth-of-type(2) > summary", menu: 1 },
    { t: "Your profile", d: "Your photo and study details live here. Other members see this when you join their groups.", sel: '#site-nav a[href$="/profile"]', menu: 1 },
    { t: "Help is always here", d: "This button stays in the corner of every page. It replays this tour, shows tips for the page you are on, opens campus directions, and customizes how StudyMore looks.", sel: ".sm-dock .fab.help" }
  ];
  var PAGE = {
    "/availability": [
      { t: "Paint your free time", d: "Click or drag across the grid. Use the buttons above for quick picks like weekday afternoons.", sel: ".availability-grid" },
      { t: "See who else is free", d: "The small number in a box is how many of your group mates are free then. A lime border means you and a mate are both free.", sel: ".av-legend" }
    ],
    "/my-groups": [{ t: "Your groups", d: "Everything you joined or created. Leave buttons are red so you do not click them by accident.", sel: ".my-groups-list, main" }],
    "/search": [{ t: "Search filters", d: "Narrow groups by course, status, and meeting type.", sel: ".search-form, form" }],
    "/semesters": [{ t: "Your semesters", d: "Create a semester, mark one active, and open it to see a month or full semester view.", sel: ".sem-main" }],
    "/campus": [{ t: "Directions", d: "Pick a building or search any place, then choose From my location or type where you are starting.", sel: ".campus-main" }],
    "/profile": [{ t: "Your photo and details", d: "Upload a photo here. It replaces the word Profile in the menu.", sel: "main" }]
  };

  function stopTour(markSeen) {
    if (!tour) return;
    tour.parts.forEach(function (p) { p.remove(); });
    window.removeEventListener("resize", tour.re); window.removeEventListener("scroll", tour.re, true);
    document.removeEventListener("keydown", tour.key);
    if (tour.openedMenu) closeNav();
    tour = null;
    if (markSeen) store(SEEN, "1");
  }
  function closeNav() {
    var n = $("#site-nav"), b = $(".menu-toggle");
    if (n && n.classList.contains("open") && b) b.click();
  }
  function startTour(list) {
    stopTour(false);
    closeMenu();
    var steps = list.filter(function (s) { return !s.sel || $(s.sel); });
    if (!steps.length) return;
    var spot = el("div", "sm-spot none"), block = el("div", "sm-block"), tip = el("div", "sm-tip");
    tip.setAttribute("role", "dialog"); tip.setAttribute("aria-live", "polite");
    document.body.appendChild(block); document.body.appendChild(spot); document.body.appendChild(tip);
    tour = { i: 0, parts: [block, spot, tip], openedMenu: false };
    function place() {
      var s = steps[tour.i], t = s.sel ? $(s.sel) : null;
      if (t && !visible(t)) t = null;
      if (!t) { spot.className = "sm-spot none"; tip.style.left = Math.max(12, (innerWidth - tip.offsetWidth) / 2) + "px"; tip.style.top = Math.max(12, (innerHeight - tip.offsetHeight) / 2) + "px"; return; }
      var r = t.getBoundingClientRect(), pad = 6;
      spot.className = "sm-spot";
      spot.style.left = (r.left - pad) + "px"; spot.style.top = (r.top - pad) + "px";
      spot.style.width = (r.width + pad * 2) + "px"; spot.style.height = (r.height + pad * 2) + "px";
      var tw = tip.offsetWidth, th = tip.offsetHeight, below = r.bottom + 16 + th < innerHeight;
      tip.style.top = (below ? r.bottom + 16 : Math.max(12, r.top - th - 16)) + "px";
      tip.style.left = Math.min(Math.max(12, r.left), Math.max(12, innerWidth - tw - 12)) + "px";
    }
    function show(i) {
      tour.i = i; var s = steps[i], last = i === steps.length - 1;
      if (s.menu && innerWidth <= 760) { var n = $("#site-nav"), b = $(".menu-toggle"); if (n && b && !n.classList.contains("open")) { b.click(); tour.openedMenu = true; } }
      else if (tour.openedMenu) { closeNav(); tour.openedMenu = false; }
      tip.innerHTML = "<h2>" + esc(s.t) + "</h2><p>" + esc(s.d) + '</p><div class="row"><span class="count">' + (i + 1) + " of " + steps.length +
        '</span><div class="btns">' + (last ? "" : '<button class="skip" type="button">Skip</button>') +
        (i > 0 ? '<button class="back" type="button">Back</button>' : "") +
        '<button class="go" type="button">' + (last ? "Done" : "Next") + "</button></div></div>";
      var sk = $(".skip", tip); if (sk) sk.onclick = function () { stopTour(true); };
      var bk = $(".back", tip); if (bk) bk.onclick = function () { show(i - 1); };
      $(".go", tip).onclick = function () { last ? stopTour(true) : show(i + 1); };
      var t = s.sel ? $(s.sel) : null;
      if (t && visible(t)) t.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
      setTimeout(place, t ? 280 : 0); place();
      $(".go", tip).focus({ preventScroll: true });
    }
    tour.re = function () { place(); };
    tour.key = function (e) {
      if (e.key === "Escape") stopTour(true);
      else if (e.key === "ArrowRight") { var g = $(".go", tip); if (g) g.click(); }
      else if (e.key === "ArrowLeft") { var b = $(".back", tip); if (b) b.click(); }
    };
    window.addEventListener("resize", tour.re); window.addEventListener("scroll", tour.re, true);
    document.addEventListener("keydown", tour.key);
    show(0);
  }
  function pageSteps() {
    var p = location.pathname, k = Object.keys(PAGE).filter(function (x) { return p === x || p.indexOf(x + "/") === 0; })[0];
    return k ? PAGE[k] : null;
  }

  /* ---------- help dock ---------- */
  var menu, dock;
  function closeMenu() { if (menu) menu.classList.remove("open"); }
  function buildDock() {
    dock = el("div", "sm-dock");
    menu = el("div", "sm-menu");
    menu.innerHTML =
      '<button type="button" data-a="tour">Take the tour</button>' +
      '<button type="button" data-a="page">Tips for this page</button>' +
      '<a href="/campus">Directions on campus</a>' +
      '<button type="button" data-a="mode">Switch light / dark</button>' +
      '<button type="button" data-a="look">Customize the look</button>' +
      '<button type="button" data-a="top">Back to top</button>';
    var top = el("button", "fab top", "&uarr;"); top.type = "button"; top.setAttribute("aria-label", "Back to top");
    var fab = el("button", "fab help", "?"); fab.type = "button"; fab.setAttribute("aria-label", "Help and settings"); fab.setAttribute("aria-haspopup", "true");
    dock.appendChild(menu); dock.appendChild(top); dock.appendChild(fab); document.body.appendChild(dock);
    fab.onclick = function (e) { e.stopPropagation(); menu.classList.toggle("open"); };
    top.onclick = function () { scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" }); };
    document.addEventListener("click", function (e) { if (!dock.contains(e.target)) closeMenu(); });
    menu.onclick = function (e) {
      var a = e.target.getAttribute && e.target.getAttribute("data-a"); if (!a) return;
      closeMenu();
      if (a === "tour") startTour(GENERAL);
      if (a === "page") { var ps = pageSteps(); startTour(ps || [{ t: "No special tips here", d: "This page works like it looks. Use the ? button any time, or take the full tour from the menu.", sel: null }]); }
      if (a === "look") openDrawer();
      if (a === "mode") toggleTheme();
      if (a === "top") scrollTo({ top: 0, behavior: "smooth" });
    };
    addEventListener("scroll", function () { top.classList.toggle("show", scrollY > 500); }, { passive: true });
    document.addEventListener("keydown", function (e) {
      if (e.key === "?" && !/input|textarea|select/i.test((e.target.tagName || ""))) { menu.classList.toggle("open"); }
    });
  }

  /* ---------- customizer ---------- */
  var drawer, scrim;
  function openDrawer() { if (!drawer) buildDrawer(); drawer.classList.add("open"); scrim.classList.add("open"); $(".x", drawer).focus(); }
  function closeDrawer() { if (drawer) { drawer.classList.remove("open"); scrim.classList.remove("open"); } }
  function buildDrawer() {
    scrim = el("div", "sm-scrim"); drawer = el("aside", "sm-drawer");
    drawer.setAttribute("aria-label", "Customize StudyMore");
    document.body.appendChild(scrim); document.body.appendChild(drawer);
    scrim.onclick = closeDrawer;
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeDrawer(); });
    render();
  }
  function seg(name, opts, cur) {
    return '<div class="sm-seg" data-seg="' + name + '">' + opts.map(function (o) { return '<button type="button" data-v="' + o[0] + '"' + (String(cur) === String(o[0]) ? ' class="on"' : "") + ">" + o[1] + "</button>"; }).join("") + "</div>";
  }
  function applyPreset(p) {
    state.c = { light: {}, dark: {} };
    if (p.id !== "zine") {
      state.c.light = { blue: p.blue, orange: p.orange, lime: p.lime, bg: p.bg };
      state.c.dark = { blue: mix(p.blue, "#ffffff", .38), orange: mix(p.orange, "#ffffff", .2), lime: p.lime };
      if (lum(p.blue) < .02) state.c.dark.blue = "#cfcfcf";
    }
    state.preset = p.id;
  }
  function render() {
    var mode = effMode(), cur = currentColors(mode);
    var html = '<button class="x" type="button" aria-label="Close">&times;</button><h2>Make it yours</h2><p style="margin:0;color:var(--muted);font-size:.9rem">Changes apply instantly and are saved on this device.</p>' +
      "<h3>Light or dark</h3>" + seg("theme", [["", "Match my device"], ["light", "Light"], ["dark", "Dark"]], state.theme || "") +
      "<h3>Themes</h3><div class=\"sm-presets\">" + PRESETS.map(function (p) {
        var c = p.id === "zine" ? DEF.light : { bg: p.bg, blue: p.blue, orange: p.orange, lime: p.lime };
        return '<button type="button" class="sm-preset' + (state.preset === p.id || (!state.preset && p.id === "zine") ? " on" : "") + '" data-p="' + p.id + '"><i>' +
          ["bg", "blue", "orange", "lime"].map(function (k) { return '<b style="background:' + c[k] + '"></b>'; }).join("") + "</i>" + p.name + "</button>";
      }).join("") + "</div><h3>Your own colors (" + mode + " mode)</h3><div class=\"sm-colors\">" +
      COLOR_LABELS.map(function (c) { return '<label><input type="color" data-c="' + c[0] + '" value="' + cur[c[0]] + '">' + c[1] + "</label>"; }).join("") + "</div>" +
      "<h3>Font</h3>" + seg("font", [["zine", "Zine"], ["clean", "Clean"], ["round", "Rounded"], ["serif", "Serif"]], state.font || "zine") +
      "<h3>Text size</h3><input type=\"range\" min=\"85\" max=\"130\" step=\"5\" value=\"" + (state.size || 100) + "\" data-size aria-label=\"Text size\">" +
      "<h3>Corners</h3>" + seg("r", [[0, "Sharp"], [8, "Soft"], [16, "Round"], [28, "Pill"]], state.r == null ? 16 : state.r) +
      "<h3>Shadows</h3>" + seg("shadow", [["hard", "Hard"], ["soft", "Soft"], ["none", "None"]], state.shadow || "hard") +
      "<h3>Motion</h3>" + seg("motion", [["on", "Full"], ["off", "Calm"]], state.motion || "on") +
      "<h3>Cursor</h3>" + seg("cursor", [["on", "Custom"], ["off", "Normal"]], state.cursor || "on") +
      '<button class="reset" type="button">Reset everything</button>';
    drawer.innerHTML = html;
    $(".x", drawer).onclick = closeDrawer;
    $$(".sm-preset", drawer).forEach(function (b) {
      b.onclick = function () { applyPreset(PRESETS.filter(function (x) { return x.id === b.dataset.p; })[0]); paint(); render(); };
    });
    $$("input[data-c]", drawer).forEach(function (i) {
      i.oninput = function () { state.c[mode][i.dataset.c] = i.value; state.preset = "custom"; paint(); };
      i.onchange = render;
    });
    $("[data-size]", drawer).oninput = function (e) { state.size = +e.target.value; paint(); };
    $$("[data-seg]", drawer).forEach(function (g) {
      g.onclick = function (e) {
        var b = e.target.closest("button"); if (!b) return;
        var v = b.dataset.v, n = g.dataset.seg;
        state[n] = n === "r" ? +v : v; paint(); render();
      };
    });
    $(".reset", drawer).onclick = function () { state = { c: { light: {}, dark: {} } }; paint(); render(); };
  }

  /* ---------- availability ---------- */
  function availability() {
    var grid = $(".availability-grid"); if (!grid) return;
    var slots = $$(".availability-slot", grid);
    var countEl = $("#availCount"), best = el("div", "av-best"), counts = {};
    var card = grid.closest(".availability-card");
    var tools = el("div", "av-tools");
    [["aft", "Weekday afternoons"], ["eve", "Evenings"], ["morn", "Mornings"], ["all", "Select all"], ["clr", "Clear"]].forEach(function (b) {
      var x = el("button", null, b[1]); x.type = "button"; x.dataset.k = b[0]; tools.appendChild(x);
    });
    var legend = el("div", "av-legend", '<span><i style="background:var(--blue)"></i>You are free</span><span><i style="background:color-mix(in srgb,var(--lime) 30%,transparent)"></i>Number = group mates free</span><span><i style="box-shadow:inset 0 0 0 3px var(--lime);background:var(--blue)"></i>You and a mate</span>');
    var scroll = grid.closest(".availability-scroll") || grid;
    scroll.parentNode.insertBefore(tools, scroll); scroll.parentNode.insertBefore(best, scroll); scroll.parentNode.insertBefore(legend, scroll);

    // color-code the time labels (morning, afternoon, evening)
    $$(".time-label", grid).forEach(function (l, i) { l.classList.add(i < 3 ? "am" : i < 8 ? "pm" : "eve"); });
    slots.forEach(function (s, i) { s.dataset.row = Math.floor(i / 5); s.dataset.col = i % 5; });

    function key(s) { return $("input", s).value; }
    function refresh() {
      var on = 0, bestN = 0, bestKey = null;
      slots.forEach(function (s) {
        var i = $("input", s), n = counts[i.value] || 0, sp = $("span", s);
        sp.dataset.n = n ? n : ""; sp.style.setProperty("--heat", Math.min(n, 3) / 3);
        s.classList.toggle("match", i.checked && n > 0);
        if (i.checked) { on++; if (n > bestN) { bestN = n; bestKey = i.value; } }
      });
      if (countEl) countEl.textContent = (on) + " free hour" + (on === 1 ? "" : "s") + " this week";
      if (bestKey) {
        var p = bestKey.split("|"), lab = $$(".time-label", grid)[Math.floor(slots.indexOf($('input[value="' + bestKey + '"]', grid).closest(".availability-slot")) / 5)];
        best.textContent = "Best overlap: " + p[0] + ", " + (lab ? lab.textContent.trim() : p[1]) + " (" + bestN + " group mate" + (bestN === 1 ? "" : "s") + " free too)";
        best.classList.add("show");
      } else best.classList.remove("show");
    }
    function setSlot(s, v) { var i = $("input", s); if (i.checked !== v) { i.checked = v; } }
    function bulk(fn) { slots.forEach(function (s) { setSlot(s, fn(+s.dataset.row, +s.dataset.col)); }); refresh(); }
    tools.onclick = function (e) {
      var k = e.target.dataset && e.target.dataset.k; if (!k) return;
      if (k === "all") bulk(function () { return true; });
      if (k === "clr") bulk(function () { return false; });
      if (k === "aft") slots.forEach(function (s) { if (+s.dataset.row >= 3 && +s.dataset.row <= 7) setSlot(s, true); });
      if (k === "eve") slots.forEach(function (s) { if (+s.dataset.row >= 8) setSlot(s, true); });
      if (k === "morn") slots.forEach(function (s) { if (+s.dataset.row <= 2) setSlot(s, true); });
      refresh();
    };

    var painting = null;
    grid.addEventListener("pointerdown", function (e) {
      if (e.pointerType !== "mouse" || e.button !== 0) return;
      var s = e.target.closest(".availability-slot"); if (!s) return;
      painting = !$("input", s).checked; setSlot(s, painting); refresh();
    });
    grid.addEventListener("pointerover", function (e) {
      if (painting === null || e.pointerType !== "mouse") return;
      var s = e.target.closest(".availability-slot"); if (s) { setSlot(s, painting); refresh(); }
    });
    addEventListener("pointerup", function () { painting = null; });
    // the label's own click would flip the box a second time after painting; touch and keyboard still use it
    grid.addEventListener("click", function (e) {
      var s = e.target.closest(".availability-slot");
      if (s && e.target.tagName !== "INPUT" && e.detail > 0 && e.pointerType !== "touch" && window.matchMedia("(pointer:fine)").matches) e.preventDefault();
    });
    grid.addEventListener("change", refresh);
    refresh();

    fetch("/api/availability/overlap", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.counts) { counts = d.counts; refresh(); } }).catch(function () {});
  }

  function greet() {
    var g = $("[data-greet]"); if (!g) return;
    var h = new Date().getHours(), w = h < 5 ? "Still up" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
    var t = $(".greet-word", g); if (t) t.textContent = w;
  }

  /* ---------- go ---------- */
  function init() {
    loadState(); paint();
    themeButton(); navAvatar();
    if (!$(".auth-card")) buildDock();
    cursor(); cursorFlag();
    availability();
    greet();
    if (SM.user && !$(".auth-card") && !store(SEEN)) setTimeout(function () { if (!store(SEEN)) startTour(GENERAL); }, 700);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
  window.StudyMoreUI = { tour: function () { startTour(GENERAL); }, customize: openDrawer };
})();
