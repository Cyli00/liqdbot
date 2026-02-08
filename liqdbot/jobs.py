"""
定时任务模块 - 处理自动监控任务和消息发送
"""
import asyncio
import logging
import time
from typing import Any

import ccxt.async_support as ccxt
from telegram.ext import ContextTypes
from telegram.error import NetworkError, TimedOut, RetryAfter

from .engine import engine
from .alerts import AlertMessages
from .config import TG_CHAT_ID, MAX_RETRIES, RETRY_DELAY


# 并行获取数据的最大并发数（避免API限速）
MAX_CONCURRENT_FETCHES = 4

# 现货溢价缓存 (秒)
SPOT_PREMIUM_CACHE_TTL = 30
# 若交易所 ticker 的时间戳误差过大，记录告警（不阻断）
SPOT_PREMIUM_MAX_TS_SKEW_MS = 10_000
_SPOT_PREMIUM_CACHE = {
    "ts": 0.0,
    "value": None,
    "meta": None,
}


async def _fetch_ticker_with_recv_ts(exchange, symbol: str):
    """拉取 ticker，并返回本地接收时间戳(ms)"""
    ticker = await exchange.fetch_ticker(symbol)
    recv_ts_ms = int(time.time() * 1000)
    return ticker, recv_ts_ms


def _ticker_ts_ms(ticker: dict, recv_ts_ms: int) -> int:
    """优先使用交易所时间戳，缺失时退回到本地接收时间戳"""
    ts = ticker.get("timestamp")
    if isinstance(ts, (int, float)) and ts > 0:
        return int(ts)
    return recv_ts_ms


