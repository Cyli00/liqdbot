# LiqdBot

LiqdBot 是一个加密货币流动性监控 Telegram 机器人：定时从交易所拉取 OHLCV 数据，检测信号并推送提醒。

## 功能
- 多标的监控（默认：BTC/USDT、ETH/USDT、SOL/USDT）
- CISD 信号
  - Swing High / Low Mitigation
  - 普通 / 强 CISD（带扫单确认）
- MACD 1h/4h 共振提醒
- Alert 冷却与去重
- **CISD & MACD Alert** 自动附带 BTC 现货溢价

## 现货溢价
公式：
```
(Coinbase BTC/USD - Avg(Binance BTC/USDT, OKX BTC/USDT)) / Coinbase BTC/USD
```
仅在 **CISD** 与 **MACD** 提醒中追加显示。

## 运行环境
- Python **>= 3.12**（见 `uv.lock`）
- 需要可访问 Binance / OKX / Coinbase
- Telegram Bot Token 与 Chat ID

## 安装
```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install ccxt pandas pandas_ta numpy python-telegram-bot python-dotenv
```
> 依赖版本锁定可参考 `uv.lock`。

## 配置
在项目根目录创建 `.env`：
```bash
TG_TOKEN=你的Telegram Bot Token
TG_CHAT_ID=你的频道/群组ID（数字）
```

策略与监控参数请在 `liqdbot/config.py` 中调整：
- `DEFAULT_SYMBOL`
- `TIMEFRAME` / `LOWER_TIMEFRAME`
- `MONITOR_INTERVAL`
- `ALERT_COOLDOWN`

## 运行
```bash
python main.py
# 或
python -m liqdbot.main
```

## Telegram 命令
- `/add BTC` 或 `/add BTC/USDT`：添加监控标的
- `/del BTC`：删除单个标的
- `/del all`：删除全部
- `/list`：查看当前监控标的
- `/status [symbol] [post]`：查看当前状态；`post` 会推送到频道

## 目录结构
- `liqdbot/engine.py`：策略引擎与数据处理
- `liqdbot/alerts.py`：Alert 消息模板
- `liqdbot/jobs.py`：定时任务与发送逻辑
- `liqdbot/handlers.py`：Telegram 命令处理
- `liqdbot/state.py`：标的状态与去重
- `liqdbot/config.py`：配置参数
