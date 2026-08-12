import time
from ... import i18n
from ... import log

logger = log.logger.getChild("System")

def system(self, msg):
    if msg.Action == "pong":
        latency = int(time.time() * 1000) - (msg.Data['time'])
        logger.info(i18n.translate("system.ping", device=msg.From if msg.From else "server", latency=f"{latency}ms"))
