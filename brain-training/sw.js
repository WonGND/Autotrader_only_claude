/* 두뇌 트레이닝 PWA 서비스 워커
 * 전략: 설치 시 전체 자산 프리캐시 → 오프라인에서도 모든 훈련 가능.
 * 자산/데이터를 수정했다면 CACHE_VERSION 을 올려야 새 버전이 배포된다. */
const CACHE_VERSION = 'bt-v5';

const ASSETS = [
  './',
  './index.html',
  './manifest.webmanifest',
  './css/style.css',
  './js/app.js',
  './js/storage.js',
  './js/data.js',
  './js/ui.js',
  './js/vocab.js',
  './js/thinking.js',
  './js/speech.js',
  './js/stats.js',
  './js/daily.js',
  './data/vocab.json',
  './data/categories.json',
  './data/thinking.json',
  './data/speech_topics.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/maskable-512.png',
  './icons/apple-touch-icon.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_VERSION).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;

  e.respondWith(
    caches.match(e.request, { ignoreSearch: true }).then((cached) => {
      if (cached) return cached;
      return fetch(e.request)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE_VERSION).then((c) => c.put(e.request, copy));
          }
          return res;
        })
        .catch(() => {
          // 오프라인 내비게이션은 앱 셸로 폴백
          if (e.request.mode === 'navigate') return caches.match('./index.html');
          return Response.error();
        });
    })
  );
});
