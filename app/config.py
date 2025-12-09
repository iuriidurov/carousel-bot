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
    logo_path: Path = BASE_DIR / "background" / "logo.png"
    
    # Airtable настройки
    airtable_api_token: Optional[str] = None
    airtable_base_id: Optional[str] = None
    airtable_table_name: Optional[str] = None  # Можно использовать название или Table ID
    airtable_table_id: Optional[str] = None  # Table ID (например, tblO5Y4TUpzjBhbUH)
    
    # Настройки retry
    gemini_max_retries: int = 3
    image_gen_max_retries: int = 2


settings = Settings(
    telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    replicate_api_key=os.getenv("REPLICATE_API_KEY", ""),
    kie_api_key=os.getenv("KIE_API_KEY", ""),
    admin_chat_id=os.getenv("ADMIN_CHAT_ID", None),
    airtable_api_token=os.getenv("AIRTABLE_API_TOKEN", None),
    airtable_base_id=os.getenv("AIRTABLE_BASE_ID", None),
    airtable_table_name=os.getenv("AIRTABLE_TABLE_NAME", None),
    airtable_table_id=os.getenv("AIRTABLE_TABLE_ID", None),
)

if not settings.telegram_token:
    raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env")
if not settings.replicate_api_key:
    raise ValueError("REPLICATE_API_KEY не задан в .env")
if not settings.kie_api_key:
    raise ValueError("KIE_API_KEY не задан в .env")

