"""
로깅 설정 모듈
컬러 출력 및 파일 로테이션을 지원하는 로거를 제공합니다.
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime


# ANSI 컬러 코드
class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"


class ColoredFormatter(logging.Formatter):
    """컬러 출력을 지원하는 로그 포매터"""

    LEVEL_COLORS = {
        logging.DEBUG: Colors.CYAN,
        logging.INFO: Colors.GREEN,
        logging.WARNING: Colors.YELLOW,
        logging.ERROR: Colors.RED,
        logging.CRITICAL: Colors.BOLD + Colors.RED,
    }

    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, Colors.WHITE)
        record.levelname = f"{color}{record.levelname}{Colors.RESET}"
        record.name = f"{Colors.BLUE}{record.name}{Colors.RESET}"
        return super().format(record)


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    지정된 이름의 로거를 반환합니다.

    Args:
        name: 로거 이름 (보통 __name__ 사용)
        level: 로그 레벨 (기본값: logging.INFO)

    Returns:
        설정된 Logger 객체
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(level)

    # 콘솔 핸들러 (컬러 출력)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_formatter = ColoredFormatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # 파일 핸들러 (일별 로테이션)
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "autotrader.log"),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    return logger
