import logging
from logging.handlers import TimedRotatingFileHandler
import os
import importlib.util

# 日志格式
log_format = logging.Formatter("%(levelname)-8s|\t%(asctime)s\t%(name)-30s\t%(message)s")
# 控制台日志
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_format)

package = "smartplaybuddy"
logdir = "logs"

name="SmtPlay"
level=logging.INFO
level=logging.DEBUG

root_path = os.path.dirname(importlib.util.find_spec(package).submodule_search_locations[0])

if os.path.basename(root_path) == "src":
    root_path = os.path.dirname(root_path)

log_path = os.path.join(root_path, logdir)

if not os.path.exists(log_path):
    os.makedirs(log_path)

# 日志
logger = logging.getLogger(name)
logger.setLevel(level)

# 控制台日志
logger.addHandler(console_handler)

# 文件日志
file_handler = TimedRotatingFileHandler(f"{log_path}/{name}.log", encoding="utf-8", when="D", interval=1, backupCount=30)
file_handler.setFormatter(log_format)
file_handler.setFormatter(log_format)
logger.addHandler(file_handler)
