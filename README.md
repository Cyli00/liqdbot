# LiqdBot - Multi-Market Monitor / 多市场监控机器人

[English](#english) | [中文](#chinese)

<a name="english"></a>

## 🇬🇧 English

**LiqdBot** is a Telegram bot for monitoring **cryptocurrency** and **China A-share** markets. It delivers real-time alerts for liquidity sweeps, MACD resonance signals, and technical breakdowns.

### Features
- **Multi-Market Support**: Crypto (via `ccxt`) and China A-shares (via `akshare`)
- **Auto Symbol Detection**: Automatically identifies market type from symbol format
- **Multi-Timeframe Analysis**: MACD resonance across 1h/4h (crypto) or 15m/60m (A-shares)
- **A-Share Trading Hours**: Only monitors during Beijing time 9:30-11:30, 13:00-15:00
- **Smart Alerting**: Cooldowns to prevent alert spam

### Strategies

| Strategy | Crypto | A-Share |
|----------|:------:|:-------:|
| CISD (Swing High/Low) | ✅ | ✅ |
| MACD Resonance | 1h/4h | 15m/60m |
| Below MA5 Alert | ❌ | ✅ |

### Requirements
- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (required for package management)

### Installation

1. **Clone the repository**
   ```bash
   git clone <repo_url>
   cd liqdbot
   ```

2. **Install dependencies**
   ```bash
   uv sync
   ```

3. **Configuration**
   Create a `.env` file in the root directory:
   ```ini
   # Telegram Config
   TG_TOKEN=your_bot_token_here
   TG_CHAT_ID=your_chat_id

   # Crypto Config
   SYMBOL=BTC/USDT
   TIMEFRAME=1h
   FETCH_LIMIT=400

   # A-Share Config
   ASHARE_SYMBOL=sh000001      # SSE Composite Index
   ASHARE_TF_SHORT=15m
   ASHARE_TF_LONG=60m
   ASHARE_MA5_PERIOD=5

   # Strategy Params
   PIVOT_LEN=12
   EXPIRY_BARS=100
   LIQUIDITY_LOOKBACK=10
   ```

### Usage

1. **Start the bot**
   ```bash
   uv run main.py
   ```

2. **Telegram Commands**
   - `/add <symbol>` : Add a symbol to monitor
     - Crypto: `/add ETH/USDT`
     - A-Share: `/add sh600519` or `/add 000001.SZ`
   - `/del <symbol>` : Remove a symbol from monitoring
   - `/list` : Show all monitored symbols

### Symbol Formats

| Market | Format Examples |
|--------|-----------------|
| Crypto | `BTC/USDT`, `ETH/USDT` |
| A-Share | `sh600519`, `sz000001`, `600519.SH`, `000001.SZ` |

### Debugging

#### Log Levels

Set `LOG_LEVEL` in `.env` to control verbosity:

```ini
LOG_LEVEL=DEBUG    # Most verbose, includes all events
LOG_LEVEL=INFO     # Default, normal operation logs
LOG_LEVEL=WARNING  # Only warnings and errors
LOG_LEVEL=ERROR    # Only errors
```

#### Performance Tuning

```ini
# Slow request warning threshold (ms)
SLOW_THRESHOLD_MS=800

# Max concurrent data fetches for /status
STATUS_MAX_CONCURRENCY=4

# A-share data cache TTL (seconds)
AKSHARE_CACHE_TTL_S=300
```

#### Running in Debug Mode

```bash
# Quick debug run
LOG_LEVEL=DEBUG uv run main.py

# Or set in .env for persistent debug mode
echo "LOG_LEVEL=DEBUG" >> .env
uv run main.py
```

#### Key Log Events

| Event | Description |
|-------|-------------|
| `market_job_start` | Market monitoring cycle begins |
| `fetch_ohlcv_slow` | Data fetch exceeded threshold |
| `status_symbol_done` | Symbol analysis completed |
| `alert_sent` | Alert delivered to Telegram |

#### Troubleshooting

1. **No alerts received**: Check `TG_TOKEN` and `TG_CHAT_ID` in `.env`
2. **A-share data missing**: Verify trading hours (Beijing 9:30-11:30, 13:00-15:00)
3. **Slow performance**: Reduce `FETCH_LIMIT` or increase `SLOW_THRESHOLD_MS`
4. **Network errors**: Check internet connection; bot has built-in retry logic

### Running as a Background Service

Use systemd to run the bot as a background service on Linux.

#### 1. Find uv path

```bash
which uv
# Example output: /home/username/.local/bin/uv
```

#### 2. Create service file

```bash
sudo nano /etc/systemd/system/liqdbot.service
```

Paste the following (replace paths as needed):

```ini
[Unit]
Description=LiqdBot - Multi-Market Monitor
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/liqdbot
ExecStart=/home/username/.local/bin/uv run main.py
Restart=always
RestartSec=10
Environment=PATH=/home/username/.local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
```

#### 3. Enable and start

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable auto-start on boot
sudo systemctl enable liqdbot.service

# Start the service
sudo systemctl start liqdbot.service
```

#### 4. Service management

```bash
# Check status
sudo systemctl status liqdbot.service

# Stop the service
sudo systemctl stop liqdbot.service

# Restart the service
sudo systemctl restart liqdbot.service

# View logs
sudo journalctl -u liqdbot.service -f
```

---

<a name="chinese"></a>

## 🇨🇳 中文

**LiqdBot** 是一个 Telegram 机器人，支持监控**加密货币**和**A股**市场。提供流动性掠夺、MACD 共振信号和技术形态的实时警报。

### 功能特性
- **多市场支持**：加密货币（ccxt）和 A股（akshare）
- **自动识别标的**：根据 symbol 格式自动判断市场类型
- **多周期分析**：MACD 共振检测（加密货币 1h/4h，A股 15m/60m）
- **A股交易时段**：仅在北京时间 9:30-11:30、13:00-15:00 监控
- **智能报警**：冷却机制防止刷屏

### 策略配置

| 策略 | 加密货币 | A股 |
|------|:-------:|:---:|
| CISD (Swing High/Low) | ✅ | ✅ |
| MACD 共振 | 1h/4h | 15m/60m |
| 跌破 MA5 提醒 | ❌ | ✅ |

### 环境要求
- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (必须使用此包管理器)

### 安装步骤

1. **克隆仓库**
   ```bash
   git clone <repo_url>
   cd liqdbot
   ```

2. **安装依赖**
   ```bash
   uv sync
   ```

3. **配置环境**
   在根目录下创建 `.env` 文件：
   ```ini
   # Telegram 配置
   TG_TOKEN=your_bot_token_here
   TG_CHAT_ID=your_chat_id

   # 加密货币配置
   SYMBOL=BTC/USDT
   TIMEFRAME=1h
   FETCH_LIMIT=400

   # A股配置
   ASHARE_SYMBOL=sh000001      # 上证指数
   ASHARE_TF_SHORT=15m
   ASHARE_TF_LONG=60m
   ASHARE_MA5_PERIOD=5

   # 策略参数
   PIVOT_LEN=12
   EXPIRY_BARS=100
   LIQUIDITY_LOOKBACK=10
   ```

### 使用方法

1. **启动机器人**
   ```bash
   uv run main.py
   ```

2. **Telegram 指令**
   - `/add <symbol>` : 添加监控标的
     - 加密货币: `/add ETH/USDT`
     - A股: `/add sh600519` 或 `/add 000001.SZ`
   - `/del <symbol>` : 移除监控标的
   - `/list` : 显示所有监控标的

### 标的格式

| 市场 | 格式示例 |
|------|----------|
| 加密货币 | `BTC/USDT`, `ETH/USDT` |
| A股 | `sh600519`, `sz000001`, `600519.SH`, `000001.SZ` |

### 调试方法

#### 日志级别

在 `.env` 中设置 `LOG_LEVEL` 控制日志详细程度：

```ini
LOG_LEVEL=DEBUG    # 最详细，包含所有事件
LOG_LEVEL=INFO     # 默认，正常运行日志
LOG_LEVEL=WARNING  # 仅警告和错误
LOG_LEVEL=ERROR    # 仅错误
```

#### 性能调优

```ini
# 慢请求警告阈值（毫秒）
SLOW_THRESHOLD_MS=800

# /status 命令最大并发数
STATUS_MAX_CONCURRENCY=4

# A股数据缓存 TTL（秒）
AKSHARE_CACHE_TTL_S=300
```

#### 调试模式运行

```bash
# 快速调试运行
LOG_LEVEL=DEBUG uv run main.py

# 或在 .env 中设置持久调试模式
echo "LOG_LEVEL=DEBUG" >> .env
uv run main.py
```

#### 关键日志事件

| 事件 | 说明 |
|------|------|
| `market_job_start` | 市场监控周期开始 |
| `fetch_ohlcv_slow` | 数据拉取超过阈值 |
| `status_symbol_done` | 标的分析完成 |
| `alert_sent` | 警报已发送到 Telegram |

#### 常见问题排查

1. **收不到警报**：检查 `.env` 中的 `TG_TOKEN` 和 `TG_CHAT_ID`
2. **A股数据缺失**：确认是否在交易时段（北京时间 9:30-11:30、13:00-15:00）
3. **性能较慢**：减少 `FETCH_LIMIT` 或增大 `SLOW_THRESHOLD_MS`
4. **网络错误**：检查网络连接；机器人内置重试机制

### 后台运行（systemd 服务）

使用 systemd 在 Linux 上将机器人作为后台服务运行。

#### 1. 查找 uv 路径

```bash
which uv
# 示例输出: /home/username/.local/bin/uv
```

#### 2. 创建服务文件

```bash
sudo nano /etc/systemd/system/liqdbot.service
```

粘贴以下内容（根据实际情况修改路径）：

```ini
[Unit]
Description=LiqdBot - Multi-Market Monitor
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/liqdbot
ExecStart=/home/username/.local/bin/uv run main.py
Restart=always
RestartSec=10
Environment=PATH=/home/username/.local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
```

#### 3. 启用并启动服务

```bash
# 重新加载 systemd
sudo systemctl daemon-reload

# 设置开机自启
sudo systemctl enable liqdbot.service

# 启动服务
sudo systemctl start liqdbot.service
```

#### 4. 服务管理命令

```bash
# 查看状态
sudo systemctl status liqdbot.service

# 停止服务
sudo systemctl stop liqdbot.service

# 重启服务
sudo systemctl restart liqdbot.service

# 查看日志
sudo journalctl -u liqdbot.service -f
```

### 项目结构

```
liqdbot/
├── main.py           # 入口
├── liqdbot/
│   ├── config.py     # 配置
│   ├── engine.py     # 核心引擎
│   ├── handlers.py   # Telegram 命令
│   ├── jobs.py       # 定时任务
│   ├── alerts.py     # 报警模板
│   ├── state.py      # 状态管理
│   └── providers/    # 数据源抽象
│       ├── base.py   # DataProvider 基类
│       ├── crypto.py # 加密货币 (ccxt)
│       └── ashare.py # A股 (akshare)
```
