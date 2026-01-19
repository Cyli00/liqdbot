"""
MA5/MA10 状态机检测策略（A股专属）
"""

import logging
from datetime import datetime, time as dt_time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pandas as pd

from ..alerts import AlertMessages
from ..config import (
    AKSHARE_MA_PERIOD,
    AKSHARE_MA5_BREAK_PCT,
    AKSHARE_MA10_PERIOD,
    AKSHARE_MA10_BREAK_PCT,
    AKSHARE_OPEN_COOLDOWN_BARS,
)
from ..providers.base import MarketType, detect_market_type

if TYPE_CHECKING:
    from ..providers.akshare import AkshareProvider
    from ..state import SymbolState


async def check_ma_alerts(
    symbol: str,
    current_price: float,
    state: "SymbolState",
    display_name: str | None = None,
    *,
    akshare_provider: "AkshareProvider",
    ma5_period: int = AKSHARE_MA_PERIOD,
    ma5_break_pct: float = AKSHARE_MA5_BREAK_PCT,
    ma10_period: int = AKSHARE_MA10_PERIOD,
    ma10_break_pct: float = AKSHARE_MA10_BREAK_PCT,
    open_cooldown_bars: int = AKSHARE_OPEN_COOLDOWN_BARS,
) -> list[tuple[str, str]]:
    """
    A股 MA5/MA10 状态机检测（基于日线均线，15m 收盘驱动）

    修正：使用日线数据计算 MA5/MA10（5日/10日均线），而非 15m K 线
    盘中动态计算：使用前 N-1 日收盘价 + 当日实时价格计算动态 MA

    - 跌破条件: close < MA * (1 - break_pct/100)
    - 站上条件: close >= MA
    - 状态机保证：跌破后不重复提醒，站上后再跌破才提醒

    Args:
        symbol: 标的代码
        current_price: 当前价格
        state: 标的状态
        display_name: 显示名称
        akshare_provider: A股数据提供者
        ma5_period: MA5周期（默认5）
        ma5_break_pct: MA5跌破阈值百分比
        ma10_period: MA10周期（默认10）
        ma10_break_pct: MA10跌破阈值百分比
        open_cooldown_bars: 开盘冷却期K线数

    Returns:
        list of (alert_type, alert_message) tuples
    """
    name = display_name or symbol
    market_type = detect_market_type(symbol)
    if market_type != MarketType.A_SHARE:
        return []

    now_ts = datetime.now(ZoneInfo("Asia/Shanghai"))
    current_time = now_ts.time()
    current_15m_ts = pd.Timestamp(now_ts.replace(tzinfo=None)).floor("15min")

    is_new_15m_bar = (
        state.last_ma_check_15m_ts is None
        or current_15m_ts > state.last_ma_check_15m_ts
    )
    if not is_new_15m_bar:
        return []

    morning_open = dt_time(9, 30)
    afternoon_open = dt_time(13, 0)
    cooldown_minutes = open_cooldown_bars * 15
    morning_cooldown_end = (
        datetime.combine(datetime.today(), morning_open)
        + timedelta(minutes=cooldown_minutes)
    ).time()
    afternoon_cooldown_end = (
        datetime.combine(datetime.today(), afternoon_open)
        + timedelta(minutes=cooldown_minutes)
    ).time()

    if (
        morning_open <= current_time < morning_cooldown_end
        or afternoon_open <= current_time < afternoon_cooldown_end
    ):
        logging.debug(
            f"[{symbol}] A股开盘冷却期，跳过 MA 检测 (当前时间: {current_time})"
        )
        state.last_ma_check_15m_ts = current_15m_ts
        return []

    # 获取日线数据计算 MA5/MA10
    # 需要 max(MA5, MA10) 根日线，多取几根以防数据不足
    required_days = max(ma5_period, ma10_period) + 5
    daily_df = await akshare_provider.fetch_ohlcv(symbol, "1d", limit=required_days)

    if daily_df is None or len(daily_df) < ma5_period:
        logging.debug(
            f"[{symbol}] 日线数据不足，无法计算 MA (需要 {ma5_period} 根)"
        )
        state.last_ma_check_15m_ts = current_15m_ts
        return []

    # MA 计算：使用已收盘的日线数据
    # 日线数据的最后一根可能是"今日未收盘"的数据，需要排除
    # MA5 = 前5个交易日的收盘价平均值（不包含今日）
    # MA10 = 前10个交易日的收盘价平均值（不包含今日）
    daily_closes = daily_df["close"].tolist()

    # 排除最后一根（今日未收盘的数据），使用已收盘的历史数据
    # 如果最后一根是今日数据，则使用 daily_closes[:-1]
    historical_closes = daily_closes[:-1]

    # 计算 MA5 和 MA10（基于已收盘的历史数据）
    ma5_value = None
    ma10_value = None

    if len(historical_closes) >= ma5_period:
        ma5_value = sum(historical_closes[-ma5_period:]) / ma5_period

    if len(historical_closes) >= ma10_period:
        ma10_value = sum(historical_closes[-ma10_period:]) / ma10_period

    msgs: list[tuple[str, str]] = []

    if ma5_value is not None:
        break_threshold_ma5 = ma5_value * (1 - ma5_break_pct / 100)
        if not state.ma5_below:
            if current_price < break_threshold_ma5:
                state.ma5_below = True
                msgs.append(
                    (
                        AlertMessages.TYPE_BELOW_MA5,
                        AlertMessages.below_ma5(name, current_price, ma5_value),
                    )
                )
        else:
            if current_price >= ma5_value:
                state.ma5_below = False
                msgs.append(
                    (
                        AlertMessages.TYPE_ABOVE_MA5,
                        AlertMessages.above_ma5(name, current_price, ma5_value),
                    )
                )

    if ma10_value is not None:
        break_threshold_ma10 = ma10_value * (1 - ma10_break_pct / 100)
        if not state.ma10_below:
            if current_price < break_threshold_ma10:
                state.ma10_below = True
                msgs.append(
                    (
                        AlertMessages.TYPE_BELOW_MA10,
                        AlertMessages.below_ma10(name, current_price, ma10_value),
                    )
                )
        else:
            if current_price >= ma10_value:
                state.ma10_below = False
                msgs.append(
                    (
                        AlertMessages.TYPE_ABOVE_MA10,
                        AlertMessages.above_ma10(name, current_price, ma10_value),
                    )
                )

    state.last_ma_check_15m_ts = current_15m_ts
    return msgs
