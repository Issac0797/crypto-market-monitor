"""Local rotating logging for explicit connection tools."""

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(level: str) -> None:
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                "logs/crypto-market-monitor.log",
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            ),
        ],
    )
    logging.Formatter.converter = time.gmtime
