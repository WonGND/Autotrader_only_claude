/* 기록 · 통계
 * 연속 훈련 일수, 모듈별 점수 추이(최근 14일, SVG 라인 차트), 최근 훈련 기록 목록.
 */
import { el, toast } from './ui.js';
import { getRecords, getStreak, todayStr, resetAllData } from './storage.js';

const MODULES = [
  { key: 'vocab', label: '어휘 회상', color: '#0ea5e9' },
  { key: 'thinking', label: '사고력', color: '#8b5cf6' },
  { key: 'speech', label: '말하기', color: '#ec4899' },
  { key: 'daily', label: '오늘의 훈련', color: '#f59e0b' },
];
const MODULE_LABEL = Object.fromEntries(MODULES.map((m) => [m.key, m.label]));

function lastNDates(n) {
  const out = [];
  const d = new Date();
  d.setDate(d.getDate() - (n - 1));
  for (let i = 0; i < n; i++) {
    out.push(todayStr(d));
    d.setDate(d.getDate() + 1);
  }
  return out;
}

/** 날짜×모듈 평균 점수 맵 */
function dailyAverages(records) {
  const map = {}; // date -> module -> {sum, n}
  for (const r of records) {
    map[r.date] = map[r.date] || {};
    const cell = (map[r.date][r.module] = map[r.date][r.module] || { sum: 0, n: 0 });
    cell.sum += r.score;
    cell.n += 1;
  }
  return (date, module) => {
    const cell = map[date] && map[date][module];
    return cell ? cell.sum / cell.n : null;
  };
}

