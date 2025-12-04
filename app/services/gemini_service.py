"""Сервис для работы с Gemini 3 Pro через Replicate API"""
import asyncio
import json
from typing import Optional
import httpx
from loguru import logger
import json_repair

from ..config import settings


class Gemini3ProTimeoutError(TimeoutError):
    """Исключение для таймаута генерации через Gemini 3 Pro"""
    def __init__(self, prediction_id: str) -> None:
        super().__init__("Gemini 3 Pro generation timed out")
        self.prediction_id = prediction_id


class GeminiService:
    """Сервис для работы с Gemini 3 Pro через Replicate API"""
    BASE_URL = "https://api.replicate.com"
    MODEL_NAME = "google/gemini-3-pro"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or settings.replicate_api_key
        if not self._api_key:
            raise RuntimeError("Replicate API key (REPLICATE_API_KEY) не задан.")
        
        self._headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(timeout=300, headers=self._headers)
        logger.info("GeminiService инициализирован")

    async def close(self) -> None:
        """Закрытие HTTP клиента"""
        await self._client.aclose()

    async def generate_json(
        self,
        topic: str,
        system_prompt: str,
        slides_count: int = 8,
        max_retries: int = 3,
    ) -> dict:
        """
        Генерирует JSON структуру карусели через Gemini 3 Pro.
        
        Args:
            topic: Тема от пользователя
            system_prompt: Системный промпт (должен содержать {slides_count} для подстановки)
            slides_count: Количество слайдов (по умолчанию 8)
            max_retries: Максимальное количество попыток (по умолчанию 3)
            
        Returns:
            Словарь с JSON структурой карусели
            
        Raises:
            RuntimeError: При ошибке генерации после всех попыток
        """
        # Форматируем системный промпт с количеством слайдов
        try:
            formatted_system_prompt = system_prompt.format(slides_count=slides_count)
        except Exception as e:
            logger.error(f"Ошибка форматирования системного промпта: {e}")
            raise RuntimeError(f"Ошибка форматирования промпта: {e}")
        
        prompt = f"{topic}\n\nСоздай структуру карусели из {slides_count} слайдов в формате JSON."
        logger.debug(f"Сформированный промпт: {prompt[:200]}...")
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Попытка {attempt + 1}/{max_retries} генерации JSON для темы: {topic[:50]}...")
                
                # Генерируем текст с отформатированным системным промптом
                response_text = await self._generate_text(prompt, formatted_system_prompt)
                
                # Проверяем, что ответ не пустой и достаточно длинный
                if not response_text or len(response_text.strip()) < 10:
                    logger.error(f"Получен пустой или слишком короткий ответ от Gemini: '{response_text}'")
                    if attempt < max_retries - 1:
                        logger.info("Повторяем попытку...")
                        await asyncio.sleep(2)
                        continue
                    else:
                        raise RuntimeError("Gemini вернул пустой ответ")
                
                # Пытаемся распарсить JSON
                try:
                    json_data = json.loads(response_text)
                    logger.info("JSON успешно распарсен")
                    return json_data
                except json.JSONDecodeError as e:
                    logger.warning(f"Ошибка парсинга JSON: {e}")
                    logger.warning(f"Полученный текст (первые 1000 символов): {response_text[:1000]}")
                    
                    # Пытаемся исправить JSON
                    if attempt < max_retries - 1:
                        logger.info("Попытка исправить JSON с помощью json_repair...")
                        try:
                            repaired_json = json_repair.repair_json(response_text)
                            json_data = json.loads(repaired_json)
                            logger.info("JSON успешно исправлен")
                            return json_data
                        except Exception as repair_error:
                            logger.warning(f"Не удалось исправить JSON: {repair_error}")
                            # Продолжаем на следующую попытку
                    else:
                        # Последняя попытка - пробуем исправить еще раз
                        logger.info("Последняя попытка исправить JSON...")
                        try:
                            repaired_json = json_repair.repair_json(response_text)
                            json_data = json.loads(repaired_json)
                            logger.info("JSON успешно исправлен на последней попытке")
                            return json_data
                        except Exception:
                            # Если не получилось - выбрасываем ошибку
                            raise RuntimeError(
                                "Произошел технический сбой, в настоящее время я не могу выполнить ваше задание. "
                                "Информация уже передана разработчикам, они исправляют проблему. "
                                "Повторите ваш запрос через некоторое время."
                            )
                
            except Exception as e:
                logger.error(f"Ошибка на попытке {attempt + 1}: {e}")
                logger.exception(f"Полный traceback ошибки на попытке {attempt + 1}:")
                if attempt == max_retries - 1:
                    raise RuntimeError(
                        "Произошел технический сбой, в настоящее время я не могу выполнить ваше задание. "
                        "Информация уже передана разработчикам, они исправляют проблему. "
                        "Повторите ваш запрос через некоторое время."
                    )
                # Ждем перед следующей попыткой
                await asyncio.sleep(2)
        
        raise RuntimeError("Не удалось сгенерировать JSON после всех попыток")

    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 1.0,
        top_p: float = 0.95,
        max_output_tokens: int = 65535,
        max_retries: int = 3,
    ) -> str:
        """
        Генерирует текст через Gemini 3 Pro через Replicate API (публичный метод).
        
        Args:
            prompt: Текст промпта
            system_instruction: Системная инструкция
            temperature: Температура выборки
            top_p: Параметр nucleus sampling
            max_output_tokens: Максимальное количество токенов
            max_retries: Максимальное количество попыток (по умолчанию 3)
            
        Returns:
            Сгенерированный текст
            
        Raises:
            RuntimeError: При ошибке генерации после всех попыток
        """
        for attempt in range(max_retries):
            try:
                logger.info(f"Попытка {attempt + 1}/{max_retries} генерации текста...")
                
                response_text = await self._generate_text(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    temperature=temperature,
                    top_p=top_p,
                    max_output_tokens=max_output_tokens,
                )
                
                # Проверяем, что ответ не пустой
                if not response_text or len(response_text.strip()) < 10:
                    logger.error(f"Получен пустой или слишком короткий ответ от Gemini: '{response_text}'")
                    if attempt < max_retries - 1:
                        logger.info("Повторяем попытку...")
                        await asyncio.sleep(2)
                        continue
                    else:
                        raise RuntimeError("Gemini вернул пустой ответ")
                
                logger.info("Текст успешно сгенерирован")
                return response_text.strip()
                
            except Exception as e:
                logger.error(f"Ошибка на попытке {attempt + 1}: {e}")
                logger.exception(f"Полный traceback ошибки на попытке {attempt + 1}:")
                if attempt == max_retries - 1:
                    raise RuntimeError(
                        "Произошел технический сбой, в настоящее время я не могу выполнить ваше задание. "
                        "Информация уже передана разработчикам, они исправляют проблему. "
                        "Повторите ваш запрос через некоторое время."
                    )
                # Ждем перед следующей попыткой
                await asyncio.sleep(2)
        
        raise RuntimeError("Не удалось сгенерировать текст после всех попыток")

    async def _generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 1.0,
        top_p: float = 0.95,
        max_output_tokens: int = 65535,
    ) -> str:
        """
        Генерирует текст через Gemini 3 Pro через Replicate API.
        
        Args:
            prompt: Текст промпта
            system_instruction: Системная инструкция
            temperature: Температура выборки
            top_p: Параметр nucleus sampling
            max_output_tokens: Максимальное количество токенов
            
        Returns:
            Сгенерированный текст
        """
        url = f"{self.BASE_URL}/v1/models/{self.MODEL_NAME}/predictions"
        
        input_data = {
            "prompt": prompt,
            "temperature": temperature,
            "top_p": top_p,
            "max_output_tokens": max_output_tokens,
        }
        
        if system_instruction:
            input_data["system_instruction"] = system_instruction
        
        payload = {
            "input": input_data,
        }

        logger.info(f"Запуск генерации Gemini 3 Pro через Replicate")
        logger.debug(f"Промпт: {prompt[:100]}...")

        try:
            logger.info("Отправка запроса на создание предсказания...")
            create_response = await self._client.post(url, json=payload)
            create_response.raise_for_status()
            create_data = create_response.json()
            
            prediction_id = create_data.get("id")
            if not prediction_id:
                logger.error(f"Replicate не вернул prediction ID: {create_data}")
                raise RuntimeError("Replicate не вернул ID предсказания")
            
            logger.info(f"Prediction ID: {prediction_id}")
            
            # Ожидаем завершения генерации
            result = await self._wait_for_result(prediction_id)
            
            # Результат - это массив строк, объединяем их
            if isinstance(result, list):
                response_text = "".join(result)
            else:
                response_text = str(result)
            
            # Логируем полученный ответ для отладки
            logger.info(f"Получен ответ от Gemini (первые 500 символов): {response_text[:500]}")
            logger.debug(f"Полный ответ от Gemini: {response_text}")
            
            return response_text
            
        except httpx.HTTPStatusError as exc:
            error_text = exc.response.text if exc.response else "Нет ответа"
            logger.error(f"HTTP ошибка Replicate API {exc.response.status_code}: {error_text}")
            try:
                error_data = exc.response.json()
                error_msg = error_data.get("detail", error_text)
            except Exception:
                error_msg = error_text
            raise RuntimeError(f"Ошибка Replicate API: {error_msg}") from exc
        except httpx.RequestError as exc:
            logger.exception("Ошибка запроса к Replicate API: {}", exc)
            raise RuntimeError(f"Ошибка подключения к Replicate API: {exc}") from exc
        except Exception as exc:
            logger.exception("Неожиданная ошибка Replicate API: {}", exc)
            raise RuntimeError(f"Неожиданная ошибка Replicate API: {exc}") from exc

    async def _wait_for_result(
        self,
        prediction_id: str,
        max_attempts: int = 120,
        check_interval: int = 2,
    ) -> list:
        """
        Ожидает завершения генерации и возвращает результат.
        
        Args:
            prediction_id: ID предсказания
            max_attempts: Максимальное количество попыток проверки
            check_interval: Интервал между проверками в секундах
            
        Returns:
            Результат генерации (массив строк)
        """
        logger.info(f"Ожидание результата для prediction_id: {prediction_id}")
        
        for attempt in range(max_attempts):
            await asyncio.sleep(check_interval)
            
            logger.debug(f"Проверка статуса, попытка {attempt + 1}/{max_attempts}...")
            get_url = f"{self.BASE_URL}/v1/predictions/{prediction_id}"
            response = await self._client.get(get_url)
            response.raise_for_status()
            data = response.json()
            
            status = data.get("status")
            
            if status == "succeeded":
                output = data.get("output")
                if output is not None:
                    logger.info(f"Генерация завершена успешно")
                    logger.debug(f"Тип output: {type(output)}, длина: {len(str(output)) if output else 0}")
                    if isinstance(output, list) and len(output) > 0:
                        logger.debug(f"Первый элемент output: {str(output[0])[:200]}")
                    return output
                logger.warning("Статус succeeded, но нет output")
                logger.warning(f"Полный ответ API: {data}")
                raise RuntimeError("Gemini 3 Pro не вернул результат")
            
            elif status == "failed":
                error = data.get("error", "Неизвестная ошибка")
                logger.error(f"Генерация завершилась с ошибкой: {status} - {error}")
                raise RuntimeError(f"Ошибка генерации: {error}")
            
            elif status == "canceled":
                logger.warning("Генерация была отменена")
                raise RuntimeError("Генерация была отменена")
            
            elif status in {"starting", "processing"}:
                continue
            
            else:
                logger.warning(f"Неизвестный статус: {status}")
                continue
        
        # Таймаут
        logger.warning(f"Таймаут ожидания результата для prediction_id: {prediction_id}")
        raise Gemini3ProTimeoutError(prediction_id)

