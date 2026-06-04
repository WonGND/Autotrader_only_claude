# AutoTrader - 자동 주식 거래 시스템

Python 기반의 완전 자동화 주식 거래 시스템입니다.
한국/미국 주식을 지원하며, 다양한 기술적 분석 전략과 백테스트 기능을 제공합니다.

## 빠른 시작

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 웹 대시보드 시작 (기본 모의 투자 모드)
python main.py web

# 3. 브라우저에서 접속
# http://localhost:8000
```

## CLI 명령어

```bash
# 웹 대시보드
python main.py web

# 백테스트
python main.py backtest --strategy ma_crossover --symbol 005930 --start 2023-01-01 --end 2023-12-31
python main.py backtest --strategy rsi --symbol AAPL --start 2022-01-01 --end 2023-12-31
python main.py backtest --strategy bollinger_bands --symbol 000660 --start 2023-01-01 --end 2023-12-31

# 자동 매매 (모의 투자)
python main.py trade --mode paper

# 상태 확인
python main.py status
```

## 프로젝트 구조

```
autotrader/
├── main.py                # CLI 진입점
├── config/
│   ├── settings.yaml      # 메인 설정
│   └── strategies.yaml    # 전략 파라미터
├── broker/
│   ├── base.py            # 브로커 추상 인터페이스 (사용자 구현)
│   └── paper_broker.py    # 모의 투자 브로커
├── strategies/
│   ├── base.py            # 전략 기본 클래스
│   ├── ma_crossover.py    # 이동평균 교차
│   ├── rsi_strategy.py    # RSI 전략
│   └── bollinger_bands.py # 볼린저 밴드
├── backtester/
│   ├── engine.py          # 백테스팅 엔진
│   └── report.py          # 결과 리포트
├── trader/
│   ├── engine.py          # 자동 매매 엔진
│   └── portfolio.py       # 포트폴리오 관리
├── data/
│   ├── fetcher.py         # 시장 데이터 수집
│   └── processor.py       # 기술적 지표 계산
├── database/
│   └── models.py          # SQLite 모델
└── web/
    ├── app.py             # FastAPI 웹 앱
    ├── static/style.css
    └── templates/         # Jinja2 HTML 템플릿
```

## 실제 증권사 API 연동 방법

`broker/base.py`를 참고하여 새 브로커 클래스를 만드세요:

```python
# broker/kis_broker.py (예시)
from broker.base import BaseBroker, Order, Position, AccountInfo

class KISBroker(BaseBroker):
    def __init__(self, app_key: str, app_secret: str, account: str):
        super().__init__()
        self.app_key = app_key
        # ... 인증 처리

    def get_balance(self) -> float:
        # 한국투자증권 API 호출
        ...

    # 나머지 추상 메서드 구현
```

이후 `config/settings.yaml`에서 `broker.type`을 변경하고,
`main.py`의 `_init_broker()` 함수에서 새 클래스를 import하여 사용하세요.

## 전략 추가 방법

```python
# strategies/my_strategy.py
from strategies.base import BaseStrategy
import pandas as pd

class MyStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return "나의 전략"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        df["signal"] = 0
        # 매매 신호 로직 구현
        # signal: 1 = 매수, -1 = 매도, 0 = 보유
        return df

    def get_required_history(self) -> int:
        return 30  # 필요한 최소 과거 일수
```

`strategies/__init__.py`의 `STRATEGY_MAP`에 등록하면 완료됩니다.

## 설정

### config/settings.yaml

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `app.mode` | 거래 모드 (paper/live) | `paper` |
| `app.web_port` | 웹 서버 포트 | `8000` |
| `trading.initial_capital` | 초기 자본금 (원) | `10,000,000` |
| `trading.max_positions` | 최대 보유 종목 수 | `5` |
| `trading.stop_loss_pct` | 손절 비율 | `0.05` (5%) |
| `trading.take_profit_pct` | 익절 비율 | `0.15` (15%) |
| `trading.position_size_pct` | 포지션 크기 비율 | `0.2` (20%) |

### config/strategies.yaml

`active_strategy` 값을 변경하여 활성 전략을 선택하세요.

## 모의 투자 수수료 설정

- 매수 수수료: 0.015%
- 매도 수수료: 0.3% (증권사 수수료 + 거래세)
- 슬리피지: 0.1%

## 라이선스

MIT License
