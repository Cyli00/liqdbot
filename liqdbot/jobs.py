import asyncio
import logging
import time
import uuid
from typing import Any
from telegram.ext import ContextTypes
from telegram.error import NetworkError, TimedOut, RetryAfter

from .engine import engine
from .config import TG_CHAT_ID, MAX_RETRIES, RETRY_DELAY, SLOW_THRESHOLD_MS
from .providers import MarketType, detect_market_type, is_akshare_trading_time

logger = logging.getLogger(__name__)

MAX_CONCURRENT_FETCHES = 4


async def send_telegram_with_retry(
    bot, chat_id: int, text: str, max_retries: int = MAX_RETRIES
) -> bool:
    for attempt in range(max_retries):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
            )
            return True
        except RetryAfter as e:
            logger.warning(f"event=telegram_rate_limit retry_after={e.retry_after}")
            await asyncio.sleep(e.retry_after)
        except (NetworkError, TimedOut) as e:
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                logger.warning(
                    f"event=telegram_network_error attempt={attempt + 1}/{max_retries} "
                    f"wait={wait_time}s err={e}"
                )
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"event=telegram_send_fail max_retries_reached err={e}")
                return False
        except Exception as e:
            logger.exception(f"event=telegram_send_error err={e}")
            return False
    return False


