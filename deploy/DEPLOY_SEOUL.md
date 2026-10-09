# 코인선물 봇 — 클라우드 24/7 무인 운용 (PC 꺼도 동작)

> 기존 `DEPLOY_GCP.md`는 **미국 리전(us-central1 등) 무료 VM** 기준인데, Bybit은 미국 IP의 API 접근을
> 막기 때문에(403) 그대로 따라 하면 봇이 주문을 못 냅니다. **아시아 리전 VM**을 쓰세요.
> `setup_server.sh`가 설치 첫 단계에서 Bybit 접속을 검사하고, 막힌 지역이면 바로 중단합니다.

## 어떤 서버를 쓸까

| 선택지 | 비용 | 리전 | 비고 |
|---|---|---|---|
| **Oracle Cloud Always Free** (추천) | 평생 무료 | 서울 / 춘천 | ARM(Ampere) 무료 한도가 넉넉함. 단 무료 인스턴스 재고가 없을 때가 있어 생성이 실패하면 시간을 두고 재시도 |
| AWS Lightsail | 월 $5 정도 | 서울 / 도쿄 | 가입·생성이 가장 쉬움. 고정 IP 무료(인스턴스에 붙어 있는 동안) |
| Vultr / Linode 등 | 월 $5~6 | 서울 / 도쿄 | 비슷함 |

봇은 가볍습니다(RAM 1GB + 스왑이면 충분). 모든 경우 **Ubuntu 22.04 또는 24.04**를 고르세요.

## 1. VM 만들기 (Oracle 기준 요약)
1. cloud.oracle.com 가입 → 홈 리전을 **South Korea Central (Seoul)** 또는 **North (Chuncheon)** 로 선택 (홈 리전은 나중에 못 바꿈)
2. Compute → Instances → Create
   - Image: **Canonical Ubuntu 24.04**
   - Shape: **VM.Standard.A1.Flex** (Always Free, 1 OCPU / 6GB면 충분) — 재고 없으면 VM.Standard.E2.1.Micro
   - SSH 키: "Generate a key pair" → 개인키 다운로드(잃어버리면 접속 불가)
3. 생성 후 **Public IP** 확인. Networking에서 **Reserved(고정) IP**로 바꿔 두면 재부팅에도 IP가 유지됩니다 (Bybit IP 화이트리스트용)

AWS Lightsail이면: 리전 서울 → Linux/Ubuntu → $5 플랜 → 생성 후 Networking 탭에서 Static IP 연결.

## 2. 접속 후 설치 (명령 4줄)
```bash
ssh -i <다운로드한키> ubuntu@<공인IP>
git clone https://github.com/WonGND/Autotrader_only_claude.git
cd Autotrader_only_claude
bash deploy/setup_server.sh
```
스크립트가 Bybit 접속 확인 → 파이썬/시간동기화/스왑 → 가상환경 → `.env` 템플릿 → systemd 등록까지 처리합니다.

## 3. 키 입력과 보안
```bash
nano ~/Autotrader_only_claude/.env     # BYBIT_API_KEY / SECRET / TELEGRAM 입력
```
Bybit → API 관리:
- **출금(Withdraw) 권한 OFF**, 거래 + 읽기만
- **IP 제한**: 설치 스크립트 마지막에 출력된 서버 공인 IP 등록
- 서버용 API 키는 PC용과 **따로 발급** (PC 봇과 동시에 같은 키로 돌리면 이중 주문 위험)

## 4. 검증 → 시작
```bash
cd ~/Autotrader_only_claude
.venv/bin/python -X utf8 run_live_trading.py    # 텔레그램 시작 알림 오면 Ctrl+C
sudo systemctl start coin-futures
journalctl -u coin-futures -f                   # 실시간 로그
```
**PC 쪽 봇은 반드시 끄세요.** 같은 계좌에 두 봇이 돌면 주문이 두 배가 됩니다 (잠금 파일은 같은 컴퓨터 안에서만 막아 줌).

## 5. 운영
| 하고 싶은 것 | 명령 |
|---|---|
| 상태 | `systemctl status coin-futures` |
| 정지 / 재시작 | `sudo systemctl stop coin-futures` / `sudo systemctl restart coin-futures` |
| VS Code에서 수정한 코드 반영 | PC에서 `git push` → 서버에서 `git pull && sudo systemctl restart coin-futures` |
| 서버 재부팅 | 자동으로 다시 시작됨 (`enable` 되어 있음) |

VS Code의 **Remote - SSH** 확장을 쓰면 PC의 VS Code에서 서버 파일을 직접 열어 수정할 수도 있습니다.

## 참고: 시계 오차 오류
PC 로그에 `PC 시계 오차 … 보정` 경고가 2만 건 넘게 있었습니다. 서버에는 `chrony`(시간 자동 동기화)를
설치하므로 이 문제와 그에 따른 `retries exceeded maximum` 오류가 크게 줄어듭니다.
