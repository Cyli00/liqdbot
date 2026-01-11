"""
命令处理模块 - 处理 Telegram Bot 命令
"""

import asyncio
from telegram import Update
from telegram.ext import ContextTypes

from .engine import engine
from .config import TG_CHAT_ID
from .providers import MarketType, detect_market_type, CryptoProvider, AShareProvider


async def add_symbol_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    指令: /add SOL 或 /add SOL/USDT 或 /add sh600519
    添加新的监控标的（不会替换已有的）
    """
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
        new_symbol = AShareProvider.normalize_symbol(raw_symbol)
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
    """
    指令: /del SOL 或 /del （删除所有）
    """
    args = context.args
    current_symbols = engine.get_all_symbols()

    if not current_symbols:
        await update.message.reply_text("💤 当前没有正在监控的标的。")
        return

    if not args:
        # 无参数：显示当前列表并提示如何删除
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
        # 删除所有
        count = len(current_symbols)
        for sym in list(current_symbols):
            engine.remove_symbol(sym)
        await update.message.reply_text(
            f"🛑 已停止所有监控（共 {count} 个）\n发送 `/add <币种>` 重新开始。",
            parse_mode="Markdown",
        )
    else:
        # 删除指定标的
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
    """
    指令: /list - 显示当前监控的所有标的
    """
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


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    手动查询：/status [symbol] [post]
    例如: /status 或 /status BTC 或 /status BTC post
    """
    symbols = engine.get_all_symbols()

    if not symbols:
        await update.message.reply_text(
            "💤 当前未监控任何标的。\n请使用 `/add BTC` 开始。", parse_mode="Markdown"
        )
        return

    args = context.args
    target_symbol = None
    should_post_to_channel = False

    # 解析参数
    for arg in args:
        if arg.lower() == "post":
            should_post_to_channel = True
        else:
            # 自动补全 /USDT
            if "/" not in arg.upper():
                target_symbol = f"{arg.upper()}/USDT"
            else:
                target_symbol = arg.upper()

    # 如果指定了标的，只查询该标的
    if target_symbol:
        if target_symbol not in symbols:
            await update.message.reply_text(
                f"❌ `{target_symbol}` 不在监控列表中", parse_mode="Markdown"
            )
            return
        symbols_to_query = [target_symbol]
    else:
        symbols_to_query = symbols

    loading_msg = await update.message.reply_text(
        f"🔄 正在分析 {len(symbols_to_query)} 个标的 (1H)..."
    )

    all_msgs = []

    for symbol in symbols_to_query:
        state = engine.get_state(symbol)
        if state is None:
            continue

        df, lower_df, htf_df = await engine.fetch_data(symbol)
        if df is None:
            all_msgs.append(f"❌ {symbol}: 数据获取失败")
            continue

        df = engine.calculate_indicators(df, lower_df)
        res = engine.analyze_market(symbol, state, df, htf_df, lower_df)
        if not res:
            all_msgs.append(f"❌ {symbol}: 分析失败")
            continue

        # 格式化输出
        res_txt = f"`{res['nearest_res']:.2f}`" if res["nearest_res"] else "无"
        sup_txt = f"`{res['nearest_sup']:.2f}`" if res["nearest_sup"] else "无"

        msg = f"📊 **{res['symbol']}** | `{res['price']:.2f}`\n"
        msg += f"⬆️ 上方阻力: {res_txt}\n"
        msg += f"⬇️ 下方支撑: {sup_txt}\n"

        if res.get("rvol_15m") is not None:
            msg += f"📊 15m RVOL: `{res['rvol_15m']:.2f}x`\n"

        if res["alerts"]:
            msg += f"📢 触发: {len(res['alerts'])} 条信号\n"

        all_msgs.append(msg)

        # 避免 API 限速
        await asyncio.sleep(0.3)

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
