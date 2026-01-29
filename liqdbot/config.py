"""
配置模块 - 仅从环境变量读取 Telegram 配置，其余使用默认值
"""
import os
import logging
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 降低 httpx 和 telegram 网络相关的日志级别，避免网络波动时大量 ERROR 日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext._utils.networkloop").setLevel(logging.WARNING)


# --- Telegram 配置 ---
TG_TOKEN = os.getenv("TG_TOKEN")
try:
    TG_CHAT_ID = int(os.getenv("TG_CHAT_ID"))
except (TypeError, ValueError):
    logging.error("❌ 请在 .env 中正确配置 TG_CHAT_ID (应为整数)")
    TG_CHAT_ID = 0

# --- 交易配置 ---
# 支持多个交易对，例如: ["BTC/USDT", "ETH/USDT"]
DEFAULT_SYMBOL = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAME = "1h"  # 强制基于1小时
LOWER_TIMEFRAME = "15m"  # 用于上下行量
FETCH_LIMIT = 400  # 主要 K 线拉取数量
MONITOR_INTERVAL = 60  # 1分钟监控一次

# --- 策略参数（贴合 Pine） ---
PIVOT_LEN = 12
EXPIRY_BARS = 100
LIQUIDITY_LOOKBACK = 10
HIDE_EXPIRED_LEVELS = True
HIDE_MITIGATED_LEVELS = False
CISD_TOLERANCE = 0.7
CISD_DEDUP_ENABLED = True

# --- 网络配置 ---
MAX_RETRIES = 3
RETRY_DELAY = 5  # 秒

# --- Alert 去重配置 ---
ALERT_COOLDOWN = 180  # 3分钟内同类型alert不重复发送
