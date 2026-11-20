/* Mounts Stripe's embedded Checkout (setup mode: saves a card, charges nothing). */
(function () {
  "use strict";
  var el = document.getElementById("checkout");
  if (!el || !window.Stripe) {
    if (el) el.textContent = "The secure card form couldn't load. Please refresh the page.";
    return;
  }
  var csrf = (document.querySelector("input[name=csrfmiddlewaretoken]") || {}).value || "";
  var stripe = window.Stripe(el.dataset.publishableKey);

  var fetchClientSecret = function () {
    return fetch(el.dataset.sessionUrl, {
      method: "POST", credentials: "same-origin", headers: {"X-CSRFToken": csrf}
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.error) throw new Error(data.error);
      return data.clientSecret;
    });
  };

  stripe.createEmbeddedCheckoutPage({fetchClientSecret: fetchClientSecret}).then(function (checkout) {
    var loading = document.getElementById("checkout-loading");
    if (loading) loading.remove();
    checkout.mount("#checkout");
  }).catch(function (err) {
    el.textContent = (err && err.message) || "Something went wrong loading the card form.";
  });
})();
