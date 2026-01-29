# LiqdBot

加密货币流动性监控机器人（Telegram 通知）。

## 功能
- 多标的监控（默认：BTC/USDT、ETH/USDT、SOL/USDT）
- CISD 策略提醒（普通 / 强）
- Swing High/Low Mitigation 提醒
- MACD 1h/4h 共振提醒
- 每条 Alert 附带 **BTC 现货溢价**

## 现货溢价
公式：
```
(Coinbase BTC/USD - Avg(Binance BTC/USDT, OKX BTC/USDT)) / Coinbase BTC/USD
```
在每条 Alert 中以百分比展示。

## 配置
在项目根目录创建 `.env`：
```bash
TG_TOKEN=你的Telegram Bot Token
TG_CHAT_ID=你的频道/群组ID（数字）
```

## 运行
```bash
python main.py
```

## Telegram 命令
- `/add BTC` 或 `/add BTC/USDT`：添加监控标的
- `/del BTC`：删除单个标的
- `/del all`：删除全部
- `/list`：查看当前监控标的
- `/status [symbol] [post]`：查看当前状态；`post` 会推送到频道
