/* 공용 UI 헬퍼 */

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

let toastTimer = null;
export function toast(msg, ms = 2200) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), ms);
}

/** 진행 바 (현재/전체) */
export function progressBar(current, total) {
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;
  const wrap = el('div', { class: 'quiz-progress' });
  const bar = el('div', { class: 'bar' }, el('div', { style: `width:${pct}%` }));
  wrap.append(bar, el('span', { class: 'label' }, `${current} / ${total}`));
  return wrap;
}

/** 카운트다운 타이머. onTick(남은초), onDone() 콜백. stop() 반환. */
export function countdown(seconds, onTick, onDone) {
  let remain = seconds;
  onTick(remain);
  const id = setInterval(() => {
    remain -= 1;
    onTick(remain);
    if (remain <= 0) {
      clearInterval(id);
      onDone();
    }
  }, 1000);
  return () => clearInterval(id);
}

export function fmtTime(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}
