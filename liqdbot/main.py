"""
LiqdBot - 主入口
加密货币流动性监控机器人
"""
import asyncio
import os
import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler
from telegram.request import HTTPXRequest

from liqdbot.config import TG_TOKEN, DEFAULT_SYMBOL, MONITOR_INTERVAL
from liqdbot.engine import engine
from liqdbot.handlers import (
    add_symbol_command,
    del_symbol_command,
    list_symbols_command,
    status_command
)
from liqdbot.jobs import check_market_job


async def main():
    if not TG_TOKEN:
        print("Error: TG_TOKEN not found in .env")
        return

    # 1. 构建 Application，配置更长的超时时间
    request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=30,
        pool_timeout=30
    )
    app = ApplicationBuilder().token(TG_TOKEN).request(request).build()

    # 2. 注册命令
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("add", add_symbol_command))
    app.add_handler(CommandHandler("del", del_symbol_command))
    app.add_handler(CommandHandler("list", list_symbols_command))

    # 3. 注册定时任务 (每 60s = 1分钟)
    job_queue = app.job_queue
    job_queue.run_repeating(check_market_job, interval=MONITOR_INTERVAL, first=5)

    logging.info(f"Bot started. Monitoring interval: {MONITOR_INTERVAL}s.")
    logging.info(f"Default Symbol: {DEFAULT_SYMBOL if DEFAULT_SYMBOL else 'None'}")
    logging.info("Multi-symbol monitoring enabled. Use /add to add symbols, /del to remove symbols, /list to view.")
    
    # 4. 手动管理生命周期
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )
    
    stop_signal = asyncio.Event()
    
    try:
        await stop_signal.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        logging.info("Stopping bot...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await engine.close_exchange()


if __name__ == "__main__":
    try:
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
