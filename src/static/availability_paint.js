(function () {
  var grid = document.querySelector(".availability-grid");
  if (!grid) return;
  var countEl = document.getElementById("availCount");

  function updateCount() {
    if (!countEl) return;
    var on = grid.querySelectorAll('input[type="checkbox"]:checked').length;
    countEl.textContent = (on * 2) + " free hours this week";
  }
  updateCount();

  var painting = null;

  function setSlot(label, value) {
    var input = label.querySelector('input[type="checkbox"]');
    if (!input) return;
    input.checked = value;
    updateCount();
  }

  grid.addEventListener("pointerdown", function (e) {
    var label = e.target.closest(".availability-slot");
    if (!label) return;
    var input = label.querySelector('input[type="checkbox"]');
    painting = !input.checked;
    setSlot(label, painting);
    e.preventDefault();
  });

  grid.addEventListener("pointerover", function (e) {
    if (painting === null) return;
    var label = e.target.closest(".availability-slot");
    if (label) setSlot(label, painting);
  });

  addEventListener("pointerup", function () { painting = null; });
})();