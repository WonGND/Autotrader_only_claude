# 코인 봇을 클라우드에 올리기 — 처음 하는 사람용 가이드

PC를 꺼도 봇이 매일 알아서 돌도록, 인터넷 회사의 컴퓨터를 한 대 빌려 거기에 봇을 설치하는 과정입니다.
처음 한 번 30~60분 정도 걸리고, 이후에는 손댈 일이 거의 없습니다.

---

## 0. 먼저 개념 정리

| 용어 | 뜻 |
|---|---|
| **클라우드** | 오라클·아마존 같은 회사가 데이터센터에 둔 컴퓨터를 인터넷으로 빌려 쓰는 것 |
| **VM (가상 머신)** | 그 회사의 큰 서버를 잘게 나눈 "나만의 컴퓨터 한 대". 모니터 없이 24시간 켜져 있음 |
| **인스턴스** | 클라우드 화면에서 VM을 부르는 이름 (같은 뜻) |
| **리전** | 그 컴퓨터가 실제로 있는 도시. Bybit은 미국을 막으므로 **서울/춘천**을 고름 |
| **Ubuntu** | VM에 깔린 운영체제(리눅스). 윈도우 대신 이걸 씀 |
| **SSH** | 내 PC에서 VM에 접속해 명령어를 치는 방법. 윈도우 PowerShell에 기본 내장 |
| **공인 IP** | VM의 인터넷 주소 (예: 158.180.x.x). Bybit API 키에 이 주소만 허용하도록 등록 |

봇이 하는 일: **4시간마다**(한국시간 01·05·09·13·17·21시 5분, 4시간봉 마감 직후) 실행 → 10종목 신호 계산 →
필요한 만큼만 주문 → 비상 손절을 거래소에 걸어 둠 → 로그 저장 → 텔레그램 보고(주문이 있을 때 + 하루 1회 요약).
실행 사이에도 손절은 거래소가 지켜 줍니다.

---

## 1. 오라클 클라우드 가입 (무료, 약 10분)

1. **https://www.oracle.com/kr/cloud/free/** 접속 → **"무료로 시작하기"**
2. 국가: **대한민국**, 이름·이메일 입력 → 이메일 인증
3. 비밀번호, **클라우드 계정 이름**(영문, 나중에 로그인할 때 씀 — 메모)
4. **홈 리전 선택** — ⚠️ 나중에 못 바꿉니다. 무료 서버는 홈 리전에서만 만들 수 있어요.
   - 1순위: `South Korea Central (Seoul)` / `South Korea North (Chuncheon)`
   - **목록에 한국이 없으면**: `Australia East (Sydney)` 또는 `Australia Southeast (Melbourne)`,
     그다음 `India West (Mumbai)` / `India South (Hyderabad)`
   - **고르면 안 되는 곳**: 미국·캐나다 전 지역, `Singapore`, `Hong Kong` (Bybit 제한 국가 → API 차단),
     `Japan` (Tokyo/Osaka — Bybit이 2026년 일본 서비스 제한을 발표해 막힐 위험)
   - 봇은 4시간에 한 번 판단하므로 서버가 멀어도(호주·인도) 속도 차이는 문제 되지 않습니다.
   - 설치 스크립트가 첫 단계에서 Bybit 접속을 검사하므로, 막힌 지역이면 설치 전에 바로 알 수 있습니다.
5. 주소·전화번호 → **카드 등록** (본인 확인용 소액 승인 후 취소됨. "Always Free" 자원만 쓰면 청구 없음)
6. 가입 완료 메일이 오면 https://cloud.oracle.com 로그인 (계정 이름 → 이메일/비밀번호)

> 무료 계정은 유료로 "업그레이드"하지 않는 한 돈이 나가지 않습니다.

## 2. VM 만들기 (약 10분)

1. 왼쪽 위 ☰ 메뉴 → **컴퓨트(Compute) → 인스턴스(Instances)** → **인스턴스 생성(Create instance)**
2. **이름**: `coin-bot`
3. **이미지와 구성(Image and shape)** → **편집**
   - 이미지 변경 → **Canonical Ubuntu** → 버전 **24.04** 선택
   - 구성(Shape) 변경 → **Ampere** → **VM.Standard.A1.Flex** → OCPU **1**, 메모리 **6GB**
     (옆에 "Always Free 사용 가능" 표시 확인)