async def send_telegram_with_retry(bot, chat_id: int, text: str, max_retries: int = MAX_RETRIES) -> bool:
    """带重试机制的 Telegram 消息发送"""
    for attempt in range(max_retries):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode='Markdown',
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30
            )
            return True
        except RetryAfter as e:
            logging.warning(f"Telegram 限速，等待 {e.retry_after} 秒...")
            await asyncio.sleep(e.retry_after)
        except (NetworkError, TimedOut) as e:
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                logging.warning(f"网络错误，{wait_time}秒后重试 ({attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(wait_time)
            else:
                logging.error(f"发送失败，已达最大重试次数: {e}")
                return False
        except Exception as e:
            logging.error(f"发送消息时发生未知错误: {e}")
            return False
    return False


async def fetch_symbol_data(symbol: str) -> tuple[str, Any, Any, Any]:
    """获取单个标的的数据，返回 (symbol, df, lower_df, htf_df)"""
    try:
        df, lower_df, htf_df = await engine.fetch_data(symbol)
        return (symbol, df, lower_df, htf_df)
    except Exception as e:
        logging.error(f"获取 {symbol} 数据失败: {e}")
        return (symbol, None, None, None)


async def fetch_spot_premium(max_age: int = SPOT_PREMIUM_CACHE_TTL):
    """获取 BTC 现货溢价 (Coinbase vs Binance/OKX 平均)，带缓存"""
    now = time.time()
    cached_val = _SPOT_PREMIUM_CACHE.get("value")
    cached_ts = _SPOT_PREMIUM_CACHE.get("ts", 0.0)
    if cached_val is not None and (now - cached_ts) < max_age:
        return cached_val

    coinbase = ccxt.coinbase()
    binance = ccxt.binance()
    okx = ccxt.okx()

    try:
        fetch_start = time.time()
        (coinbase_ticker, coinbase_recv_ts), (binance_ticker, binance_recv_ts), (okx_ticker, okx_recv_ts) = await asyncio.gather(
            _fetch_ticker_with_recv_ts(coinbase, "BTC/USD"),
            _fetch_ticker_with_recv_ts(binance, "BTC/USDT"),
            _fetch_ticker_with_recv_ts(okx, "BTC/USDT")
        )
        fetch_end = time.time()

        coinbase_price = coinbase_ticker.get("last") or coinbase_ticker.get("close")
        binance_price = binance_ticker.get("last") or binance_ticker.get("close")
        okx_price = okx_ticker.get("last") or okx_ticker.get("close")

        if coinbase_price is None or binance_price is None or okx_price is None:
            raise ValueError("missing price")
        if coinbase_price == 0:
            raise ValueError("coinbase price is zero")

        avg_price = (binance_price + okx_price) / 2
        premium = (coinbase_price - avg_price) / coinbase_price

        coinbase_ts = _ticker_ts_ms(coinbase_ticker, coinbase_recv_ts)
        binance_ts = _ticker_ts_ms(binance_ticker, binance_recv_ts)
        okx_ts = _ticker_ts_ms(okx_ticker, okx_recv_ts)
        ticker_ts_values = [coinbase_ts, binance_ts, okx_ts]

        ts_skew_ms = max(ticker_ts_values) - min(ticker_ts_values)
        fetched_at_ms = int(fetch_end * 1000)
        max_staleness_ms = max(fetched_at_ms - ts for ts in ticker_ts_values)

        if ts_skew_ms > SPOT_PREMIUM_MAX_TS_SKEW_MS:
            logging.warning(
                "现货溢价 ticker 时间戳误差较大: skew=%sms (coinbase=%s, binance=%s, okx=%s)",
                ts_skew_ms,
                coinbase_ts,
                binance_ts,
                okx_ts,
            )

        _SPOT_PREMIUM_CACHE["ts"] = fetch_end
        _SPOT_PREMIUM_CACHE["value"] = premium
        _SPOT_PREMIUM_CACHE["meta"] = {
            "fetched_at_ms": fetched_at_ms,
            "fetch_duration_ms": int((fetch_end - fetch_start) * 1000),
            "ticker_ts_skew_ms": ts_skew_ms,
            "max_staleness_ms": max_staleness_ms,
        }
        return premium
    except Exception as e:
        logging.warning(f"现货溢价获取失败: {e}")
        return None
    finally:
        await coinbase.close()
        await binance.close()
        await okx.close()


async def check_market_job(context: ContextTypes.DEFAULT_TYPE):
    """
    自动任务：每分钟运行，并行获取数据，顺序处理分析和发送
    - 数据获取：并行（使用信号量控制并发数）
    - 指标计算和分析：顺序（CPU密集型）
    - Alert发送：顺序（避免Telegram限速）
    """
    symbols = engine.get_all_symbols()
    
    if not symbols:
        return

    # 1. 并行获取所有标的的数据（使用信号量限制并发）
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
    
    async def fetch_with_semaphore(symbol: str):
        async with semaphore:
            return await fetch_symbol_data(symbol)
    
    # 并行获取所有数据
    fetch_tasks = [fetch_with_semaphore(sym) for sym in symbols]
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    
    # 2. 顺序处理分析和发送（避免CPU竞争和Telegram限速）
    all_alerts = []  # 收集所有需要发送的alerts
    
    for result in results:
        if isinstance(result, Exception):
            logging.error(f"获取数据时发生异常: {result}")
            continue
        
        symbol, df, lower_df, htf_df = result
        if df is None:
            continue
        
        state = engine.get_state(symbol)
        if state is None:
            continue
        
        try:
            df = engine.calculate_indicators(df, lower_df)
            # analyze_market 内部会计算 HTF 指标
            res = engine.analyze_market(symbol, state, df, htf_df)
        except Exception as e:
            logging.error(f"分析 {symbol} 市场数据失败: {e}")
            continue
        
        if res and res['alerts']:
            for alert_type, alert_msg in res['alerts']:
                if state.can_send_alert(alert_type):
                    all_alerts.append((state, alert_type, alert_msg))
                else:
                    logging.info(f"[{symbol}] Alert {alert_type} 在冷却期内或强度不足，跳过发送")
    
    # 3. 顺序发送所有alerts
    premium = None
    if all_alerts:
        premium = await fetch_spot_premium()

    for state, alert_type, alert_msg in all_alerts:
        final_msg = AlertMessages.append_spot_premium(alert_msg, premium)
        success = await send_telegram_with_retry(context.bot, TG_CHAT_ID, final_msg)
        if success:
            state.mark_alert_sent(alert_type)
            await asyncio.sleep(1)  # 避免Telegram限速
        else:
            await asyncio.sleep(5)
