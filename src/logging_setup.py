import sys

from loguru import logger

EVENT_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {message: <12} | {extra}"


def configure_logging(level: str = "INFO", sink=sys.stderr) -> None:
    logger.remove()
    logger.add(sink, level=level, format=EVENT_FORMAT)
