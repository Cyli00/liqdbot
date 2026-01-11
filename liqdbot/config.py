"""
配置模块 - 从环境变量加载所有配置
"""

import os
import logging
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# 降低 httpx 和 telegram 网络相关的日志级别，避免网络波动时大量 ERROR 日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext._utils.networkloop").setLevel(logging.WARNING)


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

# --- 网络配置 ---
MAX_RETRIES = 3
RETRY_DELAY = 5  # 秒

# --- Alert 去重配置 ---
ALERT_COOLDOWN = 180  # 3分钟内同类型alert不重复发送

# --- A股配置 ---
DEFAULT_ASHARE_SYMBOL = os.getenv("ASHARE_SYMBOL", "sh000001")  # 默认上证指数
ASHARE_TIMEFRAME = "60m"
ASHARE_HTF_TIMEFRAME = "60m"  # MACD 共振高周期
ASHARE_LTF_TIMEFRAME = "15m"  # MACD 共振低周期
ASHARE_LOWER_TIMEFRAME = "15m"  # 上下行量计算
ASHARE_MA_PERIOD = 5  # 5日均线

# --- 15m 放量突破/跌破配置 ---
# 仅对以下标的启用（规范化后匹配）
SR_BREAKOUT_SYMBOLS = {"BTC/USDT", "sh000001"}
# RVOL = vol / SMA(vol, N)，N 按标的分别设定
RVOL_N_CRYPTO = int(os.getenv("RVOL_N_CRYPTO", "96"))  # BTC: 96 根 15m ≈ 24h
RVOL_N_ASHARE = int(os.getenv("RVOL_N_ASHARE", "48"))  # 上证: 48 根 15m ≈ 3 个交易日
RVOL_THRESHOLD = float(os.getenv("RVOL_THRESHOLD", "2.0"))  # 放量阈值
