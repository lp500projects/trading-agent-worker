"""
Composite scoring engine.
Scores every closed trade against the goal.yaml success/failure boundaries.
schema_version: 1
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TradeRecord:
    asset: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    hold_hours: float
    entry_time: str
    exit_time: str
    closed: bool = True


@dataclass
class ScoreResult:
    """Composite score of realised return vs target, drawdown vs max, Sharpe vs min."""
    schema_version: int = 1
    realised_return_30d_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    target_return_30d_pct: float = 5.0
    max_drawdown_limit_pct: float = 8.0
    min_sharpe: float = 1.2
    composite_score: float = 0.0
    grade: str = "neutral"
    breakdown: dict = field(default_factory=dict)


def compute_returns(trades: list[TradeRecord]) -> list[float]:
    """Extract pnl_pct from closed trades."""
    return [t.pnl_pct for t in trades if t.closed]


def compute_drawdown(cumulative_pnl: list[float]) -> float:
    """Compute max drawdown from cumulative PnL series."""
    if not cumulative_pnl:
        return 0.0
    peak = cumulative_pnl[0]
    max_dd = 0.0
    for val in cumulative_pnl:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def compute_sharpe(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """Compute annualised Sharpe ratio from per-trade returns.
    Simplification: treats each trade as a period, annualises.
    """
    if len(returns) < 2:
        return 0.0
    mean_ret = sum(returns) / len(returns)
    if mean_ret == 0:
        return 0.0
    variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0:
        return 0.0
    std = variance ** 0.5
    # Rough annualisation: assume ~365 trades/year
    annual_factor = (365 / len(returns)) ** 0.5 if len(returns) > 0 else 1.0
    return (mean_ret / std) * annual_factor


def score_trades(
    trades: list[TradeRecord],
    target_return_30d_pct: float = 5.0,
    max_drawdown_limit_pct: float = 8.0,
    min_sharpe: float = 1.2,
    initial_capital: float = 10000.0,
) -> ScoreResult:
    """Compute the composite score for a set of closed trades."""
    returns = compute_returns(trades)

    # Realised return (simplified: sum of PnL %s normalised)
    realised_return = sum(returns) if returns else 0.0

    # Cumulative PnL for drawdown
    cum_pnl = []
    running = initial_capital
    for r in returns:
        running *= (1 + r / 100)
        cum_pnl.append(running)

    drawdown = compute_drawdown(cum_pnl)
    sharpe = compute_sharpe(returns)

    # Composite score: how close to targets (0-100 scale)
    # Higher is better for return and Sharpe, lower is better for drawdown
    return_score = min(realised_return / target_return_30d_pct * 50, 50) if target_return_30d_pct > 0 else 0
    dd_score = max(50 - (drawdown / max_drawdown_limit_pct * 50), 0) if max_drawdown_limit_pct > 0 else 50
    sharpe_score = min(sharpe / min_sharpe * 50, 50) if min_sharpe > 0 else 0

    composite = round(return_score + dd_score + sharpe_score, 1)

    if composite >= 80:
        grade = "excellent"
    elif composite >= 60:
        grade = "good"
    elif composite >= 40:
        grade = "neutral"
    elif composite >= 20:
        grade = "poor"
    else:
        grade = "failing"

    return ScoreResult(
        realised_return_30d_pct=round(realised_return, 2),
        max_drawdown_pct=round(drawdown, 2),
        sharpe_ratio=round(sharpe, 2),
        target_return_30d_pct=target_return_30d_pct,
        max_drawdown_limit_pct=max_drawdown_limit_pct,
        min_sharpe=min_sharpe,
        composite_score=composite,
        grade=grade,
        breakdown={
            "return_vs_target": f"{realised_return:.1f}% vs {target_return_30d_pct}%",
            "drawdown_vs_limit": f"{drawdown:.1f}% vs {max_drawdown_limit_pct}%",
            "sharpe_vs_min": f"{sharpe:.2f} vs {min_sharpe}",
            "sub_scores": {
                "return": round(return_score, 1),
                "drawdown": round(dd_score, 1),
                "sharpe": round(sharpe_score, 1),
            },
        },
    )
