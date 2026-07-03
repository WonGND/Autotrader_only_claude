/* 말하기 훈련
 * 주제를 제시하고 1~2분 말하기 → Web Speech API(SpeechRecognition)로 실시간 전사.
 * 전사 결과로 단어 수·분당 발화 속도·간투사·반복 단어를 분석한다.
 * 음성인식 미지원/마이크 거부 시 텍스트 입력 대체 모드로 자동 전환된다.
 */
import { el, progressBar, countdown, fmtTime } from './ui.js';
import { loadData } from './data.js';
import { addRecord } from './storage.js';

const SR = window.SpeechRecognition || window.webkitSpeechRecognition || null;

// 간투사(채움말) 목록: 단독 토큰으로 나타날 때만 센다
const FILLERS = ['음', '어', '그', '아', '저', '뭐', '막', '그냥', '이제', '약간', '좀', '그러니까', '어떻게보면', '있잖아'];
// 반복 단어 분석에서 제외할 흔한 기능어
const STOPWORDS = new Set([...FILLERS, '그리고', '그래서', '하지만', '그런데', '근데', '또', '더', '수', '것', '거', '게', '때', '및', '등']);

function stripPunct(tok) {
  return tok.replace(/[.,!?…~'"()‘’“”]/g, '');
}

/** 전사/입력 텍스트 발화 분석 */
export function analyzeSpeech(text, elapsedSec) {
  const tokens = text.trim().split(/\s+/).map(stripPunct).filter(Boolean);
  const totalWords = tokens.length;
  const minutes = Math.max(elapsedSec, 1) / 60;
  const wpm = Math.round(totalWords / minutes);

  let fillerCount = 0;
  const freq = new Map();
  for (const tok of tokens) {
    if (FILLERS.includes(tok)) { fillerCount += 1; continue; }
    if (tok.length >= 2 && !STOPWORDS.has(tok)) {
      freq.set(tok, (freq.get(tok) || 0) + 1);
    }
  }
  const repeated = [...freq.entries()]
    .filter(([, n]) => n >= 3)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  // 점수: 발화량 70점(한국어 기준 분당 90단어를 충분한 양으로 봄) + 유창성 30점(간투사 비율 감점)
  const target = minutes * 90;
  const volumeScore = totalWords === 0 ? 0 : Math.min(1, totalWords / target) * 70;
  const fillerRate = totalWords === 0 ? 0 : fillerCount / totalWords;
  const fluencyScore = totalWords === 0 ? 0 : Math.max(0, 1 - fillerRate * 5) * 30;
  const score = Math.round(volumeScore + fluencyScore);

  return { totalWords, wpm, fillerCount, repeated, score, elapsedSec };
}

/* ---------- 훈련 세션 ---------- */

export async function startSpeechSession(root, { seconds = 60, forceText = false, onComplete = null } = {}) {
  const { topics } = await loadData('speech_topics');
  const topic = topics[Math.floor(Math.random() * topics.length)];
  const useVoice = !forceText && !!SR;
  let elapsed = 0;
  let stopTimer = null;
  let recognition = null;
  let finalText = '';
  let running = false;
  let finished = false;

  root.innerHTML = '';

  const timerEl = el('div', { class: 'timer' }, fmtTime(seconds));
  const statusEl = el('p', { class: 'muted', style: 'text-align:center' },
    useVoice ? '아래 버튼을 누르면 녹음(음성인식)이 시작됩니다. 마이크 권한을 허용해 주세요.'
             : '이 브라우저는 한국어 음성인식을 지원하지 않아 텍스트 입력 모드로 진행합니다.');
  const transcriptBox = el('div', { class: 'transcript-box hidden' });
  const textArea = el('textarea', {
    class: 'answer-input hidden',
    placeholder: '주제에 대해 말하듯이 자유롭게 입력해 보세요.',
    style: 'min-height:160px',
  });

  const guideEl = topic.guide && topic.guide.length
    ? el('p', { class: 'muted', style: 'margin-top:8px' }, `막힐 때 참고: ${topic.guide.join(' / ')}`)
    : null;

  const startBtn = el('button', { class: 'btn block', style: 'margin-top:12px', onclick: start },
    useVoice ? '🎤 말하기 시작' : '⌨️ 입력 시작');
  const stopBtn = el('button', { class: 'btn danger block hidden', style: 'margin-top:12px', onclick: finish }, '끝내기');
  const textModeBtn = useVoice
    ? el('button', { class: 'btn ghost block small', style: 'margin-top:8px', onclick: () => {
        cleanup();
        startSpeechSession(root, { seconds, forceText: true, onComplete });
      } }, '음성 대신 텍스트로 입력하기')
    : null;

  function renderTranscript(interim) {
    transcriptBox.innerHTML = '';
    transcriptBox.append(finalText);
    if (interim) transcriptBox.append(el('span', { class: 'interim' }, ' ' + interim));
    transcriptBox.scrollTop = transcriptBox.scrollHeight;
  }

  function switchToTextMode(msg) {
    cleanup();
    root.innerHTML = '';
    root.append(el('div', { class: 'card' },
      el('div', { class: 'feedback info' }, msg),
    ));
    startSpeechSession(el2(root), { seconds, forceText: true, onComplete });
    function el2(r) {
      const holder = el('div');
      r.append(holder);
      return holder;
    }
  }

  function start() {
    if (running) return;
    running = true;
    startBtn.classList.add('hidden');
    stopBtn.classList.remove('hidden');
    if (textModeBtn) textModeBtn.classList.add('hidden');

    if (useVoice) {
      transcriptBox.classList.remove('hidden');
      statusEl.innerHTML = '';
      statusEl.append(el('span', { class: 'rec-dot' }), '듣고 있습니다… 주제에 대해 자유롭게 이야기하세요.');

      recognition = new SR();
      recognition.lang = 'ko-KR';
      recognition.continuous = true;
      recognition.interimResults = true;

      recognition.onresult = (e) => {
        let interim = '';
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const t = e.results[i][0].transcript;
          if (e.results[i].isFinal) finalText += (finalText ? ' ' : '') + t.trim();
          else interim += t;
        }
        renderTranscript(interim);
      };
      recognition.onerror = (e) => {
        if (finished) return;
        if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
          switchToTextMode('마이크 권한이 거부되었거나 음성인식을 사용할 수 없어 텍스트 입력 모드로 전환했습니다.');
        } else if (e.error === 'language-not-supported') {
          switchToTextMode('이 브라우저는 한국어 음성인식을 지원하지 않아 텍스트 입력 모드로 전환했습니다.');
        }
        // 'no-speech' 등 일시적 오류는 onend 의 자동 재시작에 맡긴다
      };
      recognition.onend = () => {
        // Chrome은 침묵이 이어지면 인식을 스스로 멈추므로, 세션이 끝나기 전이면 재시작
        if (running && !finished) {
          try { recognition.start(); } catch { /* 이미 시작된 경우 무시 */ }
        }
      };
      try {
        recognition.start();
      } catch (err) {
        switchToTextMode('음성인식을 시작할 수 없어 텍스트 입력 모드로 전환했습니다.');
        return;
      }
    } else {
      textArea.classList.remove('hidden');
      statusEl.textContent = '주제에 대해 말하듯이 자유롭게 입력하세요.';
      textArea.focus();
    }

    stopTimer = countdown(seconds, (remain) => {
      elapsed = seconds - remain;
      timerEl.textContent = fmtTime(remain);
      timerEl.classList.toggle('urgent', remain <= 10);
    }, finish);
  }

  function cleanup() {
    finished = true;
    running = false;
    if (stopTimer) stopTimer();
    if (recognition) {
      recognition.onend = null;
      recognition.onresult = null;
      recognition.onerror = null;
      try { recognition.stop(); } catch { /* noop */ }
    }
  }

  function finish() {
    if (finished) return;
    const usedSec = elapsed > 0 ? elapsed : seconds;
    cleanup();
    const text = useVoice ? finalText : textArea.value;
    const a = analyzeSpeech(text, usedSec);
    addRecord('speech', a.score, {
      mode: useVoice ? 'voice' : 'text', topic: topic.topic,
      words: a.totalWords, wpm: a.wpm, fillers: a.fillerCount,
    });
    showResult(a, text);
  }

  function showResult(a, text) {
    root.innerHTML = '';
    const repeatedText = a.repeated.length
      ? a.repeated.map(([w, n]) => `${w}(${n}회)`).join(', ')
      : '없음';

    root.append(
      el('div', { class: 'card' },
        el('div', { class: 'result-score' }, `${a.score}점`),
        el('p', { class: 'result-sub' }, `주제: ${topic.topic}`),
        el('div', { class: 'stat-grid' },
          el('div', { class: 'stat-box' }, el('div', { class: 'v' }, String(a.totalWords)), el('div', { class: 'k' }, '총 단어 수')),
          el('div', { class: 'stat-box' }, el('div', { class: 'v' }, String(a.wpm)), el('div', { class: 'k' }, '분당 단어 수(WPM)')),
          el('div', { class: 'stat-box' }, el('div', { class: 'v' }, String(a.fillerCount)), el('div', { class: 'k' }, '간투사(음·어·그 등)')),
          el('div', { class: 'stat-box' }, el('div', { class: 'v' }, fmtTime(a.elapsedSec)), el('div', { class: 'k' }, '발화 시간')),
        ),
        el('p', { class: 'muted' }, `자주 반복한 단어: ${repeatedText}`),
        a.totalWords === 0
          ? el('div', { class: 'feedback info', style: 'margin-top:8px' }, '인식된 내용이 없습니다. 마이크와 주변 소음을 확인하고 다시 시도해 보세요.')
          : null,
      ),
      text.trim()
        ? el('div', { class: 'card' },
            el('h2', { class: 'section-title' }, '전사 내용'),
            el('p', { style: 'font-size:0.95rem; white-space:pre-wrap' }, text.trim()))
        : null,
    );

    if (onComplete) {
      root.append(el('button', { class: 'btn block ok', onclick: () => onComplete(a.score) }, '다음 단계로'));
    } else {
      root.append(el('div', { class: 'btn-row' },
        el('button', { class: 'btn ghost', onclick: () => render(document.getElementById('app')) }, '다시 하기'),
        el('button', { class: 'btn', onclick: () => { location.hash = ''; } }, '홈으로'),
      ));
    }
  }

  root.append(
    el('div', { class: 'card' },
      el('p', { class: 'question-meta' }, '오늘의 말하기 주제'),
      el('p', { class: 'question-text' }, topic.topic),
      guideEl,
      timerEl,
      statusEl,
      transcriptBox,
      textArea,
      startBtn,
      stopBtn,
      textModeBtn,
    ),
  );
}

