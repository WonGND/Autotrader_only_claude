/* 어휘 회상 훈련
 * 1) 역방향 회상 퀴즈: 뜻을 보고 단어를 떠올려 입력. 틀린 단어는 간격 반복으로 우선 재출제.
 * 2) 카테고리 연상 게임: 제한 시간 안에 해당 범주의 단어를 최대한 많이 입력.
 */
import { el, progressBar, countdown } from './ui.js';
import { loadData, dateSeed, mulberry32, seededShuffle } from './data.js';
import { addRecord, getSrState, updateSr, todayStr } from './storage.js';

const CHOSEONG = ['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'];

function choseongOf(word) {
  return [...word].map((ch) => {
    const code = ch.charCodeAt(0) - 0xac00;
    if (code < 0 || code > 11171) return ch;
    return CHOSEONG[Math.floor(code / 588)];
  }).join('');
}

function normalize(s) {
  return String(s).trim().replace(/\s+/g, '').toLowerCase();
}

function isCorrect(input, item) {
  const n = normalize(input);
  if (!n) return false;
  return [item.word, ...(item.alt || [])].some((a) => normalize(a) === n);
}

/* ---------- 출제 우선순위: 복습 예정(틀린 단어 우선) → 새 단어 → 나머지 ---------- */
function pickQuizWords(words, count) {
  const sr = getSrState();
  const today = todayStr();
  const rng = mulberry32(dateSeed());

  const due = words.filter((w) => sr[w.id] && sr[w.id].due <= today)
    .sort((a, b) => (sr[a.id].box - sr[b.id].box) || (sr[b.id].wrong - sr[a.id].wrong));
  const fresh = seededShuffle(words.filter((w) => !sr[w.id]), rng);
  const rest = seededShuffle(words.filter((w) => sr[w.id] && sr[w.id].due > today), rng);

  return [...due, ...fresh, ...rest].slice(0, count);
}

/* ---------- 역방향 회상 퀴즈 ---------- */

export async function startRecallQuiz(root, { count = 10, onComplete = null } = {}) {
  const { words } = await loadData('vocab');
  const quiz = pickQuizWords(words, Math.min(count, words.length));
  let idx = 0;
  const results = []; // {item, correct, answer}

  function showQuestion() {
    const item = quiz[idx];
    root.innerHTML = '';
    let hintLevel = 0;

    const input = el('input', {
      class: 'answer-input', type: 'text', autocomplete: 'off',
      placeholder: '떠오르는 단어를 입력하세요', enterkeyhint: 'done',
    });
    const hintEl = el('p', { class: 'muted', style: 'margin-top:8px' });
    const feedback = el('div');

    const hintBtn = el('button', { class: 'btn ghost small', onclick: () => {
      hintLevel += 1;
      if (hintLevel === 1) hintEl.textContent = `힌트: ${item.word.length}글자`;
      else hintEl.textContent = `힌트: ${item.word.length}글자 · 초성 ${choseongOf(item.word)}`;
      if (hintLevel >= 2) hintBtn.disabled = true;
    } }, '힌트 보기');

    const submitBtn = el('button', { class: 'btn', onclick: submit }, '제출');
    const giveUpBtn = el('button', { class: 'btn ghost', onclick: () => finishQuestion(false, '(모르겠음)') }, '모르겠음');

    function submit() {
      if (!normalize(input.value)) return;
      finishQuestion(isCorrect(input.value, item), input.value.trim());
    }

    function finishQuestion(correct, answer) {
      updateSr(item.id, correct);
      results.push({ item, correct, answer });
      input.disabled = true;
      submitBtn.disabled = true;
      giveUpBtn.disabled = true;
      hintBtn.disabled = true;
      feedback.innerHTML = '';
      feedback.append(el('div', { class: `feedback ${correct ? 'good' : 'bad'}` },
        correct ? `정답! 「${item.word}」` : `아쉽네요. 정답은 「${item.word}」 입니다.`));
      const nextBtn = el('button', { class: 'btn block', style: 'margin-top:12px', onclick: next },
        idx + 1 < quiz.length ? '다음 문제' : '결과 보기');
      feedback.append(nextBtn);
      nextBtn.focus();
    }

    function next() {
      idx += 1;
      if (idx < quiz.length) showQuestion();
      else showResult();
    }

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !input.disabled) { e.preventDefault(); submit(); }
    });

    root.append(
      progressBar(idx + 1, quiz.length),
      el('div', { class: 'card' },
        el('p', { class: 'question-meta' }, '다음 설명에 해당하는 단어는?'),
        el('p', { class: 'question-text' }, item.meaning),
        input,
        hintEl,
        el('div', { class: 'btn-row' }, hintBtn, giveUpBtn, submitBtn),
        feedback,
      ),
    );
    input.focus();
  }

  function showResult() {
    const correctCount = results.filter((r) => r.correct).length;
    const score = Math.round((correctCount / results.length) * 100);
    addRecord('vocab', score, { mode: 'recall', total: results.length, correct: correctCount });

    root.innerHTML = '';
    const card = el('div', { class: 'card' },
      el('div', { class: 'result-score' }, `${score}점`),
      el('p', { class: 'result-sub' }, `${results.length}문제 중 ${correctCount}개 정답`),
      el('p', { class: 'muted' }, '틀린 단어는 다음 훈련에서 우선적으로 다시 나옵니다.'),
      el('ul', { class: 'review-list' },
        ...results.map((r) => el('li', {},
          el('span', { class: `mark ${r.correct ? 'o' : 'x'}` }, r.correct ? 'O' : 'X'),
          el('span', {}, `${r.item.word} — ${r.item.meaning}`),
        )),
      ),
    );
    root.append(card);

    if (onComplete) {
      root.append(el('button', { class: 'btn block ok', onclick: () => onComplete(score) }, '다음 단계로'));
    } else {
      root.append(el('div', { class: 'btn-row' },
        el('button', { class: 'btn ghost', onclick: () => render(rootOf(root)) }, '다시 하기'),
        el('button', { class: 'btn', onclick: () => { location.hash = ''; } }, '홈으로'),
      ));
    }
  }

  showQuestion();
}

