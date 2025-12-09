"""Утилиты для наложения водяного знака (логотипа) на изображения"""
import io
from pathlib import Path
from typing import Optional
from PIL import Image
from loguru import logger

from ..config import settings


async def add_watermark(
    image_bytes: bytes, 
    logo_path: Optional[Path] = None,
    position: Optional[str] = "top-left",
    is_light: bool = False
) -> bytes:
    """
    Накладывает логотип-водяной знак на изображение.
    
    Args:
        image_bytes: Байты исходного изображения
        logo_path: Путь к файлу логотипа (если None, используется из конфига)
        position: Позиция логотипа - "top-left", "bottom-left", "bottom-right" или None (без логотипа)
        is_light: Если True, делает логотип светлым (для темных фонов)
    
    Returns:
        Байты изображения с наложенным водяным знаком
        
    Примечание:
        В случае ошибки возвращает оригинальные байты, чтобы не ломать процесс генерации.
    """
    try:
        # Если позиция None, не накладываем логотип
        if position is None:
            return image_bytes
        
        # Используем путь из конфига, если не указан явно
        if logo_path is None:
            logo_path = settings.logo_path
        
        # Проверяем существование файла логотипа
        if not logo_path.exists():
            logger.warning(f"Файл логотипа не найден: {logo_path}. Пропускаем наложение водяного знака.")
            return image_bytes
        
        # 1. Открываем основное изображение из байтов
        base_image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        
        # 2. Открываем логотип
        watermark = Image.open(logo_path).convert("RGBA")
        
        # 3. Рассчитываем размер логотипа (20% от ширины слайда)
        target_width = int(base_image.width * 0.20)
        aspect_ratio = watermark.height / watermark.width
        target_height = int(target_width * aspect_ratio)
        
        # Изменяем размер логотипа с высоким качеством
        watermark = watermark.resize((target_width, target_height), Image.Resampling.LANCZOS)
        
        # 4. Если нужен светлый логотип (для темного фона), делаем его белым
        if is_light:
            # Берем альфа-канал оригинального логотипа (уже измененного размера)
            alpha_channel = watermark.split()[3]
            # Создаем белое изображение того же размера
            watermark = Image.new("RGBA", (target_width, target_height), (255, 255, 255, 255))
            # Применяем альфа-канал оригинального логотипа
            # Где был логотип (непрозрачные пиксели) - белый, где прозрачно - прозрачно
            watermark.putalpha(alpha_channel)
        
        # 5. Настраиваем прозрачность логотипа (80% непрозрачности для водяного знака)
        # Извлекаем альфа-канал
        alpha = watermark.split()[3]
        # Уменьшаем непрозрачность до 80%
        alpha = alpha.point(lambda p: int(p * 0.8))
        watermark.putalpha(alpha)
        
        # 6. Определяем позицию в зависимости от параметра
        padding = int(base_image.width * 0.05)
        if position == "top-left":
            position_coords = (padding, padding)
        elif position == "bottom-left":
            position_coords = (padding, base_image.height - target_height - padding)
        elif position == "bottom-right":
            position_coords = (base_image.width - target_width - padding, base_image.height - target_height - padding)
        else:
            # Неизвестная позиция, используем top-left по умолчанию
            logger.warning(f"Неизвестная позиция {position}, используем top-left")
            position_coords = (padding, padding)
        
        # 7. Создаем прозрачный слой для наложения
        transparent_layer = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
        transparent_layer.paste(watermark, position_coords, mask=watermark)
        
        # 8. Объединяем слои
        combined = Image.alpha_composite(base_image, transparent_layer)
        
        # 9. Конвертируем обратно в байты (PNG для сохранения прозрачности)
        output = io.BytesIO()
        combined.save(output, format="PNG", quality=95, optimize=True)
        return output.getvalue()
        
    except Exception as e:
        logger.error(f"Ошибка наложения водяного знака: {e}")
        import traceback
        logger.error(traceback.format_exc())
        # В случае ошибки возвращаем оригинал, чтобы не ломать процесс
        return image_bytes

