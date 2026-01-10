import logging
import pandas as pd
import ccxt.async_support as ccxt

from .base import DataProvider


class CryptoProvider(DataProvider):
    def __init__(self):
        self.exchange = ccxt.binance()

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int
    ) -> pd.DataFrame | None:
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        except Exception as e:
            logging.error(f"CryptoProvider fetch_ohlcv error for {symbol}: {e}")
            return None

    async def validate_symbol(self, symbol: str) -> bool:
        try:
            await self.exchange.fetch_ohlcv(symbol, "1h", limit=1)
            return True
        except Exception as e:
            logging.warning(f"CryptoProvider validate_symbol failed for {symbol}: {e}")
            return False

    @staticmethod
    def normalize_symbol(raw: str) -> str:
        raw = raw.upper()
        if "/" not in raw:
            return f"{raw}/USDT"
        return raw

    async def close(self):
        await self.exchange.close()
