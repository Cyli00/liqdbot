"""
LiqdBot - 主入口
加密货币流动性监控机器人

这是入口文件，实际逻辑在 liqdbot/ 包中
"""
import asyncio
import os

from liqdbot.main import main


if __name__ == "__main__":
    try:
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
