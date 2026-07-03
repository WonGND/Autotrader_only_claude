/* 사고력 훈련
 * 논리 퍼즐·순서 추론(객관식) + 발산적 사고(자유 서술).
 * 날짜 시드로 매일 다른 문제 세트를 구성하고, 난이도 3단계와 문제당 제한 시간 옵션을 제공한다.
 */
import { el, progressBar, countdown } from './ui.js';
import { loadData, dateSeed, mulberry32, seededShuffle } from './data.js';
import { addRecord } from './storage.js';

const DIFF_LABEL = { easy: '쉬움', normal: '보통', hard: '어려움' };
const TYPE_LABEL = { logic: '논리 퍼즐', sequence: '순서 추론', divergent: '발산적 사고' };
const MIN_DIVERGENT_CHARS = 15; // 이 글자 수 이상 쓰면 발산 문제를 '수행'으로 인정

/** 날짜 시드로 오늘의 문제 세트 구성: 객관식 위주 + 발산 1문제(마지막) */
function pickProblems(problems, difficulty, count) {
  const rng = mulberry32(dateSeed(difficulty.length)); // 난이도별로 시드 분리
  const pool = problems.filter((p) => p.difficulty === difficulty);
  const mc = seededShuffle(pool.filter((p) => p.type !== 'divergent'), rng);
  const div = seededShuffle(pool.filter((p) => p.type === 'divergent'), rng);
  const picked = mc.slice(0, Math.max(1, count - 1));
  if (div.length && picked.length < count) picked.push(div[0]);
  return picked.slice(0, count);
}

