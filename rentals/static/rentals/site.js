/*
 * Small behaviours used across pages. Kept in a file (not inline) so the site
 * can run with a strict Content-Security-Policy.
 *   data-print            click → print the page
 *   data-confirm="…"      click on a button/link → ask first; cancel if "No"
 *   #tote-search          filters the renter's tote list as they type
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
