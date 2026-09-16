import time
from ... import i18n
from ... import logger

logger = logger.logger.getChild("System")

def system(self, msg):
    if msg.Action == "pong":
        if msg.Data is None:
            return
        latency = int(time.time() * 1000) - (msg.Data['time'])
        logger.debug(i18n.translate("system.ping", device=msg.From if msg.From else "server", latency=f"{latency}ms"))
