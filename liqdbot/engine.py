"""
策略引擎模块 - 核心交易逻辑和数据分析
"""

import asyncio
import math
import re
import logging
import numpy as np
import pandas as pd
import pandas_ta as ta

from .config import (
    DEFAULT_SYMBOL,
    TIMEFRAME,
    LOWER_TIMEFRAME,
    FETCH_LIMIT,
    PIVOT_LEN,
    EXPIRY_BARS,
    LIQUIDITY_LOOKBACK,
    HIDE_EXPIRED_LEVELS,
    HIDE_MITIGATED_LEVELS,
    CISD_TOLERANCE,
    DEFAULT_ASHARE_SYMBOLS,
    ASHARE_TIMEFRAME,
    ASHARE_HTF_TIMEFRAME,
    ASHARE_LTF_TIMEFRAME,
    ASHARE_LOWER_TIMEFRAME,
    ASHARE_MA_PERIOD,
    ASHARE_MA5_BREAK_PCT,
    ASHARE_MA10_PERIOD,
    ASHARE_MA10_BREAK_PCT,
    ASHARE_OPEN_COOLDOWN_BARS,
    SR_BREAKOUT_SYMBOLS,
    RVOL_N_CRYPTO,
    RVOL_N_ASHARE,
    RVOL_N_CRYPTO_1H,
    RVOL_N_ASHARE_1H,
    RVOL_THRESHOLD,
)
from .state import SymbolState
from .alerts import AlertMessages
from .providers import (
    MarketType,
    detect_market_type,
    CryptoProvider,
    AkshareProvider,
    CoinbaseProvider,
    OkxProvider,
)


