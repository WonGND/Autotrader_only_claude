/* 앱 진입점: 해시 라우터 + 홈 화면 + 서비스 워커 등록 */
import { el, toast } from './ui.js';
import { getStreak, getDailyState, isDailyDone } from './storage.js';

const appEl = document.getElementById('app');
const titleEl = document.getElementById('topbar-title');
const backBtn = document.getElementById('btn-back');
const streakEl = document.getElementById('topbar-streak');

/* 라우트 정의. 모듈 파일은 필요할 때 동적 로드한다. */
const ROUTES = {
  home: { title: '두뇌 트레이닝', render: renderHome },
  vocab: { title: '어휘 회상 훈련', load: () => import('./vocab.js') },
  thinking: { title: '사고력 훈련', load: () => import('./thinking.js') },
  speech: { title: '말하기 훈련', load: () => import('./speech.js') },
  stats: { title: '기록 · 통계', load: () => import('./stats.js') },
  daily: { title: '오늘의 훈련', load: () => import('./daily.js') },
};

export function navigate(route) {
  location.hash = route === 'home' ? '' : `#${route}`;
}

async function route() {
  const name = (location.hash || '#home').slice(1) || 'home';
  const r = ROUTES[name] || ROUTES.home;
  titleEl.textContent = r.title;
  backBtn.classList.toggle('hidden', name === 'home');
  updateStreakBadge();
  appEl.innerHTML = '';
  appEl.scrollTop = 0;
  window.scrollTo(0, 0);

  if (r.render) {
    r.render(appEl);
    return;
  }
  try {
    const mod = await r.load();
    mod.render(appEl);
  } catch (e) {
    console.warn(`모듈(${name}) 로드 실패:`, e);
    toast('이 모듈은 아직 준비 중입니다.');
    navigate('home');
  }
}

function updateStreakBadge() {
  const s = getStreak();
  streakEl.textContent = s > 0 ? `🔥 ${s}일` : '';
}

/* ---------- 홈 화면 ---------- */

function menuCard({ icon, iconClass, title, desc, route, badge }) {
  const card = el('button', { class: 'menu-card', onclick: () => navigate(route) },
    el('div', { class: `icon ${iconClass}` }, icon),
    el('div', {},
      el('h3', {}, title),
      el('p', {}, desc),
    ),
  );
  if (badge) card.append(el('span', { class: `badge ${badge.done ? 'done' : ''}` }, badge.text));
  return card;
}

function renderHome(root) {
  const daily = getDailyState();
  const done = isDailyDone(daily);
  const doneCount = ['vocab', 'thinking', 'speech'].filter((k) => daily.steps[k] !== null).length;

  root.append(
    el('div', { class: 'home-hero' },
      el('h2', {}, '오늘도 두뇌를 깨워 볼까요?'),
      el('p', { class: 'muted' }, '사고력 · 어휘 회상 · 말하기, 하루 10~15분'),
    ),
    menuCard({
      icon: '🌟', iconClass: 'i-daily',
      title: '오늘의 훈련',
      desc: '3개 모듈을 묶은 하루 루틴 (10~15분)',
      route: 'daily',
      badge: done ? { text: '완료!', done: true } : { text: `${doneCount} / 3` },
    }),
    menuCard({
      icon: '💬', iconClass: 'i-vocab',
      title: '어휘 회상 훈련',
      desc: '뜻을 보고 단어 떠올리기 · 카테고리 연상 게임',
      route: 'vocab',
    }),
    menuCard({
      icon: '🧩', iconClass: 'i-think',
      title: '사고력 훈련',
      desc: '논리 퍼즐 · 순서 추론 · 발산적 사고',
      route: 'thinking',
    }),
    menuCard({
      icon: '🎤', iconClass: 'i-speech',
      title: '말하기 훈련',
      desc: '주제 말하기 → 음성인식 전사 · 발화 분석',
      route: 'speech',
    }),
    menuCard({
      icon: '📈', iconClass: 'i-stats',
      title: '기록 · 통계',
      desc: '점수 추이 그래프 · 연속 훈련 일수',
      route: 'stats',
    }),
  );
}

/* ---------- 초기화 ---------- */

backBtn.addEventListener('click', () => history.back());
window.addEventListener('hashchange', route);
// 훈련 기록 직후 상단 streak 배지를 즉시 갱신하기 위한 앱 내부 이벤트
window.addEventListener('bt:record-added', updateStreakBadge);
route();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch((e) => console.warn('SW 등록 실패:', e));
  });
}
