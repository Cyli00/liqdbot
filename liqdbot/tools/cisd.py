"""
CISD (Change in State of Delivery) 检测策略
"""

from collections import deque

import numpy as np
import pandas as pd


def detect_cisd(df: pd.DataFrame, cisd_tolerance: float = 0.7) -> dict:
    """
    检测 CISD 信号

    优化: 使用 numpy 数组替代 DataFrame 访问，deque 替代 list 实现 O(1) 头部操作

    Args:
        df: 包含 OHLCV 数据的 DataFrame
        cisd_tolerance: CISD 容忍度阈值

    Returns:
        dict: {
            "flag_series": list[int],  # 每根K线的CISD标志 (0=无, 1=看跌, 2=看涨)
            "flag_at_last": int,       # 最后一根K线的CISD标志
            "origin_level_at_last": float | None,  # 最后一根K线的起点价位
            "origin_idx_at_last": int | None,      # 最后一根K线的起点索引
        }
    """
    n = len(df)
    cisd_flag = [0] * n
    origin_level = [None] * n
    origin_idx = [None] * n
    close_vals = df["close"].to_numpy()
    open_vals = df["open"].to_numpy()

    # 使用 deque 实现 O(1) 的头部操作
    # 每个候选: (cand_open, cand_idx, running_max/min)
    bear_potential: deque = deque()
    bull_potential: deque = deque()

    # 预计算 bearish/bullish run open（O(n)）
    bearish_run_open = np.full(n, np.nan, dtype=np.float64)
    bullish_run_open = np.full(n, np.nan, dtype=np.float64)

    for i in range(n):
        if close_vals[i] < open_vals[i]:  # 阴线
            if i > 0 and close_vals[i - 1] < open_vals[i - 1]:
                bearish_run_open[i] = bearish_run_open[i - 1]
            else:
                bearish_run_open[i] = open_vals[i]
        if close_vals[i] > open_vals[i]:  # 阳线
            if i > 0 and close_vals[i - 1] > open_vals[i - 1]:
                bullish_run_open[i] = bullish_run_open[i - 1]
            else:
                bullish_run_open[i] = open_vals[i]

    for i in range(1, n):
        prev_close = close_vals[i - 1]
        prev_open = open_vals[i - 1]
        curr_close = close_vals[i]
        curr_open = open_vals[i]

        # 更新所有候选的 running max/min（摊销 O(1)，因为候选数量有限）
        for j in range(len(bear_potential)):
            cand_open, cand_idx, running_max = bear_potential[j]
            bear_potential[j] = (cand_open, cand_idx, max(running_max, curr_close))
        for j in range(len(bull_potential)):
            cand_open, cand_idx, running_min = bull_potential[j]
            bull_potential[j] = (cand_open, cand_idx, min(running_min, curr_close))

        # 检测新候选点
        if prev_close < prev_open and curr_close > curr_open:
            # 阴转阳：新增 bearish CISD 候选，running_max 初始为当前 close
            bear_potential.appendleft((curr_open, i, curr_close))
        if prev_close > prev_open and curr_close < curr_open:
            # 阳转阴：新增 bullish CISD 候选，running_min 初始为当前 close
            bull_potential.appendleft((curr_open, i, curr_close))

        # Bearish CISD 检查
        while bear_potential:
            cand_open, cand_idx, running_max = bear_potential[0]
            if curr_close < cand_open:
                # 使用维护的 running_max，O(1)
                highest = running_max

                top = bearish_run_open[cand_idx - 1] if cand_idx > 0 else np.nan
                if np.isnan(top):
                    top = cand_open
                denom = top - cand_open
                if denom > 0:
                    ratio = (highest - cand_open) / denom
                elif denom == 0:
                    ratio = float("inf")
                else:
                    ratio = float("-inf")
                if ratio > cisd_tolerance:
                    cisd_flag[i] = 1
                    origin_level[i] = cand_open
                    origin_idx[i] = cand_idx
                    bear_potential.clear()
                    break
                else:
                    bear_potential.popleft()  # O(1)
            else:
                break

        # Bullish CISD 检查
        while bull_potential:
            cand_open, cand_idx, running_min = bull_potential[0]
            if curr_close > cand_open:
                # 使用维护的 running_min，O(1)
                lowest = running_min

                bottom = bullish_run_open[cand_idx - 1] if cand_idx > 0 else np.nan
                if np.isnan(bottom):
                    bottom = cand_open
                denom = cand_open - bottom
                if denom > 0:
                    ratio = (cand_open - lowest) / denom
                elif denom == 0:
                    ratio = float("inf")
                else:
                    ratio = float("-inf")
                if ratio > cisd_tolerance:
                    cisd_flag[i] = 2
                    origin_level[i] = cand_open
                    origin_idx[i] = cand_idx
                    bull_potential.clear()
                    break
                else:
                    bull_potential.popleft()  # O(1)
            else:
                break

    last_idx = n - 1
    last_flag = cisd_flag[last_idx] if cisd_flag else 0
    return {
        "flag_series": cisd_flag,
        "flag_at_last": last_flag,
        "origin_level_at_last": origin_level[last_idx] if origin_level else None,
        "origin_idx_at_last": origin_idx[last_idx] if origin_idx else None,
    }
