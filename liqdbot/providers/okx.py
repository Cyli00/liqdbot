import logging
import time
import pandas as pd
import ccxt.async_support as ccxt

from .base import DataProvider
from ..config import SLOW_THRESHOLD_MS

logger = logging.getLogger(__name__)


class OkxProvider(DataProvider):
    """OKX数据提供者 - 用于获取BTC/USDT等现货数据"""

    def __init__(self):
        self.exchange = ccxt.okx()

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int
    ) -> pd.DataFrame | None:
        start_ms = time.perf_counter_ns() // 1_000_000
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            duration_ms = (time.perf_counter_ns() // 1_000_000) - start_ms
            rows = len(df)
            if duration_ms > SLOW_THRESHOLD_MS:
                logger.warning(
                    f"event=fetch_ohlcv_slow provider=okx symbol={symbol} "
                    f"tf={timeframe} limit={limit} duration_ms={duration_ms} rows={rows}"
                )
            else:
                logger.debug(
                    f"event=fetch_ohlcv provider=okx symbol={symbol} "
                    f"tf={timeframe} limit={limit} duration_ms={duration_ms} rows={rows}"
                )
            return df
        except Exception as e:
            duration_ms = (time.perf_counter_ns() // 1_000_000) - start_ms
            logger.exception(
                f"event=fetch_ohlcv_error provider=okx symbol={symbol} "
                f"tf={timeframe} limit={limit} duration_ms={duration_ms} err={e}"
            )
            return None

    async def fetch_ticker(self, symbol: str) -> dict | None:
        """获取实时ticker数据（包含最新价格）"""
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return ticker
        except Exception as e:
            logger.exception(
                f"event=fetch_ticker_error provider=okx symbol={symbol} err={e}"
            )
            return None

    async def validate_symbol(self, symbol: str) -> bool:
        try:
            await self.exchange.fetch_ohlcv(symbol, "1h", limit=1)
            return True
        except Exception:
            logger.exception(
                f"event=validate_symbol_error provider=okx symbol={symbol}"
            )
            return False

    @staticmethod
    def normalize_symbol(raw: str) -> str:
        raw = raw.upper()
        if "/" not in raw:
            return f"{raw}/USDT"
        return raw

    async def close(self):
        await self.exchange.close()
