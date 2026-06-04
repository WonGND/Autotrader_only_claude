"""
백테스트 리포트 생성 모듈

백테스트 결과를 요약하고, 자산 곡선 차트를 생성합니다.
"""

import json
import pandas as pd
from typing import Dict, Any

from database.models import BacktestResult
from utils.logger import get_logger

logger = get_logger(__name__)


class BacktestReport:
    """백테스트 결과 리포트 생성 클래스"""

    @staticmethod
    def generate_summary(result: BacktestResult) -> Dict[str, Any]:
        """
        백테스트 결과 요약 딕셔너리를 생성합니다.

        Args:
            result: BacktestResult 객체

        Returns:
            요약 딕셔너리
        """
        return {
            "전략": result.strategy,
            "종목": result.symbol,
            "시작일": result.start_date,
            "종료일": result.end_date,
            "초기 자본금": f"₩{result.initial_capital:,.0f}",
            "최종 자본금": f"₩{result.final_capital:,.0f}",
            "총 수익": f"₩{result.final_capital - result.initial_capital:,.0f}",
            "수익률": f"{result.total_return:.2%}",
            "최대낙폭(MDD)": f"{result.max_drawdown:.2%}",
            "샤프 비율": f"{result.sharpe_ratio:.2f}" if result.sharpe_ratio else "N/A",
            "승률": f"{result.win_rate:.2%}" if result.win_rate is not None else "N/A",
            "총 거래 수": result.total_trades,
            "승리 거래": result.winning_trades,
            "패배 거래": result.losing_trades,
        }

    @staticmethod
    def generate_equity_curve(result: BacktestResult) -> str:
        """
        자산 곡선 Plotly 차트를 JSON 문자열로 생성합니다.

        Args:
            result: BacktestResult 객체 (._equity_series 속성 필요)

        Returns:
            Plotly figure JSON 문자열
        """
        try:
            import plotly.graph_objects as go
            import plotly.utils

            equity_series = getattr(result, "_equity_series", None)
            if equity_series is None or equity_series.empty:
                return "{}"

            fig = go.Figure()

            # 자산 곡선
            fig.add_trace(go.Scatter(
                x=equity_series.index.astype(str).tolist(),
                y=equity_series.values.tolist(),
                mode="lines",
                name="포트폴리오 가치",
                line=dict(color="#00d4ff", width=2),
                fill="tozeroy",
                fillcolor="rgba(0, 212, 255, 0.1)",
            ))

            # 초기 자본금 기준선
            fig.add_hline(
                y=result.initial_capital,
                line_dash="dash",
                line_color="rgba(255,255,255,0.4)",
                annotation_text="초기 자본금",
            )

            fig.update_layout(
                title=f"{result.symbol} 백테스트 자산 곡선",
                xaxis_title="날짜",
                yaxis_title="포트폴리오 가치 (₩)",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#e0e0e0"),
                xaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
                yaxis=dict(
                    gridcolor="rgba(255,255,255,0.1)",
                    tickformat=",.0f",
                ),
                showlegend=True,
                legend=dict(
                    bgcolor="rgba(0,0,0,0.5)",
                    bordercolor="rgba(255,255,255,0.2)",
                ),
                margin=dict(l=10, r=10, t=50, b=10),
            )

            return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)

        except Exception as e:
            logger.error(f"자산 곡선 생성 실패: {e}")
            return "{}"

    @staticmethod
    def format_report(result: BacktestResult) -> str:
        """
        사람이 읽기 쉬운 형식의 텍스트 리포트를 생성합니다.

        Args:
            result: BacktestResult 객체

        Returns:
            포매팅된 리포트 문자열
        """
        summary = BacktestReport.generate_summary(result)
        lines = [
            "=" * 60,
            "  백테스트 결과 리포트",
            "=" * 60,
        ]
        for key, value in summary.items():
            lines.append(f"  {key:<20}: {value}")
        lines.append("=" * 60)

        # 거래 내역
        trades = getattr(result, "_trades", [])
        if trades:
            lines.append(f"\n  최근 거래 내역 (최대 10건):")
            lines.append(f"  {'날짜':<12} {'구분':<6} {'수량':>8} {'가격':>12} {'손익':>12}")
            lines.append("  " + "-" * 55)
            sell_trades = [t for t in trades if t.side == "sell"]
            for t in sell_trades[-10:]:
                date_str = t.date.strftime("%Y-%m-%d") if hasattr(t.date, "strftime") else str(t.date)[:10]
                side_str = "매도"
                lines.append(
                    f"  {date_str:<12} {side_str:<6} {t.quantity:>8,} "
                    f"{t.price:>12,.0f} {t.pnl:>12,.0f}"
                )
        return "\n".join(lines)
