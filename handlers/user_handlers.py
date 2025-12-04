"""Обработчики команд и генерации каруселей"""
import asyncio
from typing import Dict, List, Optional
import httpx
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from loguru import logger

from ..config import settings
from ..services.gemini_service import GeminiService
from ..services.image_gen_service import ImageGenService
from ..utils.prompts import (
    GEMINI_SYSTEM_PROMPT,
    get_image_prompt_slide1,
    get_image_prompt_slides_2_7,
    get_image_prompt_slide8,
)

router = Router()

# Очередь задач (user_id -> task)
tasks_queue: Dict[int, asyncio.Task] = {}

# URL фоновых изображений (загружаются при старте)
background_image1_url: Optional[str] = None
background_image2_url: Optional[str] = None


def set_background_urls(url1: str, url2: str):
    """Устанавливает URL фоновых изображений"""
    global background_image1_url, background_image2_url
    background_image1_url = url1
    background_image2_url = url2


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    await message.answer(
        "👋 Привет! Я бот для генерации Instagram-каруселей.\n\n"
        "📝 Просто отправь мне тему, и я создам карусель из 8 слайдов:\n"
        "• Слайд 1: Обложка с заголовком\n"
        "• Слайды 2-7: Раскрытие темы\n"
        "• Слайд 8: Вывод и призыв к действию\n\n"
        "💡 Пример: \"Почему тревожные люди чаще всего перфекционисты\"\n\n"
        "Используй /help для справки."
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    await message.answer(
        "📚 Справка по использованию бота:\n\n"
        "1️⃣ Отправь тему для карусели текстовым сообщением\n"
        "2️⃣ Бот сгенерирует структуру контента через Gemini-3-PRO\n"
        "3️⃣ Затем создаст 8 слайдов с изображениями\n"
        "4️⃣ Каждый слайд будет отправлен по мере готовности\n\n"
        "⏱️ Время генерации: ~5-10 минут\n"
        "📊 Формат: 4:5 (Instagram карусель)\n\n"
        "Команды:\n"
        "/start - Начать работу\n"
        "/help - Эта справка\n"
        "/history - История запросов (скоро)"
    )


@router.message(Command("history"))
async def cmd_history(message: Message):
    """Обработчик команды /history"""
    await message.answer(
        "📜 История генераций\n\n"
        "Функция истории пока не реализована.\n"
        "В будущих версиях здесь будет отображаться история ваших запросов."
    )


@router.message(Command("upload_backgrounds"))
async def cmd_upload_backgrounds(message: Message):
    """Обработчик команды /upload_backgrounds - загрузка фоновых изображений"""
    try:
        # Проверяем наличие файлов
        if not settings.image1_path.exists():
            await message.answer(f"❌ Файл {settings.image1_path} не найден")
            return
        if not settings.image2_path.exists():
            await message.answer(f"❌ Файл {settings.image2_path} не найден")
            return
        
        chat_id = message.chat.id
        
        bot = message.bot
        await message.answer("📤 Загружаю фоновые изображения...")
        
        # Загружаем image1.jpg
        with open(settings.image1_path, "rb") as f:
            from aiogram.types import BufferedInputFile
            photo1 = BufferedInputFile(f.read(), filename="image1.jpg")
            sent_photo1 = await bot.send_photo(
                chat_id=chat_id,
                photo=photo1,
            )
            file1 = await bot.get_file(sent_photo1.photo[-1].file_id)
            url1 = f"https://api.telegram.org/file/bot{settings.telegram_token}/{file1.file_path}"
            logger.info(f"URL для image1: {url1}")
        
        # Загружаем image2.jpg
        with open(settings.image2_path, "rb") as f:
            from aiogram.types import BufferedInputFile
            photo2 = BufferedInputFile(f.read(), filename="image2.jpg")
            sent_photo2 = await bot.send_photo(
                chat_id=chat_id,
                photo=photo2,
            )
            file2 = await bot.get_file(sent_photo2.photo[-1].file_id)
            url2 = f"https://api.telegram.org/file/bot{settings.telegram_token}/{file2.file_path}"
            logger.info(f"URL для image2: {url2}")
        
        # Устанавливаем URL через функцию
        set_background_urls(url1, url2)
        
        await message.answer(
            "✅ Фоновые изображения успешно загружены!\n\n"
            f"Image1 URL: {url1[:50]}...\n"
            f"Image2 URL: {url2[:50]}...\n\n"
            "Теперь можно использовать бота для генерации каруселей."
        )
        
    except Exception as e:
        logger.exception(f"Ошибка загрузки фоновых изображений: {e}")
        await message.answer(
            f"❌ Ошибка при загрузке фоновых изображений:\n{str(e)}"
        )


@router.message(F.text)
async def handle_topic(message: Message):
    """Обработчик текстовых сообщений (тема для генерации)"""
    user_id = message.from_user.id
    topic = message.text.strip()
    
    # Проверяем, не занят ли пользователь другой задачей
    if user_id in tasks_queue:
        task = tasks_queue[user_id]
        if not task.done():
            await message.answer(
                "⏳ У вас уже есть активная задача генерации.\n"
                "Пожалуйста, дождитесь её завершения."
            )
            return
    
    # Проверяем наличие фоновых изображений
    global background_image1_url, background_image2_url
    # Проверяем, что background_image2_url установлен и не пустой (он используется для большинства слайдов)
    # background_image1_url может быть пустым (используется только для слайда 1)
    if not background_image2_url or not background_image2_url.strip():
        await message.answer(
            "❌ Ошибка: фоновое изображение image2 не загружено.\n"
            "Обратитесь к администратору."
        )
        logger.error(f"Фоновое изображение image2 не загружено. URL: {background_image2_url}")
        return
    
    # Логируем статус фоновых изображений
    logger.info(f"background_image1_url: {background_image1_url[:60] if background_image1_url else 'None'}...")
    logger.info(f"background_image2_url: {background_image2_url[:60] if background_image2_url else 'None'}...")
    
    # Создаем задачу генерации
    bot = message.bot
    task = asyncio.create_task(
        generate_carousel(message, topic, bot)
    )
    tasks_queue[user_id] = task
    
    try:
        await task
    except Exception as e:
        logger.exception(f"Ошибка в задаче генерации для пользователя {user_id}: {e}")
        await message.answer(
            f"❌ Произошла ошибка при генерации карусели.\n"
            f"Попробуйте позже или обратитесь к администратору."
        )
    finally:
        # Удаляем задачу из очереди
        if user_id in tasks_queue:
            del tasks_queue[user_id]


async def generate_carousel(message: Message, topic: str, bot):
    """Основная функция генерации карусели"""
    user_id = message.from_user.id
    
    try:
        # Шаг 1: Генерация структуры контента
        await message.answer("🔄 Генерирую структуру карусели...")
        
        gemini_service = GeminiService()
        try:
            carousel_data = await gemini_service.generate_json(
                topic=topic,
                system_prompt=GEMINI_SYSTEM_PROMPT,
                max_retries=settings.gemini_max_retries,
            )
        except Exception as e:
            logger.exception(f"Ошибка генерации JSON: {e}")
            await message.answer(
                "❌ Не удалось сгенерировать структуру контента.\n"
                "Попробуйте изменить тему или повторить запрос позже."
            )
            return
        finally:
            await gemini_service.close()
        
        # Валидация структуры
        if "slides" not in carousel_data or len(carousel_data["slides"]) != 8:
            await message.answer(
                "❌ Ошибка: получена некорректная структура карусели.\n"
                "Попробуйте повторить запрос."
            )
            return
        
        slides = carousel_data["slides"]
        
        # Шаг 2: Генерация изображений
        image_service = ImageGenService()
        failed_slides: List[int] = []
        
        try:
            # Генерируем слайды последовательно
            for slide_data in slides:
                slide_number = slide_data.get("slide_number", 0)
                slide_type = slide_data.get("type", "")
                
                try:
                    # Формируем промпт в зависимости от типа слайда
                    if slide_number == 1:
                        # Слайд 1 (обложка)
                        prompt = get_image_prompt_slide1(
                            title=slide_data.get("title", ""),
                            subtitle=slide_data.get("subtitle", ""),
                            visual_idea=slide_data.get("visual_idea", ""),
                        )
                        # Проверяем, что URL валидный перед добавлением
                        # Если background_image1_url не установлен или пустой, используем background_image2_url
                        if background_image1_url and background_image1_url.strip() and (background_image1_url.startswith("http://") or background_image1_url.startswith("https://")):
                            image_input = [background_image1_url]
                            logger.info(f"Слайд 1: используем background_image1_url")
                        elif background_image2_url and background_image2_url.strip() and (background_image2_url.startswith("http://") or background_image2_url.startswith("https://")):
                            image_input = [background_image2_url]
                            logger.info(f"Слайд 1: background_image1_url не установлен, используем background_image2_url")
                        else:
                            image_input = None
                            logger.warning(f"Слайд 1: ни один фоновый URL не валиден, используем text-to-image")
                    elif slide_number == 8:
                        # Слайд 8 (финальный с CTA)
                        prompt = get_image_prompt_slide8(
                            title=slide_data.get("title", ""),
                            content=slide_data.get("content", []),
                            call_to_action=slide_data.get("call_to_action", ""),
                            background_style=slide_data.get("background_style", ""),
                            decoration=slide_data.get("decoration", ""),
                        )
                        # Проверяем, что URL валидный перед добавлением
                        image_input = [background_image2_url] if background_image2_url and background_image2_url.strip() else None
                    else:
                        # Слайды 2-7
                        prompt = get_image_prompt_slides_2_7(
                            title=slide_data.get("title", ""),
                            content=slide_data.get("content", []),
                            background_style=slide_data.get("background_style", ""),
                            decoration=slide_data.get("decoration", ""),
                        )
                        # Проверяем, что URL валидный перед добавлением
                        image_input = [background_image2_url] if background_image2_url and background_image2_url.strip() else None
                    
                    # Генерируем изображение
                    task_id = await image_service.generate_image(
                        prompt=prompt,
                        image_input=image_input,
                        aspect_ratio="4:5",
                        resolution="2K",
                        output_format="png",
                    )
                    
                    # Ждем результат
                    image_urls = await image_service.wait_for_result(task_id)
                    
                    if not image_urls:
                        logger.warning(f"Нет URL для слайда {slide_number}")
                        failed_slides.append(slide_number)
                        continue
                    
                    image_url = image_urls[0]
                    
                    # Скачиваем и отправляем изображение
                    await send_image_to_telegram(
                        bot=bot,
                        chat_id=message.chat.id,
                        image_url=image_url,
                        slide_number=slide_number,
                    )
                    
                    logger.info(f"Слайд {slide_number} успешно отправлен")
                    
                except Exception as e:
                    logger.exception(f"Ошибка генерации слайда {slide_number}: {e}")
                    failed_slides.append(slide_number)
                    continue
            
            # Повторная попытка для проблемных слайдов
            if failed_slides:
                await message.answer(
                    f"⚠️ Некоторые слайды не удалось сгенерировать с первого раза.\n"
                    f"Повторяю попытку для слайдов: {', '.join(map(str, failed_slides))}"
                )
                
                retry_failed: List[int] = []
                
                for slide_number in failed_slides:
                    slide_data = slides[slide_number - 1]  # slide_number начинается с 1
                    
                    for retry_attempt in range(settings.image_gen_max_retries):
                        try:
                            # Формируем промпт (аналогично первому проходу)
                            if slide_number == 1:
                                prompt = get_image_prompt_slide1(
                                    title=slide_data.get("title", ""),
                                    subtitle=slide_data.get("subtitle", ""),
                                    visual_idea=slide_data.get("visual_idea", ""),
                                )
                                # Проверяем, что URL валидный перед добавлением
                                # Если background_image1_url не установлен или пустой, используем background_image2_url
                                if background_image1_url and background_image1_url.strip() and (background_image1_url.startswith("http://") or background_image1_url.startswith("https://")):
                                    image_input = [background_image1_url]
                                elif background_image2_url and background_image2_url.strip() and (background_image2_url.startswith("http://") or background_image2_url.startswith("https://")):
                                    image_input = [background_image2_url]
                                else:
                                    image_input = None
                            elif slide_number == 8:
                                prompt = get_image_prompt_slide8(
                                    title=slide_data.get("title", ""),
                                    content=slide_data.get("content", []),
                                    call_to_action=slide_data.get("call_to_action", ""),
                                    background_style=slide_data.get("background_style", ""),
                                    decoration=slide_data.get("decoration", ""),
                                )
                                # Проверяем, что URL валидный перед добавлением
                                image_input = [background_image2_url] if background_image2_url and background_image2_url.strip() else None
                            else:
                                prompt = get_image_prompt_slides_2_7(
                                    title=slide_data.get("title", ""),
                                    content=slide_data.get("content", []),
                                    background_style=slide_data.get("background_style", ""),
                                    decoration=slide_data.get("decoration", ""),
                                )
                                # Проверяем, что URL валидный перед добавлением
                                image_input = [background_image2_url] if background_image2_url and background_image2_url.strip() else None
                            
                            task_id = await image_service.generate_image(
                                prompt=prompt,
                                image_input=image_input,
                                aspect_ratio="4:5",
                                resolution="2K",
                                output_format="png",
                            )
                            
                            image_urls = await image_service.wait_for_result(task_id)
                            
                            if image_urls:
                                await send_image_to_telegram(
                                    bot=bot,
                                    chat_id=message.chat.id,
                                    image_url=image_urls[0],
                                    slide_number=slide_number,
                                )
                                logger.info(f"Слайд {slide_number} успешно сгенерирован после повтора")
                                break
                            else:
                                if retry_attempt == settings.image_gen_max_retries - 1:
                                    retry_failed.append(slide_number)
                        except Exception as e:
                            logger.exception(f"Ошибка при повторе слайда {slide_number}, попытка {retry_attempt + 1}: {e}")
                            if retry_attempt == settings.image_gen_max_retries - 1:
                                retry_failed.append(slide_number)
                
                # Сообщаем о неудачных слайдах
                if retry_failed:
                    await message.answer(
                        f"❌ Не удалось сгенерировать слайды: {', '.join(map(str, retry_failed))}\n"
                        f"Остальные слайды успешно созданы."
                    )
            
            await message.answer("✅ Карусель успешно создана!")
            
        finally:
            await image_service.close()
            
    except Exception as e:
        logger.exception(f"Критическая ошибка при генерации карусели: {e}")
        await message.answer(
            "❌ Произошла критическая ошибка при генерации карусели.\n"
            "Попробуйте позже или обратитесь к администратору."
        )


async def send_image_to_telegram(bot, chat_id: int, image_url: str, slide_number: int):
    """Скачивает изображение по URL и отправляет в Telegram"""
    try:
        # Скачиваем изображение
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url, timeout=30.0)
            response.raise_for_status()
            image_data = response.content
        
        # Отправляем в Telegram
        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(image_data, filename=f"slide_{slide_number}.png")
        
        await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=f"Слайд {slide_number}/8",
        )
        
        logger.info(f"Изображение слайда {slide_number} отправлено в Telegram")
        
    except Exception as e:
        logger.exception(f"Ошибка отправки изображения слайда {slide_number}: {e}")
        raise

