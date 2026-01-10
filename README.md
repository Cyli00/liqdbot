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
