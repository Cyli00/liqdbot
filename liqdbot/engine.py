"""
策略引擎模块 - 核心交易逻辑和数据分析
"""
import math
import re
import logging
import numpy as np
import pandas as pd
import pandas_ta as ta
import ccxt.async_support as ccxt

from .config import (
    DEFAULT_SYMBOL, TIMEFRAME, LOWER_TIMEFRAME, FETCH_LIMIT,
    PIVOT_LEN, EXPIRY_BARS,
    LIQUIDITY_LOOKBACK, HIDE_EXPIRED_LEVELS, HIDE_MITIGATED_LEVELS,
    CISD_TOLERANCE
)
from .state import SymbolState
from .alerts import AlertMessages


class StrategyEngine:
    """策略引擎 - 负责数据获取、指标计算和市场分析"""
    
    def __init__(self):
        self.exchange = ccxt.binance()
        
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
        
        self.htf_timeframe = '4h'  # 高周期固定为 4h
        
        # 如果有默认标的，自动添加
        if DEFAULT_SYMBOL:
            if isinstance(DEFAULT_SYMBOL, str):
                default_symbols = [DEFAULT_SYMBOL]
            else:
                default_symbols = list(DEFAULT_SYMBOL)
            for symbol in default_symbols:
                self.add_symbol(symbol)
            logging.info(f"初始化引擎: 默认监控 {', '.join(default_symbols)}")
    
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
        try:
            # 尝试获取少量数据来验证交易对是否有效
            await self.exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=1)
            return True
        except Exception as e:
            logging.warning(f"交易对 {symbol} 验证失败: {e}")
            return False
    
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
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df.reset_index(drop=True)

    @staticmethod
    def _merge_frames(cached_df, new_df, max_size: int | None):
        if cached_df is None or cached_df.empty:
            merged = new_df.copy()
        else:
            merged = pd.concat([cached_df, new_df], ignore_index=True)

        merged = merged.drop_duplicates(subset='timestamp', keep='last')
        merged = merged.sort_values('timestamp').reset_index(drop=True)

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

    async def fetch_data(self, symbol: str, limit: int = FETCH_LIMIT, force_full: bool = False):
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
        """
        import time as time_module
        
        if not symbol:
            return None, None, None
        
        state = self.get_state(symbol)
        if state is None:
            return None, None, None
        
        now = time_module.time()
        
        # 判断是否需要完整获取
        # 1. 首次获取（无缓存）
        # 2. 强制刷新
        # 3. 距离上次完整获取超过1小时
        need_full_fetch = (
            force_full or 
            state.cached_df is None or 
            (now - state.last_fetch_time) > 3600
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
        """完整获取历史数据"""
        # 主周期数据
        ohlcv = await self.exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=limit)
        df = self._ohlcv_to_df(ohlcv)

        # 低周期数据用于上下行量
        lower_minutes = self._timeframe_to_minutes(self.lower_timeframe)
        main_minutes = self._timeframe_to_minutes(TIMEFRAME)
        ratio = max(1, math.ceil(main_minutes / lower_minutes))
        lower_limit = limit * ratio + 50  # 多抓一些防止边界缺口

        lower = await self.exchange.fetch_ohlcv(symbol, self.lower_timeframe, limit=lower_limit)
        lower_df = self._ohlcv_to_df(lower)
        
        # 高周期数据 (HTF)
        htf_minutes = self._timeframe_to_minutes(self.htf_timeframe)
        ratio_htf = max(1, math.ceil(htf_minutes / main_minutes))
        # 我们不需要那么多 HTF K线，保持合适数量即可
        htf_limit = max(100, int(limit / ratio_htf) + 20) 
        
        htf = await self.exchange.fetch_ohlcv(symbol, self.htf_timeframe, limit=htf_limit)
        htf_df = self._ohlcv_to_df(htf)

        return df, lower_df, htf_df
    
    async def _fetch_incremental_data(self, symbol: str, state):
        """
        增量获取最新数据并合并到缓存
        
        策略：
        1. 只获取最新10根K线（覆盖可能的数据更新）
        2. 根据timestamp去重合并
        3. 保持滚动窗口大小
        """
        INCREMENTAL_LIMIT = 10  # 增量获取的K线数量
        MAX_CACHE_SIZE = FETCH_LIMIT  # 缓存最大K线数量
        
        # 获取最新的主周期数据
        ohlcv = await self.exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=INCREMENTAL_LIMIT)
        new_df = self._ohlcv_to_df(ohlcv)
        
        # 合并到缓存（基于timestamp去重，保留最新数据）
        merged_df = self._merge_frames(state.cached_df, new_df, MAX_CACHE_SIZE)
        
        # 获取最新的低周期数据
        lower_minutes = self._timeframe_to_minutes(self.lower_timeframe)
        main_minutes = self._timeframe_to_minutes(TIMEFRAME)
        ratio = max(1, math.ceil(main_minutes / lower_minutes))
        lower_incremental_limit = INCREMENTAL_LIMIT * ratio + 10
        
        lower = await self.exchange.fetch_ohlcv(symbol, self.lower_timeframe, limit=lower_incremental_limit)
        new_lower_df = self._ohlcv_to_df(lower)
        
        # 合并低周期数据
        max_lower_size = MAX_CACHE_SIZE * ratio + 50
        merged_lower_df = self._merge_frames(state.cached_lower_df, new_lower_df, max_lower_size)
            
        # 获取最新的高周期数据 (HTF)
        # 优化: 4h K线更新慢，只有距离上次 HTF 更新超过 30 分钟才拉取
        import time as time_mod
        htf_update_interval = 1800  # 30 分钟
        merged_htf_df = state.cached_htf_df
        
        if (state.last_htf_fetch_time is None or 
            (time_mod.time() - state.last_htf_fetch_time) > htf_update_interval):
            htf = await self.exchange.fetch_ohlcv(symbol, self.htf_timeframe, limit=5)
            new_htf_df = self._ohlcv_to_df(htf)
            merged_htf_df = self._merge_frames(state.cached_htf_df, new_htf_df, 200)
            state.last_htf_fetch_time = time_mod.time()
        
        # 更新缓存
        state.cached_df = merged_df
        state.cached_lower_df = merged_lower_df
        state.cached_htf_df = merged_htf_df
        
        logging.debug(f"[{symbol}] 增量更新: 主周期 {len(merged_df)}, 低周期 {len(merged_lower_df)}, 高周期 {len(merged_htf_df)}")
        
        return merged_df, merged_lower_df, merged_htf_df

    def calculate_indicators(self, df, lower_df=None):
        """计算所有技术指标"""
        if df is None or df.empty:
            return df

        df = df.copy()

        # 1. Pivot Points (震荡结构) - 向量化优化
        # 使用滚动窗口计算，避免 Python 循环
        pivot_len = self.pivot_len
        n = len(df)
        
        high_vals = df['high'].to_numpy()
        low_vals = df['low'].to_numpy()
        
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
                left_max = high_vals[i - pivot_len:i].max()
                right_max = high_vals[i + 1:i + pivot_len + 1].max()
                if high_vals[i] > left_max and high_vals[i] > right_max:
                    is_pivot_high[i] = True
                
                # Pivot Low: 当前low严格小于左右各pivot_len根K线
                left_min = low_vals[i - pivot_len:i].min()
                right_min = low_vals[i + 1:i + pivot_len + 1].min()
                if low_vals[i] < left_min and low_vals[i] < right_min:
                    is_pivot_low[i] = True
        
        df['is_pivot_high'] = is_pivot_high
        df['is_pivot_low'] = is_pivot_low

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
        macd_series, signal_series, hist_series = self._macd_with_sma_signal(df['close'])
        if macd_series is not None:
            df['MACD_12_26_9'] = macd_series
            df['MACDs_12_26_9'] = signal_series
            df['MACDh_12_26_9'] = hist_series
        
        # 2. ATR (14) - 用于标准化 DIF 斜率
        atr = df.ta.atr(length=14)
        if atr is not None:
            df['ATR_14'] = atr
        else:
            df['ATR_14'] = np.nan
        
        # 3. 标准化 DIF 斜率及分位数分级
        if 'MACD_12_26_9' in df.columns and 'ATR_14' in df.columns:
            # 标准化斜率 = (DIF - DIF[1]) / ATR
            dif_change = df['MACD_12_26_9'] - df['MACD_12_26_9'].shift(1)
            df['dif_slope'] = dif_change / df['ATR_14'].replace(0, np.nan)
            df['dif_slope'] = df['dif_slope'].replace([np.inf, -np.inf], np.nan)
            
            # 滚动分位数计算（使用绝对值，因为我们关心的是斜率强度）
            abs_slope = df['dif_slope'].abs()
            df['dif_slope_q20'] = abs_slope.rolling(200, min_periods=50).quantile(0.2)
            df['dif_slope_q40'] = abs_slope.rolling(200, min_periods=50).quantile(0.4)
            df['dif_slope_q60'] = abs_slope.rolling(200, min_periods=50).quantile(0.6)
            df['dif_slope_q80'] = abs_slope.rolling(200, min_periods=50).quantile(0.8)
            
            # 计算斜率等级 (1-5级)
            conditions = [
                abs_slope < df['dif_slope_q20'],
                (abs_slope >= df['dif_slope_q20']) & (abs_slope < df['dif_slope_q40']),
                (abs_slope >= df['dif_slope_q40']) & (abs_slope < df['dif_slope_q60']),
                (abs_slope >= df['dif_slope_q60']) & (abs_slope < df['dif_slope_q80']),
                abs_slope >= df['dif_slope_q80']
            ]
            choices = [1, 2, 3, 4, 5]
            df['dif_slope_grade'] = np.select(conditions, choices, default=0)

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
        pivot_high_mask = df['is_pivot_high'].to_numpy()
        pivot_low_mask = df['is_pivot_low'].to_numpy()
        high_vals = df['high'].to_numpy()
        low_vals = df['low'].to_numpy()
        timestamps = df['timestamp'].to_numpy()
        
        end_scan_idx = len(df) - pivot_len
        for i in range(start_idx, end_scan_idx):
            if pivot_high_mask[i]:
                state.swing_levels.append({
                    'type': 'high', 
                    'price': high_vals[i], 
                    'created_at': timestamps[i], 
                    'created_idx': i,
                    'mitigated': False,
                    'mitigated_at': None
                })
            
            if pivot_low_mask[i]:
                state.swing_levels.append({
                    'type': 'low', 
                    'price': low_vals[i], 
                    'created_at': timestamps[i], 
                    'created_idx': i,
                    'mitigated': False,
                    'mitigated_at': None
                })
        
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
            age = last_idx - level['created_idx']
            if self.hide_expired_levels and age > self.expiry_bars:
                continue

            start_check_idx = level['created_idx'] + 1
            
            if start_check_idx >= check_end:
                # 还没有足够的收盘K线来判断 mitigation
                active_levels.append(level)
                continue
            
            price = level['price']
            
            if level['type'] == 'high':
                # 检查从 start_check_idx 到 check_end-1 是否有 high >= price
                if suffix_max_high[start_check_idx] >= price:
                    # 需要找第一个触及的位置（线性扫描，但通常很快就能找到）
                    for touch_idx in range(start_check_idx, check_end):
                        if high_vals[touch_idx] >= price:
                            level['mitigated'] = True
                            level['mitigated_at'] = touch_idx
                            level['mitigated_at_ts'] = timestamps[touch_idx]
                            break
            
            elif level['type'] == 'low':
                # 检查从 start_check_idx 到 check_end-1 是否有 low <= price
                if suffix_min_low[start_check_idx] <= price:
                    for touch_idx in range(start_check_idx, check_end):
                        if low_vals[touch_idx] <= price:
                            level['mitigated'] = True
                            level['mitigated_at'] = touch_idx
                            level['mitigated_at_ts'] = timestamps[touch_idx]
                            break

            active_levels.append(level)

        state.swing_levels = sorted(active_levels, key=lambda x: x['created_at'])

    def calculate_htf_indicators(self, df):
        """计算高周期 (4h) 指标: MACD, Signal Slope, Histogram Color"""
        if df is None or len(df) < 50:
            return None
        
        df = df.copy()
        
        # MACD 12, 26, 9
        # 注意: 用户策略中使用 SMA 计算 Signal 线
        macd_series, signal_series, hist_series = self._macd_with_sma_signal(df['close'])
        if macd_series is None:
            return None

        df['MACD'] = macd_series
        df['Signal'] = signal_series
        df['Hist'] = hist_series
        
        # 计算 Signal 斜率
        df['Signal_Slope'] = df['Signal'] - df['Signal'].shift(1)
        
        # 计算 Histogram 颜色状态 (Aqua/Blue/Red/Maroon)
        # Aqua: Hist > 0 and Hist > Hist[1] (强多)
        # Blue: Hist > 0 and Hist < Hist[1] (弱多)
        # Red: Hist <= 0 and Hist < Hist[1] (强空)
        # Maroon: Hist <= 0 and Hist > Hist[1] (弱空)
        
        c1 = (df['Hist'] > 0) & (df['Hist'] > df['Hist'].shift(1))
        c2 = (df['Hist'] > 0) & (df['Hist'] < df['Hist'].shift(1))
        c3 = (df['Hist'] <= 0) & (df['Hist'] < df['Hist'].shift(1))
        c4 = (df['Hist'] <= 0) & (df['Hist'] > df['Hist'].shift(1))
        
        conditions = [c1, c2, c3, c4]
        choices = ["AQUA", "BLUE", "RED", "MAROON"]
        
        df['Hist_Color'] = np.select(conditions, choices, default="GRAY")
        
        return df

    def _find_recent_wicked_level(self, state: SymbolState, last_idx: int, level_type: str):
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
            if lvl['type'] != level_type:
                continue
            mitigated_at = lvl.get('mitigated_at')
            if mitigated_at is None:
                continue
            bars_since = last_idx - mitigated_at
            if 0 <= bars_since <= self.liquidity_lookback:
                candidates.append((bars_since, mitigated_at, lvl['price']))
        
        if not candidates:
            return None, None
        
        # 取 mitigated_at 最大的（即最近被扫荡的）
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
        close_vals = df['close'].to_numpy()
        open_vals = df['open'].to_numpy()
        
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
                        ratio = float('inf')
                    else:
                        ratio = float('-inf')
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
                        ratio = float('inf')
                    else:
                        ratio = float('-inf')
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
            'flag_series': cisd_flag,
            'flag_at_last': last_flag,
            'origin_level_at_last': origin_level[last_idx] if origin_level else None,
            'origin_idx_at_last': origin_idx[last_idx] if origin_idx else None
        }

    def check_macd_resonance(self, df, htf_df):
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
        empty_info = {'slope_4h': 0.0, 'hist_color': 'GRAY', 'macd_slope_1h': 0.0, 
                      'macd_slope_4h': 0.0, 'zero_pos_1h': 'unknown', 'zero_pos_4h': 'unknown',
                      'cross_time_gap_hours': None, 'cross_1h_at': None, 'cross_4h_at': None}
        
        if df is None or htf_df is None or len(df) < 5 or len(htf_df) < 5:
            return 0, empty_info, None
            
        last_1h = df.iloc[-1]
        prev_1h = df.iloc[-2]
        
        last_4h = htf_df.iloc[-1]
        prev_4h = htf_df.iloc[-2]
        
        # 1h MACD data check
        if 'MACD_12_26_9' not in df.columns or 'MACDs_12_26_9' not in df.columns:
            return 0, empty_info, None
        if 'MACD' not in htf_df.columns or 'Signal' not in htf_df.columns:
            return 0, empty_info, None
             
        mac_1h = last_1h['MACD_12_26_9']
        sig_1h = last_1h['MACDs_12_26_9']
        prev_mac_1h = prev_1h['MACD_12_26_9']
        prev_sig_1h = prev_1h['MACDs_12_26_9']
        
        # 1h 状态判断
        is_bull_1h = mac_1h > sig_1h
        is_bear_1h = mac_1h < sig_1h
        was_bear_1h = prev_mac_1h < prev_sig_1h
        was_bull_1h = prev_mac_1h > prev_sig_1h
        
        # 交叉检测
        cross_up_1h = was_bear_1h and is_bull_1h
        cross_down_1h = was_bull_1h and is_bear_1h
        
        # 4h 状态
        mac_4h = last_4h['MACD']
        sig_4h = last_4h['Signal']
        prev_mac_4h = prev_4h['MACD']
        prev_sig_4h = prev_4h['Signal']
        
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
        ts_1h = last_1h['timestamp']
        
        # 1. 1h 快线斜率计算（标准化）
        # 使用上一根已收盘K线收盘时刻作为 x2，当前时刻作为 x1
        dif_slope_1h = 0.0
        dif_slope_grade_1h = 0
        slope_valid = False
        
        atr_1h = last_1h.get('ATR_14')
        last_open_ts = last_1h.get('timestamp')
        if (atr_1h is not None and not pd.isna(atr_1h) and atr_1h != 0 and
                last_open_ts is not None and not pd.isna(last_open_ts) and
                not pd.isna(mac_1h) and not pd.isna(prev_mac_1h)):
            bar_minutes = self._timeframe_to_minutes(TIMEFRAME)
            bar_seconds = max(bar_minutes * 60, 1)
            now_ts = pd.Timestamp.utcnow()
            x2_ts = last_open_ts
            bar_close_ts = x2_ts + pd.Timedelta(seconds=bar_seconds)
            x1_ts = now_ts
            if x1_ts < x2_ts:
                x1_ts = x2_ts
            elif x1_ts > bar_close_ts:
                x1_ts = bar_close_ts
            dt_hours = max((x1_ts - x2_ts).total_seconds() / 3600.0, 1e-6)
            dif_change = mac_1h - prev_mac_1h
            dif_slope_1h = (dif_change / atr_1h) / dt_hours
            slope_valid = True
        
        if not slope_valid:
            fallback_slope = last_1h.get('dif_slope', 0)
            dif_slope_1h = fallback_slope if not pd.isna(fallback_slope) else 0
        
        # 斜率等级（基于历史分位数）
        if slope_valid:
            q20 = last_1h.get('dif_slope_q20')
            q40 = last_1h.get('dif_slope_q40')
            q60 = last_1h.get('dif_slope_q60')
            q80 = last_1h.get('dif_slope_q80')
            if not pd.isna(q20) and not pd.isna(q40) and not pd.isna(q60) and not pd.isna(q80):
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
                fallback_grade = last_1h.get('dif_slope_grade', 0)
                dif_slope_grade_1h = int(fallback_grade) if not pd.isna(fallback_grade) else 0
        else:
            fallback_grade = last_1h.get('dif_slope_grade', 0)
            dif_slope_grade_1h = int(fallback_grade) if not pd.isna(fallback_grade) else 0
        
        # 2. 零轴位置判断
        # 金叉在零轴下方 = 左侧信号（更早期），零轴上方 = 右侧信号（趋势确认）
        zero_pos_1h = 'above' if mac_1h > 0 else 'below'
        zero_pos_4h = 'above' if mac_4h > 0 else 'below'
        
        # 3. 计算1h和4h交叉的时间间隔
        cross_1h_time = None
        cross_4h_time = None
        cross_time_gap_hours = None
        
        # 确定要找的交叉类型
        is_golden = res_golden
        
        # 查找1h最近的交叉时间及方向
        cross_1h_time, cross_1h_is_golden = self._find_last_cross_info(
            df, 'MACD_12_26_9', 'MACDs_12_26_9'
        )
        
        # 查找4h最近的交叉时间及方向
        cross_4h_time, cross_4h_is_golden = self._find_last_cross_info(
            htf_df, 'MACD', 'Signal'
        )
        
        if (cross_1h_time is not None and cross_4h_time is not None and
                cross_1h_is_golden == cross_4h_is_golden == is_golden):
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
        slope_4h = last_4h.get('Signal_Slope', 0.0)
        hist_color_4h = last_4h.get('Hist_Color', 'GRAY')
        
        info = {
            'slope_4h': slope_4h,
            'hist_color': hist_color_4h,
            'dif_slope_grade_1h': dif_slope_grade_1h,
            'zero_pos_1h': zero_pos_1h,
            'zero_pos_4h': zero_pos_4h,
            'cross_time_gap_hours': cross_time_gap_hours,
            'cross_1h_at': cross_1h_time,
            'cross_4h_at': cross_4h_time,
            'macd_1h': mac_1h,
            'macd_4h': mac_4h,
        }
        
        if res_golden:
            return 1, info, ts_1h
        if res_death:
            return -1, info, ts_1h
            
        return 0, empty_info, None
    
    def _find_last_cross_time(self, df, macd_col: str, signal_col: str, find_golden: bool):
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
            
            if pd.isna(curr_mac) or pd.isna(curr_sig) or pd.isna(prev_mac) or pd.isna(prev_sig):
                continue
            
            is_bull_now = curr_mac > curr_sig
            was_bear = prev_mac < prev_sig
            is_bear_now = curr_mac < curr_sig
            was_bull = prev_mac > prev_sig
            
            if find_golden and was_bear and is_bull_now:
                return df['timestamp'].iloc[i]
            elif not find_golden and was_bull and is_bear_now:
                return df['timestamp'].iloc[i]
        
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
            
            if pd.isna(curr_mac) or pd.isna(curr_sig) or pd.isna(prev_mac) or pd.isna(prev_sig):
                continue
            
            is_bull_now = curr_mac > curr_sig
            was_bear = prev_mac < prev_sig
            is_bear_now = curr_mac < curr_sig
            was_bull = prev_mac > prev_sig
            
            if was_bear and is_bull_now:
                return df['timestamp'].iloc[i], True
            if was_bull and is_bear_now:
                return df['timestamp'].iloc[i], False
        
        return None, None

    def analyze_market(self, symbol: str, state: SymbolState, df, htf_df=None):
        """分析指定标的的市场状况"""
        if df is None or df.empty:
            return None

        # 计算 4h 指标 (如果此函数被jobs调用时传入了htf_df，则在此处计算指标)
        if htf_df is not None:
            htf_df = self.calculate_htf_indicators(htf_df)
            # 注意: MACD 相关计算已移至下方 15 分钟检测逻辑，避免每分钟重复计算

        last_idx = len(df) - 1
        if last_idx < 1:
            return None

        last_candle = df.iloc[-1]
        current_price = last_candle['close']
        current_ts = last_candle['timestamp']
        trend_dir = "未知"
        trend_support = None

        # 更新 Pivot 数据库
        self.update_swing_levels(df, state)

        cisd_result = self.detect_cisd(df)

        msgs = []

        # --- 1. CISD 策略: Swing High/Low Mitigation Alerts ---
        # 注意：由于 mitigation 只检查已收盘的K线，需要检查上一根收盘K线
        prev_closed_idx = last_idx - 1
        for level in state.swing_levels:
            if level.get('mitigated_at') == prev_closed_idx:
                mitigated_ts = level.get('mitigated_at_ts')
                key = (level['type'], round(level['price'], 4), mitigated_ts)
                if key not in state.notified_sweeps:
                    if level['type'] == 'high':
                        msgs.append((
                            AlertMessages.TYPE_SWING_HIGH_MITIGATION,
                            AlertMessages.swing_high_mitigation(symbol, current_price, level['price'])
                        ))
                    else:
                        msgs.append((
                            AlertMessages.TYPE_SWING_LOW_MITIGATION,
                            AlertMessages.swing_low_mitigation(symbol, current_price, level['price'])
                        ))
                    state.notified_sweeps.add(key)

        # --- 2. CISD 策略: Normal/Strong CISD Alerts ---
        # 使用辅助方法在 liquidity_lookback 窗口内查找最近被扫荡的 swing level
        bars_since_high, wicked_high_level = self._find_recent_wicked_level(state, last_idx, 'high')
        bars_since_low, wicked_low_level = self._find_recent_wicked_level(state, last_idx, 'low')

        if cisd_result['flag_at_last'] != 0:
            # 只有当当前信号的时间戳晚于上一次记录的时间戳时才处理
            # cisd_result['flag_at_last'] 对应的是 last_idx 的信号，即 current_ts
            if state.last_cisd_ts is None or current_ts > state.last_cisd_ts:
                origin_level = cisd_result['origin_level_at_last']
                if origin_level is None or (isinstance(origin_level, float) and math.isnan(origin_level)):
                    state.last_cisd_ts = current_ts
                else:
                    if cisd_result['flag_at_last'] == 1:
                        # 看跌 CISD：检查是否有高点扫荡且价格低于被扫荡水平
                        if (bars_since_high is not None and 
                            wicked_high_level is not None and 
                            current_price < wicked_high_level):
                            alert_type = AlertMessages.TYPE_BEARISH_STRONG_CISD
                            alert_msg = AlertMessages.bearish_strong_cisd(
                                symbol, current_price, origin_level,
                                wicked_high_level, bars_since_high
                            )
                        else:
                            alert_type = AlertMessages.TYPE_BEARISH_NORMAL_CISD
                            alert_msg = AlertMessages.bearish_normal_cisd(symbol, current_price, origin_level)
                    else:
                        # 看涨 CISD：检查是否有低点扫荡且价格高于被扫荡水平
                        if (bars_since_low is not None and 
                            wicked_low_level is not None and 
                            current_price > wicked_low_level):
                            alert_type = AlertMessages.TYPE_BULLISH_STRONG_CISD
                            alert_msg = AlertMessages.bullish_strong_cisd(
                                symbol, current_price, origin_level,
                                wicked_low_level, bars_since_low
                            )
                        else:
                            alert_type = AlertMessages.TYPE_BULLISH_NORMAL_CISD
                            alert_msg = AlertMessages.bullish_normal_cisd(symbol, current_price, origin_level)

                    if state.should_send_cisd_origin_alert(cisd_result['flag_at_last'], origin_level, alert_type):
                        msgs.append((alert_type, alert_msg))

                    state.last_cisd_ts = current_ts

        # --- 3. MACD 共振策略 ---
        # 仅当 htf_df 可用时检测
        # 检测频率：每当新的15分钟K线收盘时检测
        if htf_df is not None:
            # 计算当前时间对应的已收盘15分钟K线时间戳
            # 当前时间 floor 到15分钟边界，即为最近已收盘的K线时间
            current_15m_ts = pd.Timestamp.now(tz='UTC').floor('15min')
            
            # 检查是否是新的15分钟K线
            is_new_15m_bar = (
                state.last_macd_check_15m_ts is None or 
                current_15m_ts > state.last_macd_check_15m_ts
            )
            
            if is_new_15m_bar:
                # 只有15分钟K线收盘时才计算 MACD 相关指标
                df_with_macd = self.calculate_macd_indicators(df)
                
                res_val, res_info, res_ts = self.check_macd_resonance(df_with_macd, htf_df)
                
                # 使用 K线时间戳去重，确保同一根K线只触发一次
                if res_val != 0 and res_ts is not None:
                    # 检查是否是新的1h K线（时间戳不同于上次触发）
                    is_new_bar = (
                        state.last_macd_resonance_ts is None or 
                        res_ts > state.last_macd_resonance_ts
                    )
                    
                    if is_new_bar:
                        if res_val == 1:
                            msgs.append((
                                AlertMessages.TYPE_MACD_RESONANCE_GOLDEN,
                                AlertMessages.macd_resonance_golden(symbol, current_price, res_info)
                            ))
                        elif res_val == -1:
                            msgs.append((
                                AlertMessages.TYPE_MACD_RESONANCE_DEATH,
                                AlertMessages.macd_resonance_death(symbol, current_price, res_info)
                            ))
                        # 记录本次触发的时间戳
                        state.last_macd_resonance_ts = res_ts
                
                # 更新已检测的15分钟K线时间戳
                state.last_macd_check_15m_ts = current_15m_ts

        # --- 4. 计算当前最近的支撑/阻力 ---
        if self.hide_mitigated_levels:
            active_highs = [x['price'] for x in state.swing_levels if not x['mitigated'] and x['type'] == 'high']
            active_lows = [x['price'] for x in state.swing_levels if not x['mitigated'] and x['type'] == 'low']
        else:
            active_highs = [x['price'] for x in state.swing_levels if x['type'] == 'high']
            active_lows = [x['price'] for x in state.swing_levels if x['type'] == 'low']
        
        nearest_res = min([x for x in active_highs if x > current_price], default=None)
        nearest_sup = max([x for x in active_lows if x < current_price], default=None)

        result = {
            'symbol': symbol,
            'price': current_price,
            'trend_dir': trend_dir,
            'trend_support': trend_support,
            'nearest_res': nearest_res,
            'nearest_sup': nearest_sup,
            'alerts': msgs,
            'last_bar_idx': last_idx
        }
        state.last_analysis = result
        return result

    async def close_exchange(self):
        """关闭交易所连接"""
        await self.exchange.close()


# 全局引擎实例
engine = StrategyEngine()
