"""
命令处理模块 - 处理 Telegram Bot 命令
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from .engine import engine
from .config import TG_CHAT_ID, STATUS_MAX_CONCURRENCY, SLOW_THRESHOLD_MS
from .providers import MarketType, detect_market_type, CryptoProvider, AkshareProvider

logger = logging.getLogger(__name__)


async def add_symbol_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ 请输入标的代码\\n"
            "加密货币: `/add SOL` 或 `/add BTC/USDT`\\n"
            "A股: `/add sh600519` 或 `/add 600519`",
            parse_mode="Markdown",
        )
        return

    raw_symbol = args[0]

    market_type = detect_market_type(raw_symbol)

    if market_type == MarketType.A_SHARE:
        new_symbol = AkshareProvider.normalize_symbol(raw_symbol)
        market_label = "🇨🇳 A股"
    else:
        new_symbol = CryptoProvider.normalize_symbol(raw_symbol)
        market_label = "🪙 Crypto"

    loading_msg = await update.message.reply_text(
        f"🔄 正在验证 `{new_symbol}` ({market_label})...", parse_mode="Markdown"
    )

    is_valid = await engine.validate_symbol(new_symbol)
    if not is_valid:
        await loading_msg.edit_text(
            f"❌ 交易对 `{new_symbol}` 不存在或无法访问\n💡 请检查代币名称是否正确",
            parse_mode="Markdown",
        )
        return

    is_new = engine.add_symbol(new_symbol)

    current_symbols = engine.get_all_symbols()
    symbols_list = ", ".join([f"`{s}`" for s in current_symbols])

    if is_new:
        await loading_msg.edit_text(
            f"✅ 已添加监控: `{new_symbol}`\n"
            f"📋 当前监控列表: {symbols_list}\n"
            f"💡 共 {len(current_symbols)} 个标的",
            parse_mode="Markdown",
        )
    else:
        await loading_msg.edit_text(
            f"⚠️ `{new_symbol}` 已在监控中，已重置状态\n📋 当前监控列表: {symbols_list}",
            parse_mode="Markdown",
        )


async def del_symbol_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    current_symbols = engine.get_all_symbols()

    if not current_symbols:
        await update.message.reply_text("💤 当前没有正在监控的标的。")
        return

    if not args:
        symbols_list = ", ".join([f"`{s}`" for s in current_symbols])
        await update.message.reply_text(
            f"📋 当前监控列表: {symbols_list}\n\n"
            f"💡 删除单个: `/del SOL`\n"
            f"💡 删除全部: `/del all`",
            parse_mode="Markdown",
        )
        return

    target = args[0].upper()

    if target == "ALL":
        count = len(current_symbols)
        for sym in list(current_symbols):
            engine.remove_symbol(sym)
        await update.message.reply_text(
            f"🛑 已停止所有监控（共 {count} 个）\n发送 `/add <币种>` 重新开始。",
            parse_mode="Markdown",
        )
    else:
        if "/" not in target:
            target = f"{target}/USDT"

        if engine.remove_symbol(target):
            remaining = engine.get_all_symbols()
            if remaining:
                symbols_list = ", ".join([f"`{s}`" for s in remaining])
                await update.message.reply_text(
                    f"🛑 已停止监控 `{target}`\n📋 剩余: {symbols_list}",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    f"🛑 已停止监控 `{target}`\n💤 当前无监控目标",
                    parse_mode="Markdown",
                )
        else:
            await update.message.reply_text(
                f"❌ `{target}` 不在监控列表中", parse_mode="Markdown"
            )


async def list_symbols_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_symbols = engine.get_all_symbols()

    if not current_symbols:
        await update.message.reply_text(
            "💤 当前没有正在监控的标的。\n请使用 `/add BTC` 添加。",
            parse_mode="Markdown",
        )
        return

    msg = "📋 **当前监控列表**\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    for i, sym in enumerate(current_symbols, 1):
        market_type = detect_market_type(sym)
        market_icon = "🇨🇳" if market_type == MarketType.A_SHARE else "🪙"
        state = engine.get_state(sym)
        if state and state.last_analysis:
            price = state.last_analysis.get("price", 0)
            trend = state.last_analysis.get("trend_dir", "未知")
            msg += f"{i}. {market_icon} `{sym}` - {price:.2f} | {trend}\n"
        else:
            msg += f"{i}. {market_icon} `{sym}` - 等待数据...\n"

    msg += f"\n💡 共 {len(current_symbols)} 个标的"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def _fetch_and_analyze_symbol(symbol: str, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        start_ms = time.perf_counter_ns() // 1_000_000
        result = {
            "symbol": symbol,
            "ok": False,
            "msg": "",
            "updated_at": None,
            "fetch_ms": 0,
            "calc_ms": 0,
            "analyze_ms": 0,
            "total_ms": 0,
        }

        try:
            fetch_start = time.perf_counter_ns() // 1_000_000
            df, lower_df, htf_df = await engine.fetch_data(symbol)
            result["fetch_ms"] = (time.perf_counter_ns() // 1_000_000) - fetch_start

            if df is None:
                result["msg"] = f"❌ {symbol}: 数据获取失败"
                result["total_ms"] = (time.perf_counter_ns() // 1_000_000) - start_ms
                return result

            state = engine.get_state(symbol)
            if state is None:
                result["msg"] = f"❌ {symbol}: 状态不存在"
                result["total_ms"] = (time.perf_counter_ns() // 1_000_000) - start_ms
                return result

            calc_start = time.perf_counter_ns() // 1_000_000
            df = engine.calculate_indicators(df, lower_df)
            result["calc_ms"] = (time.perf_counter_ns() // 1_000_000) - calc_start

            analyze_start = time.perf_counter_ns() // 1_000_000
            res = engine.analyze_market(symbol, state, df, htf_df, lower_df)
            result["analyze_ms"] = (time.perf_counter_ns() // 1_000_000) - analyze_start

            result["updated_at"] = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            result["total_ms"] = (time.perf_counter_ns() // 1_000_000) - start_ms

            if not res:
                result["msg"] = f"❌ {symbol}: 分析失败"
                return result

            res_txt = f"`{res['nearest_res']:.2f}`" if res["nearest_res"] else "无"
            sup_txt = f"`{res['nearest_sup']:.2f}`" if res["nearest_sup"] else "无"

            msg = f"📊 **{res['symbol']}** | `{res['price']:.2f}`\n"
            msg += f"⬆️ 上方阻力: {res_txt}\n"
            msg += f"⬇️ 下方支撑: {sup_txt}\n"

            if res.get("rvol_15m") is not None:
                msg += f"📊 15m RVOL: `{res['rvol_15m']:.2f}x`\n"

            if res["alerts"]:
                msg += f"📢 触发: {len(res['alerts'])} 条信号\n"

            msg += f"🕐 更新: {result['updated_at']} ({result['total_ms']}ms)"

            result["ok"] = True
            result["msg"] = msg

        except Exception as e:
            result["total_ms"] = (time.perf_counter_ns() // 1_000_000) - start_ms
            result["msg"] = f"❌ {symbol}: 异常 {e}"
            logger.exception(
                f"event=status_symbol_error req=status symbol={symbol} err={e}"
            )

        return result


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbols = engine.get_all_symbols()

    if not symbols:
        await update.message.reply_text(
            "💤 当前未监控任何标的。\n请使用 `/add BTC` 开始。", parse_mode="Markdown"
        )
        return

    args = context.args
    target_symbol = None
    should_post_to_channel = False

    for arg in args:
        if arg.lower() == "post":
            should_post_to_channel = True
        else:
            if "/" not in arg.upper():
                target_symbol = f"{arg.upper()}/USDT"
            else:
                target_symbol = arg.upper()

    if target_symbol:
        if target_symbol not in symbols:
            await update.message.reply_text(
                f"❌ `{target_symbol}` 不在监控列表中", parse_mode="Markdown"
            )
            return
        symbols_to_query = [target_symbol]
    else:
        symbols_to_query = symbols

    request_id = str(uuid.uuid4())[:8]
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0

    logger.info(
        f"event=status_start req={request_id} user={user_id} chat={chat_id} "
        f"symbols={len(symbols_to_query)} target={target_symbol or 'all'} post={should_post_to_channel}"
    )

    start_total_ms = time.perf_counter_ns() // 1_000_000

    loading_msg = await update.message.reply_text(
        f"🔄 正在分析 {len(symbols_to_query)} 个标的 (1H)..."
    )

    semaphore = asyncio.Semaphore(STATUS_MAX_CONCURRENCY)
    tasks = [_fetch_and_analyze_symbol(sym, semaphore) for sym in symbols_to_query]
    results = await asyncio.gather(*tasks)

    all_msgs = []
    ok_count = 0
    fail_count = 0
    slow_symbols = []

    for r in results:
        all_msgs.append(r["msg"])
        if r["ok"]:
            ok_count += 1
        else:
            fail_count += 1

        market_type = detect_market_type(r["symbol"])
        provider = "akshare" if market_type == MarketType.A_SHARE else "binance"

        if r["total_ms"] > SLOW_THRESHOLD_MS:
            slow_symbols.append(r["symbol"])
            logger.warning(
                f"event=status_symbol_slow req={request_id} symbol={r['symbol']} "
                f"provider={provider} fetch_ms={r['fetch_ms']} calc_ms={r['calc_ms']} "
                f"analyze_ms={r['analyze_ms']} total_ms={r['total_ms']}"
            )
        else:
            logger.debug(
                f"event=status_symbol_done req={request_id} symbol={r['symbol']} "
                f"provider={provider} fetch_ms={r['fetch_ms']} calc_ms={r['calc_ms']} "
                f"analyze_ms={r['analyze_ms']} total_ms={r['total_ms']}"
            )

    total_ms = (time.perf_counter_ns() // 1_000_000) - start_total_ms

    logger.info(
        f"event=status_done req={request_id} total_ms={total_ms} ok={ok_count} "
        f"fail={fail_count} slow_symbols={slow_symbols}"
    )

    final_msg = "———— ———— ————\n".join(all_msgs)
    final_msg = f"📋 **行情看板** (1H)\n———— ———— ————\n{final_msg}"

    if should_post_to_channel:
        try:
            await context.bot.send_message(
                chat_id=TG_CHAT_ID, text=final_msg, parse_mode="Markdown"
            )
            await loading_msg.edit_text("✅ 状态报告已推送到频道！")
        except Exception as e:
            await loading_msg.edit_text(
                f"❌ 推送频道失败: {e}\n请检查Bot是否为频道管理员。"
            )
    else:
        await loading_msg.edit_text(final_msg, parse_mode="Markdown")
