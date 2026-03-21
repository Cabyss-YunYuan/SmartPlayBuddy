import logging
from logging.handlers import TimedRotatingFileHandler
import os
import importlib


class Logger:
    """日志"""
    package = "smartplaybuddy"
    logdir = "logs"

    def __init__(self, name="smtplay", level=logging.INFO):
        root_path = os.path.dirname(importlib.util.find_spec(self.package).submodule_search_locations[0])

        if os.path.basename(root_path) == "src":
            root_path = os.path.dirname(root_path)

        log_path = os.path.join(root_path, self.logdir)

        if not os.path.exists(log_path):
            os.makedirs(log_path)

        # 日志
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        log_format = logging.Formatter("%(levelname)s:\t%(asctime)s\t%(name)s\t%(message)s")
        # 控制台日志
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(log_format)
        self.logger.addHandler(console_handler)
        # 文件日志
        file_handler = TimedRotatingFileHandler(f"{log_path}/{name}.log", encoding="utf-8", when="D", interval=1, backupCount=30)
        file_handler.setFormatter(log_format)
        file_handler.setFormatter(log_format)
        self.logger.addHandler(file_handler)

logger = Logger()