4. **네트워킹**: 기본값 그대로 (새 가상 클라우드 네트워크 생성, **공용 IPv4 주소 지정** 체크 확인)
5. **SSH 키 추가**: **"키 쌍 생성"** → **개인 키 저장** 클릭 → `ssh-key-날짜.key` 파일 다운로드
   - ⚠️ 이 파일이 VM의 열쇠입니다. 잃어버리면 접속 불가. 안전한 곳에 보관하세요.
6. **생성** 클릭 → 1~2분 뒤 상태가 **실행 중(Running)** 이 됨
7. 인스턴스 상세 화면의 **공용 IP 주소**를 메모 (예: `158.180.12.34`)

**"Out of capacity(용량 부족)" 오류가 나면**: 무료 ARM 서버 재고가 없는 것입니다.
몇 시간 뒤 다시 시도하거나, Shape를 **VM.Standard.E2.1.Micro**(AMD, 이것도 무료, RAM 1GB)로 바꾸세요.
설치 스크립트가 RAM이 작으면 스왑을 자동으로 추가합니다.

> 대안: 오라클이 계속 안 되면 **AWS Lightsail**(월 약 5달러, 서울 리전) — 가입·생성이 더 단순합니다.
> 생성 후 이 문서 3단계부터 똑같이 진행하면 됩니다 (사용자 이름이 `ubuntu`).

## 3. 내 PC에서 VM 접속하기 (SSH)

윈도우에서 **PowerShell**을 엽니다 (시작 메뉴 → "PowerShell" 검색).

```powershell
# 1) 키 파일을 .ssh 폴더로 옮기기 (다운로드 폴더 → 사용자 폴더\.ssh)
mkdir $HOME\.ssh -Force
move $HOME\Downloads\ssh-key-*.key $HOME\.ssh\oracle.key

# 2) 키 파일 권한 정리 (안 하면 "UNPROTECTED PRIVATE KEY FILE" 오류)
icacls $HOME\.ssh\oracle.key /inheritance:r /grant:r "$($env:USERNAME):(R)"

# 3) 접속 (IP는 2단계에서 메모한 공용 IP)
ssh -i $HOME\.ssh\oracle.key ubuntu@158.180.12.34
```

처음 접속 시 `Are you sure you want to continue connecting?` → **yes** 입력.
프롬프트가 `ubuntu@coin-bot:~$` 로 바뀌면 VM 안에 들어온 것입니다. 이제부터 치는 명령은 VM에서 실행됩니다.

## 4. 봇 설치 (명령 3줄, 약 5~10분)

```bash
git clone https://github.com/WonGND/Autotrader_only_claude.git
cd Autotrader_only_claude
bash deploy/setup_server.sh
```

스크립트가 하는 일: Bybit 접속 가능 여부 확인 → 프로그램 설치 → 시간 자동 동기화 → 파이썬 환경 →
`.env`(설정 파일) 생성 → 4시간마다 자동 실행 등록. 마지막에 **서버 공인 IP**를 출력합니다.

## 5. API 키 입력

**Bybit에서 서버 전용 API 키를 새로 만드세요** (PC용 키와 따로):
Bybit → 프로필 → **API** → **새 키 생성** → 시스템 생성 API 키
- 권한: **계약(Contract) 거래 – 주문·포지션** 체크, **출금(Withdraw)은 절대 체크하지 않음**
- **IP 접근 제한: "권한이 부여된 IP만 허용"** → 서버 공인 IP 입력

VM에서 설정 파일 편집:
```bash
nano .env
```
`BYBIT_API_KEY=` 와 `BYBIT_API_SECRET=` 뒤에 키를 붙여넣기 (PowerShell 창에서는 **마우스 오른쪽 클릭**이 붙여넣기).
텔레그램 알림을 쓰려면 `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`도 PC의 `.env`에서 복사.
저장: **Ctrl+O → Enter**, 나가기: **Ctrl+X**.