class StrategyEngine:
    """策略引擎 - 负责数据获取、指标计算和市场分析"""

    def __init__(self):
        self.crypto_provider = CryptoProvider()
        self.akshare_provider = AkshareProvider()
        self.coinbase_provider = CoinbaseProvider()
        self.okx_provider = OkxProvider()

        # 多标的监控: {symbol: SymbolState}
        self.symbols: dict[str, SymbolState] = {}

        # 策略参数（全局共享）
        self.lower_timeframe = LOWER_TIMEFRAME
        self.pivot_len = PIVOT_LEN
        self.expiry_bars = EXPIRY_BARS
        self.liquidity_lookback = LIQUIDITY_LOOKBACK
        self.hide_expired_levels = HIDE_EXPIRED_LEVELS
        self.hide_mitigated_levels = HIDE_MITIGATED_LEVELS
        self.cisd_tolerance = CISD_TOLERANCE

        self.htf_timeframe = "4h"  # 高周期固定为 4h

        # 如果有默认标的，自动添加
        if DEFAULT_SYMBOL:
            self.add_symbol(DEFAULT_SYMBOL)
            logging.info(f"初始化引擎: 默认监控 {DEFAULT_SYMBOL}")

        # 添加默认 A 股标的列表
        for ashare_symbol in DEFAULT_ASHARE_SYMBOLS:
            self.add_symbol(ashare_symbol)
            logging.info(f"初始化引擎: 默认监控 A股 {ashare_symbol}")

    def add_symbol(self, symbol: str) -> bool:
        """添加新的监控标的"""
        if symbol in self.symbols:
            # 如果已存在，重置状态
            self.symbols[symbol].reset()
            logging.info(f"标的 {symbol} 已存在，已重置状态")
            return False
        else:
            self.symbols[symbol] = SymbolState(symbol)
            logging.info(f"已添加监控标的: {symbol}")
            return True

    async def validate_symbol(self, symbol: str) -> bool:
        """验证交易对是否在交易所存在"""
        provider = self.get_provider_for_symbol(symbol)
        return await provider.validate_symbol(symbol)

    def remove_symbol(self, symbol: str) -> bool:
        """移除监控标的"""
        if symbol in self.symbols:
            del self.symbols[symbol]
            logging.info(f"已移除监控标的: {symbol}")
            return True
        return False

    def get_all_symbols(self) -> list[str]:
        """获取所有监控中的标的"""
        return list(self.symbols.keys())

    def get_state(self, symbol: str) -> SymbolState | None:
        """获取指定标的的状态"""
        return self.symbols.get(symbol)

    def get_provider_for_symbol(self, symbol: str):
        market_type = detect_market_type(symbol)
        if market_type == MarketType.A_SHARE:
            return self.akshare_provider
        return self.crypto_provider

    async def get_symbol_display_name(self, symbol: str) -> str:
        market_type = detect_market_type(symbol)
        if market_type != MarketType.A_SHARE:
            return symbol

        name = await self.akshare_provider.get_symbol_name(symbol)
        return name or symbol

    def get_timeframes_for_symbol(self, symbol: str) -> dict:
        market_type = detect_market_type(symbol)
        if market_type == MarketType.A_SHARE:
            return {
                "main": ASHARE_TIMEFRAME,
                "lower": ASHARE_LOWER_TIMEFRAME,
                "htf": ASHARE_HTF_TIMEFRAME,
                "ltf": ASHARE_LTF_TIMEFRAME,
            }
        return {
            "main": TIMEFRAME,
            "lower": LOWER_TIMEFRAME,
            "htf": self.htf_timeframe,
            "ltf": TIMEFRAME,
        }

    def _timeframe_to_minutes(self, tf: str) -> int:
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

    def _pandas_freq(self, tf: str) -> str:
        """转换为 pandas 频率字符串，用于 floor/resample"""
        m = re.match(r"(?i)(\d+)([smhdw])", tf)
        if not m:
            return "1h"
        value = int(m.group(1))
        unit = m.group(2).lower()
        mapping = {"s": "s", "m": "min", "h": "h", "d": "D", "w": "W"}
        return f"{value}{mapping.get(unit, 'h')}"

    @staticmethod
    def _ohlcv_to_df(ohlcv):
        df = pd.DataFrame(
            ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df.reset_index(drop=True)

    @staticmethod
    def _merge_frames(cached_df, new_df, max_size: int | None):
        if cached_df is None or cached_df.empty:
            merged = new_df.copy()
        else:
            merged = pd.concat([cached_df, new_df], ignore_index=True)

        merged = merged.drop_duplicates(subset="timestamp", keep="last")
        merged = merged.sort_values("timestamp").reset_index(drop=True)

        if max_size is not None and len(merged) > max_size:
            merged = merged.tail(max_size).reset_index(drop=True)

        return merged

    @staticmethod
    def _macd_with_sma_signal(series, fast: int = 12, slow: int = 26, signal: int = 9):
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

    async def fetch_data(
        self,
        symbol: str,
        limit: int = FETCH_LIMIT,
        force_full: bool = False,
        use_cache_if_available: bool = False,
    ):
        """
        获取指定标的的数据（支持增量更新）

        增量更新逻辑：
        1. 首次获取：拉取完整历史数据
        2. 后续获取：只拉取最新几根K线，合并到缓存
        3. 定期强制刷新：每小时完整刷新一次，防止数据漂移

        Args:
            symbol: 交易对
            limit: 完整获取时的K线数量
            force_full: 是否强制完整获取
            use_cache_if_available: 如果有缓存则直接返回（用于 /status 快速响应）
        """
        import time as time_module

        if not symbol:
            return None, None, None

        state = self.get_state(symbol)
        if state is None:
            return None, None, None

        # 快速模式：如果有缓存直接返回
        if use_cache_if_available and state.cached_df is not None:
            logging.debug(f"[{symbol}] 使用缓存数据（快速模式）")
            return state.cached_df, state.cached_lower_df, state.cached_htf_df

        now = time_module.time()

        # 判断是否需要完整获取
        # 1. 首次获取（无缓存）
        # 2. 强制刷新
        # 3. 距离上次完整获取超过1小时
        need_full_fetch = (
            force_full
            or state.cached_df is None
            or (now - state.last_fetch_time) > 3600
        )

        try:
            if need_full_fetch:
                # 完整获取
                df, lower_df, htf_df = await self._fetch_full_data(symbol, limit)
                if df is not None:
                    state.cached_df = df
                    state.cached_lower_df = lower_df
                    state.cached_htf_df = htf_df
                    state.last_fetch_time = now
                    logging.debug(f"[{symbol}] 完整获取 {len(df)} 根K线")
                return df, lower_df, htf_df
            else:
                # 增量获取
                df, lower_df, htf_df = await self._fetch_incremental_data(symbol, state)
                return df, lower_df, htf_df

        except Exception as e:
            logging.error(f"Error fetching data for {symbol}: {e}")
            # 如果增量获取失败，尝试返回缓存数据
            if state.cached_df is not None:
                logging.warning(f"[{symbol}] 使用缓存数据")
                return state.cached_df, state.cached_lower_df, state.cached_htf_df
            return None, None, None

    async def _fetch_full_data(self, symbol: str, limit: int):
        provider = self.get_provider_for_symbol(symbol)
        tfs = self.get_timeframes_for_symbol(symbol)
        market_type = detect_market_type(symbol)

        main_tf = tfs["main"]
        lower_tf = tfs["lower"]
        htf_tf = tfs["htf"]

        # 加密货币不再需要15m数据（放量突破已改用1h RVOL）
        need_lower_tf = market_type == MarketType.A_SHARE

        # 检查是否需要获取Coinbase数据（用于加密货币RVOL计算）
        need_coinbase = (
            market_type == MarketType.CRYPTO and symbol in SR_BREAKOUT_SYMBOLS
        )

        lower_minutes = self._timeframe_to_minutes(lower_tf)
        main_minutes = self._timeframe_to_minutes(main_tf)
        ratio = max(1, math.ceil(main_minutes / lower_minutes))
        lower_limit = limit * ratio + 50

        htf_minutes = self._timeframe_to_minutes(htf_tf)
        ratio_htf = max(1, math.ceil(htf_minutes / main_minutes))
        htf_limit = max(100, int(limit / ratio_htf) + 20)

        main_task = provider.fetch_ohlcv(symbol, main_tf, limit)
        htf_task = provider.fetch_ohlcv(symbol, htf_tf, htf_limit)

        tasks = [main_task, htf_task]
        task_names = ["main", "htf"]

        if need_lower_tf:
            lower_task = provider.fetch_ohlcv(symbol, lower_tf, lower_limit)
            tasks.append(lower_task)
            task_names.append("lower")

        if need_coinbase:
            # 获取Coinbase BTC/USD数据用于RVOL计算
            base_currency = symbol.split("/")[0] if "/" in symbol else symbol
            coinbase_symbol = f"{base_currency}/USD"
            coinbase_task = self.coinbase_provider.fetch_ohlcv(
                coinbase_symbol, "1h", RVOL_N_CRYPTO_1H + 10
            )
            tasks.append(coinbase_task)
            task_names.append("coinbase")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 解析结果
        df = results[0] if not isinstance(results[0], Exception) else None
        htf_df = results[1] if not isinstance(results[1], Exception) else None
        lower_df = None
        coinbase_df = None

        for i, name in enumerate(task_names):
            if name == "lower":
                lower_df = results[i] if not isinstance(results[i], Exception) else None
            elif name == "coinbase":
                coinbase_df = (
                    results[i] if not isinstance(results[i], Exception) else None
                )

        # 缓存Coinbase数据到state
        if need_coinbase:
            state = self.get_state(symbol)
            if state is not None:
                state.cached_coinbase_df = coinbase_df

        if df is None:
            return None, None, None

        return df, lower_df, htf_df

    async def _fetch_incremental_data(self, symbol: str, state):
        import time as time_mod

        provider = self.get_provider_for_symbol(symbol)
        tfs = self.get_timeframes_for_symbol(symbol)
        market_type = detect_market_type(symbol)

        main_tf = tfs["main"]
        lower_tf = tfs["lower"]
        htf_tf = tfs["htf"]

        # 加密货币不再需要15m数据（放量突破已改用1h RVOL）
        need_lower_tf = market_type == MarketType.A_SHARE

        # 检查是否需要获取Coinbase数据（用于加密货币RVOL计算）
        need_coinbase = (
            market_type == MarketType.CRYPTO and symbol in SR_BREAKOUT_SYMBOLS
        )

        INCREMENTAL_LIMIT = 10
        MAX_CACHE_SIZE = FETCH_LIMIT

        lower_minutes = self._timeframe_to_minutes(lower_tf)
        main_minutes = self._timeframe_to_minutes(main_tf)
        ratio = max(1, math.ceil(main_minutes / lower_minutes))
        lower_incremental_limit = INCREMENTAL_LIMIT * ratio + 10

        htf_update_interval = 1800
        need_htf_update = (
            state.last_htf_fetch_time is None
            or (time_mod.time() - state.last_htf_fetch_time) > htf_update_interval
        )

        # 构建任务列表
        tasks = []
        task_names = []

        # 主周期数据
        main_task = provider.fetch_ohlcv(symbol, main_tf, INCREMENTAL_LIMIT)
        tasks.append(main_task)
        task_names.append("main")

        # 低周期数据（仅A股）
        if need_lower_tf:
            lower_task = provider.fetch_ohlcv(symbol, lower_tf, lower_incremental_limit)
            tasks.append(lower_task)
            task_names.append("lower")

        # 高周期数据
        if need_htf_update:
            htf_task = provider.fetch_ohlcv(symbol, htf_tf, 5)
            tasks.append(htf_task)
            task_names.append("htf")

        # Coinbase数据（仅加密货币且在SR_BREAKOUT_SYMBOLS中）
        if need_coinbase:
            base_currency = symbol.split("/")[0] if "/" in symbol else symbol
            coinbase_symbol = f"{base_currency}/USD"
            coinbase_task = self.coinbase_provider.fetch_ohlcv(
                coinbase_symbol, "1h", RVOL_N_CRYPTO_1H + 10
            )
            tasks.append(coinbase_task)
            task_names.append("coinbase")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 解析结果
        new_df = None
        new_lower_df = None
        new_htf_df = None
        new_coinbase_df = None

        for i, name in enumerate(task_names):
            result = results[i] if not isinstance(results[i], Exception) else None
            if name == "main":
                new_df = result
            elif name == "lower":
                new_lower_df = result
            elif name == "htf":
                new_htf_df = result
                if result is not None:
                    state.last_htf_fetch_time = time_mod.time()
            elif name == "coinbase":
                new_coinbase_df = result

        # 缓存Coinbase数据
        if need_coinbase and new_coinbase_df is not None:
            state.cached_coinbase_df = new_coinbase_df

        if new_df is None:
            return state.cached_df, state.cached_lower_df, state.cached_htf_df

        merged_df = self._merge_frames(state.cached_df, new_df, MAX_CACHE_SIZE)

        # 只有A股需要合并lower_df
        if need_lower_tf:
            max_lower_size = MAX_CACHE_SIZE * ratio + 50
            merged_lower_df = self._merge_frames(
                state.cached_lower_df, new_lower_df, max_lower_size
            )
        else:
            merged_lower_df = None

        if new_htf_df is not None:
            merged_htf_df = self._merge_frames(state.cached_htf_df, new_htf_df, 200)
        else:
            merged_htf_df = state.cached_htf_df

        state.cached_df = merged_df
        state.cached_lower_df = merged_lower_df
        state.cached_htf_df = merged_htf_df

        logging.debug(
            f"[{symbol}] 增量更新: 主周期 {len(merged_df) if merged_df is not None else 0}, "
            f"低周期 {len(merged_lower_df) if merged_lower_df is not None else 0}, "
            f"高周期 {len(merged_htf_df) if merged_htf_df is not None else 0}"
        )

        return merged_df, merged_lower_df, merged_htf_df

    def calculate_indicators(self, df, lower_df=None):
        """计算所有技术指标"""
        if df is None or df.empty:
            return df

        df = df.copy()

        # 0. 将低周期成交量聚合到主周期，用于上下行量
        # 优化: 使用 numpy 向量化操作替代逐行判断
        if lower_df is not None and not lower_df.empty:
            ldf = lower_df.copy()
            close_vals = ldf["close"].to_numpy()
            open_vals = ldf["open"].to_numpy()
            vol_vals = ldf["volume"].to_numpy()

            # 向量化计算上下行量
            # 对齐 Pine: close == open (Doji) 不计入任一方向，避免系统性偏向 down_vol
            is_up = close_vals > open_vals
            is_down = close_vals < open_vals
            ldf["up_vol"] = np.where(is_up, vol_vals, 0)
            ldf["down_vol"] = np.where(is_down, vol_vals, 0)
            ldf["bucket"] = ldf["timestamp"].dt.floor(self._pandas_freq(TIMEFRAME))

            # groupby 聚合（这一步无法完全避免，但数据量已通过增量更新控制）
            vol_agg = ldf.groupby("bucket")[["up_vol", "down_vol"]].sum()
            df = df.merge(vol_agg, left_on="timestamp", right_index=True, how="left")
        else:
            df["up_vol"] = np.nan
            df["down_vol"] = np.nan

        # Pivot Points (震荡结构) - 向量化优化
        # 使用滚动窗口计算，避免 Python 循环
        pivot_len = self.pivot_len
        n = len(df)

        high_vals = df["high"].to_numpy()
        low_vals = df["low"].to_numpy()

        is_pivot_high = np.zeros(n, dtype=bool)
        is_pivot_low = np.zeros(n, dtype=bool)

        # 使用滚动最大/最小值进行向量化判断
        # 对于 pivot high: high[i] > max(high[i-pivot_len:i]) and high[i] > max(high[i+1:i+pivot_len+1])
        # 等价于: high[i] == max(high[i-pivot_len:i+pivot_len+1])

        window_size = 2 * pivot_len + 1
        if n >= window_size:
            # 注意：需要检查严格大于（不是等于）左右两侧
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

        df["is_pivot_high"] = is_pivot_high
        df["is_pivot_low"] = is_pivot_low

        return df

    def calculate_macd_indicators(self, df):
        """
        计算 MACD 相关指标（仅在 15 分钟 K 线收盘确认时调用）
        包括: MACD, ATR, DIF斜率, 分位数分级
        """
        if df is None or df.empty:
            return df

        df = df.copy()

        # 1. MACD (1h) - 用于共振策略
        # 注意: 用户策略中使用 SMA 计算 Signal 线
        macd_series, signal_series, hist_series = self._macd_with_sma_signal(
            df["close"]
        )
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

    def update_swing_levels(self, df, state: SymbolState):
        """
        更新 Swing 高低点（支撑/阻力位）

        优化: 使用向量化操作预计算 cummax/cummin，避免对每个 level 逐一扫描后续K线
        复杂度从 O(levels × bars) 降低到 O(bars + levels)
        """
        state.swing_levels = []
        pivot_len = self.pivot_len
        last_idx = len(df) - 1
        start_idx = pivot_len
        if self.hide_expired_levels:
            start_idx = max(start_idx, last_idx - self.expiry_bars)

        # 向量化提取 pivot 点（避免逐行 df.loc 访问）
        pivot_high_mask = df["is_pivot_high"].to_numpy()
        pivot_low_mask = df["is_pivot_low"].to_numpy()
        high_vals = df["high"].to_numpy()
        low_vals = df["low"].to_numpy()
        timestamps = df["timestamp"].to_numpy()

        end_scan_idx = len(df) - pivot_len
        for i in range(start_idx, end_scan_idx):
            if pivot_high_mask[i]:
                state.swing_levels.append(
                    {
                        "type": "high",
                        "price": high_vals[i],
                        "created_at": timestamps[i],
                        "created_idx": i,
                        "mitigated": False,
                        "mitigated_at": None,
                    }
                )

            if pivot_low_mask[i]:
                state.swing_levels.append(
                    {
                        "type": "low",
                        "price": low_vals[i],
                        "created_at": timestamps[i],
                        "created_idx": i,
                        "mitigated": False,
                        "mitigated_at": None,
                    }
                )

        # 检查 Mitigation - 优化版本
        # 注意：只检查已收盘的K线，排除最后一根未收盘的K线
        # 这确保 Swing High/Low 被扫掉需要1小时收盘确认

        # 预计算: 从每个位置开始到 last_idx-1 的 cummax/cummin
        # 我们需要知道从 idx+1 到 last_idx-1 范围内的最高价和最低价首次触及某水平的位置
        # 使用前缀最大值数组: prefix_max[i] = max(high[0:i+1])
        # 那么 max(high[a:b]) = 需要用 segment tree 或其他结构，这里用更简单的方法

        # 简化优化: 预计算从每个位置到 end 的 running max/min 及首次触及索引
        n = len(df)
        check_end = last_idx  # 不包含 last_idx（当前未收盘K线）

        # 对于 high levels: 需要找从 created_idx+1 开始，第一个 high >= price 的位置
        # 对于 low levels: 需要找从 created_idx+1 开始，第一个 low <= price 的位置
        # 优化: 只在有 levels 时才计算

        active_levels = []
        if not state.swing_levels:
            return

        # 按 created_idx 分组处理，使用二分查找优化
        # 但更简单的优化是：预计算从每个点向后的 cummax high 和 cummin low
        # suffix_max_high[i] = max(high[i:check_end])
        # suffix_min_low[i] = min(low[i:check_end])
        # 如果 suffix_max_high[created_idx+1] < price，则永远不会触及

        # 预计算 suffix max/min（从后往前扫描一次，O(n)）
        # 用于快速判断某个 level 是否可能被触及
        suffix_max_high = np.empty(n, dtype=np.float64)
        suffix_min_low = np.empty(n, dtype=np.float64)

        # 从 check_end-1 往前计算
        if check_end > 0:
            suffix_max_high[check_end - 1] = high_vals[check_end - 1]
            suffix_min_low[check_end - 1] = low_vals[check_end - 1]

            for i in range(check_end - 2, -1, -1):
                suffix_max_high[i] = max(high_vals[i], suffix_max_high[i + 1])
                suffix_min_low[i] = min(low_vals[i], suffix_min_low[i + 1])

        for level in state.swing_levels:
            age = last_idx - level["created_idx"]
            if self.hide_expired_levels and age > self.expiry_bars:
                continue

            start_check_idx = level["created_idx"] + 1

            if start_check_idx >= check_end:
                # 还没有足够的收盘K线来判断 mitigation
                active_levels.append(level)
                continue

            price = level["price"]

            if level["type"] == "high":
                # 检查从 start_check_idx 到 check_end-1 是否有 high >= price
                if suffix_max_high[start_check_idx] >= price:
                    # 需要找第一个触及的位置（线性扫描，但通常很快就能找到）
                    for touch_idx in range(start_check_idx, check_end):
                        if high_vals[touch_idx] >= price:
                            level["mitigated"] = True
                            level["mitigated_at"] = touch_idx
                            level["mitigated_at_ts"] = timestamps[touch_idx]
                            break

            elif level["type"] == "low":
                # 检查从 start_check_idx 到 check_end-1 是否有 low <= price
                if suffix_min_low[start_check_idx] <= price:
                    for touch_idx in range(start_check_idx, check_end):
                        if low_vals[touch_idx] <= price:
                            level["mitigated"] = True
                            level["mitigated_at"] = touch_idx
                            level["mitigated_at_ts"] = timestamps[touch_idx]
                            break

            active_levels.append(level)

        state.swing_levels = sorted(active_levels, key=lambda x: x["created_at"])

    def calculate_htf_indicators(self, df):
        """计算高周期 (4h) 指标: MACD, Signal Slope, Histogram Color"""
        if df is None or len(df) < 50:
            return None

        df = df.copy()

        # MACD 12, 26, 9
        # 注意: 用户策略中使用 SMA 计算 Signal 线
        macd_series, signal_series, hist_series = self._macd_with_sma_signal(
            df["close"]
        )
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

    def _find_recent_wicked_level(
        self, state: SymbolState, last_idx: int, level_type: str
    ):
        """
        在 liquidity_lookback 窗口内查找最近被扫荡的 swing level

        Args:
            state: SymbolState
            last_idx: 当前 K 线索引
            level_type: 'high' 或 'low'

        Returns:
            (bars_since, level_price) 或 (None, None)
        """
        candidates = []
        for lvl in state.swing_levels:
            if lvl["type"] != level_type:
                continue
            mitigated_at = lvl.get("mitigated_at")
            if mitigated_at is None:
                continue
            bars_since = last_idx - mitigated_at
            if 0 <= bars_since <= self.liquidity_lookback:
                candidates.append((bars_since, mitigated_at, lvl["price"]))

        if not candidates:
            return None, None

        # 取 mitigated_at 最大的（即最近被扫荡的）
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0], candidates[0][2]

    def _update_swing_levels_1h(self, htf_df, state: SymbolState):
        """
        更新 A股 1h Swing 高低点（用于CISD的strong信号判断）

        与 update_swing_levels 类似，但使用 state.swing_levels_1h
        """
        state.swing_levels_1h = []
        pivot_len = self.pivot_len
        last_idx = len(htf_df) - 1
        start_idx = pivot_len
        if self.hide_expired_levels:
            start_idx = max(start_idx, last_idx - self.expiry_bars)

        # 先计算 pivot 点
        htf_df = htf_df.copy()
        n = len(htf_df)
        high_vals = htf_df["high"].to_numpy()
        low_vals = htf_df["low"].to_numpy()
        timestamps = htf_df["timestamp"].to_numpy()

        is_pivot_high = np.zeros(n, dtype=bool)
        is_pivot_low = np.zeros(n, dtype=bool)

        window_size = 2 * pivot_len + 1
        if n >= window_size:
            for i in range(pivot_len, n - pivot_len):
                left_max = high_vals[i - pivot_len : i].max()
                right_max = high_vals[i + 1 : i + pivot_len + 1].max()
                if high_vals[i] > left_max and high_vals[i] > right_max:
                    is_pivot_high[i] = True

                left_min = low_vals[i - pivot_len : i].min()
                right_min = low_vals[i + 1 : i + pivot_len + 1].min()
                if low_vals[i] < left_min and low_vals[i] < right_min:
                    is_pivot_low[i] = True

        end_scan_idx = len(htf_df) - pivot_len
        for i in range(start_idx, end_scan_idx):
            if is_pivot_high[i]:
                state.swing_levels_1h.append(
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
                state.swing_levels_1h.append(
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
        check_end = last_idx
        if check_end > 0:
            suffix_max_high = np.empty(n, dtype=np.float64)
            suffix_min_low = np.empty(n, dtype=np.float64)

            suffix_max_high[check_end - 1] = high_vals[check_end - 1]
            suffix_min_low[check_end - 1] = low_vals[check_end - 1]

            for i in range(check_end - 2, -1, -1):
                suffix_max_high[i] = max(high_vals[i], suffix_max_high[i + 1])
                suffix_min_low[i] = min(low_vals[i], suffix_min_low[i + 1])

            for level in state.swing_levels_1h:
                age = last_idx - level["created_idx"]
                if self.hide_expired_levels and age > self.expiry_bars:
                    continue

                start_check_idx = level["created_idx"] + 1
                if start_check_idx >= check_end:
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

        state.swing_levels_1h = sorted(
            state.swing_levels_1h, key=lambda x: x["created_at"]
        )

    def _find_recent_wicked_level_1h(
        self, state: SymbolState, last_idx: int, level_type: str
    ):
        """
        在 1h swing levels 的 liquidity_lookback 窗口内查找最近被扫荡的 swing level

        与 _find_recent_wicked_level 类似，但使用 state.swing_levels_1h
        """
        candidates = []
        for lvl in state.swing_levels_1h:
            if lvl["type"] != level_type:
                continue
            mitigated_at = lvl.get("mitigated_at")
            if mitigated_at is None:
                continue
            bars_since = last_idx - mitigated_at
            if 0 <= bars_since <= self.liquidity_lookback:
                candidates.append((bars_since, mitigated_at, lvl["price"]))

        if not candidates:
            return None, None

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0], candidates[0][2]

    def detect_cisd(self, df):
        """
        检测 CISD 信号

        优化: 使用 numpy 数组替代 DataFrame 访问，deque 替代 list 实现 O(1) 头部操作
        """
        from collections import deque

        n = len(df)
        cisd_flag = [0] * n
        origin_level = [None] * n
        origin_idx = [None] * n
        close_vals = df["close"].to_numpy()
        open_vals = df["open"].to_numpy()

        # 使用 deque 实现 O(1) 的头部操作
        # 每个候选: (cand_open, cand_idx, running_max/min)
        bear_potential = deque()
        bull_potential = deque()

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
                    if ratio > self.cisd_tolerance:
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
                    if ratio > self.cisd_tolerance:
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

    def check_macd_resonance(self, df, htf_df, symbol: str | None = None):
        """
        检查 MACD 1h/4h 共振

        金叉共振条件（必须同时满足）：
        1. 1h 和 4h 都处于多头状态（MACD > Signal）
        2. 至少有一个周期发生了金叉（MACD 上穿 Signal）

        死叉共振条件（必须同时满足）：
        1. 1h 和 4h 都处于空头状态（MACD < Signal）
        2. 至少有一个周期发生了死叉（MACD 下穿 Signal）

        返回: (共振类型, 详细信息dict, 1h时间戳)
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
            dif_slope_grade_1h = (
                int(fallback_grade) if not pd.isna(fallback_grade) else 0
            )

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
        cross_1h_time, cross_1h_is_golden = self._find_last_cross_info(
            df, "MACD_12_26_9", "MACDs_12_26_9"
        )

        # 查找4h最近的交叉时间及方向
        cross_4h_time, cross_4h_is_golden = self._find_last_cross_info(
            htf_df, "MACD", "Signal"
        )

        if (
            cross_1h_time is not None
            and cross_4h_time is not None
            and cross_1h_is_golden == cross_4h_is_golden == is_golden
        ):
            time_diff = abs((cross_1h_time - cross_4h_time).total_seconds())
            cross_time_gap_hours = time_diff / 3600
            max_gap_hours = self._timeframe_to_minutes(self.htf_timeframe) / 60.0
            if cross_time_gap_hours > max_gap_hours:
                cross_time_gap_hours = None
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

    def _find_last_cross_time(
        self, df, macd_col: str, signal_col: str, find_golden: bool
    ):
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

    def _find_last_cross_info(self, df, macd_col: str, signal_col: str):
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

    async def analyze_market(
        self,
        symbol: str,
        state: SymbolState,
        df,
        htf_df=None,
        lower_df=None,
        display_name: str | None = None,
    ):
        """分析指定标的的市场状况"""
        # 使用显示名称（股票名称）或回退到代号
        name = display_name or symbol
        if df is None or df.empty:
            return None

        market_type = detect_market_type(symbol)

        # 计算 4h 指标 (如果此函数被jobs调用时传入了htf_df，则在此处计算指标)
        if htf_df is not None:
            htf_df = self.calculate_htf_indicators(htf_df)
            # 注意: MACD 相关计算已移至下方 15 分钟检测逻辑，避免每分钟重复计算

        last_idx = len(df) - 1
        if last_idx < 1:
            return None

        last_candle = df.iloc[-1]
        current_price = last_candle["close"]
        current_ts = last_candle["timestamp"]

        # 更新 Pivot 数据库
        # A股：CISD基于1h数据，使用 htf_df 和 swing_levels_1h
        # 加密货币：CISD基于1h数据，使用 df 和 swing_levels
        self.update_swing_levels(df, state)

        # A股CISD检测使用1h数据（htf_df），加密货币使用主周期数据（df）
        if market_type == MarketType.A_SHARE and htf_df is not None and len(htf_df) > 1:
            # A股：使用1h数据进行CISD检测
            cisd_df = htf_df
            # 更新1h swing levels（用于A股CISD的strong信号判断）
            self._update_swing_levels_1h(htf_df, state)
            cisd_last_idx = len(htf_df) - 1
            cisd_last_candle = htf_df.iloc[-1]
            cisd_current_ts = cisd_last_candle["timestamp"]
        else:
            # 加密货币：使用主周期数据
            cisd_df = df
            cisd_last_idx = last_idx
            cisd_current_ts = current_ts

        cisd_result = self.detect_cisd(cisd_df)

        msgs = []

        # --- 1. CISD 策略: Swing High/Low Mitigation Alerts ---
        # 注意：由于 mitigation 只检查已收盘的K线，需要检查上一根收盘K线
        prev_closed_idx = last_idx - 1
        for level in state.swing_levels:
            if level.get("mitigated_at") == prev_closed_idx:
                mitigated_ts = level.get("mitigated_at_ts")
                key = (level["type"], round(level["price"], 4), mitigated_ts)
                if key not in state.notified_sweeps:
                    if level["type"] == "high":
                        msgs.append(
                            (
                                AlertMessages.TYPE_SWING_HIGH_MITIGATION,
                                AlertMessages.swing_high_mitigation(
                                    name, current_price, level["price"]
                                ),
                            )
                        )
                    else:
                        msgs.append(
                            (
                                AlertMessages.TYPE_SWING_LOW_MITIGATION,
                                AlertMessages.swing_low_mitigation(
                                    name, current_price, level["price"]
                                ),
                            )
                        )
                    state.notified_sweeps.add(key)

        # --- 2. CISD 策略: Normal/Strong CISD Alerts ---
        # 使用辅助方法在 liquidity_lookback 窗口内查找最近被扫荡的 swing level
        # A股使用1h swing levels，加密货币使用主周期 swing levels
        if market_type == MarketType.A_SHARE:
            bars_since_high, wicked_high_level = self._find_recent_wicked_level_1h(
                state, cisd_last_idx, "high"
            )
            bars_since_low, wicked_low_level = self._find_recent_wicked_level_1h(
                state, cisd_last_idx, "low"
            )
        else:
            bars_since_high, wicked_high_level = self._find_recent_wicked_level(
                state, cisd_last_idx, "high"
            )
            bars_since_low, wicked_low_level = self._find_recent_wicked_level(
                state, cisd_last_idx, "low"
            )

        if cisd_result["flag_at_last"] != 0:
            # 只有当当前信号的时间戳晚于上一次记录的时间戳时才处理
            # cisd_result['flag_at_last'] 对应的是 cisd_last_idx 的信号，即 cisd_current_ts
            if state.last_cisd_ts is None or cisd_current_ts > state.last_cisd_ts:
                origin_level = cisd_result["origin_level_at_last"]
                if origin_level is None or (
                    isinstance(origin_level, float) and math.isnan(origin_level)
                ):
                    state.last_cisd_ts = cisd_current_ts
                else:
                    if cisd_result["flag_at_last"] == 1:
                        # 看跌 CISD：检查是否有高点扫荡且价格低于被扫荡水平
                        if (
                            bars_since_high is not None
                            and wicked_high_level is not None
                            and current_price < wicked_high_level
                        ):
                            alert_type = AlertMessages.TYPE_BEARISH_STRONG_CISD
                            alert_msg = AlertMessages.bearish_strong_cisd(
                                name,
                                current_price,
                                origin_level,
                                wicked_high_level,
                                bars_since_high,
                            )
                        else:
                            alert_type = AlertMessages.TYPE_BEARISH_NORMAL_CISD
                            alert_msg = AlertMessages.bearish_normal_cisd(
                                name, current_price, origin_level
                            )
                    else:
                        # 看涨 CISD：检查是否有低点扫荡且价格高于被扫荡水平
                        if (
                            bars_since_low is not None
                            and wicked_low_level is not None
                            and current_price > wicked_low_level
                        ):
                            alert_type = AlertMessages.TYPE_BULLISH_STRONG_CISD
                            alert_msg = AlertMessages.bullish_strong_cisd(
                                name,
                                current_price,
                                origin_level,
                                wicked_low_level,
                                bars_since_low,
                            )
                        else:
                            alert_type = AlertMessages.TYPE_BULLISH_NORMAL_CISD
                            alert_msg = AlertMessages.bullish_normal_cisd(
                                name, current_price, origin_level
                            )

                    if state.should_send_cisd_origin_alert(
                        cisd_result["flag_at_last"], origin_level, alert_type
                    ):
                        msgs.append((alert_type, alert_msg))

                    state.last_cisd_ts = cisd_current_ts

        # --- 5. MACD 共振策略 ---
        # 仅当 htf_df 可用时检测
        # 检测频率：每当新的15分钟K线收盘时检测
        if htf_df is not None:
            # 根据市场类型选择时区
            market_type = detect_market_type(symbol)
            if market_type == MarketType.A_SHARE:
                from datetime import datetime
                from zoneinfo import ZoneInfo

                now_ts = pd.Timestamp(
                    datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
                )
            else:
                now_ts = pd.Timestamp.utcnow().tz_localize(None)

            current_15m_ts = now_ts.floor("15min")

            # 检查是否是新的15分钟K线
            is_new_15m_bar = (
                state.last_macd_check_15m_ts is None
                or current_15m_ts > state.last_macd_check_15m_ts
            )

            # A股开盘冷却期检查：跳过开盘后前N根K线
            skip_macd_alert = False
            if market_type == MarketType.A_SHARE and is_new_15m_bar:
                from datetime import time as dt_time, datetime as dt_datetime, timedelta

                current_time = now_ts.time()
                # 上午开盘 9:30，下午开盘 13:00
                morning_open = dt_time(9, 30)
                afternoon_open = dt_time(13, 0)
                # 冷却期结束时间（开盘后 N 根 15m K线）
                cooldown_minutes = ASHARE_OPEN_COOLDOWN_BARS * 15
                # 使用 timedelta 正确计算时间
                morning_cooldown_end = (
                    dt_datetime.combine(dt_datetime.today(), morning_open)
                    + timedelta(minutes=cooldown_minutes)
                ).time()
                afternoon_cooldown_end = (
                    dt_datetime.combine(dt_datetime.today(), afternoon_open)
                    + timedelta(minutes=cooldown_minutes)
                ).time()

                if (
                    morning_open <= current_time < morning_cooldown_end
                    or afternoon_open <= current_time < afternoon_cooldown_end
                ):
                    skip_macd_alert = True
                    logging.debug(
                        f"[{symbol}] A股开盘冷却期，跳过 MACD 共振检测 "
                        f"(当前时间: {current_time})"
                    )

            if is_new_15m_bar and not skip_macd_alert:
                # 只有15分钟K线收盘时才计算 MACD 相关指标
                df_with_macd = self.calculate_macd_indicators(df)

                res_val, res_info, res_ts = self.check_macd_resonance(
                    df_with_macd, htf_df, symbol
                )

                # 使用 K线时间戳去重，确保同一根K线只触发一次
                if res_val != 0 and res_ts is not None:
                    # 检查是否是新的1h K线（时间戳不同于上次触发）
                    is_new_bar = (
                        state.last_macd_resonance_ts is None
                        or res_ts > state.last_macd_resonance_ts
                    )

                    # A股日内 MACD 仅一次提醒
                    allow_macd_alert = True
                    if market_type == MarketType.A_SHARE:
                        today_str = now_ts.strftime("%Y-%m-%d")
                        if state.last_macd_alert_date == today_str:
                            allow_macd_alert = False
                        else:
                            state.last_macd_alert_date = today_str

                    if is_new_bar and allow_macd_alert:
                        if res_val == 1:
                            msgs.append(
                                (
                                    AlertMessages.TYPE_MACD_RESONANCE_GOLDEN,
                                    AlertMessages.macd_resonance_golden(
                                        name, current_price, res_info
                                    ),
                                )
                            )
                        elif res_val == -1:
                            msgs.append(
                                (
                                    AlertMessages.TYPE_MACD_RESONANCE_DEATH,
                                    AlertMessages.macd_resonance_death(
                                        name, current_price, res_info
                                    ),
                                )
                            )
                    # 记录本次触发的时间戳（无论是否发送都更新，避免重复检测）
                    state.last_macd_resonance_ts = res_ts

            # 更新已检测的15分钟K线时间戳（无论是否跳过检测都要更新）
            if is_new_15m_bar:
                state.last_macd_check_15m_ts = current_15m_ts

        # --- 6. MA5/MA10 状态机检测 (A股专属，基于日线均线) ---
        ma_alerts = await self.check_ma_alerts(symbol, current_price, state, name)
        msgs.extend(ma_alerts)

        # --- 7. 计算当前最近的支撑/阻力 ---
        if self.hide_mitigated_levels:
            active_highs = [
                x["price"]
                for x in state.swing_levels
                if not x["mitigated"] and x["type"] == "high"
            ]
            active_lows = [
                x["price"]
                for x in state.swing_levels
                if not x["mitigated"] and x["type"] == "low"
            ]
        else:
            active_highs = [
                x["price"] for x in state.swing_levels if x["type"] == "high"
            ]
            active_lows = [x["price"] for x in state.swing_levels if x["type"] == "low"]

        nearest_res = min([x for x in active_highs if x > current_price], default=None)
        nearest_sup = max([x for x in active_lows if x < current_price], default=None)

        sr_break_alert = self.check_sr_breakout_vol(
            symbol, state, df, lower_df, nearest_res, nearest_sup, name
        )
        if sr_break_alert is not None:
            msgs.append(sr_break_alert)

        rvol_15m = self._compute_current_rvol(symbol, lower_df)

        result = {
            "symbol": symbol,
            "price": current_price,
            "trend_dir": None,
            "trend_support": None,
            "nearest_res": nearest_res,
            "nearest_sup": nearest_sup,
            "alerts": msgs,
            "last_bar_idx": last_idx,
            "rvol_15m": rvol_15m,
        }
        state.last_analysis = result
        return result

    async def check_ma_alerts(
        self,
        symbol: str,
        current_price: float,
        state: SymbolState,
        display_name: str | None = None,
    ) -> list[tuple[str, str]]:
        """
        A股 MA5/MA10 状态机检测（基于日线均线，15m 收盘驱动）

        修正：使用日线数据计算 MA5/MA10（5日/10日均线），而非 15m K 线
        盘中动态计算：使用前 N-1 日收盘价 + 当日实时价格计算动态 MA

        - 跌破条件: close < MA * (1 - break_pct/100)
        - 站上条件: close >= MA
        - 状态机保证：跌破后不重复提醒，站上后再跌破才提醒
        """
        name = display_name or symbol
        market_type = detect_market_type(symbol)
        if market_type != MarketType.A_SHARE:
            return []

        from datetime import datetime, time as dt_time, timedelta
        from zoneinfo import ZoneInfo

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
        cooldown_minutes = ASHARE_OPEN_COOLDOWN_BARS * 15
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
        required_days = max(ASHARE_MA_PERIOD, ASHARE_MA10_PERIOD) + 5
        daily_df = await self.akshare_provider.fetch_ohlcv(
            symbol, "1d", limit=required_days
        )

        if daily_df is None or len(daily_df) < ASHARE_MA_PERIOD:
            logging.debug(
                f"[{symbol}] 日线数据不足，无法计算 MA (需要 {ASHARE_MA_PERIOD} 根)"
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

        if len(historical_closes) >= ASHARE_MA_PERIOD:
            ma5_value = sum(historical_closes[-ASHARE_MA_PERIOD:]) / ASHARE_MA_PERIOD

        if len(historical_closes) >= ASHARE_MA10_PERIOD:
            ma10_value = sum(historical_closes[-ASHARE_MA10_PERIOD:]) / ASHARE_MA10_PERIOD

        msgs: list[tuple[str, str]] = []

        if ma5_value is not None:
            break_threshold_ma5 = ma5_value * (1 - ASHARE_MA5_BREAK_PCT / 100)
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
            break_threshold_ma10 = ma10_value * (1 - ASHARE_MA10_BREAK_PCT / 100)
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

    def _compute_current_rvol(self, symbol: str, lower_df) -> float | None:
        """计算当前15m RVOL（用于状态显示，保留兼容）"""
        if symbol not in SR_BREAKOUT_SYMBOLS:
            return None
        if lower_df is None or len(lower_df) < 3:
            return None

        market_type = detect_market_type(symbol)
        rvol_n = RVOL_N_ASHARE if market_type == MarketType.A_SHARE else RVOL_N_CRYPTO

        if len(lower_df) < rvol_n + 2:
            return None

        ldf = lower_df.copy()
        ldf["vol_sma"] = ldf["volume"].rolling(rvol_n).mean()
        last_bar = ldf.iloc[-2]
        vol_sma = last_bar["vol_sma"]
        if pd.isna(vol_sma) or vol_sma == 0:
            return None
        return last_bar["volume"] / vol_sma

    def _aggregate_ashare_15m_to_1h(self, lower_df) -> pd.DataFrame | None:
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

        from datetime import time as dt_time

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

    def _get_elapsed_fraction_1h(self, symbol: str) -> float:
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
            from datetime import datetime, time as dt_time
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

    async def _fetch_coinbase_1h_data(self, symbol: str) -> pd.DataFrame | None:
        """
        获取Coinbase BTC/USD的1h数据用于RVOL计算

        对于加密货币标的，使用Coinbase BTC/USD的成交量作为RVOL计算基准
        """
        # 将symbol映射到Coinbase交易对
        # BTC/USDT -> BTC/USD
        base_currency = symbol.split("/")[0] if "/" in symbol else symbol
        coinbase_symbol = f"{base_currency}/USD"

        try:
            df = await self.coinbase_provider.fetch_ohlcv(
                coinbase_symbol, "1h", RVOL_N_CRYPTO_1H + 10
            )
            return df
        except Exception as e:
            logging.warning(f"[{symbol}] 获取Coinbase {coinbase_symbol} 数据失败: {e}")
            return None

    async def fetch_spot_premium(self, symbol: str) -> dict | None:
        """
        计算现货溢价: (Coinbase BTC/USD - OKX BTC/USDT) / Coinbase BTC/USD

        返回:
            {
                "coinbase_price": float,  # Coinbase BTC/USD 价格
                "okx_price": float,        # OKX BTC/USDT 价格
                "premium_pct": float,      # 溢价百分比
                "coinbase_symbol": str,    # Coinbase交易对
                "okx_symbol": str,         # OKX交易对
            }
        """
        market_type = detect_market_type(symbol)
        if market_type != MarketType.CRYPTO:
            return None

        # 将symbol映射到对应交易对
        base_currency = symbol.split("/")[0] if "/" in symbol else symbol
        coinbase_symbol = f"{base_currency}/USD"
        okx_symbol = f"{base_currency}/USDT"

        try:
            # 并发获取两个交易所的ticker
            coinbase_ticker, okx_ticker = await asyncio.gather(
                self.coinbase_provider.fetch_ticker(coinbase_symbol),
                self.okx_provider.fetch_ticker(okx_symbol),
                return_exceptions=True,
            )

            if isinstance(coinbase_ticker, Exception) or coinbase_ticker is None:
                logging.warning(f"[{symbol}] 获取Coinbase {coinbase_symbol} ticker失败")
                return None

            if isinstance(okx_ticker, Exception) or okx_ticker is None:
                logging.warning(f"[{symbol}] 获取OKX {okx_symbol} ticker失败")
                return None

            coinbase_price = coinbase_ticker.get("last")
            okx_price = okx_ticker.get("last")

            if coinbase_price is None or okx_price is None:
                return None

            if coinbase_price == 0:
                return None

            premium_pct = (coinbase_price - okx_price) / coinbase_price * 100

            return {
                "coinbase_price": coinbase_price,
                "okx_price": okx_price,
                "premium_pct": premium_pct,
                "coinbase_symbol": coinbase_symbol,
                "okx_symbol": okx_symbol,
            }
        except Exception as e:
            logging.exception(f"[{symbol}] 计算现货溢价失败: {e}")
            return None

    def _compute_rvol_est_1h(
        self, symbol: str, df_1h, current_1h_volume: float | None = None
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
        rvol_n = (
            RVOL_N_ASHARE_1H if market_type == MarketType.A_SHARE else RVOL_N_CRYPTO_1H
        )

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
        elapsed_fraction = self._get_elapsed_fraction_1h(symbol)

        # 计算估算RVOL
        expected_volume = vol_sma * elapsed_fraction
        if expected_volume == 0:
            return None

        return current_1h_volume / expected_volume

    def check_sr_breakout_vol(
        self,
        symbol: str,
        state: SymbolState,
        df,
        lower_df,
        nearest_res: float | None,
        nearest_sup: float | None,
        display_name: str | None = None,
    ):
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
            df_1h = self._aggregate_ashare_15m_to_1h(lower_df)
            if df_1h is None or len(df_1h) < RVOL_N_ASHARE_1H + 1:
                # 更新tick状态但不触发alert
                state.last_sr_break_tick_ts = current_15m_ts
                state.last_sr_break_tick_price = current_price
                return None
        else:
            # 加密货币：优先使用Coinbase BTC/USD数据计算RVOL
            df_1h = (
                state.cached_coinbase_df if state.cached_coinbase_df is not None else df
            )
            if df_1h is None or len(df_1h) < RVOL_N_CRYPTO_1H + 1:
                state.last_sr_break_tick_ts = current_15m_ts
                state.last_sr_break_tick_price = current_price
                return None

        # --- 计算1h RVOL ---
        rvol_est = self._compute_rvol_est_1h(symbol, df_1h)

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

    async def close_exchange(self):
        """关闭交易所连接"""
        await self.crypto_provider.close()
        await self.coinbase_provider.close()
        await self.okx_provider.close()


# 全局引擎实例
engine = StrategyEngine()
