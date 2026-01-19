"""
MACD 共振检测策略
"""

import math
import re

import pandas as pd

from ..providers.base import MarketType, detect_market_type


def _timeframe_to_minutes(tf: str) -> int:
    """将 ccxt 风格的 timeframe（如 1h/15m）转为分钟"""
    m = re.match(r"(?i)(\d+)([smhdw])", tf)
    if not m:
        return 60
    value = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "s":
        return max(1, math.ceil(value / 60))
    if unit == "m":
        return value
    if unit == "h":
        return value * 60
    if unit == "d":
        return value * 60 * 24
    if unit == "w":
        return value * 60 * 24 * 7
    return 60


def _find_last_cross_time(
    df: pd.DataFrame, macd_col: str, signal_col: str, find_golden: bool
) -> pd.Timestamp | None:
    """
    在DataFrame中查找最近一次金叉或死叉发生的时间

    Args:
        df: DataFrame with MACD data
        macd_col: MACD列名
        signal_col: Signal列名
        find_golden: True=查找金叉, False=查找死叉

    Returns:
        交叉发生的时间戳，如果没找到返回None
    """
    if df is None or len(df) < 2:
        return None

    if macd_col not in df.columns or signal_col not in df.columns:
        return None

    # 从最近往前找
    for i in range(len(df) - 1, 0, -1):
        curr_mac = df[macd_col].iloc[i]
        curr_sig = df[signal_col].iloc[i]
        prev_mac = df[macd_col].iloc[i - 1]
        prev_sig = df[signal_col].iloc[i - 1]

        if (
            pd.isna(curr_mac)
            or pd.isna(curr_sig)
            or pd.isna(prev_mac)
            or pd.isna(prev_sig)
        ):
            continue

        is_bull_now = curr_mac > curr_sig
        was_bear = prev_mac < prev_sig
        is_bear_now = curr_mac < curr_sig
        was_bull = prev_mac > prev_sig

        if find_golden and was_bear and is_bull_now:
            return df["timestamp"].iloc[i]
        elif not find_golden and was_bull and is_bear_now:
            return df["timestamp"].iloc[i]

    return None


def _find_last_cross_info(
    df: pd.DataFrame, macd_col: str, signal_col: str
) -> tuple[pd.Timestamp | None, bool | None]:
    """
    查找最近一次交叉的时间与方向（True=金叉, False=死叉）
    """
    if df is None or len(df) < 2:
        return None, None

    if macd_col not in df.columns or signal_col not in df.columns:
        return None, None

    for i in range(len(df) - 1, 0, -1):
        curr_mac = df[macd_col].iloc[i]
        curr_sig = df[signal_col].iloc[i]
        prev_mac = df[macd_col].iloc[i - 1]
        prev_sig = df[signal_col].iloc[i - 1]

        if (
            pd.isna(curr_mac)
            or pd.isna(curr_sig)
            or pd.isna(prev_mac)
            or pd.isna(prev_sig)
        ):
            continue

        is_bull_now = curr_mac > curr_sig
        was_bear = prev_mac < prev_sig
        is_bear_now = curr_mac < curr_sig
        was_bull = prev_mac > prev_sig

        if was_bear and is_bull_now:
            return df["timestamp"].iloc[i], True
        if was_bull and is_bear_now:
            return df["timestamp"].iloc[i], False

    return None, None


