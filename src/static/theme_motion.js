/* StudyMore: animate the light/dark switch from the button you clicked. */
(function () {
  "use strict";
  var root = document.documentElement;
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var last = { x: innerWidth - 60, y: 30 };
  var armed = false;
  var previous = root.getAttribute("data-theme");

  addEventListener("pointerdown", function (event) {
    last = { x: event.clientX, y: event.clientY };
  }, true);

  // Ignore the first theme set while the page loads; only animate real switches.
  addEventListener("load", function () {
    previous = root.getAttribute("data-theme");
    armed = true;
  });

  new MutationObserver(function () {
    var now = root.getAttribute("data-theme");
    if (now === previous) return;
    previous = now;
    if (!armed || reduce || !document.body) return;

    root.classList.add("sm-theming");
    setTimeout(function () { root.classList.remove("sm-theming"); }, 650);

    var bg = getComputedStyle(root).getPropertyValue("--bg").trim() || (now === "dark" ? "#0e0f2b" : "#e6e9fb");
    var far = Math.hypot(Math.max(last.x, innerWidth - last.x), Math.max(last.y, innerHeight - last.y));
    var wipe = document.createElement("div");
    wipe.className = "sm-wipe";
    wipe.style.left = last.x + "px";
    wipe.style.top = last.y + "px";
    wipe.style.background = bg;
    wipe.style.setProperty("--s", ((far / 5) * 1.15).toFixed(1));
    document.body.appendChild(wipe);
    setTimeout(function () { wipe.remove(); }, 750);
  }).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
})();