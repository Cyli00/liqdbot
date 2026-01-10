"""
定时任务模块 - 处理自动监控任务和消息发送
"""

import asyncio
import logging
from typing import Any
from telegram.ext import ContextTypes
from telegram.error import NetworkError, TimedOut, RetryAfter

from .engine import engine
from .config import TG_CHAT_ID, MAX_RETRIES, RETRY_DELAY
from .providers import MarketType, detect_market_type, is_ashare_trading_time


# 并行获取数据的最大并发数（避免API限速）
MAX_CONCURRENT_FETCHES = 4


async def send_telegram_with_retry(
    bot, chat_id: int, text: str, max_retries: int = MAX_RETRIES
) -> bool:
    """带重试机制的 Telegram 消息发送"""
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
            logging.warning(f"Telegram 限速，等待 {e.retry_after} 秒...")
            await asyncio.sleep(e.retry_after)
        except (NetworkError, TimedOut) as e:
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                logging.warning(
                    f"网络错误，{wait_time}秒后重试 ({attempt + 1}/{max_retries}): {e}"
                )
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


async def check_market_job(context: ContextTypes.DEFAULT_TYPE):
    """
    自动任务：每分钟运行，并行获取数据，顺序处理分析和发送
    - 数据获取：并行（使用信号量控制并发数）
    - 指标计算和分析：顺序（CPU密集型）
    - Alert发送：顺序（避免Telegram限速）
    - A股：仅在交易时段执行
    """
    all_symbols = engine.get_all_symbols()

    if not all_symbols:
        return

    ashare_trading = is_ashare_trading_time()

    symbols_to_process = []
    for sym in all_symbols:
        market_type = detect_market_type(sym)
        if market_type == MarketType.A_SHARE:
            if ashare_trading:
                symbols_to_process.append(sym)
        else:
            symbols_to_process.append(sym)

    if not symbols_to_process:
        return

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

    async def fetch_with_semaphore(symbol: str):
        async with semaphore:
            return await fetch_symbol_data(symbol)

    fetch_tasks = [fetch_with_semaphore(sym) for sym in symbols_to_process]
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    all_alerts = []

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
            res = engine.analyze_market(symbol, state, df, htf_df)
        except Exception as e:
            logging.error(f"分析 {symbol} 市场数据失败: {e}")
            continue

        if res and res["alerts"]:
            for alert_type, alert_msg in res["alerts"]:
                if state.can_send_alert(alert_type):
                    all_alerts.append((state, alert_type, alert_msg))
                else:
                    logging.info(
                        f"[{symbol}] Alert {alert_type} 在冷却期内或强度不足，跳过发送"
                    )

    for state, alert_type, alert_msg in all_alerts:
        success = await send_telegram_with_retry(context.bot, TG_CHAT_ID, alert_msg)
        if success:
            state.mark_alert_sent(alert_type)
            await asyncio.sleep(1)
        else:
            await asyncio.sleep(5)
