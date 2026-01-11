"""
数据提供者模块 - 统一抽象层支持多市场数据源
"""

from .base import DataProvider, MarketType, detect_market_type
from .crypto import CryptoProvider
from .akshare import AkshareProvider, is_akshare_trading_time

__all__ = [
    "DataProvider",
    "MarketType",
    "detect_market_type",
    "CryptoProvider",
    "AkshareProvider",
    "is_akshare_trading_time",
]
