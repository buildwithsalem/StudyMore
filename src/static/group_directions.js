/* Walking directions shown inside the group page (Leaflet + OpenStreetMap).
   No API key needed. Needs Leaflet loaded first. */
(function () {
  var root = document.getElementById("directions");
  if (!root || typeof L === "undefined") return;

  var place = root.dataset.place || "";
  var campus = root.dataset.campus || "";
  var statusEl = document.getElementById("dir-status");
  var summaryEl = document.getElementById("dir-summary");
  var stepsEl = document.getElementById("dir-steps");
  var googleEl = document.getElementById("dir-google");
  var startInput = document.getElementById("dir-start");

  var map = L.map("dir-map", { scrollWheelZoom: false }).setView([32.7299, -97.1125], 16);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(map);

  var routeLayer = null, destMarker = null, dest = null;
  var BLUE = (getComputedStyle(document.documentElement).getPropertyValue("--blue") || "").trim() || "#2d46ff";
  function pin(cls) { return L.divIcon({ className: "dir-pin " + cls, html: "<span></span>", iconSize: [26, 26], iconAnchor: [13, 13] }); }

  function say(text, isError) {
    statusEl.textContent = text || "";
    statusEl.className = "dir-status" + (isError ? " err" : "");
  }

  function geocode(query) {
    var url = "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + encodeURIComponent(query);
    return fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        return rows && rows.length ? { lat: parseFloat(rows[0].lat), lon: parseFloat(rows[0].lon) } : null;
      });
  }

  function googleLink(origin) {
    var dest = encodeURIComponent(place + (campus ? ", " + campus : ""));
    var url = "https://www.google.com/maps/dir/?api=1&travelmode=walking&destination=" + dest;
    if (origin) url += "&origin=" + encodeURIComponent(origin);
    googleEl.href = url;
  }
  googleLink();

  function feet(m) {
    var ft = m * 3.28084;
    return ft >= 1000 ? (m / 1609.34).toFixed(1) + " mi" : Math.round(ft / 10) * 10 + " ft";
  }

  function words(step) {
    var m = step.maneuver || {}, name = step.name ? " on " + step.name : "";
    var dir = m.modifier ? m.modifier.replace("slight ", "slight ").replace("sharp ", "sharp ") : "";
    if (m.type === "depart") return "Start walking" + name;
    if (m.type === "arrive") return "Arrive at " + place;
    if (m.type === "turn") return "Turn " + dir + name;
    if (m.type === "continue" || m.type === "new name") return "Continue" + name;
    if (m.type === "roundabout" || m.type === "rotary") return "Take the roundabout" + name;
    return (dir ? "Go " + dir : "Continue") + name;
  }

  function showRoute(from) {
    say("Finding the walking route...");
    var url = "https://routing.openstreetmap.de/routed-foot/route/v1/foot/" +
      from.lon + "," + from.lat + ";" + dest.lon + "," + dest.lat +
      "?overview=full&geometries=geojson&steps=true";
    fetch(url).then(function (r) { return r.json(); }).then(function (data) {
      if (!data.routes || !data.routes.length) { say("No walking route found. Try the Google Maps link below.", true); return; }
      var route = data.routes[0];
      if (routeLayer) map.removeLayer(routeLayer);
      routeLayer = L.geoJSON(route.geometry, { style: { color: BLUE, weight: 5 } }).addTo(map);
      L.marker([from.lat, from.lon], { icon: pin("start") }).addTo(routeLayer);
      map.fitBounds(routeLayer.getBounds(), { padding: [30, 30] });

      var minutes = Math.max(1, Math.round(route.duration / 60));
      summaryEl.innerHTML = "";
      [[minutes + " min walk", ""], [feet(route.distance), " alt"]].forEach(function (t) {
        var tag = document.createElement("span"); tag.className = "dir-tag" + t[1]; tag.textContent = t[0]; summaryEl.appendChild(tag);
      });
      summaryEl.hidden = false;

      stepsEl.innerHTML = "";
      route.legs[0].steps.forEach(function (s) {
        var li = document.createElement("li");
        var t = document.createElement("span"); t.className = "txt"; t.textContent = words(s);
        var d = document.createElement("span"); d.className = "dist";
        d.textContent = s.distance > 0 ? feet(s.distance) : "";
        li.appendChild(t); li.appendChild(d); stepsEl.appendChild(li);
      });
      say("");
    }).catch(function () { say("Could not load directions right now. Try the Google Maps link below.", true); });
  }

  // Pin the destination first so the map is useful even before a route is asked for.
  geocode(place + (campus ? ", " + campus : "")).then(function (pt) {
    if (!pt) { say("We couldn't pin this exact spot on the map. Use the Google Maps link below.", true); return; }
    dest = pt;
    destMarker = L.marker([pt.lat, pt.lon], { icon: pin("") }).addTo(map).bindTooltip(place, { permanent: true, direction: "top", offset: [0, -14], className: "dir-tip" });
    map.setView([pt.lat, pt.lon], 17);
  }).catch(function () { say("The map service is not responding. Use the Google Maps link below.", true); });

  document.getElementById("dir-locate").onclick = function () {
    if (!dest) { say("Still locating the destination. Try again in a moment.", true); return; }
    if (!navigator.geolocation) { say("Your browser can't share its location. Type a starting point instead.", true); return; }
    say("Getting your location...");
    navigator.geolocation.getCurrentPosition(function (pos) {
      googleLink(pos.coords.latitude + "," + pos.coords.longitude);
      showRoute({ lat: pos.coords.latitude, lon: pos.coords.longitude });
    }, function () { say("Location access was blocked. Type a starting point instead.", true); });
  };

  function fromTyped() {
    var text = startInput.value.trim();
    if (!text) { say("Type a building or address to start from.", true); return; }
    if (!dest) { say("Still locating the destination. Try again in a moment.", true); return; }
    say("Looking up your starting point...");
    geocode(text + (campus ? ", " + campus : "")).then(function (pt) {
      if (!pt) { say("We couldn't find that starting point. Try a more specific name.", true); return; }
      googleLink(text);
      showRoute(pt);
    }).catch(function () { say("The map service is not responding. Try again soon.", true); });
  }
  document.getElementById("dir-go").onclick = fromTyped;
  startInput.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); fromTyped(); } });
})();