# 봇 로그로 Claude에게 피드백 받기

## 봇이 매일 남기는 것 (`bot_logs/` 폴더)

| 파일 | 내용 | 무엇을 보나 |
|---|---|---|
| `equity.csv` | 실행(4시간)마다 한 줄: 실계좌 자산, **실계좌 / 모델 D / 기준선 B2** 자산 배수, BTC 종가, 주문·실패·최소주문 미달 수 | 세 곡선 비교 = 실행 품질과 규칙 품질을 분리해서 판단 |
| `orders.csv` | 보낸 주문 전부 (종목, 진입/감액/청산, 금액, 이유, 성공·실패, 오류 메시지) | 주문 실패 원인, 과매매 여부 |
| `closed_pnl.csv` | **거래소가 기록한 실제 실현손익** (Bybit 체결 내역) | 진짜 손익, 수수료 영향 |
| `runs/봉시각.json` | 그 실행의 모든 판단 근거(노출 배수와 근거 포함): 목표 비중, 보유 포지션, 주문 계획과 건너뛴 이유, 비상 손절 가격, 오류 | 특정 날 "왜 그렇게 했는지" 추적 |

세 곡선의 의미:
- **실계좌**: 실제 결과
- **모델 D**: 같은 규칙을 수수료·슬리피지 포함 '이상적으로' 체결했을 때 → 실계좌와 차이가 크면 **실행 문제**(최소 주문, 주문 실패, 체결 지연)
- **기준선 B2**: 터틀 55/20 + 롱 우위를 가상으로 같이 돌린 결과 → D가 B2보다 계속 나쁘면 **규칙 문제**

## Claude에게 보여주는 방법 (둘 중 하나)

### 방법 1. zip 파일 첨부 (설정 없이 바로)
VM에서:
```bash
bash ~/Autotrader_only_claude/tools/make_feedback_bundle.sh
```
PC의 PowerShell에서 내려받기:
```powershell
scp -i $HOME\.ssh\oracle.key ubuntu@<서버IP>:~/feedback_*.zip $HOME\Downloads\
```
Claude 대화창에 zip 첨부 + 아래 요청문 붙여넣기.

### 방법 2. 비공개 GitHub 저장소로 매일 자동 업로드
1. GitHub에서 **Private** 저장소 `autotrader-bot-logs` 생성 (⚠️ 반드시 비공개 — 자산·거래 내역 포함)
2. GitHub → Settings → Developer settings → **Fine-grained token** 생성: 그 저장소만, Contents **Read and write**
3. VM의 `.env`에 추가:
   `LOG_REPO_URL=https://<토큰>@github.com/WonGND/autotrader-bot-logs.git`
4. 다음 실행부터 매일 자동 업로드. Claude에게 "WonGND/autotrader-bot-logs 저장소 로그 분석해줘"라고 하면 됩니다.

## 요청문 예시

```
코인 앙상블 봇(규칙 D) 로그야. 기준선은 B2.
1) 실계좌 vs 모델D vs B2 곡선 비교
2) 실계좌가 모델D와 차이 나는 원인 (최소주문 미달 / 주문 실패 / 체결)
3) 주문 실패·오류 정리
4) 규칙이나 설정(config/ensemble.yaml)을 바꿔야 할 근거가 있는지
```

## 판단 기준 (미리 정해 두기)
- 최소 **8~12주**는 설정을 바꾸지 않고 지켜봅니다. 몇 주 결과로 규칙을 바꾸면 과최적화가 됩니다.
- 모델 D 대비 실계좌가 크게 뒤처지면 → 규칙이 아니라 **실행(계좌 규모·최소 주문)**을 먼저 고칩니다.
- 3개월 이상 D < B2 가 이어지고 백테스트 범위를 벗어난 낙폭(MDD −25% 이상, 4h·자동1~10배 검증 최대 −22%)이 나오면 → 규칙 재검토.
- 노출 배수(`exposure_multiplier`)를 올리고 싶을 때는 `research/coin_rules_sim/README.md` 2차 검토 표의 MDD를 기준으로 판단합니다.
