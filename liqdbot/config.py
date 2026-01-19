"""
配置模块 - 从环境变量加载所有配置
"""

import os
import logging
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# --- 日志配置 ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_log_level_map = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
_effective_log_level = _log_level_map.get(LOG_LEVEL, logging.INFO)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=_effective_log_level,
)

# 降低 httpx 和 telegram 网络相关的日志级别，避免网络波动时大量 ERROR 日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext._utils.networkloop").setLevel(logging.WARNING)

# --- 性能调优配置 ---
# /status 命令并发拉取数据的最大并发数
STATUS_MAX_CONCURRENCY = int(os.getenv("STATUS_MAX_CONCURRENCY", "4"))
# 慢请求阈值（毫秒），超过此值会输出 WARNING 日志
SLOW_THRESHOLD_MS = int(os.getenv("SLOW_THRESHOLD_MS", "800"))
# A股数据缓存 TTL（秒），同一根已收盘K线在此时间内复用缓存
AKSHARE_CACHE_TTL_S = int(os.getenv("AKSHARE_CACHE_TTL_S", "600"))


def getenv_bool(key: str, default: str = "false") -> bool:
    """从环境变量读取布尔值"""
    return os.getenv(key, default).lower() in ("1", "true", "yes", "y")


# --- Telegram 配置 ---
TG_TOKEN = os.getenv("TG_TOKEN")
try:
    TG_CHAT_ID = int(os.getenv("TG_CHAT_ID"))
except (TypeError, ValueError):
    logging.error("❌ 请在 .env 中正确配置 TG_CHAT_ID (应为整数)")
    TG_CHAT_ID = 0

# --- 交易配置 ---
DEFAULT_SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
TIMEFRAME = os.getenv("TIMEFRAME", "1h")  # 强制基于1小时
LOWER_TIMEFRAME = os.getenv("LOWER_TIMEFRAME", "15m")  # 用于上下行量
FETCH_LIMIT = int(os.getenv("FETCH_LIMIT", "400"))  # 主要 K 线拉取数量
MONITOR_INTERVAL = 60  # 1分钟监控一次

# --- 策略参数（贴合 Pine） ---
PIVOT_LEN = int(os.getenv("PIVOT_LEN", "12"))
EXPIRY_BARS = int(os.getenv("EXPIRY_BARS", "100"))
LIQUIDITY_LOOKBACK = int(os.getenv("LIQUIDITY_LOOKBACK", "10"))
HIDE_EXPIRED_LEVELS = getenv_bool("HIDE_EXPIRED_LEVELS", "true")
HIDE_MITIGATED_LEVELS = getenv_bool("HIDE_MITIGATED_LEVELS", "false")
CISD_TOLERANCE = float(os.getenv("CISD_TOLERANCE", "0.7"))

# --- A股 CISD 专用参数 ---
AKSHARE_CISD_TOLERANCE = float(os.getenv("AKSHARE_CISD_TOLERANCE", "0.75"))  # A股 15m CISD 提高容忍度过滤噪声
AKSHARE_LIQUIDITY_LOOKBACK = int(os.getenv("AKSHARE_LIQUIDITY_LOOKBACK", "10"))  # 10 根 15m ≈ 2.5 小时

# --- 网络配置 ---
MAX_RETRIES = 3
RETRY_DELAY = 5  # 秒

# --- Alert 去重配置 ---
ALERT_COOLDOWN = 180  # 3分钟内同类型alert不重复发送

# --- MACD 共振配置 ---
# 是否强制要求 1h/4h（或 15m/60m）交叉时间在共振窗口内才触发（窗口=HTF周期小时数 * multiplier）
MACD_RESONANCE_ENFORCE_TIME_GAP = getenv_bool(
    "MACD_RESONANCE_ENFORCE_TIME_GAP", "false"
)
# 共振窗口倍数：1.0 表示窗口=HTF周期长度（如 4h -> 4 小时）
MACD_RESONANCE_MAX_GAP_MULTIPLIER = float(
    os.getenv("MACD_RESONANCE_MAX_GAP_MULTIPLIER", "1.0")
)

# --- A股配置 ---
DEFAULT_AKSHARE_SYMBOL = os.getenv("AKSHARE_SYMBOL", "sh000001")  # 默认上证指数
# 默认监控的 A 股标的列表
DEFAULT_AKSHARE_SYMBOLS = [
    "sh000001",  # 上证指数
    "sh000688",  # 科创50
    "sz399006",  # 创业板指
]
AKSHARE_TIMEFRAME = "15m"  # A股主周期改为15m，用于MACD共振低周期
AKSHARE_HTF_TIMEFRAME = "60m"  # MACD 共振高周期
AKSHARE_LTF_TIMEFRAME = "15m"  # MACD 共振低周期（与主周期一致）
AKSHARE_LOWER_TIMEFRAME = "15m"  # 上下行量计算
AKSHARE_MA_PERIOD = 5  # 5日均线
AKSHARE_MA5_BREAK_PCT = float(
    os.getenv("AKSHARE_MA5_BREAK_PCT", "1.0")
)  # MA5 跌破阈值 (%)
AKSHARE_MA10_PERIOD = 10  # 10日均线
AKSHARE_MA10_BREAK_PCT = float(
    os.getenv("AKSHARE_MA10_BREAK_PCT", "1.0")
)  # MA10 跌破阈值 (%)
AKSHARE_OPEN_COOLDOWN_BARS = 2  # 开盘后跳过前N根K线的MACD共振检测

# --- 放量突破/跌破配置 (1h RVOL, 15m 确认) ---
# 仅对以下标的启用（规范化后匹配）
SR_BREAKOUT_SYMBOLS = {"BTC/USDT", "sh000001"}
# RVOL 1H = current_1h_vol / SMA(prev_1h_vol, N)，N 按标的分别设定
RVOL_N_CRYPTO_1H = int(os.getenv("RVOL_N_CRYPTO_1H", "24"))  # BTC: 24 根 1h ≈ 24h
RVOL_N_AKSHARE_1H = int(
    os.getenv("RVOL_N_AKSHARE_1H", "12")
)  # 上证: 12 根 1h ≈ 3 个交易日 * 4h
RVOL_THRESHOLD = float(os.getenv("RVOL_THRESHOLD", "2.0"))  # 放量阈值
# 保留旧配置用于兼容（可后续移除）
RVOL_N_CRYPTO = int(os.getenv("RVOL_N_CRYPTO", "96"))
RVOL_N_AKSHARE = int(os.getenv("RVOL_N_AKSHARE", "48"))