## 6. 시험 실행 → 자동 실행 시작

```bash
.venv/bin/python -X utf8 run_ensemble_trading.py --dry-run
```
"앙상블 봇 일일 리포트 (DRY RUN)" 이 나오고 텔레그램이 오면 정상입니다. 주문은 나가지 않습니다.

```bash
sudo systemctl start coin-ensemble.timer       # 4시간마다 자동 실행 시작
systemctl list-timers coin-ensemble.timer      # 다음 실행 시각 확인
```

**처음 1~2주는 `.env`의 `DRY_RUN=true` 그대로** 두고 로그만 쌓습니다(주문 없음).
로그를 Claude에게 보여 점검한 뒤 `DRY_RUN=false`로 바꾸면 그다음 실행부터 실제 주문이 나갑니다.

**Bybit 마진 모드를 '교차(Cross)'로 두세요.** (Bybit → 자산 → 통합거래계좌 → 마진 모드)
봇은 최대 10배까지 노출을 키우는데, 교차 마진이면 계좌 전체로 증거금을 공유해 종목 하나가 먼저 청산되는 일이 없습니다.
격리 마진이면 봇이 자동으로 레버리지를 보수적으로 낮추고 텔레그램으로 알려 줍니다.

⚠️ **실주문으로 바꾸기 전에 PC의 예전 봇(run_live_trading.py)을 꼭 끄세요.** 같은 계좌에서 두 봇이 돌면 주문이 꼬입니다.
예전 봇이 남긴 포지션은 새 봇이 첫 실행 때 목표에 맞게 정리합니다(관리 대상 10종목 밖의 포지션은 건드리지 않고 알림만).

## 7. 접속을 끊어도 되나요?

네. `exit` 를 치거나 PowerShell 창을 닫아도, PC를 꺼도 VM은 계속 켜져 있고 봇은 4시간마다 실행됩니다.

## 8. 자주 쓰는 명령 (VM에 접속한 상태에서)

| 하고 싶은 것 | 명령 |
|---|---|
| 지금 바로 1회 실행 | `sudo systemctl start coin-ensemble.service` |
| 최근 실행 기록 | `journalctl -u coin-ensemble.service -n 100` |
| 자산 곡선 보기 | `cat ~/Autotrader_only_claude/bot_logs/equity.csv` |
| 자동 실행 멈추기 / 다시 켜기 | `sudo systemctl stop coin-ensemble.timer` / `start` |
| 새 코드 받기 (VS Code에서 push한 뒤) | `cd ~/Autotrader_only_claude && git pull` |
| 피드백 자료 만들기 | `bash ~/Autotrader_only_claude/tools/make_feedback_bundle.sh` |

## 9. (선택) VS Code로 서버 파일 직접 열기

VS Code 확장 **Remote - SSH** 설치 → 왼쪽 아래 `><` 아이콘 → **Connect to Host** →
`ubuntu@<공인IP>` 입력 → (처음에만) `C:\Users\<이름>\.ssh\config` 에 아래 추가:
```
Host coin-bot
    HostName 158.180.12.34
    User ubuntu
    IdentityFile ~/.ssh/oracle.key
```
이후 `coin-bot` 을 고르면 PC의 VS Code에서 서버 폴더를 그대로 열고 터미널도 쓸 수 있습니다.

## 문제 해결

| 증상 | 원인·해결 |
|---|---|
| 설치 스크립트가 "Bybit API 응답 코드 403" 으로 중단 | 차단 지역 VM. 서울·춘천·시드니·멜버른·뭄바이 리전으로 다시 생성 |
| `Permission denied (publickey)` | 키 파일 경로 오타 또는 3단계 `icacls` 미실행 |
| 텔레그램에 "10003 / invalid api key" | `.env` 키 오타, 또는 Bybit IP 제한에 서버 IP 미등록 |
| 리포트에 "최소주문 미달"이 많음 | 계좌 규모가 작아 목표 금액이 최소 주문보다 작음 — 정상 동작(건너뛰고 기록). `FEEDBACK.md` 참고 |
