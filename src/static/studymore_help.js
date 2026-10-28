/* StudyMore: corner help button, first-time tutorial, campus directions.
   Add to your base template before </body>:
   <link rel="stylesheet" href="{{ url_for('static', filename='studymore_help.css') }}">
   <script src="{{ url_for('static', filename='studymore_help.js') }}"></script>
   Optionally set before it: <script>window.STUDYMORE_CAMPUS="Your University, City, ST";</script> */
(function () {
  var CAMPUS = window.STUDYMORE_CAMPUS || "";
  // EDIT: your campus buildings
  var PLACES = window.STUDYMORE_PLACES || ["Main Library", "Student Union", "Science Building", "Engineering Hall"];
  var SEEN = "studymore_tutorial_seen";

  var STEPS = [
    { t: "Welcome to StudyMore", d: "Find study groups for your classes, match with people who share your schedule, and plan sessions together.", sel: null },
    { t: "Find a group", d: "Search by course, then filter by status or meeting type. Join an open group in one click.", sel: '.topbar nav a[href*="search"], .topbar nav a:first-child' },
    { t: "Set your availability", d: "Tell us when you are free. Smart matches and group heatmaps use this to find the best meeting time.", sel: '.topbar nav a[href*="availab"]' },
    { t: "Plan your semester", d: "Pick the semester you are working on and add or upload your syllabus to plan study sessions.", sel: '.topbar nav a[href*="semesters"]' },
    { t: "Need help later?", d: "The ? button in the corner replays this tour and gets you directions around campus.", sel: ".help-fab" }
  ];

  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html) e.innerHTML = html; return e; }

  // Help button + menu
  var fab = el("button", "help-fab", "?"); fab.type = "button"; fab.setAttribute("aria-label", "Help");
  var menu = el("div", "help-menu");
  menu.innerHTML = '<button data-a="tour">Replay tutorial</button><button data-a="dir">Directions on campus</button>';
  document.body.appendChild(menu); document.body.appendChild(fab);
  fab.onclick = function () { menu.classList.toggle("open"); };
  document.addEventListener("click", function (e) { if (!menu.contains(e.target) && e.target !== fab) menu.classList.remove("open"); });
  menu.onclick = function (e) {
    var a = e.target.getAttribute && e.target.getAttribute("data-a");
    if (a === "tour") startTour(); if (a === "dir") openDirections();
    if (a) menu.classList.remove("open");
  };

  // Overlay dialog
  var overlay = el("div", "sm-overlay"); var dialog = el("div", "sm-dialog");
  overlay.appendChild(dialog); document.body.appendChild(overlay);
  var lit = null;
  function unlight() { if (lit) { lit.classList.remove("sm-highlight"); lit = null; } }
  function close() { overlay.classList.remove("open"); unlight(); }
  overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });

  function startTour() { show(0); }
  function show(i) {
    unlight();
    var s = STEPS[i], last = i === STEPS.length - 1;
    var dots = STEPS.map(function (_, k) { return "<i" + (k === i ? ' class="on"' : "") + "></i>"; }).join("");
    dialog.innerHTML = '<div class="sm-dots">' + dots + "</div><h2>" + s.t + "</h2><p>" + s.d + "</p>" +
      '<div class="sm-row"><button class="sm-skip" type="button">Skip</button><button class="primary" type="button">' + (last ? "Get started" : "Next") + "</button></div>";
    dialog.querySelector(".sm-skip").onclick = function () { done(); };
    dialog.querySelector(".primary").onclick = function () { last ? done() : show(i + 1); };
    if (s.sel) { var t = document.querySelector(s.sel); if (t) { t.classList.add("sm-highlight"); lit = t; } }
    overlay.classList.add("open");
  }
  function done() { try { localStorage.setItem(SEEN, "1"); } catch (e) {} close(); }

  // Directions: opens Google Maps from the person's location to the chosen place
  function openDirections() {
    unlight();
    var opts = PLACES.map(function (p) { return "<option>" + p + "</option>"; }).join("");
    dialog.innerHTML = "<h2>Directions on campus</h2><p>Pick a building or type any place. Maps opens with directions from where you are.</p>" +
      '<label class="dir-field">Building<select id="dirSel"><option value="">Choose a building</option>' + opts + "</select></label>" +
      '<label class="dir-field">Or any place<input id="dirTxt" type="text" placeholder="e.g. Coffee shop near the library"></label>' +
      '<div class="sm-row"><button class="sm-skip" type="button">Cancel</button><button class="primary" type="button">Get directions</button></div>';
    dialog.querySelector(".sm-skip").onclick = close;
    dialog.querySelector(".primary").onclick = function () {
      var place = dialog.querySelector("#dirTxt").value.trim() || dialog.querySelector("#dirSel").value;
      if (!place) return;
      window.open("https://www.google.com/maps/dir/?api=1&travelmode=walking&destination=" +
        encodeURIComponent(place + (CAMPUS ? ", " + CAMPUS : "")), "_blank", "noopener");
      close();
    };
    overlay.classList.add("open");
  }
  window.StudyMoreDirections = function (place) {
    window.open("https://www.google.com/maps/dir/?api=1&destination=" + encodeURIComponent(place + (CAMPUS ? ", " + CAMPUS : "")), "_blank", "noopener");
  };

  var seen = false; try { seen = localStorage.getItem(SEEN); } catch (e) {}
  if (!seen && !document.querySelector(".auth-card")) setTimeout(startTour, 500);
})();
