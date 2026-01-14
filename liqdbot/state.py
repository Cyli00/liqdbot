"""
状态管理模块 - 每个标的的独立状态容器
"""

import time
from collections import OrderedDict
from .config import ALERT_COOLDOWN


SIGNAL_STRENGTH = {
    "swing_high_mitigation": 1,
    "swing_low_mitigation": 1,
    "bearish_normal_cisd": 2,
    "bullish_normal_cisd": 2,
    "bearish_strong_cisd": 3,
    "bullish_strong_cisd": 3,
    "macd_resonance_golden": 3,
    "macd_resonance_death": 3,
    "below_ma5": 2,
    "above_ma5": 2,
    "below_ma10": 2,
    "above_ma10": 2,
    "breakout_resistance_vol": 3,
    "breakdown_support_vol": 3,
}

SIGNAL_GROUPS = {
    "bullish": [
        "swing_low_mitigation",
        "bullish_normal_cisd",
        "bullish_strong_cisd",
        "macd_resonance_golden",
        "breakout_resistance_vol",
    ],
    "bearish": [
        "swing_high_mitigation",
        "bearish_normal_cisd",
        "bearish_strong_cisd",
        "macd_resonance_death",
        "breakdown_support_vol",
    ],
    "ma5": ["below_ma5", "above_ma5"],
    "ma10": ["below_ma10", "above_ma10"],
}

SIGNAL_GROUP_BY_TYPE = {
    alert_type: group for group, types in SIGNAL_GROUPS.items() for alert_type in types
}


def get_signal_group(alert_type: str) -> str:
    """获取信号所属分组"""
    return SIGNAL_GROUP_BY_TYPE.get(alert_type, alert_type)


class SymbolState:
    """每个标的的独立状态"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.reset()

    def can_send_alert(self, alert_type: str) -> bool:
        """
        检查是否可以发送该类型的alert
        规则：
        1. 冷却期外：可以发送
        2. 冷却期内但新信号强度更高：可以发送
        3. 冷却期内且新信号强度不高于上次：不发送
        """
        now = time.time()
        group = get_signal_group(alert_type)
        last_sent = self.alert_sent_times.get(group, 0)
        last_strength = self.alert_sent_strength.get(group, 0)
        new_strength = SIGNAL_STRENGTH.get(alert_type, 1)

        # 冷却期外，可以发送
        if (now - last_sent) >= ALERT_COOLDOWN:
            return True

        # 冷却期内，但新信号更强，也可以发送
        if new_strength > last_strength:
            return True

        return False

    def mark_alert_sent(self, alert_type: str):
        """记录alert发送时间和强度"""
        group = get_signal_group(alert_type)
        self.alert_sent_times[group] = time.time()
        self.alert_sent_strength[group] = SIGNAL_STRENGTH.get(alert_type, 1)

    def should_send_cisd_origin_alert(
        self, flag: int, origin_level: float, alert_type: str, *, max_history: int = 200
    ) -> bool:
        """
        CISD 起点价位去重（同一标的内）：
        - 同方向(flag) + 同起点价位(按消息展示精度round到2位) 只提醒一次
        - 若后续同 key 触发更强信号（如 normal -> strong），允许升级提醒
        - 通过 max_history 限制历史长度，避免无限增长
        """
        try:
            origin_key_level = round(float(origin_level), 2)
        except Exception:
            return True

        key = (int(flag), origin_key_level)
        new_strength = SIGNAL_STRENGTH.get(alert_type, 1)
        prev_strength = self.cisd_origin_alert_strength.get(key, 0)

        # 已提醒过且不更强：不再重复提醒
        if new_strength <= prev_strength:
            return False

        # 记录/升级提醒强度，并做简单 LRU 裁剪
        self.cisd_origin_alert_strength[key] = new_strength
        try:
            self.cisd_origin_alert_strength.move_to_end(key)
        except Exception:
            pass
        while len(self.cisd_origin_alert_strength) > max_history:
            self.cisd_origin_alert_strength.popitem(last=False)

        return True

    def reset(self):
        """重置状态（标的被重新添加时调用）"""
        self.swing_levels = []
        self.last_analysis = {}
        self.last_cisd_ts = None
        self.cisd_origin_alert_strength = OrderedDict()
        self.notified_sweeps = set()
        self.alert_sent_times = {}
        self.alert_sent_strength = {}
        # 重置数据缓存
        self.cached_df = None
        self.cached_lower_df = None
        self.cached_htf_df = None
        self.last_fetch_time = 0
        self.last_htf_fetch_time = (
            None  # HTF 数据上次拉取时间（优化：4h数据不需要每分钟拉取）
        )
        self.last_macd_resonance = 0
        self.last_macd_resonance_ts = None
        self.last_macd_check_15m_ts = None
        self.last_ma5_alert_bar_ts = None
        self.last_sr_break_15m_ts = None
        # 放量突破 15m tick 门控
        self.last_sr_break_tick_ts = None  # 上次触发放量突破的 15m tick 时间戳
        self.last_sr_break_tick_price = None  # 上次触发时的价格
        # A股 1h swing levels（用于放量突破，避免影响现有15m策略）
        self.swing_levels_1h = []
        # --- MA5/MA10 状态机 (A股) ---
        # True = 当前处于"已跌破"状态，False = 在均线上方
        self.ma5_below: bool = False
        self.ma10_below: bool = False
        self.last_ma_check_15m_ts = None  # 用于确保每根 15m K线只处理一次
        # --- MACD A股日内去重 ---
        # 格式: "YYYY-MM-DD"，当天已触发后不再重复
        self.last_macd_alert_date: str | None = None
