/* StudyMore availability page.
   - Desktop: the weekly grid, with its columns sized to the real number of days.
   - Phones: a day-by-day view (tabs + big tappable time rows) that drives the same checkboxes. */
(function () {
    var grid = document.querySelector(".availability-grid");
    if (!grid) { return; }

    var headers = [].slice.call(grid.querySelectorAll(".grid-header:not(.time-header)"));
    var days = headers.length;
    if (days > 0) { grid.style.setProperty("--days", days); }

    var labels = [].slice.call(grid.querySelectorAll(".time-label"));
    labels.forEach(function (l) { l.dataset.full = l.textContent.replace(/\s+/g, " ").trim(); });

    var today = new Date().toLocaleDateString("en-US", { weekday: "long" });

    /* reads "9-10 AM", "11 AM-12 PM", "12-1 PM", "9:00 AM - 10:00 AM"; returns start hour 0-23 */
    function startHour(text) {
        var m = text.match(/^\s*(\d{1,2})(?::\d{2})?\s*([AaPp])?[Mm]?\s*[-–— to]+\s*(\d{1,2})(?::\d{2})?\s*([AaPp])?[Mm]?/);
        if (!m) {
            var one = text.match(/^\s*(\d{1,2})(?::\d{2})?\s*([AaPp])?/);
            if (!one) { return null; }
            var h1 = parseInt(one[1], 10);
            if (one[2]) { return one[2].toLowerCase() === "p" ? (h1 % 12) + 12 : (h1 % 12); }
            return h1;
        }
        var s = parseInt(m[1], 10), e = parseInt(m[3], 10);
        var sm = m[2] ? m[2].toLowerCase() : null;
        var em = m[4] ? m[4].toLowerCase() : null;
        var startPM;
        if (sm) { startPM = sm === "p"; }
        else if (em === "p") { startPM = (s === 12) || (s < e && e !== 12); }
        else if (em === "a") { startPM = false; }
        else { return s; }
        return startPM ? (s % 12) + 12 : (s % 12);
    }

    /* time-of-day bands on the grid labels, and today's column */
    labels.forEach(function (l) {
        var h = startHour(l.dataset.full);
        if (h !== null) {
            l.classList.toggle("am", h < 12);
            l.classList.toggle("pm", h >= 12 && h < 17);
            l.classList.toggle("eve", h >= 17);
        }
        l.title = l.dataset.full;
    });
    headers.forEach(function (h) {
        var full = h.querySelector(".d-full");
        if (full && full.textContent.trim() === today) { h.classList.add("is-today"); }
    });

    /* ---------- phone day view ---------- */
    var inputs = [].slice.call(grid.querySelectorAll(".availability-slot input"));
    if (!days || !labels.length || inputs.length !== days * labels.length) { return; }  /* unexpected shape: keep the grid */

    var names = headers.map(function (h) {
        var f = h.querySelector(".d-full");
        return f ? f.textContent.trim() : h.textContent.trim();
    });
    var shorts = headers.map(function (h, j) {
        var s = h.querySelector(".d-short");
        return s ? s.textContent.trim() : names[j].slice(0, 3);
    });

    var scroll = grid.closest(".availability-scroll") || grid.parentNode;
    var wrap = document.createElement("div");
    wrap.className = "av-day-view";
    wrap.innerHTML =
        '<div class="av-tabs" role="tablist"></div>' +
        '<div class="av-day-tools"><button type="button" data-a="all">Select whole day</button>' +
        '<button type="button" data-a="none">Clear day</button></div>' +
        '<div class="av-rows"></div>';
    scroll.parentNode.insertBefore(wrap, scroll.nextSibling);

    var tabs = wrap.querySelector(".av-tabs");
    var rows = wrap.querySelector(".av-rows");
    var cur = Math.max(0, names.indexOf(today));

    function at(i, j) { return inputs[i * days + j]; }
    function countDay(j) {
        var n = 0;
        for (var i = 0; i < labels.length; i++) { if (at(i, j).checked) { n++; } }
        return n;
    }
    function bandOf(i) {
        var h = startHour(labels[i].dataset.full);
        if (h === null) { return ""; }
        return h < 12 ? "Morning" : (h < 17 ? "Afternoon" : "Evening");
    }
    function fire(input) { input.dispatchEvent(new Event("change", { bubbles: true })); }

    function render() {
        tabs.innerHTML = "";
        names.forEach(function (n, j) {
            var b = document.createElement("button");
            b.type = "button";
            b.className = "av-tab" + (j === cur ? " on" : "");
            b.setAttribute("role", "tab");
            b.setAttribute("aria-selected", j === cur ? "true" : "false");
            b.innerHTML = '<span class="av-tab-day"></span><span class="av-tab-n"></span>';
            b.firstChild.textContent = shorts[j];
            var c = countDay(j);
            b.lastChild.textContent = c ? c + " free" : "none";
            b.addEventListener("click", function () { cur = j; render(); });
            tabs.appendChild(b);
        });

        rows.innerHTML = "";
        var last = "";
        labels.forEach(function (l, i) {
            var band = bandOf(i);
            if (band && band !== last) {
                var h = document.createElement("h3");
                h.className = "av-band";
                h.textContent = band;
                rows.appendChild(h);
                last = band;
            }
            var input = at(i, cur);
            var r = document.createElement("button");
            r.type = "button";
            r.className = "av-row" + (input.checked ? " on" : "") + (band ? " " + band.toLowerCase() : "");
            r.setAttribute("aria-pressed", input.checked ? "true" : "false");
            r.innerHTML = '<span class="av-time"></span><span class="av-state"></span>';
            r.firstChild.textContent = l.dataset.full;
            r.lastChild.textContent = input.checked ? "✓ Free" : "Tap if free";
            r.addEventListener("click", function () {
                input.checked = !input.checked;
                fire(input);
                render();
            });
            rows.appendChild(r);
        });
    }

    wrap.querySelector(".av-day-tools").addEventListener("click", function (e) {
        var a = e.target.getAttribute && e.target.getAttribute("data-a");
        if (!a) { return; }
        var want = a === "all";
        for (var i = 0; i < labels.length; i++) {
            var input = at(i, cur);
            if (input.checked !== want) { input.checked = want; fire(input); }
        }
        render();
    });

    /* stay in sync when the quick-pick buttons (or anything else) change the boxes */
    var form = grid.closest("form");
    if (form) { form.addEventListener("change", render); }
    [].slice.call(document.querySelectorAll(".av-tools button")).forEach(function (b) {
        b.addEventListener("click", function () { setTimeout(render, 0); setTimeout(render, 80); });
    });

    render();
})();