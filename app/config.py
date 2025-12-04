"""Конфигурация бота"""
from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import Optional
import os

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)


class Settings(BaseModel):
    telegram_token: str
    replicate_api_key: str
    kie_api_key: str
    admin_chat_id: Optional[str] = None  # ID чата для загрузки фоновых изображений
    background_dir: Path = BASE_DIR / "background"
    image1_path: Path = BASE_DIR / "background" / "image1.jpg"
    image2_path: Path = BASE_DIR / "background" / "image2.jpg"
    
    # Настройки retry
    gemini_max_retries: int = 3
    image_gen_max_retries: int = 2


settings = Settings(
    telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    replicate_api_key=os.getenv("REPLICATE_API_KEY", ""),
    kie_api_key=os.getenv("KIE_API_KEY", ""),
    admin_chat_id=os.getenv("ADMIN_CHAT_ID", None),
)

if not settings.telegram_token:
    raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env")
if not settings.replicate_api_key:
    raise ValueError("REPLICATE_API_KEY не задан в .env")
if not settings.kie_api_key:
    raise ValueError("KIE_API_KEY не задан в .env")

