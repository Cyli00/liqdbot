"""
Alert 消息模板 - 定义所有 Telegram 通知消息的格式
"""


class AlertMessages:
    """定义所有 Alert 消息模板，区分 Liquidation Reversal 和 CISD 策略"""
    
    # Alert 类型常量
    TYPE_SHORT_LIQ_SPIKE = "short_liq_spike"
    TYPE_LONG_LIQ_SPIKE = "long_liq_spike"
    TYPE_BULLISH_ST_START = "bullish_st_start"
    TYPE_BEARISH_ST_START = "bearish_st_start"
    TYPE_SWING_HIGH_MITIGATION = "swing_high_mitigation"
    TYPE_SWING_LOW_MITIGATION = "swing_low_mitigation"
    TYPE_BEARISH_NORMAL_CISD = "bearish_normal_cisd"
    TYPE_BULLISH_NORMAL_CISD = "bullish_normal_cisd"
    TYPE_BEARISH_STRONG_CISD = "bearish_strong_cisd"
    TYPE_BULLISH_STRONG_CISD = "bullish_strong_cisd"
    
    # ==================== Liquidation Reversal 策略 ====================
    @staticmethod
    def short_liq_spike(symbol: str, price: float) -> str:
        """Short Liquidation Spike - 空头趋势中检测到巨额上行量"""
        return (
            f"🔔 **流动性反转，向下插针**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 价格: `{price:.2f}`\n"
            f"📝 说明: 空头趋势中出现巨额买单（可能诱多/空头止损）"
        )
    
    @staticmethod
    def long_liq_spike(symbol: str, price: float) -> str:
        """Long Liquidation Spike - 多头趋势中检测到巨额下行量"""
        return (
            f"🔔 **流动性反转，向上插针**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 价格: `{price:.2f}`\n"
            f"📝 说明: 多头趋势中出现巨额卖单（可能诱空/多头止损）"
        )
    
    @staticmethod
    def bullish_st_start(symbol: str, price: float, supertrend: float) -> str:
        """Bullish ST Start - Supertrend 翻多（挤仓确认）"""
        return (
            f"🚀 **短期趋势反转，多头启动**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 价格: `{price:.2f}`\n"
            f"📈 Supertrend: `{supertrend:.2f}`\n"
            f"📝 说明: 挤仓后 Supertrend 翻多确认，趋势反转向上"
        )
    
    @staticmethod
    def bearish_st_start(symbol: str, price: float, supertrend: float) -> str:
        """Bearish ST Start - Supertrend 翻空（挤仓确认）"""
        return (
            f"📉 **短期趋势反转，空头启动**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 价格: `{price:.2f}`\n"
            f"📈 Supertrend: `{supertrend:.2f}`\n"
            f"📝 说明: 挤仓后 Supertrend 翻空确认，趋势反转向下"
        )
    
    # ==================== CISD 策略 ====================
    @staticmethod
    def swing_high_mitigation(symbol: str, price: float, level: float) -> str:
        """Swing High Mitigation - 上方阻力位被触及"""
        return (
            f"🔔 **上方支撑被扫除**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 当前价: `{price:.2f}`\n"
            f"🎯 阻力位: `{level:.2f}`\n"
        )
    
    @staticmethod
    def swing_low_mitigation(symbol: str, price: float, level: float) -> str:
        """Swing Low Mitigation - 下方支撑位被触及"""
        return (
            f"🔔 **下方支撑被扫除**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 当前价: `{price:.2f}`\n"
            f"🎯 支撑位: `{level:.2f}`\n"
        )
    
    @staticmethod
    def bearish_normal_cisd(symbol: str, price: float, origin_level: float) -> str:
        """Bearish Normal CISD - 普通看跌 CISD 信号"""
        return (
            f"🔻 **普通看跌 CISD 信号**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 当前价: `{price:.2f}`\n"
            f"🎯 起点价位: `{origin_level:.2f}`\n"
            f"📝 说明: 看跌结构确认，可能继续下跌"
        )
    
    @staticmethod
    def bullish_normal_cisd(symbol: str, price: float, origin_level: float) -> str:
        """Bullish Normal CISD - 普通看涨 CISD 信号"""
        return (
            f"📈 **普通看涨 CISD 信号**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 当前价: `{price:.2f}`\n"
            f"🎯 起点价位: `{origin_level:.2f}`\n"
            f"📝 说明: 看涨结构确认，可能继续上涨"
        )
    
    @staticmethod
    def bearish_strong_cisd(symbol: str, price: float, origin_level: float, sweep_level: float, bars_since: int) -> str:
        """Strong Bearish CISD - 带流动性扫单的强看跌 CISD 信号"""
        return (
            f"🔻🔻 **强看跌 CISD 信号**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 当前价: `{price:.2f}`\n"
            f"🎯 起点价位: `{origin_level:.2f}`\n"
            f"💥 扫单价位: `{sweep_level:.2f}` ({bars_since} 根K线内)\n"
            f"📝 说明: 扫除上方流动性后反转下跌，高概率做空信号"
        )
    
    @staticmethod
    def bullish_strong_cisd(symbol: str, price: float, origin_level: float, sweep_level: float, bars_since: int) -> str:
        """Strong Bullish CISD - 带流动性扫单的强看涨 CISD 信号"""
        return (
            f"🔺🔺 **强看涨 CISD 信号**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 当前价: `{price:.2f}`\n"
            f"🎯 起点价位: `{origin_level:.2f}`\n"
            f"💥 扫单价位: `{sweep_level:.2f}` ({bars_since} 根K线内)\n"
            f"📝 说明: 扫除下方流动性后反转上涨，高概率做多信号"
        )
    
    # ==================== MACD 共振策略 ====================
    TYPE_MACD_RESONANCE_GOLDEN = "macd_resonance_golden"
    TYPE_MACD_RESONANCE_DEATH = "macd_resonance_death"

    @staticmethod
    def macd_resonance_golden(symbol: str, price: float, slope: float, hist_state: str) -> str:
        """MACD Resonance Golden Cross"""
        emoji_map = {
            "AQUA": "🟢 动能强劲 (Aqua)",
            "BLUE": "⚪️ 动能减弱 (Blue)",
            "RED": "🔴 动能反向增强 (Red)",
            "MAROON": "🟡 动能反向减弱 (Maroon)"
        }
        state_str = emoji_map.get(hist_state, hist_state)
        slope_emoji = "📈" if slope > 0 else "📉"
        
        return (
            f"🚀 **MACD 1h/4h 共振金叉**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 当前价: `{price:.2f}`\n"
            f"{slope_emoji} 4h Signal斜率: `{slope:+.4f}`\n"
            f"📊 4h 动能: {state_str}\n"
            f"📝 说明: 1h 与 4h 周期趋势多头共振"
        )

    @staticmethod
    def macd_resonance_death(symbol: str, price: float, slope: float, hist_state: str) -> str:
        """MACD Resonance Death Cross"""
        emoji_map = {
            "AQUA": "🟢 动能反向强劲 (Aqua)",
            "BLUE": "⚪️ 动能反向减弱 (Blue)",
            "RED": "🔴 动能强劲 (Red)",
            "MAROON": "🟡 动能减弱 (Maroon)"
        }
        state_str = emoji_map.get(hist_state, hist_state)
        slope_emoji = "📈" if slope > 0 else "📉"
        
        return (
            f"📉 **MACD 1h/4h 共振死叉**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 当前价: `{price:.2f}`\n"
            f"{slope_emoji} 4h Signal斜率: `{slope:+.4f}`\n"
            f"📊 4h 动能: {state_str}\n"
            f"📝 说明: 1h 与 4h 周期趋势空头共振"
        )
