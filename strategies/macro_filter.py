"""
매크로 시장 필터 (Macro Market Filter)

Fear & Greed Index, VIX, BTC 도미넌스를 종합해 거래 허용 여부와
포지션 크기를 결정합니다.

종합 매크로 점수: -100 (극단 하락장) ~ +100 (극단 상승장)
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MacroSignal:
    """매크로 필터 결과"""
    date: str
    fg_value: float            # Fear & Greed (0~100)
    vix: float                 # VIX 수치
    btc_rel: float             # BTC 상대 강도
    macro_score: float         # 종합 점수 (-100 ~ +100)
    allow_long: bool           # 롱 진입 허용 여부
    allow_short: bool          # 숏 진입 허용 여부
    position_modifier: float   # 포지션 크기 배율 (0.3 ~ 1.0)
    reason: str                # 판단 근거


class MacroFilter:
    """
    종합 매크로 시장 필터

    규칙:
        1. Fear & Greed — 극단 구간에서 역추세 포지션 차단
            - Extreme Fear (<20): 롱 신호 강화 가능하지만 숏 차단
            - Extreme Greed (>80): 숏 신호 강화 가능하지만 롱 차단
            - 정상 범위 (20~80): 모든 방향 허용

        2. VIX — 고공포 시 포지션 축소
            - VIX > 40: 전체 포지션 30% 수준으로 대폭 축소
            - VIX > 30: 포지션 50% 수준으로 축소
            - VIX > 25: 포지션 70% 수준으로 축소
            - VIX < 20: 정상 (100%)

        3. BTC 상대 강도 — 알트 거래 조정
            - BTC 상대 강도 > +15: 알트 롱 차단 (BTC로 자금 쏠림)
            - BTC 상대 강도 < -15: 알트 숏 차단 (알트 시즌, 숏 리스크 높음)

        4. 종합 매크로 점수 (-100~+100)
            - 점수 기반 포지션 크기 자동 조정
    """

    # Fear & Greed 임계값
    FG_EXTREME_FEAR  = 20    # 이하: 극단 공포
    FG_EXTREME_GREED = 80    # 이상: 극단 탐욕
    FG_FEAR          = 35    # 이하: 공포
    FG_GREED         = 65    # 이상: 탐욕

    # VIX 임계값
    VIX_NORMAL   = 20
    VIX_ELEVATED = 25
    VIX_HIGH     = 30
    VIX_EXTREME  = 40

    # BTC 도미넌스 임계값
    BTC_DOM_HIGH = 15   # BTC 강세 (알트 약세)
    BTC_DOM_LOW  = -15  # 알트 강세 (BTC 약세)

    def __init__(
        self,
        use_fg: bool = True,
        use_vix: bool = True,
        use_btc_dom: bool = True,
        is_btc: bool = False,         # BTC 거래 시 도미넌스 필터 미적용
    ):
        self.use_fg = use_fg
        self.use_vix = use_vix
        self.use_btc_dom = use_btc_dom
        self.is_btc = is_btc

    def evaluate(
        self,
        fg_value: float,
        vix: float,
        btc_rel: float,
        date: str = "",
    ) -> MacroSignal:
        """
        매크로 지표를 종합해 거래 허용 여부 및 포지션 배율 반환

        크립토 역발상 원칙:
            - F&G 극단 공포(< 20) + 롱  → 최적 매수 시점, 포지션 적극적
            - F&G 극단 탐욕(> 80) + 롱  → 과열 위험, 포지션 축소
            - VIX > 35 → 전통 시장 패닉, 전체 포지션 보수적
            - BTC 강세 → 알트 롱 차단 (자금이 BTC로 쏠림)

        Args:
            fg_value:  Fear & Greed Index (0~100)
            vix:       VIX 수치
            btc_rel:   BTC 상대 강도 (-100~+100)
            date:      날짜 문자열 (로깅용)
        """
        allow_long  = True
        allow_short = True
        modifier    = 1.0
        reasons     = []
        score       = 0.0

        # ── 1. Fear & Greed — 역발상 로직 ───────────────────────
        # 크립토 F&G는 역지표: 극단 공포 = 매수 기회, 극단 탐욕 = 위험
        if self.use_fg:
            if fg_value <= self.FG_EXTREME_FEAR:
                # 극단 공포(<20): 숏만 차단, 롱은 오히려 적극적
                allow_short = False
                modifier *= 1.15      # 롱 포지션 15% 확대 (역발상 매수)
                score -= 30
                reasons.append(f"역발상매수기회(F&G={fg_value})")
            elif fg_value <= self.FG_FEAR:
                # 공포(20~35): 숏 진입 신중, 롱은 정상
                modifier *= 1.05      # 롱 소폭 확대
                score -= 15
                reasons.append(f"공포→롱유리(F&G={fg_value})")
            elif fg_value >= self.FG_EXTREME_GREED:
                # 극단 탐욕(>80): 롱 차단, 숏은 매력적
                allow_long = False
                modifier *= 1.10      # 숏 포지션 소폭 확대
                score += 30
                reasons.append(f"과열→롱위험(F&G={fg_value})")
            elif fg_value >= self.FG_GREED:
                # 탐욕(65~80): 롱 포지션 축소
                modifier *= 0.80
                score += 15
                reasons.append(f"탐욕→주의(F&G={fg_value})")
            # 중립(35~65): 아무 조정 없음

        # ── 2. VIX — 전통 시장 리스크 ──────────────────────────
        # VIX 급등은 크립토에도 전통적으로 부정적 (유동성 회수, 리스크오프)
        if self.use_vix:
            if vix >= self.VIX_EXTREME:
                # VIX 40+: 글로벌 패닉 (코로나, 전쟁 급등 등)
                # 롱/숏 모두 포지션 대폭 축소 (방향 불확실, 변동성 극대)
                modifier *= 0.35
                score -= 50
                reasons.append(f"글로벌패닉VIX({vix:.0f})")
            elif vix >= self.VIX_HIGH:
                # VIX 30~40: 고위험 구간
                modifier *= 0.60
                score -= 30
                reasons.append(f"VIX고위험({vix:.0f})")
            elif vix >= self.VIX_ELEVATED:
                # VIX 25~30: 주의 구간
                modifier *= 0.80
                score -= 15
                reasons.append(f"VIX상승({vix:.0f})")
            # VIX < 25: 정상, 조정 없음

        # ── 3. BTC 상대 강도 — 알트 자금 흐름 ──────────────────
        # BTC 독주장(도미넌스 급등) 때는 알트 롱이 위험
        if self.use_btc_dom and not self.is_btc:
            if btc_rel >= self.BTC_DOM_HIGH:
                # BTC 강세 > +15: 알트로 자금 유입 없음, 알트 롱 차단
                allow_long = False
                score -= 10
                reasons.append(f"BTC독주→알트롱차단(+{btc_rel:.0f})")
            elif btc_rel <= self.BTC_DOM_LOW:
                # 알트 강세 < -15: 알트 숏 위험 (알트 시즌)
                allow_short = False
                score += 10
                reasons.append(f"알트시즌→숏차단({btc_rel:.0f})")

        # ── 최종 클리핑 ──────────────────────────────────────────
        macro_score = float(np.clip(score, -100, 100))
        modifier = float(np.clip(modifier, 0.25, 1.3))  # 최대 1.3배까지 허용

        reason = " | ".join(reasons) if reasons else "매크로 정상"

        return MacroSignal(
            date=date,
            fg_value=fg_value,
            vix=vix,
            btc_rel=btc_rel,
            macro_score=macro_score,
            allow_long=allow_long,
            allow_short=allow_short,
            position_modifier=modifier,
            reason=reason,
        )

    def evaluate_from_row(self, row: pd.Series, date: str = "", symbol: str = "") -> MacroSignal:
        """DataFrame 행에서 직접 평가"""
        fg_value = float(row.get("fg_value", 50))
        vix      = float(row.get("vix_close", 20))
        btc_rel  = float(row.get("btc_rel_strength", 0))
        # BTC 자신은 도미넌스 필터 패스
        self.is_btc = "BTC" in symbol.upper()
        return self.evaluate(fg_value, vix, btc_rel, date)


def score_to_label(score: float) -> str:
    """매크로 점수를 사람이 읽기 쉬운 레이블로 변환"""
    if score <= -60:
        return "극단 하락장"
    elif score <= -30:
        return "하락장"
    elif score <= -10:
        return "약세"
    elif score <= 10:
        return "중립"
    elif score <= 30:
        return "강세"
    elif score <= 60:
        return "상승장"
    else:
        return "극단 상승장"
