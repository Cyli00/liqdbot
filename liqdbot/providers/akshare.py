import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
import pandas as pd

from .base import DataProvider
from ..config import SLOW_THRESHOLD_MS, AKSHARE_CACHE_TTL_S

logger = logging.getLogger(__name__)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# 专用线程池，用于并行拉取 akshare 数据
_AKSHARE_EXECUTOR = ThreadPoolExecutor(max_workers=6, thread_name_prefix="akshare")

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


def is_akshare_trading_time() -> bool:
    now = datetime.now(SHANGHAI_TZ)

    if now.weekday() >= 5:
        return False

    current_time = now.time()
    morning_session = (dt_time(9, 30), dt_time(11, 30))
    afternoon_session = (dt_time(13, 0), dt_time(15, 0))

    return (
        morning_session[0] <= current_time <= morning_session[1]
        or afternoon_session[0] <= current_time <= afternoon_session[1]
    )


def _timeframe_to_seconds(tf: str) -> int:
    m = re.match(r"(?i)(\d+)([smhdw])", tf)
    if not m:
        return 3600
    value = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "s":
        return value
    if unit == "m":
        return value * 60
    if unit == "h":
        return value * 3600
    if unit == "d":
        return value * 86400
    if unit == "w":
        return value * 604800
    return 3600


