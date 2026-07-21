// Metnos service worker minimale.
// Soddisfa il requisito Chrome per il banner "Installa" (PWA installability):
// SW registrato + fetch handler. Niente cache offline per ora (la chat e' live).
// Espansione futura: app shell cache, offline fallback page, push notifications.

const SW_VERSION = "metnos-v1";

self.addEventListener("install", (event) => {
  // Attiva subito senza aspettare reload.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Fetch handler pass-through (richiesto da Chrome per installability).
self.addEventListener("fetch", (event) => {
  // Lascia che il browser gestisca tutto. In futuro: stale-while-revalidate
  // sull'app shell (/, /static/*).
});
