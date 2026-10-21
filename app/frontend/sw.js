const CACHE='pablo-vpn-telegram-v3';
const ASSETS=['./','./index.html','./styles.css','./app.js','./manifest.webmanifest','./assets/pablo-vpn.svg','./assets/telegram-web-app.js','./assets/pablo-vpn-192.png','./assets/pablo-vpn-512.png','./assets/pablo-vpn-180.png'];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS)).then(()=>self.skipWaiting())));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==CACHE).map(x=>caches.delete(x)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;const u=new URL(e.request.url);if(u.pathname.startsWith('/api/')||u.pathname.startsWith('/telegram/'))return;e.respondWith(fetch(e.request).then(r=>{if(r.ok)caches.open(CACHE).then(c=>c.put(e.request,r.clone()));return r}).catch(()=>caches.match(e.request).then(h=>h||caches.match('./index.html'))))});
