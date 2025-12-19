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
    Z_LENGTH, Z_THRESH, TIMEOUT_BARS, PIVOT_LEN, EXPIRY_BARS,
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
        self.timeout_bars = TIMEOUT_BARS
        self.z_len = Z_LENGTH
        self.z_thresh = Z_THRESH
        self.pivot_len = PIVOT_LEN
        self.expiry_bars = EXPIRY_BARS
        self.liquidity_lookback = LIQUIDITY_LOOKBACK
        self.hide_expired_levels = HIDE_EXPIRED_LEVELS
        self.hide_mitigated_levels = HIDE_MITIGATED_LEVELS
        self.cisd_tolerance = CISD_TOLERANCE
        
        self.htf_timeframe = '4h'  # 高周期固定为 4h
        
        # 如果有默认标的，自动添加
        if DEFAULT_SYMBOL:
            self.add_symbol(DEFAULT_SYMBOL)
            logging.info(f"初始化引擎: 默认监控 {DEFAULT_SYMBOL}")
    
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
        # HTF 更新频率低，但这里为了简单，每次都检查一下，开销不大
        htf = await self.exchange.fetch_ohlcv(symbol, self.htf_timeframe, limit=5) # 只抓最新的几根
        new_htf_df = self._ohlcv_to_df(htf)
        
        # 合并高周期数据
        merged_htf_df = self._merge_frames(state.cached_htf_df, new_htf_df, 200)
        
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

        # 0. 将低周期成交量聚合到主周期，用于上下行量
        if lower_df is not None and not lower_df.empty:
            ldf = lower_df.copy()
            ldf['up_vol'] = np.where(ldf['close'] > ldf['open'], ldf['volume'], 0)
            ldf['down_vol'] = np.where(ldf['close'] < ldf['open'], ldf['volume'], 0)
            ldf['bucket'] = ldf['timestamp'].dt.floor(self._pandas_freq(TIMEFRAME))
            vol_agg = ldf.groupby('bucket')[['up_vol', 'down_vol']].sum()
            df = df.merge(vol_agg, left_on='timestamp', right_index=True, how='left')
        else:
            df['up_vol'] = 0
            df['down_vol'] = 0

        df[['up_vol', 'down_vol']] = df[['up_vol', 'down_vol']].fillna(0)

        # 1. Supertrend (趋势)
        st = df.ta.supertrend(length=10, multiplier=2.0)
        
        if st is not None and not st.empty:
            df['supertrend'] = st[f'SUPERT_{10}_{2.0}']
            df['supertrend_dir'] = st[f'SUPERTd_{10}_{2.0}']
        else:
            df['supertrend'] = np.nan
            df['supertrend_dir'] = 0

        # 2. Volume Z-Score (爆仓量)
        up_mean = df['up_vol'].rolling(self.z_len).mean()
        up_std = df['up_vol'].rolling(self.z_len).std().replace(0, np.nan)
        down_mean = df['down_vol'].rolling(self.z_len).mean()
        down_std = df['down_vol'].rolling(self.z_len).std().replace(0, np.nan)

        df['z_up'] = (df['up_vol'] - up_mean) / up_std
        df['z_down'] = (df['down_vol'] - down_mean) / down_std
        df[['z_up', 'z_down']] = df[['z_up', 'z_down']].replace([np.inf, -np.inf], np.nan)

        # 3. Pivot Points (震荡结构) - 使用非centered算法，只依赖历史数据
        # 一个点被确认为pivot high需要：左边pivot_len根K线的high都低于它，右边pivot_len根K线的high也都低于它
        # 这意味着pivot确认会有pivot_len根K线的延迟，但这是实盘必须的
        pivot_len = self.pivot_len
        df['is_pivot_high'] = False
        df['is_pivot_low'] = False
        
        for i in range(pivot_len, len(df) - pivot_len):
            # 检查是否为 pivot high：当前high是左右各pivot_len根K线中的最高点
            left_highs = df['high'].iloc[i - pivot_len:i]
            right_highs = df['high'].iloc[i + 1:i + pivot_len + 1]
            current_high = df['high'].iloc[i]
            
            if (left_highs < current_high).all() and (right_highs < current_high).all():
                df.loc[df.index[i], 'is_pivot_high'] = True
            
            # 检查是否为 pivot low：当前low是左右各pivot_len根K线中的最低点
            left_lows = df['low'].iloc[i - pivot_len:i]
            right_lows = df['low'].iloc[i + 1:i + pivot_len + 1]
            current_low = df['low'].iloc[i]
            
            if (left_lows > current_low).all() and (right_lows > current_low).all():
                df.loc[df.index[i], 'is_pivot_low'] = True
        
        # 4. MACD (1h) - 用于共振策略
        # 注意: 用户策略中使用 SMA 计算 Signal 线
        # macd = fastMA(EMA) - slowMA(EMA)
        # signal = sma(macd, signalLength)
        macd_series, signal_series, hist_series = self._macd_with_sma_signal(df['close'])
        if macd_series is not None:
            df['MACD_12_26_9'] = macd_series
            df['MACDs_12_26_9'] = signal_series
            df['MACDh_12_26_9'] = hist_series

        return df

    def update_swing_levels(self, df, state: SymbolState):
        """更新 Swing 高低点（支撑/阻力位）"""
        state.swing_levels = []
        pivot_len = self.pivot_len
        
        for i in range(pivot_len, len(df) - pivot_len):
            ts = df['timestamp'].iloc[i]
            
            if df.loc[df.index[i], 'is_pivot_high']:
                state.swing_levels.append({
                    'type': 'high', 
                    'price': df['high'].iloc[i], 
                    'created_at': ts, 
                    'created_idx': i,
                    'mitigated': False,
                    'mitigated_at': None
                })
            
            if df['is_pivot_low'].iloc[i]:
                state.swing_levels.append({
                    'type': 'low', 
                    'price': df['low'].iloc[i], 
                    'created_at': ts, 
                    'created_idx': i,
                    'mitigated': False,
                    'mitigated_at': None
                })
        
        # 检查 Mitigation
        active_levels = []
        last_idx = len(df) - 1
        for level in state.swing_levels:
            age = last_idx - level['created_idx']
            if self.hide_expired_levels and age > self.expiry_bars:
                continue

            future_candles = df.iloc[level['created_idx'] + 1:]
            if future_candles.empty:
                active_levels.append(level)
                continue
            
            if level['type'] == 'high':
                touch_mask = future_candles['high'] >= level['price']
                if touch_mask.any():
                    touch_idx = int(touch_mask.idxmax())
                    level['mitigated'] = True
                    level['mitigated_at'] = int(touch_idx)
                    level['mitigated_at_ts'] = df['timestamp'].iloc[touch_idx]
            
            elif level['type'] == 'low':
                touch_mask = future_candles['low'] <= level['price']
                if touch_mask.any():
                    touch_idx = int(touch_mask.idxmax())
                    level['mitigated'] = True
                    level['mitigated_at'] = int(touch_idx)
                    level['mitigated_at_ts'] = df['timestamp'].iloc[touch_idx]

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

    def detect_liq_reversal(self, df):
        """检测 Liquidation Reversal 信号"""
        n = len(df)
        plottrnd = [0] * n
        lastliqdir = 0
        lastliqidx = 0
        valid = False
        short_liq_at_last = False
        long_liq_at_last = False
        z_up_last = 0
        z_down_last = 0

        for i in range(n):
            direction = df['supertrend_dir'].iloc[i]
            prev_dir = df['supertrend_dir'].iloc[i-1] if i > 0 else direction

            short_liq = direction < 0 and df['z_up'].iloc[i] > self.z_thresh
            long_liq = direction > 0 and df['z_down'].iloc[i] > self.z_thresh

            is_cross = i > 0 and direction != prev_dir and direction * prev_dir <= 0
            if is_cross:
                plottrnd[i] = 0

            if i > 0 and prev_dir > 0 and direction < 0 and lastliqdir == 1 and valid:
                if self.timeout_bars == 0 or i - lastliqidx <= self.timeout_bars:
                    plottrnd[i] = 1
                    valid = False

            if i > 0 and prev_dir < 0 and direction > 0 and lastliqdir == -1 and valid:
                if self.timeout_bars == 0 or i - lastliqidx <= self.timeout_bars:
                    plottrnd[i] = -1
                    valid = False

            if short_liq:
                lastliqdir = -1
                lastliqidx = i
                valid = True
                if i == n - 1:
                    short_liq_at_last = True
                    z_up_last = df['z_up'].iloc[i]

            if long_liq:
                lastliqdir = 1
                lastliqidx = i
                valid = True
                if i == n - 1:
                    long_liq_at_last = True
                    z_down_last = df['z_down'].iloc[i]

        new_bull = plottrnd[-1] == -1 and (n == 1 or plottrnd[-2] != -1)
        new_bear = plottrnd[-1] == 1 and (n == 1 or plottrnd[-2] != 1)

        return {
            'plottrnd': plottrnd,
            'short_liq_at_last': short_liq_at_last,
            'long_liq_at_last': long_liq_at_last,
            'z_up_last': z_up_last,
            'z_down_last': z_down_last,
            'new_bull_reversal': new_bull,
            'new_bear_reversal': new_bear,
            'last_idx': n - 1
        }

    def detect_cisd(self, df):
        """检测 CISD 信号"""
        bear_potential = []
        bull_potential = []
        cisd_flag = [0] * len(df)
        origin_level = [None] * len(df)
        origin_idx = [None] * len(df)

        for i in range(1, len(df)):
            prev = df.iloc[i - 1]
            curr = df.iloc[i]

            if prev['close'] < prev['open'] and curr['close'] > curr['open']:
                bear_potential.insert(0, (curr['open'], i))
            if prev['close'] > prev['open'] and curr['close'] < curr['open']:
                bull_potential.insert(0, (curr['open'], i))

            # Bearish CISD 检查
            while bear_potential:
                cand_open, cand_idx = bear_potential[0]
                if curr['close'] < cand_open:
                    highest = df.loc[cand_idx:i, 'close'].max()
                    top = None
                    j = cand_idx - 1
                    while j >= 0 and df['close'].iloc[j] < df['open'].iloc[j]:
                        top = df['open'].iloc[j]
                        j -= 1
                    if top is None:
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
                        bear_potential.pop(0)
                else:
                    break

            # Bullish CISD 检查
            while bull_potential:
                cand_open, cand_idx = bull_potential[0]
                if curr['close'] > cand_open:
                    lowest = df.loc[cand_idx:i, 'close'].min()
                    bottom = None
                    j = cand_idx - 1
                    while j >= 0 and df['close'].iloc[j] > df['open'].iloc[j]:
                        bottom = df['open'].iloc[j]
                        j -= 1
                    if bottom is None:
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
                        bull_potential.pop(0)
                else:
                    break

        last_idx = len(df) - 1
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
        共振金叉: (1h金叉 & 4h多头) 或 (4h金叉 & 1h多头)
        共振死叉: (1h死叉 & 4h空头) 或 (4h死叉 & 1h空头)
        """
        if df is None or htf_df is None or len(df) < 5 or len(htf_df) < 5:
            return 0, 0.0, "GRAY"
            
        last_1h = df.iloc[-1]
        prev_1h = df.iloc[-2]
        
        last_4h = htf_df.iloc[-1]
        prev_4h = htf_df.iloc[-2]
        
        # 1h 状态
        # 检查是否刚金叉/死叉: Signal翻越
        # 1h MACD data check
        if 'MACD_12_26_9' not in df.columns or 'MACDs_12_26_9' not in df.columns:
            return 0, 0.0, "GRAY"
             
        mac_1h = last_1h['MACD_12_26_9']
        sig_1h = last_1h['MACDs_12_26_9']
        prev_mac_1h = prev_1h['MACD_12_26_9']
        prev_sig_1h = prev_1h['MACDs_12_26_9']
        
        # Cross detection
        cross_up_1h = (prev_mac_1h < prev_sig_1h) and (mac_1h >= sig_1h)
        cross_down_1h = (prev_mac_1h > prev_sig_1h) and (mac_1h <= sig_1h)
        is_bull_1h = mac_1h > sig_1h
        is_bear_1h = mac_1h < sig_1h
        
        # 4h 状态
        mac_4h = last_4h['MACD']
        sig_4h = last_4h['Signal']
        prev_mac_4h = prev_4h['MACD']
        prev_sig_4h = prev_4h['Signal']
        
        cross_up_4h = (prev_mac_4h < prev_sig_4h) and (mac_4h >= sig_4h)
        cross_down_4h = (prev_mac_4h > prev_sig_4h) and (mac_4h <= sig_4h)
        is_bull_4h = mac_4h > sig_4h
        is_bear_4h = mac_4h < sig_4h
        
        # 辅助数据
        slope_4h = last_4h.get('Signal_Slope', 0.0)
        hist_color_4h = last_4h.get('Hist_Color', 'GRAY')
        
        # 只有在产生交叉的瞬间才视为触发信号
        # Case 1: 1h Cross, 4h Confirm
        res_golden = (cross_up_1h and is_bull_4h) or (cross_up_4h and is_bull_1h)
        res_death = (cross_down_1h and is_bear_4h) or (cross_down_4h and is_bear_1h)
        
        if res_golden:
            return 1, slope_4h, hist_color_4h
        if res_death:
            return -1, slope_4h, hist_color_4h
            
        return 0, 0.0, "GRAY"

    def analyze_market(self, symbol: str, state: SymbolState, df, htf_df=None):
        """分析指定标的的市场状况"""
        if df is None or df.empty:
            return None

        # 计算 4h 指标 (如果此函数被jobs调用时传入了htf_df，则在此处计算指标)
        if htf_df is not None:
            htf_df = self.calculate_htf_indicators(htf_df)

        last_idx = len(df) - 1
        if last_idx < 1:
            return None

        last_candle = df.iloc[-1]
        current_price = last_candle['close']
        current_ts = last_candle['timestamp']
        supertrend_val = last_candle['supertrend']

        # 更新 Pivot 数据库
        self.update_swing_levels(df, state)

        liq_result = self.detect_liq_reversal(df)
        cisd_result = self.detect_cisd(df)

        msgs = []

        # --- 1. CISD 策略: Swing High/Low Mitigation Alerts ---
        for level in state.swing_levels:
            if level.get('mitigated_at') == last_idx:
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

        # --- 2. Liquidation Reversal 策略: Bullish/Bearish ST Start Alerts ---
        curr_dir = last_candle['supertrend_dir']
        liq_signal_ts = df['timestamp'].iloc[liq_result['last_idx']]
        
        if liq_result['new_bull_reversal']:
            if state.last_liq_signal_ts is None or liq_signal_ts > state.last_liq_signal_ts:
                msgs.append((
                    AlertMessages.TYPE_BULLISH_ST_START,
                    AlertMessages.bullish_st_start(symbol, current_price, supertrend_val)
                ))
                state.last_liq_signal_ts = liq_signal_ts
        if liq_result['new_bear_reversal']:
            if state.last_liq_signal_ts is None or liq_signal_ts > state.last_liq_signal_ts:
                msgs.append((
                    AlertMessages.TYPE_BEARISH_ST_START,
                    AlertMessages.bearish_st_start(symbol, current_price, supertrend_val)
                ))
                state.last_liq_signal_ts = liq_signal_ts

        # --- 3. Liquidation Reversal 策略: Liquidation Spike Alerts ---
        if liq_result['short_liq_at_last']:
            msgs.append((
                AlertMessages.TYPE_SHORT_LIQ_SPIKE,
                AlertMessages.short_liq_spike(symbol, current_price, liq_result['z_up_last'])
            ))
        if liq_result['long_liq_at_last']:
            msgs.append((
                AlertMessages.TYPE_LONG_LIQ_SPIKE,
                AlertMessages.long_liq_spike(symbol, current_price, liq_result['z_down_last'])
            ))

        # --- 4. CISD 策略: Normal/Strong CISD Alerts ---
        # 使用辅助方法在 liquidity_lookback 窗口内查找最近被扫荡的 swing level
        bars_since_high, wicked_high_level = self._find_recent_wicked_level(state, last_idx, 'high')
        bars_since_low, wicked_low_level = self._find_recent_wicked_level(state, last_idx, 'low')

        if cisd_result['flag_at_last'] != 0:
            # 只有当当前信号的时间戳晚于上一次记录的时间戳时才处理
            # cisd_result['flag_at_last'] 对应的是 last_idx 的信号，即 current_ts
            if state.last_cisd_ts is None or current_ts > state.last_cisd_ts:
                origin_level = cisd_result['origin_level_at_last']
                
                if cisd_result['flag_at_last'] == 1:
                    # 看跌 CISD：检查是否有高点扫荡且价格低于被扫荡水平
                    if (bars_since_high is not None and 
                        wicked_high_level is not None and 
                        current_price < wicked_high_level):
                        msgs.append((
                            AlertMessages.TYPE_BEARISH_STRONG_CISD,
                            AlertMessages.bearish_strong_cisd(
                                symbol, current_price, origin_level, 
                                wicked_high_level, bars_since_high
                            )
                        ))
                    else:
                        msgs.append((
                            AlertMessages.TYPE_BEARISH_NORMAL_CISD,
                            AlertMessages.bearish_normal_cisd(symbol, current_price, origin_level)
                        ))
                else:
                    # 看涨 CISD：检查是否有低点扫荡且价格高于被扫荡水平
                    if (bars_since_low is not None and 
                        wicked_low_level is not None and 
                        current_price > wicked_low_level):
                        msgs.append((
                            AlertMessages.TYPE_BULLISH_STRONG_CISD,
                            AlertMessages.bullish_strong_cisd(
                                symbol, current_price, origin_level,
                                wicked_low_level, bars_since_low
                            )
                        ))
                    else:
                        msgs.append((
                            AlertMessages.TYPE_BULLISH_NORMAL_CISD,
                            AlertMessages.bullish_normal_cisd(symbol, current_price, origin_level)
                        ))
                
                state.last_cisd_ts = current_ts

        # --- 5. MACD 共振策略 ---
        # 仅当 htf_df 可用时检测
        if htf_df is not None:
            res_val, res_slope, res_color = self.check_macd_resonance(df, htf_df)
            
            # 使用 state.last_macd_resonance 防止在同一状态下重复报警
            # 只有当状态发生变化（例如从 0 -> 1），或者保持状态但这是新的K线（通过时间戳判断?）
            # 由于 check_macd_resonance 主要检测 Cross，Cross 只在特定K线发生，所以主要是检测 res_val != 0
            
            if res_val == 1:
                # Golden Resonance
                # 只有当上次状态不是 1 时才报，或者基于冷却期 (can_send_alert 控制)
                # 这里我们单纯产生 Alert，去重交给 state.can_send_alert
                msgs.append((
                    AlertMessages.TYPE_MACD_RESONANCE_GOLDEN,
                    AlertMessages.macd_resonance_golden(symbol, current_price, res_slope, res_color)
                ))
            elif res_val == -1:
                # Death Resonance
                msgs.append((
                    AlertMessages.TYPE_MACD_RESONANCE_DEATH,
                    AlertMessages.macd_resonance_death(symbol, current_price, res_slope, res_color)
                ))

        # --- 6. 计算当前最近的支撑/阻力 ---
        active_highs = [x['price'] for x in state.swing_levels if not x['mitigated'] and x['type']=='high']
        active_lows = [x['price'] for x in state.swing_levels if not x['mitigated'] and x['type']=='low']
        
        nearest_res = min([x for x in active_highs if x > current_price], default=None)
        nearest_sup = max([x for x in active_lows if x < current_price], default=None)

        result = {
            'symbol': symbol,
            'price': current_price,
            'trend_dir': "多头 🐂" if curr_dir == 1 else "空头 🐻",
            'trend_support': supertrend_val,
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
