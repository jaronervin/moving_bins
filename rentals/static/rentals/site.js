/*
 * Small behaviours used across pages. Kept in a file (not inline) so the site
 * can run with a strict Content-Security-Policy.
 *   data-print            click → print the page
 *   data-confirm="…"      click on a button/link → ask first; cancel if "No"
 *   #tote-search          filters the renter's tote list as they type
 *   input[data-phone]     formats US numbers as you type: 7037032511 → (703) 703-2511
 *   [data-swap-addresses] swaps the moving-out and moving-in address boxes
 */
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-print], [data-confirm]");
    if (!el) return;
    if (el.hasAttribute("data-confirm") && !window.confirm(el.getAttribute("data-confirm"))) {
      e.preventDefault();
      return;
    }
    if (el.hasAttribute("data-print")) {
      e.preventDefault();
      window.print();
    }
  });

  // US phone numbers: (703) 703-2511. Anything starting with "+" (other
  // countries) is left as typed. Keeps the cursor after the same digit.
  function formatPhone(digits) {
    if (digits.length === 11 && digits[0] === "1") digits = digits.slice(1);
    if (digits.length > 10) return null;  // not a US number; leave as typed
    if (digits.length < 4) return digits.length ? "(" + digits : "";
    if (digits.length < 7) return "(" + digits.slice(0, 3) + ") " + digits.slice(3);
    return "(" + digits.slice(0, 3) + ") " + digits.slice(3, 6) + "-" + digits.slice(6);
  }
  document.addEventListener("input", function (e) {
    var el = e.target;
    if (!el.matches || !el.matches("input[data-phone]") || el.value.trim()[0] === "+") return;
    if (e.inputType && e.inputType.indexOf("delete") === 0 && /[^0-9]$/.test(el.value)) return;  // let backspace past ")" or "-"
    var caret = el.selectionStart, before = el.value.slice(0, caret).replace(/\D/g, "").length;
    var formatted = formatPhone(el.value.replace(/\D/g, ""));
    if (formatted === null || formatted === el.value) return;
    el.value = formatted;
    var pos = 0, seen = 0;
    while (pos < formatted.length && seen < before) { if (/\d/.test(formatted[pos])) seen++; pos++; }
    el.setSelectionRange(pos, pos);
  });
  document.querySelectorAll("input[data-phone]").forEach(function (el) {
    var formatted = el.value && el.value.trim()[0] !== "+" && formatPhone(el.value.replace(/\D/g, ""));
    if (formatted) el.value = formatted;
  });

  // Addresses step: swap moving-out and moving-in (e.g. the checked address
  // was really the new home).
  var swap = document.querySelector("[data-swap-addresses]");
  if (swap) {
    swap.addEventListener("click", function () {
      ["line1", "line2", "city", "state", "zip_code"].forEach(function (f) {
        var a = document.querySelector("[name='dropoff-" + f + "']");
        var b = document.querySelector("[name='pickup-" + f + "']");
        if (!a || !b) return;
        var t = a.value; a.value = b.value; b.value = t;
      });
      var unknown = document.querySelector("[name='pickup-unknown']");
      var pickupFilled = (document.querySelector("[name='pickup-line1']") || {}).value;
      if (unknown && pickupFilled) unknown.checked = false;  // it's known now
    });
  }

  // "Do we deliver to you?": ask the server and show the answer in place.
  var area = document.getElementById("area-check");
  if (area && window.fetch) {
    area.addEventListener("submit", function (e) {
      e.preventDefault();
      var out = document.getElementById("area-result"), button = area.querySelector("button");
      out.className = "area-result";
      out.textContent = "Checking…";
      button.disabled = true;
      fetch(area.action, {method: "POST", body: new FormData(area), credentials: "same-origin",
                          headers: {"X-Requested-With": "fetch"}})
        .then(function (r) { return r.json(); })
        .then(function (data) {
          out.textContent = data.message;
          out.className = "area-result " + data.status;
        })
        .catch(function () { out.textContent = "We couldn't check that right now. Please try again."; })
        .then(function () { button.disabled = false; });
    });
  }

  var input = document.getElementById("tote-search");
  if (input) {
    var items = document.querySelectorAll("#tote-list li");
    var none = document.getElementById("tote-none");
    input.addEventListener("input", function () {
      var words = input.value.toLowerCase().split(/\s+/).filter(Boolean), shown = 0;
      items.forEach(function (li) {
        var text = li.dataset.search.toLowerCase();
        var hit = words.every(function (w) { return text.indexOf(w) !== -1; });
        li.hidden = !hit;
        if (hit) shown++;
      });
      none.hidden = shown !== 0;
    });
  }
})();
