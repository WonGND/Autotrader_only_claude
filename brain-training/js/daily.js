/* 오늘의 훈련: 어휘 회상(8문제) → 사고력(4문제) → 말하기(1분)를 묶은 10~15분 루틴.
 * 단계별 진행 상태는 날짜가 바뀌면 초기화된다.
 */
import { el } from './ui.js';
import { addRecord, getDailyState, setDailyStep, isDailyDone, getStreak } from './storage.js';
import { startRecallQuiz } from './vocab.js';
import { startThinkingSession } from './thinking.js';
import { startSpeechSession } from './speech.js';

const STEPS = [
  { key: 'vocab', label: '어휘 회상 퀴즈', desc: '8문제 · 약 4분', icon: '💬' },
  { key: 'thinking', label: '사고력 문제', desc: '4문제 · 약 5분', icon: '🧩' },
  { key: 'speech', label: '주제 말하기', desc: '1분 · 약 3분', icon: '🎤' },
];

function runStep(root, key) {
  const onComplete = (score) => {
    const state = setDailyStep(key, score);
    if (isDailyDone(state)) {
      const scores = STEPS.map((s) => state.steps[s.key]);
      const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
      addRecord('daily', avg, {
        vocab: state.steps.vocab, thinking: state.steps.thinking, speech: state.steps.speech,
      });
    }
    render(root);
  };

  if (key === 'vocab') startRecallQuiz(root, { count: 8, onComplete });
  else if (key === 'thinking') startThinkingSession(root, { difficulty: 'normal', count: 4, timeLimit: 0, onComplete });
  else startSpeechSession(root, { seconds: 60, onComplete });
}

export function render(root) {
  const state = getDailyState();
  const done = isDailyDone(state);
  const nextStep = STEPS.find((s) => state.steps[s.key] === null);

  root.innerHTML = '';

  if (done) {
    const streak = getStreak();
    root.append(
      el('div', { class: 'card', style: 'text-align:center' },
        el('div', { style: 'font-size:3rem' }, '🎉'),
        el('h2', { class: 'section-title', style: 'margin-top:6px' }, '오늘의 훈련 완료!'),
        el('p', { class: 'muted' }, `연속 훈련 ${streak}일째. 내일 또 만나요!`),
      ),
    );
  } else {
    root.append(
      el('div', { class: 'card' },
        el('h2', { class: 'section-title' }, '🌟 오늘의 훈련'),
        el('p', { class: 'muted' }, '세 가지 훈련을 순서대로 진행합니다. 중간에 나가도 오늘 안에는 이어서 할 수 있습니다.'),
      ),
    );
  }

  const listCard = el('div', { class: 'card' });
  STEPS.forEach((s, i) => {
    const score = state.steps[s.key];
    const isNext = nextStep && nextStep.key === s.key;
    const row = el('div', { style: `display:flex; align-items:center; gap:12px; padding:10px 0; ${i < STEPS.length - 1 ? 'border-bottom:1px solid var(--line);' : ''}` },
      el('span', { style: 'font-size:1.4rem' }, s.icon),
      el('div', { style: 'flex:1' },
        el('div', { style: 'font-weight:700' }, `${i + 1}단계. ${s.label}`),
        el('div', { class: 'muted' }, s.desc),
      ),
      score !== null
        ? el('span', { class: 'badge done', style: 'background:#dcfce7; color:#16a34a; font-weight:700; font-size:0.85rem; padding:4px 10px; border-radius:999px' }, `✓ ${score}점`)
        : (isNext ? el('span', { style: 'color:var(--primary); font-weight:700' }, '다음 ▶') : el('span', { class: 'muted' }, '대기')),
    );
    listCard.append(row);
  });
  root.append(listCard);

  if (!done) {
    root.append(el('button', { class: 'btn block', onclick: () => runStep(root, nextStep.key) },
      state.steps[STEPS[0].key] === null ? '오늘의 훈련 시작' : `이어서 하기 (${STEPS.findIndex((s) => s.key === nextStep.key) + 1}단계)`));
  } else {
    root.append(el('div', { class: 'btn-row' },
      el('button', { class: 'btn ghost', onclick: () => { location.hash = '#stats'; } }, '통계 보기'),
      el('button', { class: 'btn', onclick: () => { location.hash = ''; } }, '홈으로'),
    ));
  }
}