def check_macd_resonance(
    df: pd.DataFrame,
    htf_df: pd.DataFrame,
    symbol: str | None = None,
    htf_timeframe: str = "4h",
) -> tuple[int, dict, pd.Timestamp | None]:
    """
    检查 MACD 1h/4h 共振

    金叉共振条件（必须同时满足）：
    1. 1h 和 4h 都处于多头状态（MACD > Signal）
    2. 至少有一个周期发生了金叉（MACD 上穿 Signal）

    死叉共振条件（必须同时满足）：
    1. 1h 和 4h 都处于空头状态（MACD < Signal）
    2. 至少有一个周期发生了死叉（MACD 下穿 Signal）

    Args:
        df: 低周期数据（1h/15m），需包含 MACD_12_26_9, MACDs_12_26_9 列
        htf_df: 高周期数据（4h/60m），需包含 MACD, Signal, Hist_Color 列
        symbol: 标的代码（用于判断市场类型）
        htf_timeframe: 高周期时间框架

    Returns:
        (共振类型, 详细信息dict, 1h时间戳)
        - 共振类型: 1=金叉共振, -1=死叉共振, 0=无共振
        - 详细信息: slope_4h, hist_color, macd_slope_1h, macd_slope_4h,
                   zero_position_1h, zero_position_4h, cross_time_gap
    """
    empty_info = {
        "slope_4h": 0.0,
        "hist_color": "GRAY",
        "macd_slope_1h": 0.0,
        "macd_slope_4h": 0.0,
        "zero_pos_1h": "unknown",
        "zero_pos_4h": "unknown",
        "cross_time_gap_hours": None,
        "cross_1h_at": None,
        "cross_4h_at": None,
    }

    if df is None or htf_df is None or len(df) < 5 or len(htf_df) < 5:
        return 0, empty_info, None

    last_1h = df.iloc[-1]
    prev_1h = df.iloc[-2]

    last_4h = htf_df.iloc[-1]
    prev_4h = htf_df.iloc[-2]

    # 1h MACD data check
    if "MACD_12_26_9" not in df.columns or "MACDs_12_26_9" not in df.columns:
        return 0, empty_info, None
    if "MACD" not in htf_df.columns or "Signal" not in htf_df.columns:
        return 0, empty_info, None

    mac_1h = last_1h["MACD_12_26_9"]
    sig_1h = last_1h["MACDs_12_26_9"]
    prev_mac_1h = prev_1h["MACD_12_26_9"]
    prev_sig_1h = prev_1h["MACDs_12_26_9"]

    # 1h 状态判断
    is_bull_1h = mac_1h > sig_1h
    is_bear_1h = mac_1h < sig_1h
    was_bear_1h = prev_mac_1h < prev_sig_1h
    was_bull_1h = prev_mac_1h > prev_sig_1h

    # 交叉检测
    cross_up_1h = was_bear_1h and is_bull_1h
    cross_down_1h = was_bull_1h and is_bear_1h

    # 4h 状态
    mac_4h = last_4h["MACD"]
    sig_4h = last_4h["Signal"]
    prev_mac_4h = prev_4h["MACD"]
    prev_sig_4h = prev_4h["Signal"]

    is_bull_4h = mac_4h > sig_4h
    is_bear_4h = mac_4h < sig_4h
    was_bear_4h = prev_mac_4h < prev_sig_4h
    was_bull_4h = prev_mac_4h > prev_sig_4h

    cross_up_4h = was_bear_4h and is_bull_4h
    cross_down_4h = was_bull_4h and is_bear_4h

    # 共振判断
    has_cross_up = cross_up_1h or cross_up_4h
    res_golden = is_bull_1h and is_bull_4h and has_cross_up

    has_cross_down = cross_down_1h or cross_down_4h
    res_death = is_bear_1h and is_bear_4h and has_cross_down

    if not res_golden and not res_death:
        return 0, empty_info, None

    # ========== 计算详细信息 ==========
    ts_1h = last_1h["timestamp"]

    dif_slope_1h = 0.0
    dif_slope_grade_1h = 0
    slope_valid = False

    atr_1h = last_1h.get("ATR_14")
    if (
        atr_1h is not None
        and not pd.isna(atr_1h)
        and atr_1h != 0
        and not pd.isna(mac_1h)
        and not pd.isna(prev_mac_1h)
    ):
        dif_change = mac_1h - prev_mac_1h
        dif_slope_1h = dif_change / atr_1h
        slope_valid = True

    if not slope_valid:
        fallback_slope = last_1h.get("dif_slope", 0)
        dif_slope_1h = fallback_slope if not pd.isna(fallback_slope) else 0

    # 斜率等级（基于历史分位数）
    if slope_valid:
        q20 = last_1h.get("dif_slope_q20")
        q40 = last_1h.get("dif_slope_q40")
        q60 = last_1h.get("dif_slope_q60")
        q80 = last_1h.get("dif_slope_q80")
        if (
            not pd.isna(q20)
            and not pd.isna(q40)
            and not pd.isna(q60)
            and not pd.isna(q80)
        ):
            abs_slope = abs(dif_slope_1h)
            if abs_slope < q20:
                dif_slope_grade_1h = 1
            elif abs_slope < q40:
                dif_slope_grade_1h = 2
            elif abs_slope < q60:
                dif_slope_grade_1h = 3
            elif abs_slope < q80:
                dif_slope_grade_1h = 4
            else:
                dif_slope_grade_1h = 5
        else:
            fallback_grade = last_1h.get("dif_slope_grade", 0)
            dif_slope_grade_1h = (
                int(fallback_grade) if not pd.isna(fallback_grade) else 0
            )
    else:
        fallback_grade = last_1h.get("dif_slope_grade", 0)
        dif_slope_grade_1h = int(fallback_grade) if not pd.isna(fallback_grade) else 0

    # 2. 零轴位置判断
    # 金叉在零轴下方 = 左侧信号（更早期），零轴上方 = 右侧信号（趋势确认）
    zero_pos_1h = "above" if mac_1h > 0 else "below"
    zero_pos_4h = "above" if mac_4h > 0 else "below"

    # 3. 计算1h和4h交叉的时间间隔
    cross_1h_time = None
    cross_4h_time = None
    cross_time_gap_hours = None

    # 确定要找的交叉类型
    is_golden = res_golden

    # 查找1h最近的交叉时间及方向
    cross_1h_time, cross_1h_is_golden = _find_last_cross_info(
        df, "MACD_12_26_9", "MACDs_12_26_9"
    )

    # 查找4h最近的交叉时间及方向
    cross_4h_time, cross_4h_is_golden = _find_last_cross_info(htf_df, "MACD", "Signal")

    if (
        cross_1h_time is not None
        and cross_4h_time is not None
        and cross_1h_is_golden == cross_4h_is_golden == is_golden
    ):
        time_diff = abs((cross_1h_time - cross_4h_time).total_seconds())
        cross_time_gap_hours = time_diff / 3600
    else:
        cross_1h_time = None
        cross_4h_time = None
        cross_time_gap_hours = None

    # 辅助数据
    slope_4h = last_4h.get("Signal_Slope", 0.0)
    hist_color_4h = last_4h.get("Hist_Color", "GRAY")

    # 获取时间周期信息用于显示
    market_type = detect_market_type(symbol) if symbol else MarketType.CRYPTO
    if market_type == MarketType.A_SHARE:
        ltf_label = "15m"
        htf_label = "60m"
    else:
        ltf_label = "1h"
        htf_label = "4h"

    info = {
        "slope_4h": slope_4h,
        "hist_color": hist_color_4h,
        "dif_slope_grade_1h": dif_slope_grade_1h,
        "zero_pos_1h": zero_pos_1h,
        "zero_pos_4h": zero_pos_4h,
        "cross_time_gap_hours": cross_time_gap_hours,
        "cross_1h_at": cross_1h_time,
        "cross_4h_at": cross_4h_time,
        "macd_1h": mac_1h,
        "macd_4h": mac_4h,
        "ltf_label": ltf_label,
        "htf_label": htf_label,
    }

    if res_golden:
        return 1, info, ts_1h
    if res_death:
        return -1, info, ts_1h

    return 0, empty_info, None
