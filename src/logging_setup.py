import sys
from pathlib import Path

from loguru import logger

EVENT_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {message: <12} | {extra}"


def configure_logging(
    level: str = "INFO",
    sink=sys.stderr,
    events=None,
    log_file: str | Path = None,
) -> None:
    """sink получает только события из events (или все), log_file - все события."""
    logger.remove()
    wanted = frozenset(events) if events is not None else None
    logger.add(
        sink,
        level=level,
        format=EVENT_FORMAT,
        filter=(lambda record: record["message"] in wanted)
        if wanted is not None
        else None,
    )

    if log_file is not None:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        logger.add(log_file, level=level, format=EVENT_FORMAT, encoding="utf-8")
