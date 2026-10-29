/* One Cards / List switch for every list on the site. The choice is remembered on this device.
   Mark a list with data-layout-list (or it picks up .results, .my-groups-list and .session-card-list). */
(function () {
  var KEY = "sm-view";
  var lists = Array.prototype.slice.call(document.querySelectorAll("[data-layout-list], .results, .my-groups-list, .session-card-list"));
  lists = lists.filter(function (l, i) { return lists.indexOf(l) === i && l.children.length > 1; });
  if (!lists.length) return;
  var view = "cards";
  try { view = localStorage.getItem(KEY) || "cards"; } catch (e) {}
  if (view !== "cards" && view !== "list") view = "cards";

  var seg = document.createElement("div");
  seg.className = "layout-seg";
  seg.innerHTML = '<div class="seg" role="group" aria-label="How to show the list">' +
    '<button type="button" data-v="cards">Cards</button><button type="button" data-v="list">List</button></div>';
  lists[0].parentNode.insertBefore(seg, lists[0]);

  function apply(v) {
    lists.forEach(function (l) { l.classList.remove("layout-cards", "layout-list"); l.classList.add("layout-" + v); });
    Array.prototype.forEach.call(seg.querySelectorAll("button"), function (b) {
      b.setAttribute("aria-current", b.getAttribute("data-v") === v ? "true" : "false");
    });
    try { localStorage.setItem(KEY, v); } catch (e) {}
  }
  seg.addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (b) apply(b.getAttribute("data-v"));
  });
  apply(view);
})();