function buildChart(records, days = 14) {
  const dates = lastNDates(days);
  const avg = dailyAverages(records);
  const W = 560, H = 220;
  const padL = 34, padR = 10, padT = 12, padB = 26;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const x = (i) => padL + (days === 1 ? plotW / 2 : (i / (days - 1)) * plotW);
  const y = (score) => padT + (1 - score / 100) * plotH;

  let svg = '';
  // 가로 눈금 (0/50/100)
  for (const v of [0, 50, 100]) {
    svg += `<line x1="${padL}" y1="${y(v)}" x2="${W - padR}" y2="${y(v)}" stroke="#e5e7eb" stroke-width="1"/>`;
    svg += `<text x="${padL - 6}" y="${y(v) + 4}" text-anchor="end" font-size="10" fill="#9ca3af">${v}</text>`;
  }
  // 날짜 라벨(3~4개만)
  const labelEvery = Math.ceil(days / 4);
  dates.forEach((d, i) => {
    if (i % labelEvery !== 0 && i !== days - 1) return;
    const [, m, dd] = d.split('-');
    svg += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" font-size="10" fill="#9ca3af">${Number(m)}/${Number(dd)}</text>`;
  });
  // 모듈별 라인 + 점
  let hasAny = false;
  for (const mod of MODULES) {
    const pts = dates.map((d, i) => {
      const v = avg(d, mod.key);
      return v === null ? null : [x(i), y(v)];
    }).map((p, i) => ({ p, i })).filter((o) => o.p);
    if (!pts.length) continue;
    hasAny = true;
    if (pts.length > 1) {
      const path = pts.map((o, k) => `${k === 0 ? 'M' : 'L'}${o.p[0].toFixed(1)},${o.p[1].toFixed(1)}`).join(' ');
      svg += `<path d="${path}" fill="none" stroke="${mod.color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
    }
    for (const o of pts) {
      svg += `<circle cx="${o.p[0].toFixed(1)}" cy="${o.p[1].toFixed(1)}" r="3.5" fill="${mod.color}"/>`;
    }
  }

  const wrap = el('div', { class: 'chart-wrap' });
  wrap.innerHTML = `<svg class="chart-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="최근 ${days}일 점수 추이">${svg}</svg>`;
  return { wrap, hasAny };
}

export function render(root) {
  const records = getRecords().slice().sort((a, b) => b.ts - a.ts);
  const streak = getStreak();
  const week = lastNDates(7);
  const weekCount = records.filter((r) => week.includes(r.date)).length;

  root.innerHTML = '';
  root.append(
    el('div', { class: 'card' },
      el('div', { class: 'stat-grid' },
        el('div', { class: 'stat-box' }, el('div', { class: 'v' }, `🔥 ${streak}일`), el('div', { class: 'k' }, '연속 훈련')),
        el('div', { class: 'stat-box' }, el('div', { class: 'v' }, String(records.length)), el('div', { class: 'k' }, '누적 훈련 횟수')),
        el('div', { class: 'stat-box' }, el('div', { class: 'v' }, String(weekCount)), el('div', { class: 'k' }, '최근 7일 훈련')),
        el('div', { class: 'stat-box' },
          el('div', { class: 'v' }, records.length ? `${records[0].score}점` : '—'),
          el('div', { class: 'k' }, '마지막 점수')),
      ),
    ),
  );

  const { wrap, hasAny } = buildChart(records);
  const chartCard = el('div', { class: 'card' },
    el('h2', { class: 'section-title' }, '최근 14일 점수 추이'),
  );
  if (hasAny) {
    chartCard.append(wrap, el('div', { class: 'legend' },
      ...MODULES.map((m) => el('span', {},
        el('span', { class: 'dot', style: `background:${m.color}` }), m.label)),
    ));
  } else {
    chartCard.append(el('p', { class: 'muted' }, '아직 기록이 없습니다. 훈련을 시작하면 여기에 그래프가 그려집니다.'));
  }
  root.append(chartCard);

  const recent = records.slice(0, 20);
  const historyCard = el('div', { class: 'card' }, el('h2', { class: 'section-title' }, '최근 기록'));
  if (recent.length) {
    historyCard.append(el('table', { class: 'history-table' },
      el('thead', {}, el('tr', {},
        el('th', {}, '날짜'), el('th', {}, '모듈'), el('th', {}, '내용'), el('th', {}, '점수'))),
      el('tbody', {},
        ...recent.map((r) => el('tr', {},
          el('td', {}, r.date.slice(5).replace('-', '/')),
          el('td', {}, MODULE_LABEL[r.module] || r.module),
          el('td', {}, detailText(r)),
          el('td', {}, `${r.score}점`),
        )),
      ),
    ));
  } else {
    historyCard.append(el('p', { class: 'muted' }, '기록이 없습니다.'));
  }
  root.append(historyCard);

  root.append(el('div', { class: 'card' },
    el('h2', { class: 'section-title' }, '데이터 관리'),
    el('p', { class: 'muted' }, '모든 기록은 이 브라우저에만 저장됩니다. 초기화하면 훈련 기록과 단어 복습 상태가 모두 삭제됩니다.'),
    el('button', { class: 'btn danger small', style: 'margin-top:10px', onclick: () => {
      if (confirm('정말 모든 훈련 데이터를 삭제할까요? 되돌릴 수 없습니다.')) {
        resetAllData();
        toast('모든 데이터를 초기화했습니다.');
        render(root);
      }
    } }, '모든 데이터 초기화'),
  ));
}

function detailText(r) {
  const d = r.detail || {};
  if (r.module === 'vocab') {
    if (d.mode === 'category') return `연상 게임 · ${d.category} ${d.total}개`;
    return `회상 퀴즈 ${d.correct}/${d.total}`;
  }
  if (r.module === 'thinking') {
    const diff = { easy: '쉬움', normal: '보통', hard: '어려움' }[d.difficulty] || '';
    return `${diff} ${d.correct}/${d.total}`;
  }
  if (r.module === 'speech') {
    return `${d.mode === 'voice' ? '음성' : '텍스트'} · ${d.words}단어`;
  }
  if (r.module === 'daily') return '3개 모듈 완료';
  return '';
}
