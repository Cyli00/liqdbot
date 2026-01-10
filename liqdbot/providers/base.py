from abc import ABC, abstractmethod
from enum import Enum
import pandas as pd


class MarketType(Enum):
    CRYPTO = "crypto"
    A_SHARE = "ashare"


def detect_market_type(symbol: str) -> MarketType:
    symbol_upper = symbol.upper()

    if symbol_upper.startswith(("SH", "SZ")) or ".X" in symbol_upper:
        return MarketType.A_SHARE

    if symbol.replace(".", "").isdigit() and len(symbol.replace(".", "")) == 6:
        return MarketType.A_SHARE

    return MarketType.CRYPTO


class DataProvider(ABC):
    @abstractmethod
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int
    ) -> pd.DataFrame | None:
        pass

    @abstractmethod
    async def validate_symbol(self, symbol: str) -> bool:
        pass

    @staticmethod
    @abstractmethod
    def normalize_symbol(raw: str) -> str:
        pass

    @staticmethod
    def standardize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        df = df.reset_index(drop=True) if "index" not in df.columns else df

        column_mapping = {
            "日期": "timestamp",
            "time": "timestamp",
            "date": "timestamp",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
        }

        df = df.rename(columns=column_mapping)

        required_cols = ["timestamp", "open", "high", "low", "close", "volume"]
        for col in required_cols:
            if col not in df.columns:
                df[col] = 0 if col == "volume" else None

        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        return df[required_cols]
