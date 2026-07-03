/* localStorage 기반 저장소. 서버 없이 브라우저 로컬에만 기록한다.
 * 키 구조:
 *  bt_records : 훈련 기록 배열 [{ts, date, module, score, detail}]
 *  bt_sr      : 어휘 간격 반복 상태 { [wordId]: {box, due, wrong, right} }
 *  bt_daily   : 오늘의 훈련 진행 상태 { date, steps: {vocab, thinking, speech} }
 */

const PREFIX = 'bt_';

function get(key, fallback) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function set(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch (e) {
    console.warn('저장 실패:', e);
  }
}

/** 로컬 기준 YYYY-MM-DD */
export function todayStr(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/* ---------- 훈련 기록 ---------- */

export function addRecord(module, score, detail = {}) {
  const records = get('records', []);
  records.push({
    ts: Date.now(),
    date: todayStr(),
    module,
    score: Math.round(score),
    detail,
  });
  set('records', records);
}

export function getRecords() {
  return get('records', []);
}

/** 연속 훈련 일수: 오늘(또는 어제)부터 거꾸로 이어진 날짜 수 */
export function getStreak() {
  const dates = new Set(getRecords().map((r) => r.date));
  if (dates.size === 0) return 0;
  const day = new Date();
  // 오늘 기록이 없으면 어제부터 센다(오늘 아직 안 했어도 streak 유지 표시)
  if (!dates.has(todayStr(day))) day.setDate(day.getDate() - 1);
  let streak = 0;
  while (dates.has(todayStr(day))) {
    streak += 1;
    day.setDate(day.getDate() - 1);
  }
  return streak;
}

/* ---------- 간격 반복(spaced repetition) ---------- */

// box(레이트너 상자)별 다음 복습 간격(일). 틀리면 box 0으로 강등.
const SR_INTERVALS = [0, 1, 3, 7, 14, 30];

export function getSrState() {
  return get('sr', {});
}

export function updateSr(wordId, correct) {
  const sr = getSrState();
  const cur = sr[wordId] || { box: 0, due: todayStr(), wrong: 0, right: 0 };
  if (correct) {
    cur.box = Math.min(cur.box + 1, SR_INTERVALS.length - 1);
    cur.right += 1;
  } else {
    cur.box = 0;
    cur.wrong += 1;
  }
  const next = new Date();
  next.setDate(next.getDate() + SR_INTERVALS[cur.box]);
  cur.due = todayStr(next);
  sr[wordId] = cur;
  set('sr', sr);
}

/* ---------- 오늘의 훈련 진행 상태 ---------- */

export function getDailyState() {
  const s = get('daily', null);
  if (!s || s.date !== todayStr()) {
    return { date: todayStr(), steps: { vocab: null, thinking: null, speech: null } };
  }
  return s;
}

export function setDailyStep(step, score) {
  const s = getDailyState();
  s.steps[step] = Math.round(score);
  set('daily', s);
  return s;
}

/** 오늘의 훈련 3단계를 모두 마쳤는지 */
export function isDailyDone(state = getDailyState()) {
  return ['vocab', 'thinking', 'speech'].every((k) => state.steps[k] !== null);
}
