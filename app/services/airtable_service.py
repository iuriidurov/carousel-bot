"""Сервис для работы с Airtable API"""
from datetime import datetime
from typing import Optional, Dict, Any, List
from pyairtable import Table
from loguru import logger

from ..config import settings


class AirtableService:
    """Сервис для работы с Airtable API"""
    
    def __init__(
        self,
        api_token: Optional[str] = None,
        base_id: Optional[str] = None,
        table_id: Optional[str] = None
    ):
        self._api_token = api_token or settings.airtable_api_token
        self._base_id = base_id or settings.airtable_base_id
        self._table_id = table_id or settings.airtable_table_id
        
        if not self._api_token:
            raise RuntimeError("Airtable API token (AIRTABLE_API_TOKEN) не задан.")
        if not self._base_id:
            raise RuntimeError("Airtable Base ID (AIRTABLE_BASE_ID) не задан.")
        if not self._table_id:
            raise RuntimeError("Airtable Table ID (AIRTABLE_TABLE_ID) не задан.")
        
        # Инициализируем таблицу
        # pyairtable может работать как с Table ID, так и с Table Name
        # Попробуем использовать Table ID
        self._table = Table(self._api_token, self._base_id, self._table_id)
        logger.info(f"AirtableService инициализирован: Base ID={self._base_id}, Table ID={self._table_id}")
    
    def create_carousel_record(
        self,
        topic: str,
        slides_count: int,
        image1_url: Optional[str],
        slides_prompts: Dict[int, str],
        slides_images: Dict[int, str],
        infographic_prompt: Optional[str] = None,
        infographic_image_url: Optional[str] = None,
        post_text: Optional[str] = None
    ) -> str:
        """
        Создает запись в Airtable с данными о карусели.
        
        Args:
            topic: Тема карусели
            slides_count: Количество слайдов
            image1_url: URL изображения для первого слайда (от пользователя)
            slides_prompts: Словарь {номер_слайда: промпт}
            slides_images: Словарь {номер_слайда: url_изображения}
            infographic_prompt: Промпт для инфографики (если была сгенерирована)
            infographic_image_url: URL изображения инфографики (если была сгенерирована)
            post_text: Текст поста (если был сгенерирован)
        
        Returns:
            Record ID созданной записи
        """
        try:
            logger.info(f"[AIRTABLE] Начинаю создание записи для темы: {topic}, слайдов: {slides_count}")
            logger.info(f"[AIRTABLE] Промптов: {len(slides_prompts)}, изображений: {len(slides_images)}")
            
            # Формируем данные для записи
            # Для поля Date в Airtable используем формат YYYY-MM-DD (только дата, без времени)
            today = datetime.now().date().isoformat()
            record_data: Dict[str, Any] = {
                "Тема от пользователя": topic,
                "Дата запроса пользователя по теме": today,
                "Количество слайдов": slides_count,
            }
            logger.debug(f"[AIRTABLE] Базовые поля заполнены: тема, дата, количество слайдов")
            
            # Добавляем background/image1 (Attachment)
            if image1_url:
                record_data["background/image1"] = [{"url": image1_url}]
                logger.debug(f"[AIRTABLE] Добавлен image1_url: {image1_url[:50]}...")
            
            # Добавляем промпты для слайдов (1-8)
            prompts_added = 0
            for slide_num in range(1, 9):
                prompt_key = f"Prompt_slide{slide_num}"
                if slide_num in slides_prompts:
                    prompt_value = slides_prompts[slide_num]
                    record_data[prompt_key] = prompt_value
                    prompts_added += 1
                    logger.info(f"[AIRTABLE] ===== ПРОМПТ ДЛЯ СЛАЙДА {slide_num} (что сохраняется в Airtable) =====")
                    logger.info(f"[AIRTABLE] {prompt_value}")
                    logger.info(f"[AIRTABLE] ===== КОНЕЦ ПРОМПТА ДЛЯ СЛАЙДА {slide_num} =====")
                    logger.debug(f"[AIRTABLE] Длина промпта для слайда {slide_num}: {len(prompt_value)} символов")
                # Если слайда нет, поле остается пустым
            logger.info(f"[AIRTABLE] Добавлено промптов: {prompts_added}")
            
            # Добавляем изображения слайдов (1-8) как Attachment
            images_added = 0
            for slide_num in range(1, 9):
                visual_key = f"Visual_slide{slide_num}"
                if slide_num in slides_images:
                    image_url = slides_images[slide_num]
                    record_data[visual_key] = [{"url": image_url}]
                    images_added += 1
                    logger.debug(f"[AIRTABLE] Добавлено изображение для слайда {slide_num}: {image_url[:50]}...")
                # Если слайда нет, поле остается пустым
            logger.info(f"[AIRTABLE] Добавлено изображений: {images_added}")
            
            # Добавляем инфографику (если есть)
            if infographic_prompt:
                record_data["Prompt_infografic"] = infographic_prompt
                logger.debug(f"[AIRTABLE] Добавлен промпт инфографики (длина: {len(infographic_prompt)} символов)")
            if infographic_image_url:
                record_data["Visual_infografic"] = [{"url": infographic_image_url}]
                logger.debug(f"[AIRTABLE] Добавлено изображение инфографики: {infographic_image_url[:50]}...")
            
            # Добавляем пост (если есть)
            if post_text:
                record_data["Post_text"] = post_text
                logger.debug(f"[AIRTABLE] Добавлен текст поста (длина: {len(post_text)} символов)")
            
            # Создаем запись
            logger.info(f"[AIRTABLE] Отправляю запрос на создание записи в Airtable...")
            record = self._table.create(record_data)
            record_id = record["id"]
            logger.info(f"[AIRTABLE] ✅ Запись успешно создана в Airtable с Record ID: {record_id}")
            logger.info(f"[AIRTABLE] Всего полей в записи: {len(record_data)}")
            
            return record_id
            
        except Exception as e:
            logger.error(f"Ошибка создания записи в Airtable: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise
    
    def get_record_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        """
        Получает запись по Record ID.
        
        Args:
            record_id: ID записи в Airtable
        
        Returns:
            Словарь с данными записи или None, если запись не найдена
        """
        try:
            logger.info(f"[AIRTABLE] Читаю запись {record_id} из Airtable...")
            record = self._table.get(record_id)
            logger.info(f"[AIRTABLE] ✅ Запись {record_id} успешно прочитана из Airtable")
            return record
        except Exception as e:
            logger.error(f"[AIRTABLE] ❌ Ошибка получения записи {record_id} из Airtable: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def get_slide_prompt(self, record_id: str, slide_num: int) -> Optional[str]:
        """
        Получает промпт для конкретного слайда из записи.
        
        Args:
            record_id: ID записи в Airtable
            slide_num: Номер слайда (1-8)
        
        Returns:
            Промпт для слайда или None, если не найден
        """
        try:
            logger.info(f"[AIRTABLE] Получаю промпт для слайда {slide_num} из записи {record_id}...")
            record = self.get_record_by_id(record_id)
            if not record:
                logger.error(f"[AIRTABLE] ❌ Запись {record_id} не найдена")
                return None
            
            fields = record.get("fields", {})
            prompt_key = f"Prompt_slide{slide_num}"
            prompt = fields.get(prompt_key)
            
            if prompt:
                logger.info(f"[AIRTABLE] ✅ Промпт для слайда {slide_num} успешно получен. Длина: {len(prompt)} символов")
                logger.debug(f"[AIRTABLE] Промпт (первые 100 символов): {prompt[:100]}...")
            else:
                logger.warning(f"[AIRTABLE] ⚠️ Промпт для слайда {slide_num} не найден в записи {record_id}")
            
            return prompt
            
        except Exception as e:
            logger.error(f"[AIRTABLE] ❌ Ошибка получения промпта для слайда {slide_num}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def update_slide_image(self, record_id: str, slide_num: int, image_url: str) -> bool:
        """
        Обновляет изображение слайда в записи (заменяет старое на новое).
        
        Args:
            record_id: ID записи в Airtable
            slide_num: Номер слайда (1-8)
            image_url: URL нового изображения
        
        Returns:
            True если обновление успешно, False в противном случае
        """
        try:
            visual_key = f"Visual_slide{slide_num}"
            update_data = {
                visual_key: [{"url": image_url}]
            }
            
            logger.info(f"[AIRTABLE] Обновляю изображение слайда {slide_num} в записи {record_id}")
            logger.debug(f"[AIRTABLE] URL нового изображения: {image_url[:80]}...")
            self._table.update(record_id, update_data)
            logger.info(f"[AIRTABLE] ✅ Изображение слайда {slide_num} успешно обновлено в Airtable")
            
            return True
            
        except Exception as e:
            logger.error(f"[AIRTABLE] ❌ Ошибка обновления изображения слайда {slide_num}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def update_infographic_image(self, record_id: str, image_url: str, prompt: Optional[str] = None) -> bool:
        """
        Обновляет изображение инфографики в записи.
        
        Args:
            record_id: ID записи в Airtable
            image_url: URL нового изображения
            prompt: Промпт для инфографики (опционально)
        
        Returns:
            True если обновление успешно, False в противном случае
        """
        try:
            update_data = {
                "Visual_infografic": [{"url": image_url}]
            }
            
            # Если передан промпт, добавляем его в обновление
            if prompt:
                update_data["Prompt_infografic"] = prompt
                logger.debug(f"[AIRTABLE] Также обновляю промпт инфографики (длина: {len(prompt)} символов)")
            
            logger.info(f"[AIRTABLE] Обновляю изображение инфографики в записи {record_id}")
            logger.debug(f"[AIRTABLE] URL нового изображения: {image_url[:80]}...")
            self._table.update(record_id, update_data)
            logger.info(f"[AIRTABLE] ✅ Изображение инфографики успешно обновлено в Airtable")
            
            return True
            
        except Exception as e:
            logger.error(f"[AIRTABLE] ❌ Ошибка обновления изображения инфографики: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def update_post_text(self, record_id: str, post_text: str) -> bool:
        """
        Обновляет текст поста в записи.
        
        Args:
            record_id: ID записи в Airtable
            post_text: Новый текст поста
        
        Returns:
            True если обновление успешно, False в противном случае
        """
        try:
            update_data = {
                "Post_text": post_text
            }
            
            logger.info(f"[AIRTABLE] Обновляю текст поста в записи {record_id}")
            logger.debug(f"[AIRTABLE] Длина текста поста: {len(post_text)} символов")
            self._table.update(record_id, update_data)
            logger.info(f"[AIRTABLE] ✅ Текст поста успешно обновлен в Airtable")
            
            return True
            
        except Exception as e:
            logger.error(f"[AIRTABLE] ❌ Ошибка обновления текста поста: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