/* ---------- 설정 화면 ---------- */

export function render(root) {
  root.innerHTML = '';
  let seconds = 60;

  const secChips = el('div', { class: 'chip-group' });
  [[60, '1분'], [90, '1분 30초'], [120, '2분']].forEach(([s, label]) => {
    const chip = el('button', { class: `chip ${s === seconds ? 'active' : ''}`, onclick: () => {
      seconds = s;
      secChips.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
    } }, label);
    secChips.append(chip);
  });

  const supportNote = SR
    ? el('p', { class: 'muted' }, '이 브라우저에서 음성인식을 사용할 수 있습니다. 시작 시 마이크 권한을 요청합니다.')
    : el('div', { class: 'feedback info' }, '이 브라우저는 음성인식(Web Speech API)을 지원하지 않습니다. 텍스트 입력 모드로 훈련이 진행됩니다. 음성 훈련은 Chrome(데스크톱/안드로이드)을 권장합니다.');

  root.append(
    el('div', { class: 'card' },
      el('h2', { class: 'section-title' }, '🎤 말하기 훈련'),
      el('p', { class: 'muted' }, '주제를 보고 정해진 시간 동안 말합니다. 말한 내용이 실시간으로 전사되고, 끝나면 발화량·속도·간투사·반복 단어를 분석해 드립니다.'),
      el('p', { class: 'muted', style: 'margin-top:10px; font-weight:700' }, '말하기 시간'),
      secChips,
      supportNote,
      el('button', { class: 'btn block', style: 'margin-top:12px', onclick: () => startSpeechSession(root, { seconds }) }, '주제 받고 시작하기'),
    ),
  );
}
