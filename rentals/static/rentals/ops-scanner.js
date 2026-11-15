/*
 * Owner scanner pages: read tote QR codes with the phone camera and send each
 * code to /ops/api/scan. Uses the browser's built-in BarcodeDetector where it
 * exists (Chrome on Android), and the jsQR library otherwise (iPhone Safari).
 * A typed code works the same way, for labels that won't scan.
 */
(function () {
  "use strict";
  var cfg = window.SCAN;
  var video = document.getElementById("video");
  var msg = document.getElementById("scan-msg");
  var list = document.getElementById("scanned");
  var lastCode = "", lastAt = 0, busy = false, detector = null, canvas, ctx;

  function csrf() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function codeFrom(text) {
    text = (text || "").trim().toUpperCase().replace(/\/+$/, "");
    var i = text.lastIndexOf("/Q/");
    if (i !== -1) text = text.slice(i + 3);
    text = text.split(/[?#]/)[0];
    return /^[A-Z0-9]{1,8}$/.test(text) ? text : "";
  }

  var audio;
  function feedback(ok) {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      var o = audio.createOscillator(), g = audio.createGain();
      o.frequency.value = ok ? 880 : 220; g.gain.value = 0.08;
      o.connect(g); g.connect(audio.destination); o.start(); o.stop(audio.currentTime + (ok ? 0.08 : 0.25));
    } catch (e) { /* sound is optional */ }
    if (navigator.vibrate) navigator.vibrate(ok ? 40 : [80, 60, 80]);
  }

  function show(text, ok) {
    msg.textContent = text;
    msg.className = "scan-msg " + (ok ? "ok" : "bad");
  }

  function addChip(code) {
    if (!list || list.querySelector('[data-code="' + code + '"]')) return;
    var li = document.createElement("li"), b = document.createElement("button");
    b.type = "button"; b.dataset.code = code; b.textContent = code;
    li.appendChild(b); list.insertBefore(li, list.firstChild);
  }

  function removeChip(code) {
    var b = list && list.querySelector('[data-code="' + code + '"]');
    if (b) b.parentNode.remove();
  }

  function send(code, undo) {
    if (busy) return;
    busy = true;
    var body = {mode: cfg.mode, reference: cfg.reference, code: code, undo: !!undo};
    if (cfg.mode === "check_in") {
      var picked = document.querySelector('input[name="outcome"]:checked');
      body.outcome = picked ? picked.value : "good";
      body.lid_salvaged = document.getElementById("lid-salvaged").checked;
    }
    fetch(cfg.api, {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()},
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (data) {
      show(data.message, data.ok);
      feedback(data.ok);
      if (!data.ok) return;
      if (undo) removeChip(data.code); else addChip(data.code);
      if (data.done !== undefined) {
        document.getElementById("done").textContent = data.done;
        document.getElementById("total").textContent = data.total;
      }
      if (cfg.mode === "check_in") {
        document.querySelector('input[name="outcome"][value="good"]').checked = true;
        document.getElementById("lid-salvaged").checked = false;
      }
    }).catch(function () {
      show("Couldn't reach the server. Check the connection and scan again.", false);
      feedback(false);
    }).then(function () { busy = false; });
  }

  function found(text) {
    var code = codeFrom(text), now = Date.now();
    if (!code || (code === lastCode && now - lastAt < 2500)) return;
    lastCode = code; lastAt = now;
    send(code, false);
  }

  function tick() {
    if (video.readyState >= 2) {
      if (detector) {
        detector.detect(video).then(function (codes) {
          if (codes.length) found(codes[0].rawValue);
        }).catch(function () {});
      } else if (window.jsQR) {
        canvas = canvas || document.createElement("canvas");
        var w = video.videoWidth, h = video.videoHeight, scale = Math.min(1, 640 / w);
        canvas.width = w * scale; canvas.height = h * scale;
        ctx = ctx || canvas.getContext("2d", {willReadFrequently: true});
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        var img = ctx.getImageData(0, 0, canvas.width, canvas.height);
        var hit = window.jsQR(img.data, img.width, img.height, {inversionAttempts: "dontInvert"});
        if (hit) found(hit.data);
      }
    }
    setTimeout(tick, 150);
  }

  function loadJsQR() {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js";
      s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  function startCamera() {
    var ready = ("BarcodeDetector" in window)
      ? window.BarcodeDetector.getSupportedFormats().then(function (f) {
          if (f.indexOf("qr_code") !== -1) detector = new window.BarcodeDetector({formats: ["qr_code"]});
          else return loadJsQR();
        })
      : loadJsQR();
    ready.then(function () {
      return navigator.mediaDevices.getUserMedia({video: {facingMode: "environment"}, audio: false});
    }).then(function (stream) {
      video.srcObject = stream; video.play();
      document.getElementById("start-camera").hidden = true;
      show("Scanning… point at a QR code.", true);
      tick();
    }).catch(function () {
      show("Camera unavailable. Allow camera access, or type codes below.", false);
    });
  }

  document.getElementById("start-camera").addEventListener("click", startCamera);
  document.getElementById("manual").addEventListener("submit", function (e) {
    e.preventDefault();
    var input = document.getElementById("manual-code"), code = codeFrom(input.value);
    if (code) { send(code, false); input.value = ""; }
    else show("Codes are letters and numbers, like K7Q.", false);
  });
  if (list && cfg.mode !== "check_in") {
    list.addEventListener("click", function (e) {
      var code = e.target.dataset && e.target.dataset.code;
      if (code && confirm("Undo the scan of " + code + "?")) send(code, true);
    });
  }
})();
