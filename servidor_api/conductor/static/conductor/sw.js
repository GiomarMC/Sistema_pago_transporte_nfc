const CACHE = 'subepe-conductor-shell-v1';
const ARCHIVOS = [
  '/conductor/',
  '/static/conductor/rele.js',
  '/static/conductor/protocolo.js',
  '/static/conductor/pendientes.js',
  '/static/conductor/estilo.css',
];
self.addEventListener('install', evento => {
  evento.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ARCHIVOS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', evento => {
  evento.waitUntil(caches.keys().then(claves => Promise.all(
    claves.filter(c => c.startsWith('subepe-conductor-shell-') && c !== CACHE).map(c => caches.delete(c))
  )).then(() => self.clients.claim()));
});
self.addEventListener('fetch', evento => {
  if (evento.request.method !== 'GET') return;
  const url = new URL(evento.request.url);
  if (url.origin !== location.origin || !ARCHIVOS.includes(url.pathname)) return;
  evento.respondWith(fetch(evento.request).then(respuesta => {
    if (respuesta.ok && !respuesta.redirected) {
      const copia = respuesta.clone();
      caches.open(CACHE).then(cache => cache.put(evento.request, copia));
    }
    return respuesta;
  }).catch(async () => (await caches.match(evento.request)) || Response.error()));
});
