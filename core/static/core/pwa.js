/* Registers the service worker and shows "Versi baru tersedia · Muat ulang" when an update waits (D74). */
(function () {
  if (!("serviceWorker" in navigator)) return;
  let reloading = false;

  function showBanner(worker) {
    const banner = document.getElementById("sw-update");
    if (!banner) return;
    banner.hidden = false;
    banner.querySelector("button").onclick = function () {
      worker.postMessage({ type: "SKIP_WAITING" });
    };
  }

  navigator.serviceWorker.addEventListener("controllerchange", function () {
    if (reloading) return;
    reloading = true;
    window.location.reload();
  });

  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(function (reg) {
      if (reg.waiting && navigator.serviceWorker.controller) showBanner(reg.waiting);
      reg.addEventListener("updatefound", function () {
        const worker = reg.installing;
        if (!worker) return;
        worker.addEventListener("statechange", function () {
          if (worker.state === "installed" && navigator.serviceWorker.controller) showBanner(worker);
        });
      });
      setInterval(function () { reg.update().catch(function () {}); }, 60 * 60 * 1000);
    }).catch(function () { /* PWA is optional; the app works without it */ });
  });
})();
