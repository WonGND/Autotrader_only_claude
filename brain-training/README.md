# 두뇌 트레이닝 PWA

사고력 · 어휘 회상력 · 말하기 능력을 훈련하는 개인용 웹앱입니다.
빌드 도구 없이 순수 HTML/CSS/JS(ES Modules)로 만들어져 있어, 정적 파일을 서빙하기만 하면 동작합니다.
모든 데이터는 브라우저 로컬(localStorage)에 저장되며 서버가 필요 없습니다.

## 로컬에서 실행하기

이 폴더(`brain-training/`)를 아무 정적 서버로 열면 됩니다. (ES 모듈·서비스 워커 때문에 `file://`로는 열 수 없습니다)

```bash
cd brain-training
python3 -m http.server 8000
# 또는: npx serve .
```

브라우저에서 http://localhost:8000 접속. 음성인식까지 확인하려면 **Chrome**을 사용하세요.

## 폴더 구조

```
brain-training/
├── index.html            # 앱 셸 (단일 페이지)
├── manifest.webmanifest  # PWA 매니페스트
├── sw.js                 # 서비스 워커 (오프라인 캐시)
├── css/style.css
├── js/
│   ├── app.js            # 라우터 + 홈 화면
│   ├── storage.js        # localStorage 기록/간격반복/streak
│   ├── data.js           # JSON 로더 + 날짜 시드 난수
│   ├── ui.js             # 공용 UI 헬퍼
│   ├── vocab.js          # 어휘 회상 훈련
│   ├── thinking.js       # 사고력 훈련
│   ├── speech.js         # 말하기 훈련 (음성인식)
│   ├── stats.js          # 기록/통계
│   └── daily.js          # 오늘의 훈련 루틴
├── data/                 # ★ 문제 데이터 — JSON 편집만으로 추가 가능
│   ├── vocab.json        # 어휘 회상 단어 (60개)
│   ├── categories.json   # 연상 게임 카테고리 (12개)
│   ├── thinking.json     # 사고력 문제 (36개)
│   └── speech_topics.json# 말하기 주제 (24개)
└── icons/                # 앱 아이콘
```

## 문제·단어 추가하는 방법 (코드 수정 불필요)

`data/` 폴더의 JSON 파일에 항목을 추가하면 됩니다. 각 파일 맨 위의 `_설명` 필드에 형식 설명이 있습니다.

- **단어 추가** — `data/vocab.json`의 `words` 배열에 추가:
  ```json
  { "id": "w061", "word": "정답단어", "meaning": "문제로 보여줄 뜻/설명", "alt": ["정답으로 인정할 다른 표현"] }
  ```
- **카테고리 추가** — `data/categories.json`의 `categories` 배열에 `{ "id": "...", "name": "...", "known": [단어들] }`
- **사고력 문제 추가** — `data/thinking.json`의 `problems` 배열에:
  - 객관식: `type`을 `logic` 또는 `sequence`로, `choices` 배열과 `answer`(0부터 세는 정답 위치), `explanation`
  - 서술형: `type: "divergent"`, `answer` 없이 `examples`(예시 아이디어 배열)
  - `difficulty`는 `easy` / `normal` / `hard`
- **말하기 주제 추가** — `data/speech_topics.json`의 `topics` 배열에 `{ "id": "...", "topic": "...", "guide": [보조 질문] }`

> **주의:** 배포된 앱은 서비스 워커가 파일을 캐시합니다. 데이터/코드를 수정해 재배포할 때는
> `sw.js` 맨 위의 `CACHE_VERSION`을 한 단계 올려 주세요 (예: `bt-v5` → `bt-v6`).
> 새 데이터 파일을 만들었다면 `sw.js`의 `ASSETS` 목록에도 경로를 추가해야 오프라인에서 동작합니다.

## 배포하기 (GitHub Pages)

앱이 저장소의 하위 폴더에 있으므로 `gh-pages` 브랜치로 폴더만 밀어 올리는 방식을 사용합니다.
모든 경로가 상대 경로라 `https://<계정>.github.io/<저장소>/` 같은 하위 경로에서도 그대로 동작합니다.

```bash
# 1) 저장소 루트에서, 배포할 브랜치(예: main)에 앱이 커밋되어 있는 상태에서
git subtree push --prefix brain-training origin gh-pages

# 2) GitHub 저장소 → Settings → Pages 에서
#    Source: "Deploy from a branch", Branch: gh-pages / (root) 선택 후 저장

# 3) 1~2분 뒤 접속
#    https://<계정>.github.io/<저장소이름>/
```

이후 업데이트할 때마다 1)의 명령만 다시 실행하면 됩니다.
(`gh-pages` 브랜치가 앞서 나가 거부되면: `git push origin \`git subtree split --prefix brain-training HEAD\`:gh-pages --force`)

### 대안: Netlify Drop (명령어 없이 가장 간단)

1. https://app.netlify.com/drop 접속 (무료 계정)
2. `brain-training` 폴더를 브라우저 창에 드래그 앤 드롭
3. 발급된 `https://<이름>.netlify.app` 주소로 접속

> PWA(서비스 워커·설치)는 **HTTPS에서만** 동작합니다. GitHub Pages와 Netlify는 기본 HTTPS라 추가 설정이 필요 없습니다.

## 스마트폰에 설치하기

**Android (Chrome) — 음성인식 지원, 권장**
1. Chrome으로 배포 주소 접속
2. 우측 상단 ⋮ 메뉴 → **"홈 화면에 추가"** (또는 자동으로 뜨는 "앱 설치" 배너)
3. 홈 화면의 "두뇌훈련" 아이콘으로 실행 — 주소창 없는 전체 화면 앱으로 열립니다

**iPhone (Safari)**
1. Safari로 배포 주소 접속
2. 하단 공유 버튼(⬆︎) → **"홈 화면에 추가"**
3. iOS의 음성인식 지원은 기기/버전에 따라 다르며, 미지원 시 앱이 자동으로 텍스트 입력 모드로 전환됩니다

**설치 확인 체크리스트**
- [ ] 홈 화면 아이콘이 보라색 뇌 모양으로 표시된다
- [ ] 실행 시 브라우저 주소창 없이 전체 화면으로 열린다
- [ ] 비행기 모드에서도 어휘/사고력 훈련이 동작한다 (말하기 음성인식은 온라인 필요)
- [ ] 말하기 훈련 시작 시에만 마이크 권한을 요청한다

## 브라우저 지원

| 기능 | Chrome(데스크톱/안드로이드) | Safari(iOS) | Firefox |
|---|---|---|---|
| 훈련·기록·PWA 설치 | ✅ | ✅ | ✅(설치는 제한적) |
| 한국어 음성인식 | ✅ | 부분 지원 | ❌ → 텍스트 모드 자동 전환 |

음성인식(Web Speech API)은 브라우저가 음성을 서버로 보내 처리하므로 **온라인 상태**여야 합니다.
미지원/권한 거부/오프라인 상황에서는 기능이 깨지지 않고 텍스트 입력 대체 모드로 안내합니다.