export async function startThinkingSession(root, {
  difficulty = 'normal', count = 5, timeLimit = 0, onComplete = null,
} = {}) {
  const { problems } = await loadData('thinking');
  const quiz = pickProblems(problems, difficulty, count);
  let idx = 0;
  const results = []; // {item, correct, timedOut}

  function showQuestion() {
    const item = quiz[idx];
    root.innerHTML = '';
    let stopTimer = null;
    let answered = false;

    const timerEl = timeLimit ? el('div', { class: 'timer' }) : null;
    const card = el('div', { class: 'card' },
      el('p', { class: 'question-meta' }, `${TYPE_LABEL[item.type]} · ${DIFF_LABEL[item.difficulty]}`),
      el('p', { class: 'question-text' }, item.question),
    );
    if (timerEl) card.prepend(timerEl);

    const feedback = el('div');

    function nextButton() {
      const btn = el('button', { class: 'btn block', style: 'margin-top:12px', onclick: () => {
        idx += 1;
        if (idx < quiz.length) showQuestion();
        else showResult();
      } }, idx + 1 < quiz.length ? '다음 문제' : '결과 보기');
      return btn;
    }

    if (item.type === 'divergent') {
      // 자유 서술: 정답 없음. 일정 분량 이상 쓰면 수행으로 인정.
      const ta = el('textarea', { class: 'answer-input', placeholder: '떠오르는 생각을 자유롭게 적어 보세요 (정답은 없습니다)' });
      const doneBtn = el('button', { class: 'btn block', style: 'margin-top:12px', onclick: () => finishDivergent(false) }, '작성 완료');
      card.append(ta, doneBtn, feedback);

      function finishDivergent(timedOut) {
        if (answered) return;
        answered = true;
        if (stopTimer) stopTimer();
        const text = ta.value.trim();
        const ok = text.length >= MIN_DIVERGENT_CHARS;
        results.push({ item, correct: ok, timedOut });
        ta.disabled = true;
        doneBtn.disabled = true;
        feedback.append(el('div', { class: `feedback ${ok ? 'good' : 'info'}` },
          timedOut ? '시간 종료! ' : '',
          ok ? '좋아요, 생각을 충분히 펼쳤습니다.' : `조금 더 길게(${MIN_DIVERGENT_CHARS}자 이상) 써 보면 더 좋은 훈련이 됩니다.`));
        if (item.examples && item.examples.length) {
          feedback.append(el('div', { class: 'feedback info', style: 'margin-top:8px' },
            `예시 아이디어: ${item.examples.join(' · ')}`));
        }
        feedback.append(nextButton());
      }

      if (timeLimit) {
        // 발산 문제는 생각할 시간이 더 필요하므로 제한 시간을 2배로
        stopTimer = countdown(timeLimit * 2, (r) => {
          timerEl.textContent = `${r}초`;
          timerEl.classList.toggle('urgent', r <= 10);
        }, () => finishDivergent(true));
      }
      root.append(progressBar(idx + 1, quiz.length), card);
      ta.focus();
      return;
    }

    // 객관식
    const choiceBtns = [];
    const choicesEl = el('div', { class: 'choices' },
      ...item.choices.map((c, i) => {
        const b = el('button', { class: 'choice-btn', onclick: () => finishMC(i, false) }, `${i + 1}. ${c}`);
        choiceBtns.push(b);
        return b;
      }),
    );
    card.append(choicesEl, feedback);

    function finishMC(chosen, timedOut) {
      if (answered) return;
      answered = true;
      if (stopTimer) stopTimer();
      const correct = chosen === item.answer;
      results.push({ item, correct, timedOut });
      choiceBtns.forEach((b, i) => {
        b.disabled = true;
        if (i === item.answer) b.classList.add('correct');
        else if (i === chosen && !correct) b.classList.add('wrong');
      });
      const head = timedOut ? '시간 종료! ' : '';
      feedback.append(el('div', { class: `feedback ${correct ? 'good' : 'bad'}` },
        `${head}${correct ? '정답입니다!' : '오답입니다.'} ${item.explanation || ''}`));
      feedback.append(nextButton());
    }

    if (timeLimit) {
      stopTimer = countdown(timeLimit, (r) => {
        timerEl.textContent = `${r}초`;
        timerEl.classList.toggle('urgent', r <= 10);
      }, () => finishMC(-1, true));
    }
    root.append(progressBar(idx + 1, quiz.length), card);
  }

  function showResult() {
    const correctCount = results.filter((r) => r.correct).length;
    const score = Math.round((correctCount / results.length) * 100);
    addRecord('thinking', score, { difficulty, total: results.length, correct: correctCount });

    root.innerHTML = '';
    root.append(
      el('div', { class: 'card' },
        el('div', { class: 'result-score' }, `${score}점`),
        el('p', { class: 'result-sub' }, `${DIFF_LABEL[difficulty]} 난이도 · ${results.length}문제 중 ${correctCount}개 수행`),
        el('ul', { class: 'review-list' },
          ...results.map((r) => el('li', {},
            el('span', { class: `mark ${r.correct ? 'o' : 'x'}` }, r.correct ? 'O' : 'X'),
            el('span', {}, `[${TYPE_LABEL[r.item.type]}] ${r.item.question.slice(0, 40)}${r.item.question.length > 40 ? '…' : ''}`),
          )),
        ),
      ),
    );

    if (onComplete) {
      root.append(el('button', { class: 'btn block ok', onclick: () => onComplete(score) }, '다음 단계로'));
    } else {
      root.append(el('div', { class: 'btn-row' },
        el('button', { class: 'btn ghost', onclick: () => render(document.getElementById('app')) }, '다시 하기'),
        el('button', { class: 'btn', onclick: () => { location.hash = ''; } }, '홈으로'),
      ));
    }
  }

  showQuestion();
}

/* ---------- 설정 화면 ---------- */

export function render(root) {
  root.innerHTML = '';
  let difficulty = 'normal';
  let timeLimit = 0;

  function chipGroup(options, initial, onPick) {
    const group = el('div', { class: 'chip-group' });
    options.forEach(([value, label]) => {
      const chip = el('button', { class: `chip ${value === initial ? 'active' : ''}`, onclick: () => {
        onPick(value);
        group.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
        chip.classList.add('active');
      } }, label);
      group.append(chip);
    });
    return group;
  }

  root.append(
    el('div', { class: 'card' },
      el('h2', { class: 'section-title' }, '🧩 사고력 훈련'),
      el('p', { class: 'muted' }, '논리 퍼즐과 순서 추론 4문제 + 발산적 사고 1문제. 문제 세트는 매일 바뀝니다.'),
      el('p', { class: 'muted', style: 'margin-top:10px; font-weight:700' }, '난이도'),
      chipGroup([['easy', '쉬움'], ['normal', '보통'], ['hard', '어려움']], difficulty, (v) => { difficulty = v; }),
      el('p', { class: 'muted', style: 'margin-top:10px; font-weight:700' }, '문제당 제한 시간'),
      chipGroup([[0, '없음'], [30, '30초'], [60, '60초']], timeLimit, (v) => { timeLimit = v; }),
      el('button', { class: 'btn block', style: 'margin-top:14px', onclick: () => startThinkingSession(root, { difficulty, timeLimit }) }, '시작하기'),
    ),
  );
}
