"""Утилиты для работы с URL фоновых изображений"""
import json
from pathlib import Path
from typing import Optional, Tuple
from loguru import logger

BACKGROUND_URLS_FILE = Path(__file__).resolve().parent.parent / "background_urls.json"

def save_background_urls(url1: str, url2: str):
    """Сохраняет URL фоновых изображений в файл (теперь используется только url2)"""
    try:
        data = {
            "image1_url": url1,  # Может быть пустой строкой, так как image1 теперь запрашивается у пользователя
            "image2_url": url2
        }
        with open(BACKGROUND_URLS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"✅ URL фона image2 сохранен в {BACKGROUND_URLS_FILE}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения URL: {e}")
        return False

def load_background_urls() -> Optional[Tuple[str, str]]:
    """Загружает URL фоновых изображений из файла"""
    try:
        logger.info(f"Попытка загрузки URL из файла: {BACKGROUND_URLS_FILE}")
        logger.info(f"Файл существует: {BACKGROUND_URLS_FILE.exists()}")
        
        if not BACKGROUND_URLS_FILE.exists():
            logger.info("Файл с URL фонов не найден")
            return None
        
        with open(BACKGROUND_URLS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        logger.info(f"Данные из файла: {data}")
        
        url1 = data.get("image1_url")
        url2 = data.get("image2_url")
        
        if url1 and url2:
            logger.info("✅ URL фонов загружены из файла")
            return (url1, url2)
        else:
            logger.warning("В файле отсутствуют URL")
            return None
            
    except Exception as e:
        logger.error(f"Ошибка загрузки URL: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

