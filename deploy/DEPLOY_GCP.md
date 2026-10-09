# 코인선물 봇 — Google Cloud 무료 VM 배포 가이드 (24/7 무인 운용)

> ⚠️ **사용 중단 권장**: GCP 무료 VM은 미국 리전만 가능한데 Bybit은 미국 IP의 API를 차단합니다(403).
> 아시아 리전 기준인 [`DEPLOY_SEOUL.md`](DEPLOY_SEOUL.md)를 따르세요.

PC를 꺼도 클라우드에서 봇이 계속 돌도록 하는 절차. **Google Cloud `e2-micro` Always Free**(평생 무료, 자동 청구 없음) 기준.

> 한 줄 요약: 무료 리눅스 VM 1대를 만들고 → 봇을 올리고 → `systemd` 서비스로 등록하면,
> 부팅 시 자동 시작되고 죽어도 자동 재시작됩니다. (텔레그램 dev_bot의 켜고/끄기 역할을 systemd가 대신함)

---

## 0. 미리 알아둘 점
- **가입 시 카드 등록이 필요**합니다(무료 한도 내에선 청구 없음, GCP는 자동 청구 안 함).
- 무료 조건을 **반드시** 지켜야 함: 리전 `us-west1`/`us-east1`/`us-central1` 중 하나, 머신 `e2-micro` **1대만**, 부팅 디스크 **표준(Standard) 30GB 이하**(SSD/Balanced 금지).
- RAM이 1GB로 작아 **스왑 2GB**를 꼭 추가합니다(아래 4단계). 코인선물 봇 자체는 가벼워 충분합니다.

---

## 1. VM 만들기 (웹 콘솔)
1. https://console.cloud.google.com → 프로젝트 생성
2. **Compute Engine → VM 인스턴스 → 만들기**
   - 리전: **us-central1** (Always Free 대상)
   - 머신 구성: 시리즈 **E2**, 머신 유형 **e2-micro**
   - 부팅 디스크: **Ubuntu 24.04 LTS**, 디스크 종류 **표준 영구 디스크**, 크기 **30GB**
   - 방화벽: HTTP/HTTPS 체크 **해제**(웹서버 아님). SSH만 사용.
3. **네트워킹 → IP 주소**: 외부 IP를 **고정(static)으로 예약**.
   - Bybit가 IP 화이트리스트로 봇 IP를 확인하므로 IP가 고정이어야 함.
   - 고정 IP는 *실행 중인 인스턴스에 붙어 있는 동안* 무료.

## 2. 접속 (SSH)
- 콘솔의 VM 행에서 **SSH** 버튼으로 브라우저 접속(가장 간단). 또는 본인 SSH 키 등록 후 터미널 접속.

## 3. 기본 패키지 설치
```bash
sudo apt update && sudo apt -y upgrade
sudo apt install -y python3 python3-venv python3-pip git
```

## 4. 스왑 2GB 추가 (1GB RAM 보완 — 중요)
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h   # Swap 2.0Gi 보이면 성공
```

## 5. 봇 코드 올리기
업로드용 패키지는 **이미 만들어 뒀습니다**: `C:\Users\원용\Desktop\Wonyong company\coin_deploy.tar.gz`
(.venv·로그·캐시·`.env`·키 모두 제외됨. `.env`는 6단계에서 VM에 직접 작성.)

- 콘솔 SSH 창 우상단 **⚙ → 파일 업로드**로 `coin_deploy.tar.gz` 전송 (또는 `gcloud compute scp`).

**VM에서:**
```bash
cd ~ && tar -xzf coin_deploy.tar.gz   # ~/autotrader_only_claude 생성됨
cd autotrader_only_claude
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## 6. `.env` 작성 (키는 절대 압축/깃에 넣지 말 것)
```bash
nano ~/autotrader_only_claude/.env
```
```
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_TESTNET=false
TRADING_AUTO_CONFIRM=true      # 무인 운용(콘솔 입력 없음)이라 반드시 true
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
```
```bash
chmod 600 ~/autotrader_only_claude/.env   # 본인만 읽기
```

## 7. 🔒 Bybit 보안 (실계좌 필수)
Bybit 웹 → API 관리에서:
- **출금(Withdraw) 권한 OFF**, 거래(Trade) + 읽기만 허용
- **IP 제한**에 1단계에서 예약한 **VM 고정 IP** 등록

## 8. 먼저 손으로 한 번 실행해 검증
```bash
cd ~/autotrader_only_claude
.venv/bin/python -X utf8 run_live_trading.py
```
- 텔레그램으로 "자동매매 시작 [실계좌]" 알림이 오면 정상.
- `Ctrl+C`로 멈추고 다음 단계로.

## 9. systemd 서비스 등록 (자동 시작/재시작)
> 이 폴더의 `coin-futures.service` 안의 `User=` 와 경로를 본인 계정/경로로 수정한 뒤 진행.
> (콘솔 SSH 기본 사용자명은 보통 본인 구글 계정 아이디. `whoami`로 확인 후 그 값으로 `botuser`를 바꿀 것.)
```bash
sudo cp ~/autotrader_only_claude/deploy/coin-futures.service /etc/systemd/system/
sudo nano /etc/systemd/system/coin-futures.service   # User= / 경로 수정
sudo systemctl daemon-reload
sudo systemctl enable --now coin-futures
```

## 10. 운영/모니터링
```bash
systemctl status coin-futures        # 상태
journalctl -u coin-futures -f        # 실시간 로그
sudo systemctl restart coin-futures  # 재시작
sudo systemctl stop coin-futures     # 정지
```
- VM 재부팅 후에도 `enable` 덕분에 자동으로 다시 뜹니다.
- 우리가 추가한 **일일 손실 서킷브레이커·청산가 게이트·SL 검증·모니터 자동재시작**이 무인 운용 안전판으로 함께 작동합니다.

---

## 참고: 미국주식 주기적 봇(us_rotation 월1회 / us_daytrade 일1회)
이건 상시 떠 있을 필요가 없어 **GitHub Actions 크론**(카드·VM 불필요, 무료)이 더 적합합니다.
원하면 `.github/workflows/` 워크플로를 따로 만들어 드립니다. (이 VM은 코인봇 전용으로 두는 게 1GB RAM에 안전)