def _get_current_bar_open_ts(tf: str) -> float:
    bar_seconds = _timeframe_to_seconds(tf)
    now_ts = time.time()
    return (now_ts // bar_seconds) * bar_seconds


class AkshareProvider(DataProvider):
    # 常用指数硬编码映射（避免大量数据拉取）
    INDEX_NAME_MAP = {
        "sh000001": "上证指数",
        "sh000688": "科创50",
        "sz399006": "创业板指",
    }

    def __init__(self):
        self._ak = None
        self._cache: dict[tuple[str, str], tuple[pd.DataFrame, float, float]] = {}
        self._name_cache: dict[str, str] = {}  # 按需缓存的名称

    def _get_akshare(self):
        if self._ak is None:
            import akshare as ak

            self._ak = ak
        return self._ak

    async def get_symbol_name(self, symbol: str) -> str | None:
        # 标准化 symbol
        normalized = symbol.lower().strip()

        # 1. 先查硬编码的指数映射
        if normalized in self.INDEX_NAME_MAP:
            return self.INDEX_NAME_MAP[normalized]

        # 2. 再查按需缓存
        if normalized in self._name_cache:
            return self._name_cache[normalized]

        code = self._extract_stock_code(symbol)
        if code and code in self._name_cache:
            return self._name_cache[code]

        # 3. 按需查询单个股票/ETF 名称
        name = await self._fetch_single_name(symbol)
        if name:
            # 缓存结果
            self._name_cache[normalized] = name
            if code:
                self._name_cache[code] = name
        return name

    async def _fetch_single_name(self, symbol: str) -> str | None:
        """按需查询单个股票/ETF 名称"""
        try:
            ak = self._get_akshare()
            loop = asyncio.get_running_loop()
            code = self._extract_stock_code(symbol)
            if not code:
                return None

            # 判断是否是 ETF（代码以 1 或 5 开头）
            if code.startswith(("1", "5")):
                # ETF
                try:
                    df = await loop.run_in_executor(None, ak.fund_etf_spot_em)
                    if df is not None and not df.empty:
                        # 查找匹配的代码
                        for col in ["代码", "基金代码", "code"]:
                            if col in df.columns:
                                name_col = None
                                for nc in ["名称", "基金简称", "name"]:
                                    if nc in df.columns:
                                        name_col = nc
                                        break
                                if name_col:
                                    match = df[df[col].astype(str).str.contains(code, na=False)]
                                    if not match.empty:
                                        return str(match.iloc[0][name_col]).strip()
                except Exception as e:
                    logger.debug(f"event=fetch_etf_name_error symbol={symbol} err={e}")
            else:
                # 股票 - 使用 stock_individual_info_em 获取单个股票信息
                try:
                    df = await loop.run_in_executor(
                        None,
                        lambda: ak.stock_individual_info_em(symbol=code)
                    )
                    if df is not None and not df.empty:
                        # 返回格式是 item/value 两列
                        for _, row in df.iterrows():
                            item = str(row.get("item", "")).strip()
                            if item in ("股票简称", "股票名称", "名称"):
                                return str(row.get("value", "")).strip()
                except Exception as e:
                    logger.debug(f"event=fetch_stock_name_error symbol={symbol} err={e}")

            return None
        except Exception as e:
            logger.warning(f"event=fetch_single_name_error symbol={symbol} err={e}")
            return None

    def _extract_stock_code(self, symbol: str) -> str:
        symbol = symbol.lower().strip()
        if symbol.startswith(("sh", "sz")):
            return symbol[2:]
        if ".x" in symbol:
            return symbol.split(".")[0]
        return symbol

    def _is_index(self, symbol: str) -> bool:
        symbol_lower = symbol.lower().strip()
        code = self._extract_stock_code(symbol)
        if symbol_lower.startswith("sh") and code.startswith("000"):
            return True
        if symbol_lower.startswith("sz") and code.startswith("399"):
            return True
        return False

    def _should_use_cache(
        self, symbol: str, timeframe: str
    ) -> tuple[bool, pd.DataFrame | None, str]:
        cache_key = (symbol, timeframe)
        if cache_key not in self._cache:
            return False, None, "no_cache"

        cached_df, cached_at, cached_bar_open = self._cache[cache_key]
        current_bar_open = _get_current_bar_open_ts(timeframe)
        now = time.time()

        if now - cached_at > AKSHARE_CACHE_TTL_S:
            return False, None, "ttl_expired"

        if current_bar_open > cached_bar_open:
            return False, None, "new_bar"

        return True, cached_df, "cache_hit"

    def _update_cache(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        cache_key = (symbol, timeframe)
        current_bar_open = _get_current_bar_open_ts(timeframe)
        self._cache[cache_key] = (df.copy(), time.time(), current_bar_open)

    def _get_date_range(self, timeframe: str) -> tuple[str, str]:
        """根据时间周期获取时间范围：15m 拉 1 个月，其他拉 3 个月"""
        now = datetime.now(SHANGHAI_TZ)
        period_minutes = _timeframe_to_seconds(timeframe) // 60

        if period_minutes <= 15:
            # 15m 及以下：拉取近 1 个月
            start_dt = now - pd.Timedelta(days=30)
        else:
            # 60m 及以上：拉取近 3 个月
            start_dt = now - pd.Timedelta(days=90)

        return (
            start_dt.strftime("%Y-%m-%d 09:30:00"),
            now.strftime("%Y-%m-%d %H:%M:%S"),
        )

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int
    ) -> pd.DataFrame | None:
        use_cache, cached_df, cache_reason = self._should_use_cache(symbol, timeframe)
        if use_cache and cached_df is not None:
            logger.debug(
                f"event=fetch_ohlcv_cache provider=akshare symbol={symbol} "
                f"tf={timeframe} reason={cache_reason} rows={len(cached_df)}"
            )
            return cached_df.tail(limit).reset_index(drop=True)

        start_ms = time.perf_counter_ns() // 1_000_000
        try:
            ak = self._get_akshare()
            period = TIMEFRAME_MAP.get(timeframe, timeframe)
            stock_code = self._extract_stock_code(symbol)

            loop = asyncio.get_running_loop()

            is_index = self._is_index(symbol)
            start_date, end_date = self._get_date_range(timeframe)

            if period in ("daily", "weekly", "monthly"):
                if is_index:
                    df = await loop.run_in_executor(
                        _AKSHARE_EXECUTOR,
                        lambda: ak.index_zh_a_hist(symbol=stock_code, period=period),
                    )
                else:
                    df = await loop.run_in_executor(
                        _AKSHARE_EXECUTOR,
                        lambda: ak.stock_zh_a_hist(
                            symbol=stock_code, period=period, adjust="qfq"
                        ),
                    )
            else:
                # 分钟级数据：拉取近 1-3 个月
                if is_index:
                    df = await loop.run_in_executor(
                        _AKSHARE_EXECUTOR,
                        lambda: ak.index_zh_a_hist_min_em(
                            symbol=stock_code,
                            period=period,
                            start_date=start_date,
                            end_date=end_date,
                        ),
                    )
                else:
                    df = await loop.run_in_executor(
                        _AKSHARE_EXECUTOR,
                        lambda: ak.stock_zh_a_hist_min_em(
                            symbol=stock_code,
                            period=period,
                            start_date=start_date,
                            end_date=end_date,
                            adjust="qfq",
                        ),
                    )

            if df is None or df.empty:
                duration_ms = (time.perf_counter_ns() // 1_000_000) - start_ms
                logger.warning(
                    f"event=fetch_ohlcv_empty provider=akshare symbol={symbol} "
                    f"tf={timeframe} duration_ms={duration_ms}"
                )
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

            full_df = df[required_cols].reset_index(drop=True)

            self._update_cache(symbol, timeframe, full_df)

            result_df = full_df.tail(limit).reset_index(drop=True)
            duration_ms = (time.perf_counter_ns() // 1_000_000) - start_ms
            rows = len(result_df)

            if duration_ms > SLOW_THRESHOLD_MS:
                logger.warning(
                    f"event=fetch_ohlcv_slow provider=akshare symbol={symbol} "
                    f"tf={timeframe} limit={limit} duration_ms={duration_ms} "
                    f"rows={rows} cache_reason={cache_reason}"
                )
            else:
                logger.debug(
                    f"event=fetch_ohlcv provider=akshare symbol={symbol} "
                    f"tf={timeframe} limit={limit} duration_ms={duration_ms} "
                    f"rows={rows} cache_reason={cache_reason}"
                )

            return result_df

        except Exception as e:
            duration_ms = (time.perf_counter_ns() // 1_000_000) - start_ms
            logger.exception(
                f"event=fetch_ohlcv_error provider=akshare symbol={symbol} "
                f"tf={timeframe} limit={limit} duration_ms={duration_ms} err={e}"
            )
            return None

    async def validate_symbol(self, symbol: str) -> bool:
        try:
            df = await self.fetch_ohlcv(symbol, "1d", 1)
            return df is not None and not df.empty
        except Exception:
            logger.exception(
                f"event=validate_symbol_error provider=akshare symbol={symbol}"
            )
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
