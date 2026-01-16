"""
SR (Support/Resistance) 放量突破检测策略
"""

from datetime import time as dt_time
from typing import TYPE_CHECKING

import pandas as pd

from ..alerts import AlertMessages
from ..config import (
    SR_BREAKOUT_SYMBOLS,
    RVOL_N_ASHARE_1H,
    RVOL_N_CRYPTO_1H,
    RVOL_THRESHOLD,
)
from ..providers.base import MarketType, detect_market_type

if TYPE_CHECKING:
    from ..state import SymbolState


def _aggregate_ashare_15m_to_1h(lower_df: pd.DataFrame) -> pd.DataFrame | None:
    """
    将A股15m K线聚合为1h K线（按交易时段桶）

    A股交易时段：
    - 上午: 9:30-11:30 (2小时)
    - 下午: 13:00-15:00 (2小时)

    1h桶划分：
    - 9:30-10:30 (第1小时)
    - 10:30-11:30 (第2小时)
    - 13:00-14:00 (第3小时)
    - 14:00-15:00 (第4小时)
    """
    if lower_df is None or lower_df.empty:
        return None

    ldf = lower_df.copy()

    # 提取时间信息
    ldf["time"] = ldf["timestamp"].dt.time
    ldf["date"] = ldf["timestamp"].dt.date

    def get_1h_bucket(ts):
        """根据时间戳返回1h桶的起始时间"""
        t = ts.time()
        d = ts.date()

        # 上午第1小时: 9:30-10:30
        if dt_time(9, 30) <= t < dt_time(10, 30):
            return pd.Timestamp(
                year=d.year, month=d.month, day=d.day, hour=9, minute=30
            )
        # 上午第2小时: 10:30-11:30
        elif dt_time(10, 30) <= t < dt_time(11, 30):
            return pd.Timestamp(
                year=d.year, month=d.month, day=d.day, hour=10, minute=30
            )
        # 下午第1小时: 13:00-14:00
        elif dt_time(13, 0) <= t < dt_time(14, 0):
            return pd.Timestamp(
                year=d.year, month=d.month, day=d.day, hour=13, minute=0
            )
        # 下午第2小时: 14:00-15:00
        elif dt_time(14, 0) <= t < dt_time(15, 0):
            return pd.Timestamp(
                year=d.year, month=d.month, day=d.day, hour=14, minute=0
            )
        else:
            # 非交易时段，返回 None
            return None

    ldf["bucket_1h"] = ldf["timestamp"].apply(get_1h_bucket)
    ldf = ldf.dropna(subset=["bucket_1h"])

    if ldf.empty:
        return None

    # 按1h桶聚合OHLCV
    agg_df = (
        ldf.groupby("bucket_1h")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .reset_index()
    )

    agg_df = agg_df.rename(columns={"bucket_1h": "timestamp"})
    agg_df = agg_df.sort_values("timestamp").reset_index(drop=True)

    return agg_df


def _get_elapsed_fraction_1h(symbol: str) -> float:
    """
    计算当前1h K线已经过的时间比例

    返回值范围: 0.25 ~ 1.0
    - 15分钟: 0.25
    - 30分钟: 0.5
    - 45分钟: 0.75
    - 60分钟: 1.0

    设置下限0.25避免刚开1h时除数过小
    """
    market_type = detect_market_type(symbol)

    if market_type == MarketType.A_SHARE:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        current_time = now.time()

        # 确定当前所在的1h桶及其起始时间
        if dt_time(9, 30) <= current_time < dt_time(10, 30):
            bucket_start_minutes = 9 * 60 + 30
        elif dt_time(10, 30) <= current_time < dt_time(11, 30):
            bucket_start_minutes = 10 * 60 + 30
        elif dt_time(13, 0) <= current_time < dt_time(14, 0):
            bucket_start_minutes = 13 * 60
        elif dt_time(14, 0) <= current_time < dt_time(15, 0):
            bucket_start_minutes = 14 * 60
        else:
            # 非交易时段，返回1.0（使用完整K线）
            return 1.0

        current_minutes = current_time.hour * 60 + current_time.minute
        elapsed_minutes = current_minutes - bucket_start_minutes
        fraction = elapsed_minutes / 60.0
    else:
        # 加密货币：UTC时间
        now = pd.Timestamp.utcnow()
        elapsed_minutes = now.minute
        fraction = elapsed_minutes / 60.0

    # 设置下限0.25，避免刚开1h时除数过小
    return max(0.25, min(1.0, fraction))


