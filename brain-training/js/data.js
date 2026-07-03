/* data/*.json 로더. 문제·단어 추가는 코드 수정 없이 JSON 편집만으로 가능하다. */

const cache = {};

export async function loadData(name) {
  if (cache[name]) return cache[name];
  const res = await fetch(`data/${name}.json`);
  if (!res.ok) throw new Error(`${name}.json 로드 실패 (${res.status})`);
  cache[name] = await res.json();
  return cache[name];
}

/* ---------- 날짜 시드 난수 (매일 다른 문제 세트) ---------- */

export function dateSeed(extra = 0) {
  const d = new Date();
  return d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate() + extra;
}

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** 시드 난수로 배열 셔플(원본 보존) */
export function seededShuffle(arr, rng) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}
