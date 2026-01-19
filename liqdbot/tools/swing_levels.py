"""
Swing Levels (支撑/阻力位) 计算模块
"""

import numpy as np
import pandas as pd

from .indicators import calculate_pivot_points


def update_swing_levels(
    df: pd.DataFrame,
    swing_levels: list,
    pivot_len: int,
    expiry_bars: int,
    hide_expired_levels: bool,
) -> list:
    """
    更新 Swing 高低点（支撑/阻力位）

    Args:
        df: K线数据
        swing_levels: 现有的swing levels列表（会被清空重建）
        pivot_len: Pivot周期
        expiry_bars: 过期K线数
        hide_expired_levels: 是否隐藏过期的levels

    Returns:
        更新后的swing_levels列表
    """
    swing_levels.clear()
    last_idx = len(df) - 1
    start_idx = pivot_len
    if hide_expired_levels:
        start_idx = max(start_idx, last_idx - expiry_bars)

    # 计算 pivot 点
    is_pivot_high, is_pivot_low = calculate_pivot_points(df, pivot_len)

    high_vals = df["high"].to_numpy()
    low_vals = df["low"].to_numpy()
    timestamps = df["timestamp"].to_numpy()

    end_scan_idx = len(df) - pivot_len
    for i in range(start_idx, end_scan_idx):
        if is_pivot_high[i]:
            swing_levels.append(
                {
                    "type": "high",
                    "price": high_vals[i],
                    "created_at": timestamps[i],
                    "created_idx": i,
                    "mitigated": False,
                    "mitigated_at": None,
                }
            )

        if is_pivot_low[i]:
            swing_levels.append(
                {
                    "type": "low",
                    "price": low_vals[i],
                    "created_at": timestamps[i],
                    "created_idx": i,
                    "mitigated": False,
                    "mitigated_at": None,
                }
            )

    # 检查 Mitigation
    _check_mitigation(
        df, swing_levels, last_idx, expiry_bars, hide_expired_levels
    )

    return sorted(swing_levels, key=lambda x: x["created_at"])


def _check_mitigation(
    df: pd.DataFrame,
    swing_levels: list,
    last_idx: int,
    expiry_bars: int,
    hide_expired_levels: bool,
) -> None:
    """
    检查 swing levels 是否被触及（mitigation）

    Args:
        df: K线数据
        swing_levels: swing levels列表
        last_idx: 最后一根K线索引
        expiry_bars: 过期K线数
        hide_expired_levels: 是否隐藏过期的levels
    """
    n = len(df)
    check_end = last_idx  # 不包含 last_idx（当前未收盘K线）

    if not swing_levels or check_end <= 0:
        return

    high_vals = df["high"].to_numpy()
    low_vals = df["low"].to_numpy()
    timestamps = df["timestamp"].to_numpy()

    # 预计算 suffix max/min（从后往前扫描一次，O(n)）
    suffix_max_high = np.empty(n, dtype=np.float64)
    suffix_min_low = np.empty(n, dtype=np.float64)

    suffix_max_high[check_end - 1] = high_vals[check_end - 1]
    suffix_min_low[check_end - 1] = low_vals[check_end - 1]

    for i in range(check_end - 2, -1, -1):
        suffix_max_high[i] = max(high_vals[i], suffix_max_high[i + 1])
        suffix_min_low[i] = min(low_vals[i], suffix_min_low[i + 1])

    active_levels = []
    for level in swing_levels:
        age = last_idx - level["created_idx"]
        if hide_expired_levels and age > expiry_bars:
            continue

        start_check_idx = level["created_idx"] + 1

        if start_check_idx >= check_end:
            active_levels.append(level)
            continue

        price = level["price"]

        if level["type"] == "high":
            if suffix_max_high[start_check_idx] >= price:
                for touch_idx in range(start_check_idx, check_end):
                    if high_vals[touch_idx] >= price:
                        level["mitigated"] = True
                        level["mitigated_at"] = touch_idx
                        level["mitigated_at_ts"] = timestamps[touch_idx]
                        break

        elif level["type"] == "low":
            if suffix_min_low[start_check_idx] <= price:
                for touch_idx in range(start_check_idx, check_end):
                    if low_vals[touch_idx] <= price:
                        level["mitigated"] = True
                        level["mitigated_at"] = touch_idx
                        level["mitigated_at_ts"] = timestamps[touch_idx]
                        break

        active_levels.append(level)

    swing_levels.clear()
    swing_levels.extend(active_levels)


def find_recent_wicked_level(
    swing_levels: list, last_idx: int, level_type: str, liquidity_lookback: int
) -> tuple[int | None, float | None]:
    """
    在 liquidity_lookback 窗口内查找最近被扫荡的 swing level

    Args:
        swing_levels: swing levels列表
        last_idx: 当前 K 线索引
        level_type: 'high' 或 'low'
        liquidity_lookback: 回溯窗口大小

    Returns:
        (bars_since, level_price) 或 (None, None)
    """
    candidates = []
    for lvl in swing_levels:
        if lvl["type"] != level_type:
            continue
        mitigated_at = lvl.get("mitigated_at")
        if mitigated_at is None:
            continue
        bars_since = last_idx - mitigated_at
        if 0 <= bars_since <= liquidity_lookback:
            candidates.append((bars_since, mitigated_at, lvl["price"]))

    if not candidates:
        return None, None

    # 取 mitigated_at 最大的（即最近被扫荡的）
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0], candidates[0][2]
