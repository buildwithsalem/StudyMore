/* Walking directions shown inside the group page (Leaflet + CARTO/Photon).
   No API key needed. Needs Leaflet loaded first. */
(function () {
  var root = document.getElementById("directions");
  if (!root) return;
  if (typeof L === "undefined") {
    // map library blocked or offline: still give a working Google Maps link
    window.StudyMoreSetPlace = function (name, onCampus) {
      var g = document.getElementById("dir-google"), c = root.dataset.campus || "";
      if (g) g.href = "https://www.google.com/maps/dir/?api=1&travelmode=walking&destination=" + encodeURIComponent(name + (onCampus !== false && c ? ", " + c : ""));
      var st = document.getElementById("dir-status");
      if (st) { st.textContent = "The map could not load. Use the Google Maps link below."; st.className = "dir-status err"; }
    };
    return;
  }

  var place = root.dataset.place || "";
  var nearCampus = true;
  var campus = root.dataset.campus || "";
  var statusEl = document.getElementById("dir-status");
  var summaryEl = document.getElementById("dir-summary");
  var stepsEl = document.getElementById("dir-steps");
  var googleEl = document.getElementById("dir-google");
  var startInput = document.getElementById("dir-start");

  var map = L.map("dir-map", { scrollWheelZoom: false }).setView([32.7299, -97.1125], 16);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 20,
    subdomains: "abcd",
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
  }).addTo(map);

  var routeLayer = null, destMarker = null, dest = null;

  function say(text, isError) {
    statusEl.textContent = text || "";
    statusEl.className = "dir-status" + (isError ? " err" : "");
  }

  function geocode(query) {
    var url = "https://photon.komoot.io/api/?limit=1&lat=32.7299&lon=-97.1125&q=" + encodeURIComponent(query);
    return fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var f = data && data.features && data.features[0];
        if (!f) return null;
        var coords = f.geometry.coordinates; // [lon, lat]
        return { lat: coords[1], lon: coords[0] };
      });
  }

  function googleLink(origin) {
    if (!place) { googleEl.removeAttribute("href"); return; }
    var dest = encodeURIComponent(place + (nearCampus && campus ? ", " + campus : ""));
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
      routeLayer = L.geoJSON(route.geometry, { style: { color: "#2d46ff", weight: 5 } }).addTo(map);
      L.circleMarker([from.lat, from.lon], { radius: 7, color: "#fff", weight: 2, fillColor: "#2d46ff", fillOpacity: 1 }).addTo(routeLayer);
      map.fitBounds(routeLayer.getBounds(), { padding: [30, 30] });

      var minutes = Math.max(1, Math.round(route.duration / 60));
      summaryEl.textContent = minutes + " min walk \u00B7 " + feet(route.distance);
      summaryEl.hidden = false;

      stepsEl.innerHTML = "";
      route.legs[0].steps.forEach(function (s) {
        var li = document.createElement("li");
        var t = document.createElement("span"); t.textContent = words(s);
        var d = document.createElement("span"); d.className = "dist";
        d.textContent = s.distance > 0 ? feet(s.distance) : "";
        li.appendChild(t); li.appendChild(d); stepsEl.appendChild(li);
      });
      say("");
    }).catch(function () { say("Could not load directions right now. Try the Google Maps link below.", true); });
  }

  function setPlace(name, onCampus) {
    place = name; nearCampus = onCampus !== false; dest = null;
    if (destMarker) { map.removeLayer(destMarker); destMarker = null; }
    if (routeLayer) { map.removeLayer(routeLayer); routeLayer = null; }
    summaryEl.hidden = true; stepsEl.innerHTML = "";
    googleLink();
    say("Locating " + name + "...");
    geocode(name + (nearCampus && campus ? ", " + campus : "")).then(function (pt) {
      if (place !== name) return;
      if (!pt) { say("We couldn't pin this exact spot on the map. Use the Google Maps link below.", true); return; }
      dest = pt; say("");
      destMarker = L.marker([pt.lat, pt.lon]).addTo(map).bindPopup(name);
      map.setView([pt.lat, pt.lon], 17);
    }).catch(function () { say("The map service is not responding. Use the Google Maps link below.", true); });
  }
  window.StudyMoreSetPlace = setPlace;
  if (place) setPlace(place, true);

  document.getElementById("dir-locate").onclick = function () {
    if (!dest) { say(place ? "Still locating the destination. Try again in a moment." : "Pick a place first.", true); return; }
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