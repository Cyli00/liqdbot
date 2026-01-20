"""
策略引擎模块 - 核心交易逻辑和数据分析
"""

import asyncio
import math
import re
import logging
import numpy as np
import pandas as pd

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
    AKSHARE_CISD_TOLERANCE,
    AKSHARE_LIQUIDITY_LOOKBACK,
    DEFAULT_AKSHARE_SYMBOLS,
    AKSHARE_TIMEFRAME,
    AKSHARE_HTF_TIMEFRAME,
    AKSHARE_LTF_TIMEFRAME,
    AKSHARE_LOWER_TIMEFRAME,
    AKSHARE_MA_PERIOD,
    AKSHARE_MA5_BREAK_PCT,
    AKSHARE_MA10_PERIOD,
    AKSHARE_MA10_BREAK_PCT,
    AKSHARE_OPEN_COOLDOWN_BARS,
    SR_BREAKOUT_SYMBOLS,
    RVOL_N_AKSHARE,
    RVOL_N_CRYPTO_1H,
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
from .tools import (
    detect_cisd,
    check_macd_resonance,
    check_ma_alerts,
    check_sr_breakout_vol,
    macd_with_sma_signal,
    calculate_macd_indicators,
    calculate_htf_indicators as calc_htf_indicators,
    calculate_pivot_points,
    calculate_up_down_volume,
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

        # A股专用参数
        self.AKSHARE_CISD_TOLERANCE = AKSHARE_CISD_TOLERANCE
        self.AKSHARE_LIQUIDITY_LOOKBACK = AKSHARE_LIQUIDITY_LOOKBACK

        self.htf_timeframe = "4h"  # 高周期固定为 4h

        # 如果有默认标的，自动添加
        if DEFAULT_SYMBOL:
            self.add_symbol(DEFAULT_SYMBOL)
            logging.info(f"初始化引擎: 默认监控 {DEFAULT_SYMBOL}")

        # 添加默认 A 股标的列表
        for akshare_symbol in DEFAULT_AKSHARE_SYMBOLS:
            self.add_symbol(akshare_symbol)
            logging.info(f"初始化引擎: 默认监控 A股 {akshare_symbol}")

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
                "main": AKSHARE_TIMEFRAME,
                "lower": AKSHARE_LOWER_TIMEFRAME,
                "htf": AKSHARE_HTF_TIMEFRAME,
                "ltf": AKSHARE_LTF_TIMEFRAME,
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

    def is_new_bar_closed(self, df, state: SymbolState, timeframe: str) -> bool:
        """
        检测指定时间周期的K线是否收盘（是否有新的K线）

        Args:
            df: K线数据
            state: 标的状态
            timeframe: 时间周期 ('15m', '1h', '4h')

        Returns:
            bool: True表示有新K线收盘，需要重新计算指标
        """
        if df is None or df.empty:
            return False

        # 获取最后一根K线的时间戳
        last_bar_ts = df.iloc[-1]["timestamp"]

        # 根据时间周期选择对应的状态字段
        if timeframe == "15m":
            last_recorded_ts = state.last_15m_bar_ts
        elif timeframe == "1h":
            last_recorded_ts = state.last_1h_bar_ts
        elif timeframe == "4h":
            last_recorded_ts = state.last_4h_bar_ts
        else:
            return True  # 未知周期，默认需要计算

        # 如果是首次检查或时间戳不同，说明有新K线
        is_new = last_recorded_ts is None or last_bar_ts != last_recorded_ts

        # 更新状态
        if is_new:
            if timeframe == "15m":
                state.last_15m_bar_ts = last_bar_ts
            elif timeframe == "1h":
                state.last_1h_bar_ts = last_bar_ts
            elif timeframe == "4h":
                state.last_4h_bar_ts = last_bar_ts

        return is_new

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
        return macd_with_sma_signal(series, fast, slow, signal)

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
        # A股不再需要单独的 htf 数据（CISD 改用 15m）
        # 加密货币仍使用 htf 用于其他分析
        if market_type == MarketType.A_SHARE:
            htf_task = provider.fetch_ohlcv(symbol, htf_tf, htf_limit)
        else:
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
                if isinstance(results[i], Exception):
                    logging.warning(
                        f"[{symbol}] Coinbase 数据获取失败，RVOL 计算将使用 Binance 1h 数据"
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
                if isinstance(results[i], Exception):
                    logging.debug(
                        f"[{symbol}] Coinbase 增量数据获取失败，RVOL 计算将使用缓存或 Binance 1h 数据"
                    )

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

    def calculate_indicators(self, df, lower_df=None, symbol: str | None = None, state: SymbolState | None = None):
        """计算所有技术指标

        Args:
            df: 主周期K线数据
            lower_df: 低周期K线数据（用于上下行量计算）
            symbol: 标的代码（用于获取正确的时间周期）
            state: 标的状态（用于检查是否需要重新计算）

        优化逻辑：
            - Pivot Points 和 Up/Down Volume 只在主周期K线收盘时重新计算
            - 对于加密货币（1h主周期），只在1h K线收盘时计算
            - 对于A股（15m主周期），只在15m K线收盘时计算
            - 如果没有新K线且df已有指标列，直接返回
        """
        if df is None or df.empty:
            return df

        # 检查是否需要重新计算（只在主周期K线收盘时）
        need_recalculate = True
        if state is not None and symbol is not None:
            # 根据市场类型确定主周期
            market_type = detect_market_type(symbol)
            if market_type == MarketType.A_SHARE:
                check_timeframe = "15m"
            else:
                check_timeframe = "1h"

            # 检查是否有新的主周期K线
            is_new_bar = self.is_new_bar_closed(df, state, check_timeframe)

            # 如果没有新K线，且df已经有指标列，就不需要重新计算
            has_indicators = all(col in df.columns for col in ["is_pivot_high", "is_pivot_low"])

            if not is_new_bar and has_indicators:
                need_recalculate = False
                logging.debug(f"[{symbol}] 跳过指标计算（无新{check_timeframe} K线）")

        if not need_recalculate:
            return df

        df = df.copy()

        # 0. 将低周期成交量聚合到主周期，用于上下行量
        # 根据标的类型获取正确的主周期频率
        if symbol:
            tfs = self.get_timeframes_for_symbol(symbol)
            main_tf_freq = self._pandas_freq(tfs["main"])
        else:
            main_tf_freq = self._pandas_freq(TIMEFRAME)
        df = calculate_up_down_volume(df, lower_df, main_tf_freq)

        # Pivot Points (震荡结构)
        is_pivot_high, is_pivot_low = calculate_pivot_points(df, self.pivot_len)
        df["is_pivot_high"] = is_pivot_high
        df["is_pivot_low"] = is_pivot_low

        return df

    def calculate_macd_indicators(self, df):
        """计算 MACD 相关指标（仅在 15 分钟 K 线收盘确认时调用）"""
        return calculate_macd_indicators(df)

    def update_swing_levels(self, df, state: SymbolState, symbol: str | None = None, force_update: bool = False):
        """
        更新 Swing 高低点（支撑/阻力位）

        优化: 使用向量化操作预计算 cummax/cummin，避免对每个 level 逐一扫描后续K线
        复杂度从 O(levels × bars) 降低到 O(bars + levels)

        Args:
            df: K线数据
            state: 标的状态
            symbol: 标的代码（用于确定市场类型）
            force_update: 是否强制更新（默认False，只在主周期K线收盘时更新）

        优化逻辑：
            - 只在主周期K线收盘时重新计算 swing levels
            - 对于加密货币（1h主周期），只在1h K线收盘时更新
            - 对于A股（15m主周期），只在15m K线收盘时更新
            - 如果没有新K线，直接返回（除非force_update=True）
        """
        # 检查是否需要更新（只在主周期K线收盘时）
        if not force_update and symbol is not None:
            # 根据市场类型确定主周期
            market_type = detect_market_type(symbol)
            if market_type == MarketType.A_SHARE:
                check_timeframe = "15m"
            else:
                check_timeframe = "1h"

            is_new_bar = self.is_new_bar_closed(df, state, check_timeframe)
            if not is_new_bar and state.swing_levels:
                # 没有新K线且已有swing_levels，跳过更新
                return

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
        # 这确保 Swing High/Low 被扫掉需要收盘确认

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
        return calc_htf_indicators(df)

    def _find_recent_wicked_level(
        self, state: SymbolState, last_idx: int, level_type: str, lookback: int = None
    ):
        """
        在 liquidity_lookback 窗口内查找最近被扫荡的 swing level

        Args:
            state: SymbolState
            last_idx: 当前 K 线索引
            level_type: 'high' 或 'low'
            lookback: 自定义回溯窗口，默认使用 self.liquidity_lookback

        Returns:
            (bars_since, level_price) 或 (None, None)
        """
        if lookback is None:
            lookback = self.liquidity_lookback

        candidates = []
        for lvl in state.swing_levels:
            if lvl["type"] != level_type:
                continue
            mitigated_at = lvl.get("mitigated_at")
            if mitigated_at is None:
                continue
            bars_since = last_idx - mitigated_at
            if 0 <= bars_since <= lookback:
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
        # 所有市场都更新主周期的 swing_levels（用于 CISD 和 mitigation alerts）
        self.update_swing_levels(df, state, symbol)

        # A股和加密货币都使用主周期数据（15m）进行 CISD 检测
        cisd_df = df
        cisd_last_idx = last_idx
        cisd_current_ts = current_ts

        # A股使用专用的 tolerance 参数
        if market_type == MarketType.A_SHARE:
            cisd_tolerance = self.AKSHARE_CISD_TOLERANCE
        else:
            cisd_tolerance = self.cisd_tolerance

        cisd_result = detect_cisd(cisd_df, cisd_tolerance=cisd_tolerance)

        msgs = []

        # --- 1. CISD 策略: Swing High/Low Mitigation Alerts ---
        # 注意：由于 mitigation 只检查已收盘的K线，需要检查上一根收盘K线
        prev_closed_idx = last_idx - 1
        max_notified_sweeps = 200
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
                    state.notified_sweeps[key] = True
                    try:
                        state.notified_sweeps.move_to_end(key)
                    except Exception:
                        pass
                    while len(state.notified_sweeps) > max_notified_sweeps:
                        state.notified_sweeps.popitem(last=False)

        # --- 2. CISD 策略: Normal/Strong CISD Alerts ---
        # 使用辅助方法在 liquidity_lookback 窗口内查找最近被扫荡的 swing level
        # A股和加密货币都使用主周期 swing levels
        # A股使用专用的 liquidity_lookback 参数
        if market_type == MarketType.A_SHARE:
            liquidity_lookback = self.AKSHARE_LIQUIDITY_LOOKBACK
        else:
            liquidity_lookback = self.liquidity_lookback

        bars_since_high, wicked_high_level = self._find_recent_wicked_level(
            state, cisd_last_idx, "high", lookback=liquidity_lookback
        )
        bars_since_low, wicked_low_level = self._find_recent_wicked_level(
            state, cisd_last_idx, "low", lookback=liquidity_lookback
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
                cooldown_minutes = AKSHARE_OPEN_COOLDOWN_BARS * 15
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

                tfs = self.get_timeframes_for_symbol(symbol)
                res_val, res_info, res_ts = check_macd_resonance(
                    df_with_macd,
                    htf_df,
                    symbol,
                    htf_timeframe=tfs["htf"],
                    ltf_timeframe=tfs.get("ltf"),
                )

                # A股使用连续确认机制：需要连续2根15m K线都检测到同类型共振才发送
                if market_type == MarketType.A_SHARE:
                    if res_val != 0 and res_ts is not None:
                        # 检测到共振
                        if state.pending_macd_resonance is not None:
                            # 有待确认的共振，检查是否是同类型
                            pending_val, pending_info, pending_ts, pending_15m_ts = (
                                state.pending_macd_resonance
                            )
                            # 检查是否是连续的15m K线（间隔应该是15分钟）
                            # 特殊处理：A股午休期间（11:30-13:00），11:30的K线和13:00的K线视为连续
                            time_diff = (
                                current_15m_ts - pending_15m_ts
                            ).total_seconds() / 60

                            # 正常连续：14-16分钟
                            is_normal_consecutive = 14 <= time_diff <= 16

                            # 午休跨越：11:30 -> 13:00 (90分钟间隔)
                            # 检查 pending 是否是 11:15-11:30 的K线，current 是否是 13:00-13:15 的K线
                            is_lunch_break_consecutive = False
                            if 89 <= time_diff <= 91:  # 约90分钟
                                pending_time = pending_15m_ts.time()
                                current_time_check = current_15m_ts.time()
                                from datetime import time as dt_time

                                # pending 应该是 11:15 (代表 11:15-11:30 这根K线)
                                # current 应该是 13:00 (代表 13:00-13:15 这根K线)
                                if dt_time(11, 0) <= pending_time <= dt_time(
                                    11, 30
                                ) and dt_time(13, 0) <= current_time_check <= dt_time(
                                    13, 15
                                ):
                                    is_lunch_break_consecutive = True

                            is_consecutive = (
                                is_normal_consecutive or is_lunch_break_consecutive
                            )

                            if pending_val == res_val and is_consecutive:
                                # 连续2根K线都是同类型共振，确认信号
                                # 检查是否是新的K线（时间戳不同于上次触发）
                                is_new_bar = (
                                    state.last_macd_resonance_ts is None
                                    or res_ts > state.last_macd_resonance_ts
                                )

                                # A股日内 MACD 仅一次提醒
                                allow_macd_alert = True
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
                                # 记录本次触发的时间戳
                                state.last_macd_resonance_ts = res_ts
                                # 清除待确认状态
                                state.pending_macd_resonance = None
                            else:
                                # 不是连续的或类型不同，更新待确认状态
                                state.pending_macd_resonance = (
                                    res_val,
                                    res_info,
                                    res_ts,
                                    current_15m_ts,
                                )
                        else:
                            # 没有待确认的共振，记录当前检测结果
                            state.pending_macd_resonance = (
                                res_val,
                                res_info,
                                res_ts,
                                current_15m_ts,
                            )
                    else:
                        # 没有检测到共振，清除待确认状态
                        state.pending_macd_resonance = None
                else:
                    # 加密货币：不使用连续确认，直接发送
                    # 使用 K线时间戳去重，确保同一根K线只触发一次
                    if res_val != 0 and res_ts is not None:
                        # 检查是否是新的1h K线（时间戳不同于上次触发）
                        is_new_bar = (
                            state.last_macd_resonance_ts is None
                            or res_ts > state.last_macd_resonance_ts
                        )

                        if is_new_bar:
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
        ma_alerts = await check_ma_alerts(
            symbol,
            current_price,
            state,
            name,
            akshare_provider=self.akshare_provider,
            ma5_period=AKSHARE_MA_PERIOD,
            ma5_break_pct=AKSHARE_MA5_BREAK_PCT,
            ma10_period=AKSHARE_MA10_PERIOD,
            ma10_break_pct=AKSHARE_MA10_BREAK_PCT,
            open_cooldown_bars=AKSHARE_OPEN_COOLDOWN_BARS,
        )
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

        sr_break_alert = check_sr_breakout_vol(
            symbol, state, df, lower_df, nearest_res, nearest_sup, name
        )
        if sr_break_alert is not None:
            msgs.append(sr_break_alert)

        rvol = self._compute_current_rvol(symbol, state, lower_df)

        result = {
            "symbol": symbol,
            "price": current_price,
            "trend_dir": None,
            "trend_support": None,
            "nearest_res": nearest_res,
            "nearest_sup": nearest_sup,
            "alerts": msgs,
            "last_bar_idx": last_idx,
            "rvol": rvol,
        }
        state.last_analysis = result
        return result

    def _compute_current_rvol(
        self, symbol: str, state: SymbolState, lower_df
    ) -> float | None:
        """计算当前 RVOL（用于状态显示）

        - A股：使用 lower_df（15m 数据）
        - 加密货币：使用 state.cached_coinbase_df（Coinbase 1h 数据）
        """
        if symbol not in SR_BREAKOUT_SYMBOLS:
            return None

        market_type = detect_market_type(symbol)

        if market_type == MarketType.A_SHARE:
            # A股：使用 15m 数据
            if lower_df is None or len(lower_df) < 3:
                return None
            rvol_n = RVOL_N_AKSHARE
            if len(lower_df) < rvol_n + 2:
                return None
            ldf = lower_df.copy()
            ldf["vol_sma"] = ldf["volume"].rolling(rvol_n).mean()
            last_bar = ldf.iloc[-2]
            vol_sma = last_bar["vol_sma"]
            if pd.isna(vol_sma) or vol_sma == 0:
                return None
            return last_bar["volume"] / vol_sma
        else:
            # 加密货币：使用 Coinbase 1h 数据
            coinbase_df = state.cached_coinbase_df
            if coinbase_df is None or len(coinbase_df) < 3:
                return None
            rvol_n = RVOL_N_CRYPTO_1H
            if len(coinbase_df) < rvol_n + 2:
                return None
            cdf = coinbase_df.copy()
            cdf["vol_sma"] = cdf["volume"].rolling(rvol_n).mean()
            last_bar = cdf.iloc[-2]
            vol_sma = last_bar["vol_sma"]
            if pd.isna(vol_sma) or vol_sma == 0:
                return None
            return last_bar["volume"] / vol_sma

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
        计算现货溢价: (Coinbase BTC/USD - USDT均价) / Coinbase BTC/USD

        USDT均价 = (OKX BTC/USDT + Binance BTC/USDT) / 2

        返回:
            {
                "coinbase_price": float,  # Coinbase BTC/USD 价格
                "usdt_avg_price": float,   # OKX + Binance 平均价格
                "okx_price": float,        # OKX BTC/USDT 价格
                "binance_price": float,    # Binance BTC/USDT 价格
                "premium_pct": float,      # 溢价百分比
                "coinbase_symbol": str,    # Coinbase交易对
                "usdt_symbol": str,        # USDT交易对
            }
        """
        market_type = detect_market_type(symbol)
        if market_type != MarketType.CRYPTO:
            return None

        # 将symbol映射到对应交易对
        base_currency = symbol.split("/")[0] if "/" in symbol else symbol
        coinbase_symbol = f"{base_currency}/USD"
        usdt_symbol = f"{base_currency}/USDT"

        try:
            # 并发获取三个交易所的ticker
            coinbase_ticker, okx_ticker, binance_ticker = await asyncio.gather(
                self.coinbase_provider.fetch_ticker(coinbase_symbol),
                self.okx_provider.fetch_ticker(usdt_symbol),
                self.crypto_provider.fetch_ticker(usdt_symbol),
                return_exceptions=True,
            )

            if isinstance(coinbase_ticker, Exception) or coinbase_ticker is None:
                logging.warning(f"[{symbol}] 获取Coinbase {coinbase_symbol} ticker失败")
                return None

            if isinstance(okx_ticker, Exception) or okx_ticker is None:
                logging.warning(f"[{symbol}] 获取OKX {usdt_symbol} ticker失败")
                return None

            if isinstance(binance_ticker, Exception) or binance_ticker is None:
                logging.warning(f"[{symbol}] 获取Binance {usdt_symbol} ticker失败")
                return None

            coinbase_price = coinbase_ticker.get("last")
            okx_price = okx_ticker.get("last")
            binance_price = binance_ticker.get("last")

            if coinbase_price is None or okx_price is None or binance_price is None:
                return None

            if coinbase_price == 0:
                return None

            # 计算 USDT 市场平均价格
            usdt_avg_price = (okx_price + binance_price) / 2

            # 计算溢价百分比
            premium_pct = (coinbase_price - usdt_avg_price) / coinbase_price * 100

            return {
                "coinbase_price": coinbase_price,
                "usdt_avg_price": usdt_avg_price,
                "okx_price": okx_price,
                "binance_price": binance_price,
                "premium_pct": premium_pct,
                "coinbase_symbol": coinbase_symbol,
                "usdt_symbol": usdt_symbol,
            }
        except Exception as e:
            logging.exception(f"[{symbol}] 计算现货溢价失败: {e}")
            return None

    async def close_exchange(self):
        """关闭交易所连接"""
        await self.crypto_provider.close()
        await self.coinbase_provider.close()
        await self.okx_provider.close()


# 全局引擎实例
engine = StrategyEngine()
