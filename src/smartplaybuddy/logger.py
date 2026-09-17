"""
日志模块。
配置根 logger：同时输出到控制台和按天轮转的日志文件。
"""
import logging
from logging.handlers import TimedRotatingFileHandler
import os
import importlib.util

# 自定义 TRACE 级别(数值低于 DEBUG)：承载逐帧协议日志等高频噪音。
# 默认已被总闸门(capture_level=TRACE)放行，但只落入日志流的独立 TRACE 档，供网页按需订阅；
# 本地控制台/文件由 level(INFO) 过滤，看不到 TRACE。想在本地控制台直接看逐帧，把 level 改成 TRACE。
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(self, message, *args, **kwargs):
    """为所有 Logger 注入 trace() 便捷方法(等价 logger.log(TRACE, ...))。"""
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)


logging.Logger.trace = _trace
# 把级别常量也挂到 Logger 类：其余文件用 logger.TRACE 即可，无需 from ..logger import TRACE
logging.Logger.TRACE = TRACE

# 日志格式
log_format = logging.Formatter("%(levelname)-8s|\t%(asctime)s\t%(name)-30s\t%(message)s")

# 控制台 Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_format)

package = "smartplaybuddy"
logdir = "logs"

name = "SmtPlay"
# 本地 handler(控制台/文件)的输出级别。保持 INFO：本地输出不受逐帧噪音影响。
# 想在本地控制台直接看逐帧协议日志，临时改成 TRACE 即可。
level = logging.INFO
# level = logging.DEBUG

# 定位项目根目录（用于存放日志文件）
root_path = os.path.dirname(importlib.util.find_spec(package).submodule_search_locations[0])
if os.path.basename(root_path) == "src":
    root_path = os.path.dirname(root_path)

log_path = os.path.join(root_path, logdir)
if not os.path.exists(log_path):
    os.makedirs(log_path)

# 根 logger
logger = logging.getLogger(name)
logger.setLevel(TRACE)
console_handler.setLevel(level)
logger.addHandler(console_handler)

# 文件 Handler（按天轮转，保留 30 天）
file_handler = TimedRotatingFileHandler(f"{log_path}/{name}.log", encoding="utf-8", when="D", interval=1, backupCount=30)
file_handler.setFormatter(log_format)
file_handler.setLevel(level)
logger.addHandler(file_handler)
