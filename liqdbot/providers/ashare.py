import asyncio
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo
import pandas as pd

from .base import DataProvider

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

TIMEFRAME_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "60m": "60",
    "4h": "240",
    "1d": "daily",
    "1w": "weekly",
    "1M": "monthly",
}


def is_ashare_trading_time() -> bool:
    """判断当前是否为A股交易时段（北京时间 9:30-11:30, 13:00-15:00，周一至周五）"""
    now = datetime.now(SHANGHAI_TZ)

    if now.weekday() >= 5:
        return False

    current_time = now.time()
    morning_session = (time(9, 30), time(11, 30))
    afternoon_session = (time(13, 0), time(15, 0))

    return (
        morning_session[0] <= current_time <= morning_session[1]
        or afternoon_session[0] <= current_time <= afternoon_session[1]
    )


class AShareProvider(DataProvider):
    def __init__(self):
        self._ak = None

    def _get_akshare(self):
        if self._ak is None:
            import akshare as ak

            self._ak = ak
        return self._ak

    def _extract_stock_code(self, symbol: str) -> str:
        symbol = symbol.lower().strip()
        if symbol.startswith(("sh", "sz")):
            return symbol[2:]
        if ".x" in symbol:
            return symbol.split(".")[0]
        return symbol

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int
    ) -> pd.DataFrame | None:
        try:
            ak = self._get_akshare()
            period = TIMEFRAME_MAP.get(timeframe, timeframe)
            stock_code = self._extract_stock_code(symbol)

            loop = asyncio.get_running_loop()

            if period in ("daily", "weekly", "monthly"):
                df = await loop.run_in_executor(
                    None,
                    lambda: ak.stock_zh_a_hist(
                        symbol=stock_code, period=period, adjust="qfq"
                    ),
                )
            else:
                df = await loop.run_in_executor(
                    None,
                    lambda: ak.stock_zh_a_hist_min_em(
                        symbol=stock_code, period=period, adjust="qfq"
                    ),
                )

            if df is None or df.empty:
                return None

            column_mapping = {
                "日期": "timestamp",
                "时间": "timestamp",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
            }
            df = df.rename(columns=column_mapping)

            if "timestamp" not in df.columns:
                for col in df.columns:
                    if (
                        "日期" in str(col)
                        or "时间" in str(col)
                        or col in ("date", "datetime")
                    ):
                        df = df.rename(columns={col: "timestamp"})
                        break

            if "timestamp" not in df.columns and len(df.columns) > 0:
                df = df.rename(columns={df.columns[0]: "timestamp"})

            if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
                df["timestamp"] = pd.to_datetime(df["timestamp"])

            required_cols = ["timestamp", "open", "high", "low", "close", "volume"]
            for col in required_cols:
                if col not in df.columns:
                    df[col] = 0

            df = df.tail(limit)

            return df[required_cols].reset_index(drop=True)

        except Exception as e:
            logging.error(f"AShareProvider fetch_ohlcv error for {symbol}: {e}")
            return None

    async def validate_symbol(self, symbol: str) -> bool:
        try:
            df = await self.fetch_ohlcv(symbol, "1d", 1)
            return df is not None and not df.empty
        except Exception as e:
            logging.warning(f"AShareProvider validate_symbol failed for {symbol}: {e}")
            return False

    @staticmethod
    def normalize_symbol(raw: str) -> str:
        raw = raw.lower().strip()

        if raw.startswith(("sh", "sz")):
            return raw

        if ".x" in raw.lower():
            code = raw.split(".")[0]
            suffix = raw.split(".")[1].lower()
            if "sh" in suffix:
                return f"sh{code}"
            elif "sz" in suffix:
                return f"sz{code}"

        if raw.isdigit() and len(raw) == 6:
            if raw.startswith(("6", "5", "9")):
                return f"sh{raw}"
            else:
                return f"sz{raw}"

        return raw
