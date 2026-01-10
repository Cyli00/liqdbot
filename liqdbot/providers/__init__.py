"""
数据提供者模块 - 统一抽象层支持多市场数据源
"""

from .base import DataProvider, MarketType, detect_market_type
from .crypto import CryptoProvider
from .ashare import AShareProvider, is_ashare_trading_time

__all__ = [
    "DataProvider",
    "MarketType",
    "detect_market_type",
    "CryptoProvider",
    "AShareProvider",
    "is_ashare_trading_time",
]
