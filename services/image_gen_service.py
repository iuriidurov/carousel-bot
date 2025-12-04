"""Сервис для работы с Nana Banana Pro через Kie.ai API"""
import asyncio
import json
from typing import Optional, List
import httpx
from loguru import logger

from ..config import settings


class NanaBananaProTimeoutError(TimeoutError):
    """Исключение для таймаута генерации изображений Nana Banana Pro"""
    def __init__(self, task_id: str) -> None:
        super().__init__("Nana Banana Pro image generation timed out")
        self.task_id = task_id


class ImageGenService:
    """Сервис для генерации изображений через Kie.ai API (Nana Banana Pro)"""
    CREATE_TASK_URL = "https://api.kie.ai/api/v1/jobs/createTask"
    STATUS_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or settings.kie_api_key
        if not self._api_key:
            raise RuntimeError("Kie.ai API key (KIE_API_KEY) не задан.")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={"Authorization": f"Bearer {self._api_key}"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def generate_image(
        self,
        prompt: str,
        image_input: Optional[List[str]] = None,
        aspect_ratio: str = "4:5",
        resolution: str = "2K",
        output_format: str = "png",
    ) -> str:
        """
        Создает задачу генерации изображения через Nana Banana Pro API.
        
        Args:
            prompt: Текст описания изображения (до 5000 символов)
            image_input: Список URL изображений для использования в качестве референса (до 8 изображений)
            aspect_ratio: Соотношение сторон (по умолчанию 4:5 для Instagram)
            resolution: Разрешение (по умолчанию 2K)
            output_format: Формат вывода (по умолчанию png)
            
        Returns:
            taskId для отслеживания статуса генерации
            
        Raises:
            RuntimeError: При ошибке создания задачи
        """
        if len(prompt) > 5000:
            raise ValueError("Промпт не может превышать 5000 символов")
        
        if image_input and len(image_input) > 8:
            raise ValueError("Можно использовать не более 8 изображений")
        
        # Фильтруем пустые значения и None из image_input
        original_image_input = image_input
        if image_input:
            # Убираем None, пустые строки и невалидные URL
            filtered_image_input = [
                url for url in image_input 
                if url and isinstance(url, str) and url.strip() and (url.startswith("http://") or url.startswith("https://"))
            ]
            # Если после фильтрации список пуст, используем пустой список
            image_input = filtered_image_input if filtered_image_input else []
            if original_image_input != image_input:
                logger.warning(f"Отфильтрованы невалидные URL из image_input. Было: {original_image_input}, стало: {image_input}")
        else:
            image_input = []
        
        payload = {
            "model": "nano-banana-pro",
            "input": {
                "prompt": prompt,
                "image_input": image_input,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "output_format": output_format,
            },
        }

        logger.info(f"Запуск Nana Banana Pro через Kie.ai")
        logger.info(f"Промпт: {prompt[:100]}...")
        logger.info(f"Соотношение сторон: {aspect_ratio}")
        logger.info(f"Разрешение: {resolution}")
        logger.info(f"Формат: {output_format}")
        if image_input:
            logger.info(f"Изображений для референса: {len(image_input)}")
            for i, url in enumerate(image_input, 1):
                logger.info(f"  URL {i}: {url[:80]}...")
        else:
            logger.info("Изображений для референса: 0 (text-to-image режим)")
        # Логируем полный payload для отладки (скрываем только длинные URL)
        payload_for_log = payload.copy()
        if "input" in payload_for_log and "image_input" in payload_for_log["input"]:
            image_input_log = payload_for_log["input"]["image_input"]
            if image_input_log:
                payload_for_log["input"]["image_input"] = [url[:80] + "..." if len(url) > 80 else url for url in image_input_log]
        logger.info(f"Payload для отправки: {payload_for_log}")

        try:
            logger.info("Отправка запроса на создание задачи Nana Banana Pro...")
            create_response = await self._client.post(self.CREATE_TASK_URL, json=payload)
            create_response.raise_for_status()
            create_data = create_response.json()
            
            logger.info(f"Ответ на создание: {create_data}")
            
            code = create_data.get("code")
            if code != 200:
                msg = create_data.get("msg", "Неизвестная ошибка")
                logger.error(f"Nana Banana Pro API вернул ошибку: {code} - {msg}")
                logger.error(f"Отправленный payload: {payload}")
                logger.error(f"Полный ответ API: {create_data}")
                raise RuntimeError(f"Ошибка Nana Banana Pro API: {msg}")
            
            task_id = create_data.get("data", {}).get("taskId")
            if not task_id:
                logger.error(f"Nana Banana Pro не вернул taskId: {create_data}")
                raise RuntimeError("Nana Banana Pro не вернул ID задачи")
            
            logger.info(f"Task ID: {task_id}")
            return task_id
            
        except httpx.HTTPStatusError as exc:
            error_text = exc.response.text if exc.response else "Нет ответа"
            logger.error(f"HTTP ошибка Nana Banana Pro API {exc.response.status_code}: {error_text}")
            logger.error(f"Отправленный payload: {payload}")
            try:
                error_data = exc.response.json()
                error_msg = error_data.get("msg", error_text)
                error_code = error_data.get("code", "unknown")
                logger.error(f"Код ошибки API: {error_code}, Сообщение: {error_msg}")
            except Exception:
                error_msg = error_text
            raise RuntimeError(f"Ошибка Nana Banana Pro API: {error_msg}") from exc
        except httpx.RequestError as exc:
            logger.exception("Ошибка запроса к Nana Banana Pro API: {}", exc)
            raise RuntimeError(f"Ошибка подключения к Nana Banana Pro API: {exc}") from exc
        except Exception as exc:
            logger.exception("Неожиданная ошибка Nana Banana Pro API: {}", exc)
            raise RuntimeError(f"Неожиданная ошибка Nana Banana Pro API: {exc}") from exc

    async def get_task_status(self, task_id: str) -> dict:
        """
        Получает статус задачи генерации.
        
        Args:
            task_id: ID задачи
            
        Returns:
            Словарь с данными задачи
            
        Raises:
            RuntimeError: При ошибке запроса
        """
        try:
            response = await self._client.get(f"{self.STATUS_URL}?taskId={task_id}")
            response.raise_for_status()
            data = response.json()
            
            code = data.get("code")
            if code != 200:
                msg = data.get("msg", "Неизвестная ошибка")
                logger.error(f"Nana Banana Pro API вернул ошибку при проверке статуса: {code} - {msg}")
                raise RuntimeError(f"Ошибка Nana Banana Pro API: {msg}")
            
            return data.get("data", {})
            
        except httpx.HTTPStatusError as exc:
            error_text = exc.response.text if exc.response else "Нет ответа"
            logger.error(f"HTTP ошибка Nana Banana Pro API {exc.response.status_code}: {error_text}")
            raise RuntimeError(f"Ошибка Nana Banana Pro API: {error_text}") from exc
        except httpx.RequestError as exc:
            logger.exception("Ошибка запроса к Nana Banana Pro API: {}", exc)
            raise RuntimeError(f"Ошибка подключения к Nana Banana Pro API: {exc}") from exc

    async def wait_for_result(
        self,
        task_id: str,
        max_wait_time: int = 300,
        poll_interval: int = 3,
    ) -> List[str]:
        """
        Ожидает завершения генерации и возвращает URL изображений.
        
        Args:
            task_id: ID задачи
            max_wait_time: Максимальное время ожидания в секундах (по умолчанию 5 минут)
            poll_interval: Интервал опроса в секундах
            
        Returns:
            Список URL сгенерированных изображений
            
        Raises:
            NanaBananaProTimeoutError: При превышении времени ожидания
            RuntimeError: При ошибке генерации
        """
        start_time = asyncio.get_event_loop().time()
        
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > max_wait_time:
                logger.warning(f"Таймаут ожидания результата для task_id: {task_id}")
                raise NanaBananaProTimeoutError(task_id)
            
            task_data = await self.get_task_status(task_id)
            state = task_data.get("state")
            
            logger.info(f"Статус задачи {task_id}: {state}")
            
            if state == "success":
                result_json_str = task_data.get("resultJson")
                if not result_json_str:
                    logger.error(f"Результат пуст для task_id: {task_id}")
                    raise RuntimeError("Результат генерации пуст")
                
                try:
                    result_json = json.loads(result_json_str)
                    result_urls = result_json.get("resultUrls", [])
                    if not result_urls:
                        logger.error(f"Нет URL в результате для task_id: {task_id}")
                        raise RuntimeError("Нет URL изображений в результате")
                    
                    logger.info(f"Получено {len(result_urls)} изображений")
                    return result_urls
                    
                except json.JSONDecodeError as exc:
                    logger.exception("Ошибка парсинга resultJson: {}", exc)
                    raise RuntimeError(f"Ошибка парсинга результата: {exc}") from exc
                    
            elif state == "fail":
                fail_code = task_data.get("failCode", "unknown")
                fail_msg = task_data.get("failMsg", "Неизвестная ошибка")
                logger.error(f"Генерация не удалась: {fail_code} - {fail_msg}")
                raise RuntimeError(f"Генерация не удалась: {fail_msg}")
            
            # Ждем перед следующим опросом
            await asyncio.sleep(poll_interval)