def _compute_rvol_est_1h(
    symbol: str,
    df_1h: pd.DataFrame,
    current_1h_volume: float | None = None,
) -> float | None:
    """
    计算进行中1h K线的估算RVOL

    rvol_est_1h = current_1h_volume / (SMA(prev_1h_volume, N) * elapsed_fraction)
    """
    if symbol not in SR_BREAKOUT_SYMBOLS:
        return None
    if df_1h is None or len(df_1h) < 3:
        return None

    market_type = detect_market_type(symbol)
    rvol_n = RVOL_N_ASHARE_1H if market_type == MarketType.A_SHARE else RVOL_N_CRYPTO_1H

    if len(df_1h) < rvol_n + 1:
        return None

    # 计算历史1h成交量的SMA（不包含当前进行中的K线）
    # 使用倒数第2根到倒数第(rvol_n+1)根的数据
    hist_volumes = df_1h["volume"].iloc[-(rvol_n + 1) : -1]
    vol_sma = hist_volumes.mean()

    if pd.isna(vol_sma) or vol_sma == 0:
        return None

    # 获取当前1h成交量
    if current_1h_volume is None:
        current_1h_volume = df_1h["volume"].iloc[-1]

    # 获取已经过时间比例
    elapsed_fraction = _get_elapsed_fraction_1h(symbol)

    # 计算估算RVOL
    expected_volume = vol_sma * elapsed_fraction
    if expected_volume == 0:
        return None

    return current_1h_volume / expected_volume


def check_sr_breakout_vol(
    symbol: str,
    state: "SymbolState",
    df: pd.DataFrame,
    lower_df: pd.DataFrame | None,
    nearest_res: float | None,
    nearest_sup: float | None,
    display_name: str | None = None,
) -> tuple[str, str] | None:
    """
    检测放量突破/跌破（基于1h RVOL，15m收盘确认）

    逻辑：
    1. 使用1h K线计算RVOL（进行中估算）
    2. 使用15m收盘价确认突破
    3. 节拍门控：只在新15m收盘时检测

    Args:
        symbol: 标的代码
        state: 标的状态
        df: 主周期数据（加密1h，A股15m）
        lower_df: 低周期数据（15m，A股用于聚合1h）
        nearest_res: 最近阻力位（来自1h swing levels）
        nearest_sup: 最近支撑位（来自1h swing levels）
        display_name: 显示名称

    Returns:
        (alert_type, alert_message) 或 None
    """
    name = display_name or symbol
    if symbol not in SR_BREAKOUT_SYMBOLS:
        return None

    market_type = detect_market_type(symbol)

    # --- 15m 节拍门控 ---
    if market_type == MarketType.A_SHARE:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now_ts = pd.Timestamp(
            datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        )
    else:
        now_ts = pd.Timestamp.utcnow().tz_localize(None)

    current_15m_ts = now_ts.floor("15min")

    # 检查是否是新的15m tick
    is_new_15m_tick = (
        state.last_sr_break_tick_ts is None
        or current_15m_ts > state.last_sr_break_tick_ts
    )

    if not is_new_15m_tick:
        return None

    # --- 获取当前价格 ---
    if market_type == MarketType.A_SHARE:
        # A股：从15m数据获取当前价格
        if lower_df is None or len(lower_df) < 2:
            return None
        current_price = lower_df["close"].iloc[-1]
        prev_price = state.last_sr_break_tick_price
    else:
        # 加密货币：从1h数据获取当前价格
        if df is None or len(df) < 2:
            return None
        current_price = df["close"].iloc[-1]
        prev_price = state.last_sr_break_tick_price

    # --- 准备1h数据用于RVOL计算 ---
    if market_type == MarketType.A_SHARE:
        # A股：从15m聚合到1h
        df_1h = _aggregate_ashare_15m_to_1h(lower_df)
        if df_1h is None or len(df_1h) < RVOL_N_ASHARE_1H + 1:
            # 更新tick状态但不触发alert
            state.last_sr_break_tick_ts = current_15m_ts
            state.last_sr_break_tick_price = current_price
            return None
    else:
        # 加密货币：优先使用Coinbase BTC/USD数据计算RVOL
        df_1h = state.cached_coinbase_df if state.cached_coinbase_df is not None else df
        if df_1h is None or len(df_1h) < RVOL_N_CRYPTO_1H + 1:
            state.last_sr_break_tick_ts = current_15m_ts
            state.last_sr_break_tick_price = current_price
            return None

    # --- 计算1h RVOL ---
    rvol_est = _compute_rvol_est_1h(symbol, df_1h)

    if rvol_est is None or rvol_est < RVOL_THRESHOLD:
        # 更新tick状态
        state.last_sr_break_tick_ts = current_15m_ts
        state.last_sr_break_tick_price = current_price
        return None

    # --- 检测突破（使用15m价格变化确认） ---
    # 首次运行时没有prev_price，跳过
    if prev_price is None:
        state.last_sr_break_tick_ts = current_15m_ts
        state.last_sr_break_tick_price = current_price
        return None

    alert = None

    # 突破阻力位：prev_price <= nearest_res < current_price
    if nearest_res is not None and prev_price <= nearest_res < current_price:
        alert = (
            AlertMessages.TYPE_BREAKOUT_RESISTANCE_VOL,
            AlertMessages.breakout_resistance_vol(
                name, current_price, nearest_res, rvol_est
            ),
        )

    # 跌破支撑位：prev_price >= nearest_sup > current_price
    elif nearest_sup is not None and prev_price >= nearest_sup > current_price:
        alert = (
            AlertMessages.TYPE_BREAKDOWN_SUPPORT_VOL,
            AlertMessages.breakdown_support_vol(
                name, current_price, nearest_sup, rvol_est
            ),
        )

    # 更新tick状态
    state.last_sr_break_tick_ts = current_15m_ts
    state.last_sr_break_tick_price = current_price

    return alert
