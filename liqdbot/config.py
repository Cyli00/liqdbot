"""
配置模块 - 从环境变量加载敏感配置，其他使用默认值
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

# --- Telegram 配置（敏感，从环境变量读取） ---
TG_TOKEN = os.getenv("TG_TOKEN")
try:
    TG_CHAT_ID = int(os.getenv("TG_CHAT_ID"))
except (TypeError, ValueError):
    logging.error("❌ 请在 .env 中正确配置 TG_CHAT_ID (应为整数)")
    TG_CHAT_ID = 0

# --- 性能调优配置 ---
STATUS_MAX_CONCURRENCY = 4  # /status 命令并发拉取数据的最大并发数
SLOW_THRESHOLD_MS = 800  # 慢请求阈值（毫秒）
AKSHARE_CACHE_TTL_S = 600  # A股数据缓存 TTL（秒）

# --- Crypto 交易配置 ---
DEFAULT_CRYPTO_SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
]
DEFAULT_SYMBOL = DEFAULT_CRYPTO_SYMBOLS[0]  # 兼容旧代码
TIMEFRAME = "1h"  # 主周期
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

# --- 网络配置 ---
MAX_RETRIES = 3
RETRY_DELAY = 5  # 秒

# --- Alert 去重配置 ---
ALERT_COOLDOWN = 180  # 3分钟内同类型alert不重复发送

# --- MACD 共振配置 ---
MACD_RESONANCE_ENFORCE_TIME_GAP = False  # 是否强制要求交叉时间在共振窗口内
MACD_RESONANCE_MAX_GAP_MULTIPLIER = 1.0  # 共振窗口倍数

# --- A股配置 ---
DEFAULT_AKSHARE_SYMBOLS = [
    "sh000001",  # 上证指数
    "sh000688",  # 科创50
    "sz399006",  # 创业板指
]
DEFAULT_AKSHARE_SYMBOL = DEFAULT_AKSHARE_SYMBOLS[0]  # 兼容旧代码
AKSHARE_TIMEFRAME = "15m"  # A股主周期
AKSHARE_HTF_TIMEFRAME = "60m"  # MACD 共振高周期
AKSHARE_LTF_TIMEFRAME = "15m"  # MACD 共振低周期
AKSHARE_LOWER_TIMEFRAME = "15m"  # 上下行量计算
AKSHARE_MA_PERIOD = 5  # 5日均线
AKSHARE_MA5_BREAK_PCT = 1.0  # MA5 跌破阈值 (%)
AKSHARE_MA10_PERIOD = 10  # 10日均线
AKSHARE_MA10_BREAK_PCT = 1.0  # MA10 跌破阈值 (%)
AKSHARE_OPEN_COOLDOWN_BARS = 2  # 开盘后跳过前N根K线的MACD共振检测
AKSHARE_CISD_TOLERANCE = 0.75  # A股 CISD 容忍度
AKSHARE_LIQUIDITY_LOOKBACK = 10  # 10 根 15m ≈ 2.5 小时

# --- 放量突破/跌破配置 ---
SR_BREAKOUT_SYMBOLS = {"BTC/USDT", "sh000001"}
RVOL_N_CRYPTO_1H = 24  # BTC: 24 根 1h ≈ 24h
RVOL_N_AKSHARE_1H = 12  # 上证: 12 根 1h ≈ 3 个交易日
RVOL_THRESHOLD = 2.0  # 放量阈值
RVOL_N_CRYPTO = 96  # 兼容旧配置
RVOL_N_AKSHARE = 48  # 兼容旧配置
