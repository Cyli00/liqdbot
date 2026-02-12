"""
状态管理模块 - 每个标的的独立状态容器
"""
import time
from collections import OrderedDict
from .config import ALERT_COOLDOWN


# 信号强度等级定义（数值越大越强）
SIGNAL_STRENGTH = {
    # CISD 策略
    "swing_high_mitigation": 1,  # 普通扫单
    "swing_low_mitigation": 1,
    "bearish_strong_cisd": 3,    # 强CISD（最强）
    "bullish_strong_cisd": 3,
    
    # MACD 共振策略
    "macd_resonance_golden": 3,
    "macd_resonance_death": 3,
}

# 信号分组（同组内比较强度）
SIGNAL_GROUPS = {
    "bullish": ["swing_low_mitigation", "bullish_strong_cisd", "macd_resonance_golden"],
    "bearish": ["swing_high_mitigation", "bearish_strong_cisd", "macd_resonance_death"],
}

SIGNAL_GROUP_BY_TYPE = {
    alert_type: group
    for group, types in SIGNAL_GROUPS.items()
    for alert_type in types
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

    def should_send_cisd_origin_alert(self, flag: int, origin_level: float, alert_type: str, *, max_history: int = 200) -> bool:
        """
        CISD 起点价位去重（同一标的内）：
        - 同方向(flag) + 同起点价位(按消息展示精度round到2位) 只提醒一次
        - 若后续同 key 触发更强信号，允许升级提醒
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
        self.sweep_history = []
        self.sweep_history_keys = set()
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
        self.last_htf_fetch_time = None  # HTF 数据上次拉取时间（优化：4h数据不需要每分钟拉取）
        self.last_macd_resonance = 0
        self.last_macd_resonance_ts = None  # 上次MACD共振触发的交叉时间戳
        self.last_macd_check_1h_ts = None  # 上次MACD检测时的1小时K线时间戳