/* ---------- 카테고리 연상 게임 ---------- */

export async function startCategoryGame(root, { seconds = 60, categoryId = null, onComplete = null } = {}) {
  const { categories } = await loadData('categories');
  const cat = categories.find((c) => c.id === categoryId)
    || categories[Math.floor(Math.random() * categories.length)];
  const knownSet = new Set(cat.known.map(normalize));

  const entries = []; // {raw, norm, known}
  const entered = new Set();

  root.innerHTML = '';
  const timerEl = el('div', { class: 'timer' });
  const countEl = el('p', { class: 'result-sub' }, '0개');
  const tagList = el('div', { class: 'tag-list' });
  const input = el('input', {
    class: 'answer-input', type: 'text', autocomplete: 'off',
    placeholder: '단어 입력 후 엔터', enterkeyhint: 'send',
  });

  function addEntry() {
    const raw = input.value.trim();
    const norm = normalize(raw);
    input.value = '';
    input.focus();
    if (!norm) return;
    if (entered.has(norm)) {
      tagList.prepend(el('span', { class: 'tag dup' }, raw));
      return;
    }
    entered.add(norm);
    entries.push({ raw, norm, known: knownSet.has(norm) });
    tagList.prepend(el('span', { class: 'tag' }, raw));
    countEl.textContent = `${entries.length}개`;
  }

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addEntry(); }
  });

  const stopTimer = countdown(seconds, (remain) => {
    timerEl.textContent = `${remain}초`;
    timerEl.classList.toggle('urgent', remain <= 10);
  }, finish);

  const stopBtn = el('button', { class: 'btn danger block', style: 'margin-top:12px', onclick: () => { stopTimer(); finish(); } }, '그만하기');

  function finish() {
    input.disabled = true;
    const total = entries.length;
    const knownCount = entries.filter((e) => e.known).length;
    const score = Math.min(100, total * 10);
    addRecord('vocab', score, { mode: 'category', category: cat.name, total, known: knownCount });

    root.innerHTML = '';
    const card = el('div', { class: 'card' },
      el('div', { class: 'result-score' }, `${score}점`),
      el('p', { class: 'result-sub' }, `「${cat.name}」 ${total}개 (10개 이상이면 100점)`),
      el('p', { class: 'muted' }, `사전에 있는 답 ${knownCount}개 · 사전 밖의 답 ${total - knownCount}개 (사전 밖의 답도 개수에 포함됩니다. 맞는 답인지 스스로 확인해 보세요.)`),
      el('div', { class: 'tag-list' },
        ...entries.map((e2) => el('span', { class: `tag ${e2.known ? '' : 'extra'}` }, e2.raw)),
      ),
    );
    root.append(card);

    if (onComplete) {
      root.append(el('button', { class: 'btn block ok', onclick: () => onComplete(score) }, '다음 단계로'));
    } else {
      root.append(el('div', { class: 'btn-row' },
        el('button', { class: 'btn ghost', onclick: () => render(rootOf(root)) }, '다시 하기'),
        el('button', { class: 'btn', onclick: () => { location.hash = ''; } }, '홈으로'),
      ));
    }
  }

  root.append(
    el('div', { class: 'card' },
      el('p', { class: 'question-meta' }, '카테고리 연상 게임'),
      el('p', { class: 'question-text' }, `「${cat.name}」에 해당하는 단어를 최대한 많이!`),
      timerEl,
      countEl,
      input,
      stopBtn,
      tagList,
    ),
  );
  input.focus();
}

/* ---------- 모드 선택 화면 ---------- */

function rootOf(node) {
  return document.getElementById('app');
}

export function render(root) {
  root.innerHTML = '';
  let quizCount = 10;
  let gameSeconds = 60;

  const countChips = el('div', { class: 'chip-group' });
  [5, 10, 15].forEach((n) => {
    const chip = el('button', { class: `chip ${n === quizCount ? 'active' : ''}`, onclick: () => {
      quizCount = n;
      countChips.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
    } }, `${n}문제`);
    countChips.append(chip);
  });

  const secChips = el('div', { class: 'chip-group' });
  [30, 60, 90].forEach((s) => {
    const chip = el('button', { class: `chip ${s === gameSeconds ? 'active' : ''}`, onclick: () => {
      gameSeconds = s;
      secChips.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
    } }, `${s}초`);
    secChips.append(chip);
  });

  root.append(
    el('div', { class: 'card' },
      el('h2', { class: 'section-title' }, '💬 역방향 회상 퀴즈'),
      el('p', { class: 'muted' }, '뜻과 설명을 보고 해당 단어를 떠올려 입력합니다. 틀린 단어는 간격 반복 방식으로 우선 재출제됩니다.'),
      countChips,
      el('button', { class: 'btn block', style: 'margin-top:10px', onclick: () => startRecallQuiz(root, { count: quizCount }) }, '시작하기'),
    ),
    el('div', { class: 'card' },
      el('h2', { class: 'section-title' }, '⚡ 카테고리 연상 게임'),
      el('p', { class: 'muted' }, '무작위 카테고리가 제시되면 제한 시간 안에 해당하는 단어를 최대한 많이 입력하세요.'),
      secChips,
      el('button', { class: 'btn block', style: 'margin-top:10px', onclick: () => startCategoryGame(root, { seconds: gameSeconds }) }, '시작하기'),
    ),
  );
}