async def fetch_symbol_data(symbol: str) -> tuple[str, Any, Any, Any]:
    start_ms = time.perf_counter_ns() // 1_000_000
    try:
        df, lower_df, htf_df = await engine.fetch_data(symbol)
        duration_ms = (time.perf_counter_ns() // 1_000_000) - start_ms

        market_type = detect_market_type(symbol)
        provider = "akshare" if market_type == MarketType.A_SHARE else "binance"

        if duration_ms > SLOW_THRESHOLD_MS:
            logger.warning(
                f"event=job_fetch_slow symbol={symbol} provider={provider} "
                f"duration_ms={duration_ms}"
            )
        else:
            logger.debug(
                f"event=job_fetch_done symbol={symbol} provider={provider} "
                f"duration_ms={duration_ms}"
            )

        return (symbol, df, lower_df, htf_df)
    except Exception as e:
        duration_ms = (time.perf_counter_ns() // 1_000_000) - start_ms
        logger.exception(
            f"event=job_fetch_error symbol={symbol} duration_ms={duration_ms} err={e}"
        )
        return (symbol, None, None, None)


async def check_market_job(context: ContextTypes.DEFAULT_TYPE):
    run_id = str(uuid.uuid4())[:8]
    job_start_ms = time.perf_counter_ns() // 1_000_000

    all_symbols = engine.get_all_symbols()

    if not all_symbols:
        return

    akshare_trading = is_akshare_trading_time()

    symbols_to_process = []
    for sym in all_symbols:
        market_type = detect_market_type(sym)
        if market_type == MarketType.A_SHARE:
            if akshare_trading:
                symbols_to_process.append(sym)
        else:
            symbols_to_process.append(sym)

    if not symbols_to_process:
        logger.debug(
            f"event=market_job_skip run={run_id} reason=no_symbols_to_process "
            f"akshare_trading={akshare_trading}"
        )
        return

    logger.info(
        f"event=market_job_start run={run_id} symbols_total={len(all_symbols)} "
        f"symbols_process={len(symbols_to_process)} akshare_trading={akshare_trading}"
    )

    fetch_start_ms = time.perf_counter_ns() // 1_000_000
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

    async def fetch_with_semaphore(symbol: str):
        async with semaphore:
            return await fetch_symbol_data(symbol)

    fetch_tasks = [fetch_with_semaphore(sym) for sym in symbols_to_process]
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    fetch_duration_ms = (time.perf_counter_ns() // 1_000_000) - fetch_start_ms
    fetch_ok = sum(
        1 for r in results if not isinstance(r, Exception) and r[1] is not None
    )
    fetch_fail = len(results) - fetch_ok

    logger.info(
        f"event=market_fetch_done run={run_id} ok={fetch_ok} fail={fetch_fail} "
        f"duration_ms={fetch_duration_ms}"
    )

    analyze_start_ms = time.perf_counter_ns() // 1_000_000
    all_alerts = []
    cooldown_skipped = 0

    for result in results:
        if isinstance(result, Exception):
            logger.error(f"event=market_job_exception run={run_id} err={result}")
            continue

        symbol, df, lower_df, htf_df = result
        if df is None:
            continue

        state = engine.get_state(symbol)
        if state is None:
            continue

        try:
            df = engine.calculate_indicators(df, lower_df, symbol, state)
            display_name = await engine.get_symbol_display_name(symbol)
            res = await engine.analyze_market(
                symbol, state, df, htf_df, lower_df, display_name
            )
        except Exception as e:
            logger.exception(
                f"event=market_analyze_error run={run_id} symbol={symbol} err={e}"
            )
            continue

    if res and res["alerts"]:
        for alert_type, alert_msg in res["alerts"]:
            if state.can_send_alert(alert_type):
                all_alerts.append((state, symbol, alert_type, alert_msg))
            else:
                cooldown_skipped += 1
                logger.debug(
                    f"event=alert_cooldown_skip run={run_id} symbol={symbol} "
                    f"alert_type={alert_type}"
                )

    analyze_duration_ms = (time.perf_counter_ns() // 1_000_000) - analyze_start_ms

    logger.info(
        f"event=market_analyze_done run={run_id} alerts={len(all_alerts)} "
        f"cooldown_skipped={cooldown_skipped} duration_ms={analyze_duration_ms}"
    )

    send_start_ms = time.perf_counter_ns() // 1_000_000
    sent_ok = 0
    sent_fail = 0

    for state, symbol, alert_type, alert_msg in all_alerts:
        # 对加密货币警报添加现货溢价信息
        final_msg = alert_msg
        market_type = detect_market_type(symbol)
        if market_type == MarketType.CRYPTO:
            try:
                premium_info = await engine.fetch_spot_premium(symbol)
                if premium_info is not None:
                    premium_pct = premium_info["premium_pct"]
                    coinbase_symbol = premium_info["coinbase_symbol"]
                    usdt_symbol = premium_info["usdt_symbol"]
                    coinbase_price = premium_info["coinbase_price"]
                    usdt_avg_price = premium_info["usdt_avg_price"]
                    okx_price = premium_info["okx_price"]
                    binance_price = premium_info["binance_price"]
                    premium_sign = "+" if premium_pct >= 0 else ""
                    final_msg += (
                        f"\n\n💱 **现货溢价**\n"
                        f"Coinbase `{coinbase_symbol}`: `{coinbase_price:,.2f}`\n"
                        f"USDT均价 `{usdt_symbol}`: `{usdt_avg_price:,.2f}`\n"
                        f"  ├─ OKX: `{okx_price:,.2f}`\n"
                        f"  └─ Binance: `{binance_price:,.2f}`\n"
                        f"溢价: `{premium_sign}{premium_pct:.3f}%`"
                    )
            except Exception as e:
                logger.warning(
                    f"event=fetch_spot_premium_error symbol={symbol} err={e}"
                )

        success = await send_telegram_with_retry(context.bot, TG_CHAT_ID, final_msg)
        if success:
            state.mark_alert_sent(alert_type)
            sent_ok += 1
            await asyncio.sleep(1)
        else:
            sent_fail += 1
            await asyncio.sleep(5)

    send_duration_ms = (time.perf_counter_ns() // 1_000_000) - send_start_ms

    if all_alerts:
        logger.info(
            f"event=market_send_done run={run_id} sent={sent_ok} fail={sent_fail} "
            f"duration_ms={send_duration_ms}"
        )

    job_duration_ms = (time.perf_counter_ns() // 1_000_000) - job_start_ms

    logger.info(
        f"event=market_job_done run={run_id} total_ms={job_duration_ms} "
        f"fetch_ms={fetch_duration_ms} analyze_ms={analyze_duration_ms} "
        f"send_ms={send_duration_ms}"
    )
