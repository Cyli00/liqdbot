"""
Alert 消息模板 - 定义所有 Telegram 通知消息的格式
"""


class AlertMessages:
    """定义所有 Alert 消息模板，区分 CISD 和 MACD 共振策略"""

    # Alert 类型常量 - CISD 策略
    TYPE_SWING_HIGH_MITIGATION = "swing_high_mitigation"
    TYPE_SWING_LOW_MITIGATION = "swing_low_mitigation"
    TYPE_BEARISH_NORMAL_CISD = "bearish_normal_cisd"
    TYPE_BULLISH_NORMAL_CISD = "bullish_normal_cisd"
    TYPE_BEARISH_STRONG_CISD = "bearish_strong_cisd"
    TYPE_BULLISH_STRONG_CISD = "bullish_strong_cisd"

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
    def bearish_strong_cisd(
        symbol: str,
        price: float,
        origin_level: float,
        sweep_level: float,
        bars_since: int,
    ) -> str:
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
    def bullish_strong_cisd(
        symbol: str,
        price: float,
        origin_level: float,
        sweep_level: float,
        bars_since: int,
    ) -> str:
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

    # ==================== MA5 策略 (A股专属) ====================
    TYPE_BELOW_MA5 = "below_ma5"

    # ==================== 15m 放量突破/跌破 ====================
    TYPE_BREAKOUT_RESISTANCE_VOL = "breakout_resistance_vol"
    TYPE_BREAKDOWN_SUPPORT_VOL = "breakdown_support_vol"

    @staticmethod
    def breakout_resistance_vol(
        symbol: str, price: float, level: float, rvol: float
    ) -> str:
        return (
            f"🚀 **放量突破阻力位** (15m)\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 收盘价: `{price:.2f}`\n"
            f"🎯 阻力位: `{level:.2f}`\n"
            f"📊 RVOL: `{rvol:.2f}x`"
        )

    @staticmethod
    def breakdown_support_vol(
        symbol: str, price: float, level: float, rvol: float
    ) -> str:
        return (
            f"⚠️ **放量跌破支撑位** (15m)\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 收盘价: `{price:.2f}`\n"
            f"🎯 支撑位: `{level:.2f}`\n"
            f"📊 RVOL: `{rvol:.2f}x`"
        )

    @staticmethod
    def _get_slope_grade_desc(grade: int) -> str:
        """
        根据分位数等级返回描述
        1级: < q20 (很弱)
        2级: q20-q40 (较弱)
        3级: q40-q60 (中等)
        4级: q60-q80 (较强)
        5级: > q80 (很强)
        """
        grade_map = {
            1: "⚪ 一级（很弱）",
            2: "🟡 二级（较弱）",
            3: "🟠 三级（中等）",
            4: "🟢 四级（较强）",
            5: "⚡ 五级（很强）",
        }
        return grade_map.get(grade, "❓ 未知")

    @staticmethod
    def _get_zero_position_desc(zero_pos: str, is_golden: bool) -> str:
        """
        根据零轴位置返回信号类型描述
        金叉：零轴下方=左侧做多信号，零轴上方=右侧做多信号
        死叉：零轴上方=左侧做空信号，零轴下方=右侧做空信号
        """
        if is_golden:
            if zero_pos == "below":
                return "🔵 左侧信号（零轴下方金叉，底部反转）"
            else:
                return "🟢 右侧信号（零轴上方金叉，趋势延续）"
        else:
            if zero_pos == "above":
                return "🔴 左侧信号（零轴上方死叉，顶部反转）"
            else:
                return "🟠 右侧信号（零轴下方死叉，趋势延续）"

    @staticmethod
    def macd_resonance_golden(symbol: str, price: float, info: dict) -> str:
        """MACD Resonance Golden Cross"""
        hist_state = info.get("hist_color", "GRAY")
        emoji_map = {
            "AQUA": "🟢 动能强劲",
            "BLUE": "⚪️ 动能减弱",
            "RED": "🔴 动能反向增强",
            "MAROON": "🟡 动能反向减弱",
        }
        state_str = emoji_map.get(hist_state, hist_state)

        # 时间周期标签
        ltf_label = info.get("ltf_label", "1h")
        htf_label = info.get("htf_label", "4h")

        dif_slope_grade_1h = info.get("dif_slope_grade_1h", 0)
        grade_desc_1h = AlertMessages._get_slope_grade_desc(dif_slope_grade_1h)

        # 零轴位置
        zero_pos_1h = info.get("zero_pos_1h", "unknown")
        zero_pos_4h = info.get("zero_pos_4h", "unknown")
        pos_desc_1h = AlertMessages._get_zero_position_desc(zero_pos_1h, True)
        pos_desc_4h = AlertMessages._get_zero_position_desc(zero_pos_4h, True)

        # 时间间隔
        time_gap = info.get("cross_time_gap_hours")
        if time_gap is not None:
            if time_gap < 1:
                time_gap_str = f"{int(time_gap * 60)} 分钟"
            else:
                time_gap_str = f"{time_gap:.1f} 小时"
        else:
            time_gap_str = "未知"

        return (
            f"🚀 **MACD {ltf_label}/{htf_label} 共振金叉**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 当前价: `{price:.2f}`\n"
            f"\n"
            f"**📊 {ltf_label} 周期:**\n"
            f"  • 动量强度: {grade_desc_1h}\n"
            f"  • 位置: {pos_desc_1h}\n"
            f"\n"
            f"**📊 {htf_label} 周期:**\n"
            f"  • 位置: {pos_desc_4h}\n"
            f"  • 动能: {state_str}\n"
            f"\n"
            f"⏱ **交叉时间间隔:** {time_gap_str}\n"
            f"📝 说明: {ltf_label} 与 {htf_label} 周期趋势多头共振"
        )

    @staticmethod
    def macd_resonance_death(symbol: str, price: float, info: dict) -> str:
        """MACD Resonance Death Cross"""
        hist_state = info.get("hist_color", "GRAY")
        emoji_map = {
            "AQUA": "🟢 动能反向强劲",
            "BLUE": "⚪️ 动能反向减弱",
            "RED": "🔴 动能强劲",
            "MAROON": "🟡 动能减弱",
        }
        state_str = emoji_map.get(hist_state, hist_state)

        # 时间周期标签
        ltf_label = info.get("ltf_label", "1h")
        htf_label = info.get("htf_label", "4h")

        dif_slope_grade_1h = info.get("dif_slope_grade_1h", 0)
        grade_desc_1h = AlertMessages._get_slope_grade_desc(dif_slope_grade_1h)

        # 零轴位置
        zero_pos_1h = info.get("zero_pos_1h", "unknown")
        zero_pos_4h = info.get("zero_pos_4h", "unknown")
        pos_desc_1h = AlertMessages._get_zero_position_desc(zero_pos_1h, False)
        pos_desc_4h = AlertMessages._get_zero_position_desc(zero_pos_4h, False)

        # 时间间隔
        time_gap = info.get("cross_time_gap_hours")
        if time_gap is not None:
            if time_gap < 1:
                time_gap_str = f"{int(time_gap * 60)} 分钟"
            else:
                time_gap_str = f"{time_gap:.1f} 小时"
        else:
            time_gap_str = "未知"

        return (
            f"📉 **MACD {ltf_label}/{htf_label} 共振死叉**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 当前价: `{price:.2f}`\n"
            f"\n"
            f"**📊 {ltf_label} 周期:**\n"
            f"  • 动量强度: {grade_desc_1h}\n"
            f"  • 位置: {pos_desc_1h}\n"
            f"\n"
            f"**📊 {htf_label} 周期:**\n"
            f"  • 位置: {pos_desc_4h}\n"
            f"  • 动能: {state_str}\n"
            f"\n"
            f"⏱ **交叉时间间隔:** {time_gap_str}\n"
            f"📝 说明: {ltf_label} 与 {htf_label} 周期趋势空头共振"
        )

    @staticmethod
    def below_ma5(symbol: str, price: float, ma5: float) -> str:
        diff_pct = (price - ma5) / ma5 * 100
        return (
            f"⚠️ **跌破5日均线**\n"
            f"📍 标的: `{symbol}`\n"
            f"💰 当前价: `{price:.2f}`\n"
            f"📊 MA5: `{ma5:.2f}`\n"
            f"📉 偏离: `{diff_pct:.2f}%`"
        )
