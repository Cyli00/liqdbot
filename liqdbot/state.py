"""
状态管理模块 - 每个标的的独立状态容器
"""
import time
from .config import ALERT_COOLDOWN


# 信号强度等级定义（数值越大越强）
SIGNAL_STRENGTH = {
    # Liquidation Reversal 策略
    "short_liq_spike": 1,        # 普通爆仓信号
    "long_liq_spike": 1,
    "bullish_st_start": 2,       # ST确认信号（更强）
    "bearish_st_start": 2,
    
    # CISD 策略
    "swing_high_mitigation": 1,  # 普通扫单
    "swing_low_mitigation": 1,
    "bearish_normal_cisd": 2,    # 普通CISD
    "bullish_normal_cisd": 2,
    "bearish_strong_cisd": 3,    # 强CISD（最强）
    "bullish_strong_cisd": 3,
    
    # MACD 共振策略
    "macd_resonance_golden": 3,
    "macd_resonance_death": 3,
}

# 信号分组（同组内比较强度）
SIGNAL_GROUPS = {
    "bullish": ["long_liq_spike", "swing_low_mitigation", "bullish_normal_cisd", "bullish_strong_cisd", "bullish_st_start", "macd_resonance_golden"],
    "bearish": ["short_liq_spike", "swing_high_mitigation", "bearish_normal_cisd", "bearish_strong_cisd", "bearish_st_start", "macd_resonance_death"],
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
    
    def reset(self):
        """重置状态（标的被重新添加时调用）"""
        self.swing_levels = []
        self.last_analysis = {}
        self.last_liq_signal_ts = None
        self.last_cisd_ts = None
        self.notified_sweeps = set()
        self.alert_sent_times = {}
        self.alert_sent_strength = {}
        # 重置数据缓存
        self.cached_df = None
        self.cached_lower_df = None
        self.cached_htf_df = None
        self.last_fetch_time = 0
        self.last_macd_resonance = 0
