import logging
import logging.handlers
import os
from pathlib import Path

def setup_logging(log_dir: str = "/app/logs", log_level: str = "INFO") -> logging.Logger:
    """Налаштування логування з ротацією файлів."""
    
    # Створити директорію для логів
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    
    # Основний логер
    logger = logging.getLogger("finstack")
    logger.setLevel(getattr(logging, log_level))
    
    # Формат логу
    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Файловий handler з ротацією (10 MB per file, 5 files max)
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "finstack.log"),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console handler (тільки INFO+ на консолі)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

__all__ = ["setup_logging"]
