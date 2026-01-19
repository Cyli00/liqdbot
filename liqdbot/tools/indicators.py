"""
技术指标计算模块
"""

import numpy as np
import pandas as pd
import pandas_ta as ta


def macd_with_sma_signal(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series | None, pd.Series | None, pd.Series | None]:
    """
    计算 MACD，使用 SMA 作为 Signal 线

    Args:
        series: 收盘价序列
        fast: 快线周期
        slow: 慢线周期
        signal: 信号线周期

    Returns:
        (macd_series, signal_series, hist_series) 或 (None, None, None)
    """
    fast_ma = ta.ema(series, length=fast)
    slow_ma = ta.ema(series, length=slow)
    if fast_ma is None or slow_ma is None:
        return None, None, None

    macd_series = fast_ma - slow_ma
    signal_series = ta.sma(macd_series, length=signal)
    if signal_series is None:
        return None, None, None

    hist_series = macd_series - signal_series
    return macd_series, signal_series, hist_series


def calculate_macd_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算 MACD 相关指标（仅在 15 分钟 K 线收盘确认时调用）
    包括: MACD, ATR, DIF斜率, 分位数分级

    Args:
        df: K线数据

    Returns:
        添加了MACD指标的DataFrame
    """
    if df is None or df.empty:
        return df

    df = df.copy()

    # 1. MACD (1h) - 用于共振策略
    macd_series, signal_series, hist_series = macd_with_sma_signal(df["close"])
    if macd_series is not None:
        df["MACD_12_26_9"] = macd_series
        df["MACDs_12_26_9"] = signal_series
        df["MACDh_12_26_9"] = hist_series

    # 2. ATR (14) - 用于标准化 DIF 斜率
    atr = df.ta.atr(length=14)
    if atr is not None:
        df["ATR_14"] = atr
    else:
        df["ATR_14"] = np.nan

    # 3. 标准化 DIF 斜率及分位数分级
    if "MACD_12_26_9" in df.columns and "ATR_14" in df.columns:
        # 标准化斜率 = (DIF - DIF[1]) / ATR
        dif_change = df["MACD_12_26_9"] - df["MACD_12_26_9"].shift(1)
        df["dif_slope"] = dif_change / df["ATR_14"].replace(0, np.nan)
        df["dif_slope"] = df["dif_slope"].replace([np.inf, -np.inf], np.nan)

        # 滚动分位数计算（使用绝对值，因为我们关心的是斜率强度）
        abs_slope = df["dif_slope"].abs()
        df["dif_slope_q20"] = abs_slope.rolling(200, min_periods=50).quantile(0.2)
        df["dif_slope_q40"] = abs_slope.rolling(200, min_periods=50).quantile(0.4)
        df["dif_slope_q60"] = abs_slope.rolling(200, min_periods=50).quantile(0.6)
        df["dif_slope_q80"] = abs_slope.rolling(200, min_periods=50).quantile(0.8)

        # 计算斜率等级 (1-5级)
        conditions = [
            abs_slope < df["dif_slope_q20"],
            (abs_slope >= df["dif_slope_q20"]) & (abs_slope < df["dif_slope_q40"]),
            (abs_slope >= df["dif_slope_q40"]) & (abs_slope < df["dif_slope_q60"]),
            (abs_slope >= df["dif_slope_q60"]) & (abs_slope < df["dif_slope_q80"]),
            abs_slope >= df["dif_slope_q80"],
        ]
        choices = [1, 2, 3, 4, 5]
        df["dif_slope_grade"] = np.select(conditions, choices, default=0)

    return df


def calculate_htf_indicators(df: pd.DataFrame) -> pd.DataFrame | None:
    """
    计算高周期 (4h/60m) 指标: MACD, Signal Slope, Histogram Color

    Args:
        df: 高周期K线数据

    Returns:
        添加了指标的DataFrame，或None
    """
    if df is None or len(df) < 50:
        return None

    df = df.copy()

    # MACD 12, 26, 9
    macd_series, signal_series, hist_series = macd_with_sma_signal(df["close"])
    if macd_series is None:
        return None

    df["MACD"] = macd_series
    df["Signal"] = signal_series
    df["Hist"] = hist_series

    # 计算 Signal 斜率
    df["Signal_Slope"] = df["Signal"] - df["Signal"].shift(1)

    # 计算 Histogram 颜色状态 (Aqua/Blue/Red/Maroon)
    # Aqua: Hist > 0 and Hist > Hist[1] (强多)
    # Blue: Hist > 0 and Hist < Hist[1] (弱多)
    # Red: Hist <= 0 and Hist < Hist[1] (强空)
    # Maroon: Hist <= 0 and Hist > Hist[1] (弱空)

    c1 = (df["Hist"] > 0) & (df["Hist"] > df["Hist"].shift(1))
    c2 = (df["Hist"] > 0) & (df["Hist"] < df["Hist"].shift(1))
    c3 = (df["Hist"] <= 0) & (df["Hist"] < df["Hist"].shift(1))
    c4 = (df["Hist"] <= 0) & (df["Hist"] > df["Hist"].shift(1))

    conditions = [c1, c2, c3, c4]
    choices = ["AQUA", "BLUE", "RED", "MAROON"]

    df["Hist_Color"] = np.select(conditions, choices, default="GRAY")

    return df


def calculate_pivot_points(
    df: pd.DataFrame, pivot_len: int
) -> tuple[np.ndarray, np.ndarray]:
    """
    计算 Pivot 高低点

    Args:
        df: K线数据
        pivot_len: Pivot 周期

    Returns:
        (is_pivot_high, is_pivot_low) 布尔数组
    """
    n = len(df)
    high_vals = df["high"].to_numpy()
    low_vals = df["low"].to_numpy()

    is_pivot_high = np.zeros(n, dtype=bool)
    is_pivot_low = np.zeros(n, dtype=bool)

    window_size = 2 * pivot_len + 1
    if n >= window_size:
        for i in range(pivot_len, n - pivot_len):
            # Pivot High: 当前high严格大于左右各pivot_len根K线
            left_max = high_vals[i - pivot_len : i].max()
            right_max = high_vals[i + 1 : i + pivot_len + 1].max()
            if high_vals[i] > left_max and high_vals[i] > right_max:
                is_pivot_high[i] = True

            # Pivot Low: 当前low严格小于左右各pivot_len根K线
            left_min = low_vals[i - pivot_len : i].min()
            right_min = low_vals[i + 1 : i + pivot_len + 1].min()
            if low_vals[i] < left_min and low_vals[i] < right_min:
                is_pivot_low[i] = True

    return is_pivot_high, is_pivot_low


def calculate_up_down_volume(
    df: pd.DataFrame, lower_df: pd.DataFrame | None, main_tf_freq: str
) -> pd.DataFrame:
    """
    计算上下行量

    Args:
        df: 主周期K线数据
        lower_df: 低周期K线数据
        main_tf_freq: 主周期频率字符串（如 "1h"）

    Returns:
        添加了up_vol和down_vol的DataFrame
    """
    df = df.copy()

    if lower_df is not None and not lower_df.empty:
        ldf = lower_df.copy()
        close_vals = ldf["close"].to_numpy()
        open_vals = ldf["open"].to_numpy()
        vol_vals = ldf["volume"].to_numpy()

        # 向量化计算上下行量
        # 对齐 Pine: close == open (Doji) 不计入任一方向
        is_up = close_vals > open_vals
        is_down = close_vals < open_vals
        ldf["up_vol"] = np.where(is_up, vol_vals, 0)
        ldf["down_vol"] = np.where(is_down, vol_vals, 0)
        ldf["bucket"] = ldf["timestamp"].dt.floor(main_tf_freq)

        # groupby 聚合
        vol_agg = ldf.groupby("bucket")[["up_vol", "down_vol"]].sum()
        df = df.merge(vol_agg, left_on="timestamp", right_index=True, how="left")
    else:
        df["up_vol"] = np.nan
        df["down_vol"] = np.nan

    return df
