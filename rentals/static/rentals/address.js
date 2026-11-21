/*
 * Address suggestions on the booking "Addresses" step (Google Places).
 *
 * Only runs when the page has #places-config with a browser key (set
 * GOOGLE_MAPS_BROWSER_KEY). Typing in a "Street address" box shows up to five
 * Google suggestions; picking one fills street, city, state and ZIP and moves
 * to "Apt / unit". Typing by hand still works: the server checks every address
 * with Google either way.
 */
(function () {
  "use strict";
  var config = document.getElementById("places-config");
  if (!config || !config.dataset.key) return;

  var places = null;  // google.maps.places once loaded

  window.sproutPlacesReady = function () {
    google.maps.importLibrary("places").then(function (lib) {
      places = lib;
      document.querySelectorAll("input[name$='-line1']").forEach(enhance);
    }).catch(function () { /* suggestions are optional */ });
  };
  var s = document.createElement("script");
  s.src = "https://maps.googleapis.com/maps/api/js?key=" + encodeURIComponent(config.dataset.key) +
          "&loading=async&callback=sproutPlacesReady&v=weekly";
  s.async = true;
  document.head.appendChild(s);

  function field(prefix, name) { return document.querySelector("[name='" + prefix + "-" + name + "']"); }

  function enhance(input) {
    var prefix = input.name.replace(/-line1$/, "");
    if (input.hasAttribute("data-full-address")) input.removeAttribute("autocomplete");
    input.setAttribute("autocomplete", "off");  // one dropdown, not two
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");

    var wrap = document.createElement("div");
    wrap.className = "suggest";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var list = document.createElement("ul");
    list.className = "suggest-list";
    list.id = prefix + "-suggestions";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    input.setAttribute("aria-controls", list.id);
    wrap.appendChild(list);

    var token = null, timer = null, items = [], active = -1, seq = 0;
    var center = {lat: parseFloat(config.dataset.lat), lng: parseFloat(config.dataset.lng)};

    function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; }
    function highlight(i) {
      active = i;
      Array.prototype.forEach.call(list.children, function (li, j) {
        li.setAttribute("aria-selected", j === i ? "true" : "false");
      });
    }
    function show(suggestions) {
      list.innerHTML = "";
      items = suggestions.filter(function (sug) { return sug.placePrediction; }).slice(0, 5);
      if (!items.length) return close();
      items.forEach(function (sug, i) {
        var p = sug.placePrediction, li = document.createElement("li");
        li.setAttribute("role", "option");
        li.id = list.id + "-" + i;
        var main = document.createElement("b");
        main.textContent = p.mainText ? p.mainText.toString() : p.text.toString();
        li.appendChild(main);
        if (p.secondaryText) {
          var rest = document.createElement("span");
          rest.textContent = " " + p.secondaryText.toString();
          li.appendChild(rest);
        }
        li.addEventListener("mousedown", function (e) { e.preventDefault(); choose(i); });
        list.appendChild(li);
      });
      var credit = document.createElement("li");
      credit.className = "suggest-credit";
      credit.textContent = "powered by Google";
      list.appendChild(credit);
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      highlight(-1);
    }

    function search() {
      var text = input.value.trim();
      if (text.length < 3) return close();
      token = token || new places.AutocompleteSessionToken();
      var mine = ++seq;
      places.AutocompleteSuggestion.fetchAutocompleteSuggestions({
        input: text,
        sessionToken: token,
        includedRegionCodes: ["us"],
        locationBias: {center: center, radius: 50000},
      }).then(function (res) {
        if (mine === seq) show(res.suggestions || []);
      }).catch(close);
    }

    function choose(i) {
      var item = items[i];
      if (!item) return;
      close();
      if (input.hasAttribute("data-full-address")) {  // one box: keep the whole address
        input.value = item.placePrediction.text.toString();
        token = null;
        if (input.form) input.form.requestSubmit();
        return;
      }
      var place = item.placePrediction.toPlace();
      place.fetchFields({fields: ["addressComponents"]}).then(function () {
        token = null;  // a new session for the next search
        var part = {};
        (place.addressComponents || []).forEach(function (c) {
          c.types.forEach(function (t) { part[t] = c; });
        });
        function long(t) { return part[t] ? part[t].longText : ""; }
        function short(t) { return part[t] ? part[t].shortText : ""; }
        var street = [long("street_number"), short("route")].filter(Boolean).join(" ");
        input.value = street || item.placePrediction.mainText.toString();
        var city = long("locality") || long("sublocality") || long("neighborhood") || long("postal_town");
        if (city && field(prefix, "city")) field(prefix, "city").value = city;
        var state = field(prefix, "state"), code = short("administrative_area_level_1");
        if (state && code && state.querySelector("option[value='" + code + "']")) state.value = code;
        if (short("postal_code") && field(prefix, "zip_code")) field(prefix, "zip_code").value = short("postal_code");
        var unit = field(prefix, "line2");
        if (unit) unit.focus();
      }).catch(function () { /* leave what they typed */ });
    }

    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(search, 200);
    });
    input.addEventListener("keydown", function (e) {
      if (list.hidden) return;
      if (e.key === "ArrowDown") { e.preventDefault(); highlight(Math.min(active + 1, items.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight(Math.max(active - 1, 0)); }
      else if (e.key === "Enter" && active >= 0) { e.preventDefault(); choose(active); }
      else if (e.key === "Escape") { close(); }
      input.setAttribute("aria-activedescendant", active >= 0 ? list.id + "-" + active : "");
    });
    input.addEventListener("blur", function () { setTimeout(close, 150); });
  }
})();
