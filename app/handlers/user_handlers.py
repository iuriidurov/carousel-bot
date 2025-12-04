import asyncio
import json
from typing import Dict, List, Optional, Any
import httpx
from telegram import Update, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes
from loguru import logger

async def check_url_availability(url: str) -> bool:
    """Проверяет доступность URL изображения"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.head(url, follow_redirects=True)
            return response.status_code == 200
    except Exception as e:
        logger.warning(f"Ошибка проверки доступности URL {url[:50]}...: {e}")
        return False

from ..config import settings
from ..services.gemini_service import GeminiService
from ..services.image_gen_service import ImageGenService
from ..utils.prompts import (
    GEMINI_SYSTEM_PROMPT,
    GEMINI_INFographic_SYSTEM_PROMPT,
    POST_FROM_CAROUSEL_SYSTEM_PROMPT,
    POST_WITHOUT_CAROUSEL_SYSTEM_PROMPT,
    get_image_prompt_slide1,
    get_image_prompt_slides_2_7,
    get_image_prompt_slide8,
    get_infographic_prompt,
    get_infographic_image_prompt,
)
from ..utils.background_utils import save_background_urls

# Глобальные переменные
tasks_queue: Dict[int, asyncio.Task] = {}
background_image2_url: Optional[str] = None  # image2 остается постоянным
pending_requests: Dict[int, Dict[str, any]] = {}  # user_id -> {"topic": str, "image1_url": Optional[str], "slides_count": Optional[int]}
waiting_for_infographic: Dict[int, str] = {}  # user_id -> topic (темы, для которых ждем ответ о инфографике)
waiting_for_post: Dict[int, Dict[str, Any]] = {}  # user_id -> {"topic": str, "carousel_data": dict}
waiting_for_post_topic: Dict[int, bool] = {}  # user_id -> True (ожидаем тему для поста без карусели)
carousel_data_storage: Dict[int, dict] = {}  # user_id -> carousel_data (сохранение JSON карусели)
user_mode: Dict[int, str] = {}  # user_id -> "carousel" или "infographic" (режим работы пользователя)

# Список разрешенных пользователей
ALLOWED_USER_IDS = [649760082, 617934115]

def is_user_allowed(user_id: int) -> bool:
    """Проверяет, разрешен ли доступ пользователю"""
    return user_id in ALLOWED_USER_IDS

async def send_access_denied_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет сообщение об отказе в доступе"""
    await update.message.reply_text(
        "Добрый день. Это частный бот, который умеет генерировать карусели и инфографику для соцсетей. "
        "Если хотите воспользоваться его функциями, обратитесь сюда: @Iurii_Durov",
        reply_markup=ReplyKeyboardRemove()
    )

def set_background_urls(url1: str, url2: str):
    """Устанавливает URL фоновых изображений (теперь используется только для image2)"""
    global background_image2_url
    background_image2_url = url2  # image1 теперь запрашивается у пользователя каждый раз

def get_main_keyboard():
    """Создает главную клавиатуру с кнопками выбора режима"""
    keyboard = [
        [KeyboardButton("📊 Карусель"), KeyboardButton("📈 Инфографика")],
        [KeyboardButton("📝 Написать пост")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    
    if not is_user_allowed(user_id):
        await send_access_denied_message(update, context)
        return
    
    await update.message.reply_text(
        "👋 Привет! Я бот для создания Instagram-каруселей и инфографики.\n\n"
        "Выберите режим работы:",
        reply_markup=get_main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    user_id = update.effective_user.id
    
    # Проверка доступа
    if not is_user_allowed(user_id):
        await send_access_denied_message(update, context)
        return
    
    await update.message.reply_text(
        "📖 Как пользоваться ботом:\n\n"
        "1️⃣ Отправь текст с темой карусели.\n"
        "   Например: «Почему перфекционисты склонны к тревожности»\n\n"
        "2️⃣ Бот попросит прислать изображение для первого слайда.\n"
        "   📸 Отправь фотографию, которую хочешь использовать.\n\n"
        "3️⃣ Бот попросит указать количество слайдов.\n"
        "   🔢 Напиши число от 2 до 20 (например: 5, 8, 10)\n\n"
        "4️⃣ Бот сгенерирует структуру и тексты через Gemini.\n\n"
        "5️⃣ Затем бот создаст визуальные слайды.\n\n"
        "⏱ Процесс может занять 3-5 минут.\n\n"
        "💡 Слайды будут приходить по мере готовности.",
        reply_markup=ReplyKeyboardRemove()
    )

async def upload_backgrounds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для ручной загрузки image2 (image1 теперь запрашивается у пользователя каждый раз)"""
    user_id = update.effective_user.id
    
    if not is_user_allowed(user_id):
        await send_access_denied_message(update, context)
        return
    
    # Проверка доступа
    if not is_user_allowed(user_id):
        await send_access_denied_message(update, context)
        return
    
    global background_image2_url
    
    if not settings.image2_path.exists():
        await update.message.reply_text("Ошибка: Файл image2.jpg не найден на сервере.")
        return

    status_msg = await update.message.reply_text("Загружаю фоновое изображение image2...")
    
    try:
        # Загружаем только image2 (image1 теперь запрашивается у пользователя каждый раз)
        with open(settings.image2_path, "rb") as f:
            msg2 = await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f)
            file2 = await context.bot.get_file(msg2.photo[-1].file_id)
            url2 = file2.file_path
            if not url2.startswith("http"):
                 url2 = f"https://api.telegram.org/file/bot{settings.telegram_token}/{url2}"

        set_background_urls("", url2)  # Передаем пустую строку для url1
        
        # Сохраняем URL в файл (только url2)
        save_background_urls("", url2)
        
        await status_msg.edit_text(
            f"✅ Фоновое изображение image2 обновлено и сохранено!\nURL: {url2[:50]}...",
        )
        await update.message.reply_text(
            "Готово! Теперь можешь отправлять темы для генерации каруселей.\n\n"
            "📸 Для каждой генерации бот будет запрашивать изображение для первого слайда.",
            reply_markup=ReplyKeyboardRemove()
        )
        
    except Exception as e:
        logger.error(f"Ошибка загрузки фона: {e}")
        await status_msg.edit_text("Ошибка при загрузке изображения.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основной обработчик текстовых сообщений (тем и количества слайдов)"""
    user_id = update.effective_user.id
    text = update.message.text

    # Проверка доступа
    if not is_user_allowed(user_id):
        await send_access_denied_message(update, context)
        return

    # Обработка выбора режима работы через кнопки
    if text in ["📊 Карусель", "Карусель"]:
        user_mode[user_id] = "carousel"
        await update.message.reply_text(
            "📊 Выбран режим: Карусель\n\n"
            "📝 Отправьте тему, и я сгенерирую для вас карусель с текстом и визуалом.\n\n"
            "📸 После отправки темы бот попросит:\n"
            "   1. Прислать изображение для первого слайда\n"
            "   2. Указать количество слайдов (от 2 до 20)",
            reply_markup=get_main_keyboard()
        )
        return
    
    if text in ["📈 Инфографика", "Инфографика"]:
        user_mode[user_id] = "infographic"
        await update.message.reply_text(
            "📈 Выбран режим: Инфографика\n\n"
            "📝 Отправьте тему, и я сгенерирую для вас инфографику по этой теме.",
            reply_markup=get_main_keyboard()
        )
        return
    
    if text in ["📝 Написать пост", "Написать пост"]:
        waiting_for_post_topic[user_id] = True
        await update.message.reply_text(
            "📝 Режим: Написание поста\n\n"
            "📝 Отправьте тему поста, и я создам для вас готовый пост для соцсетей.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # Проверяем, ожидаем ли мы ответ о инфографике
    if user_id in waiting_for_infographic:
        topic = waiting_for_infographic.pop(user_id)
        text_lower = text.lower().strip()
        
        if text_lower in ["да", "yes", "y", "ок", "хочу", "создай"]:
            # Пользователь хочет инфографику
            await update.message.reply_text(
                "📊 Отлично! Генерирую инфографику...",
                reply_markup=get_main_keyboard()
            )
            
            # Запускаем генерацию инфографики
            task = asyncio.create_task(generate_infographic(update, context, topic))
            tasks_queue[user_id] = task
            
            try:
                await task
            except Exception as e:
                logger.exception(f"Ошибка в task генерации инфографики для пользователя {user_id}: {e}")
            finally:
                if user_id in tasks_queue:
                    del tasks_queue[user_id]
            return
        elif text_lower in ["нет", "no", "n", "не хочу", "не надо"]:
            # Пользователь не хочет инфографику - спрашиваем про пост
            if user_id in carousel_data_storage:
                waiting_for_post[user_id] = {
                    "topic": topic,
                    "carousel_data": carousel_data_storage[user_id]
                }
                await update.message.reply_text(
                    "Хорошо! Если понадобится инфографика, просто напишите тему снова.\n\n"
                    "📝 Хотите получить пост для соцсетей на основе этой карусели?\n\n"
                    "Ответьте «да» или «нет».",
                    reply_markup=ReplyKeyboardRemove()
                )
            else:
                await update.message.reply_text(
                    "Хорошо! Если понадобится инфографика, просто напишите тему снова.",
                    reply_markup=ReplyKeyboardRemove()
                )
            return
        else:
            # Непонятный ответ, уточняем
            await update.message.reply_text(
                "Пожалуйста, ответьте «да» или «нет».",
                reply_markup=get_main_keyboard()
            )
            # Возвращаем тему обратно в ожидание
            waiting_for_infographic[user_id] = topic
            return

    # Проверяем, ожидаем ли мы ответ о посте
    if user_id in waiting_for_post:
        data = waiting_for_post.pop(user_id)
        topic = data["topic"]
        carousel_data = data["carousel_data"]
        text_lower = text.lower().strip()
        
        if text_lower in ["да", "yes", "y", "ок", "хочу", "создай"]:
            # Пользователь хочет пост
            await update.message.reply_text(
                "📝 Отлично! Генерирую пост...",
                reply_markup=ReplyKeyboardRemove()
            )
            
            # Запускаем генерацию поста
            task = asyncio.create_task(generate_post(update, context, topic, carousel_data))
            tasks_queue[user_id] = task
            
            try:
                await task
            except Exception as e:
                logger.exception(f"Ошибка в task генерации поста для пользователя {user_id}: {e}")
            finally:
                if user_id in tasks_queue:
                    del tasks_queue[user_id]
                # Очищаем сохраненные данные
                if user_id in carousel_data_storage:
                    del carousel_data_storage[user_id]
            return
        elif text_lower in ["нет", "no", "n", "не хочу", "не надо"]:
            # Пользователь не хочет пост
            await update.message.reply_text(
                "Хорошо! Если понадобится пост, просто напишите тему снова.",
                reply_markup=ReplyKeyboardRemove()
            )
            # Очищаем сохраненные данные
            if user_id in carousel_data_storage:
                del carousel_data_storage[user_id]
            return
        else:
            # Непонятный ответ, уточняем
            await update.message.reply_text(
                "Пожалуйста, ответьте «да» или «нет».",
                reply_markup=ReplyKeyboardRemove()
            )
            # Возвращаем данные обратно в ожидание
            waiting_for_post[user_id] = data
            return

    # Проверяем, ожидаем ли мы тему для поста (без карусели)
    if user_id in waiting_for_post_topic:
        topic = text.strip()
        if not topic:
            await update.message.reply_text(
                "Пожалуйста, отправьте тему для поста.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        # Удаляем из ожидания
        del waiting_for_post_topic[user_id]
        
        # Запускаем генерацию поста
        task = asyncio.create_task(generate_post_standalone(update, context, topic))
        tasks_queue[user_id] = task
        
        try:
            await task
        except Exception as e:
            logger.exception(f"Ошибка в task генерации поста для пользователя {user_id}: {e}")
        finally:
            if user_id in tasks_queue:
                del tasks_queue[user_id]
        return

    # Проверяем, что image2 загружен (он постоянный) - только для режимов карусели и инфографики
    if not background_image2_url:
        logger.warning(f"Попытка использования бота без загруженного image2. URL2: {background_image2_url}")
        await update.message.reply_text(
            "⚠️ Бот не настроен: отсутствует фоновое изображение image2.\n\n"
            "Пожалуйста, выполните команду /upload_backgrounds для загрузки фона.\n"
            "Или попросите администратора настроить бота.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    if user_id in tasks_queue and not tasks_queue[user_id].done():
        await update.message.reply_text(
            "⏳ Вы уже запустили генерацию. Пожалуйста, дождитесь завершения.",
            reply_markup=get_main_keyboard()
        )
        return

    # Определяем режим работы пользователя
    mode = user_mode.get(user_id, "carousel")  # По умолчанию режим карусели
    
    # Обработка режима "Инфографика"
    if mode == "infographic":
        topic = text.strip()
        if not topic:
            await update.message.reply_text(
                "Пожалуйста, отправьте тему для инфографики.",
                reply_markup=get_main_keyboard()
            )
            return
        
        # Запускаем генерацию инфографики в отдельном режиме
        task = asyncio.create_task(generate_infographic_standalone(update, context, topic))
        tasks_queue[user_id] = task
        
        try:
            await task
        except Exception as e:
            logger.exception(f"Ошибка в task генерации инфографики для пользователя {user_id}: {e}")
        finally:
            if user_id in tasks_queue:
                del tasks_queue[user_id]
        return

    # Режим "Карусель" - продолжаем как раньше
    # Проверяем, ожидаем ли мы количество слайдов от этого пользователя
    if user_id in pending_requests and pending_requests[user_id].get("image1_url") and not pending_requests[user_id].get("slides_count"):
        # Пользователь уже отправил изображение, теперь ждем количество слайдов
        try:
            slides_count = int(text.strip())
            if slides_count < 2 or slides_count > 20:
                await update.message.reply_text(
                    "❌ Количество слайдов должно быть от 2 до 20.\n"
                    "Пожалуйста, укажите корректное число.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            
            # Сохраняем количество слайдов
            pending_requests[user_id]["slides_count"] = slides_count
            topic = pending_requests[user_id]["topic"]
            image1_url = pending_requests[user_id]["image1_url"]
            
            # Удаляем запрос из pending
            del pending_requests[user_id]
            
            await update.message.reply_text(
                f"✅ Принято! Количество слайдов: {slides_count}\n\n"
                "⏳ Отправляю запрос на генерацию...",
                reply_markup=ReplyKeyboardRemove()
            )
            
            # Запускаем генерацию
            task = asyncio.create_task(generate_carousel(update, context, topic, image1_url, slides_count))
            tasks_queue[user_id] = task
            
            try:
                await task
            except Exception as e:
                logger.exception(f"Ошибка в task для пользователя {user_id}: {e}")
            finally:
                if user_id in tasks_queue:
                    del tasks_queue[user_id]
                    
        except ValueError:
            await update.message.reply_text(
                "❌ Пожалуйста, укажите число (например: 5, 8, 10).",
                reply_markup=ReplyKeyboardRemove()
            )
        return

    # Если это не количество слайдов, значит это новая тема
    topic = text
    pending_requests[user_id] = {
        "topic": topic,
        "image1_url": None,
        "slides_count": None
    }
    await update.message.reply_text(
        f"✅ Принято! Тема: «{topic}»\n\n"
        f"📸 Пришлите изображение, которое будем использовать в первом слайде.",
        reply_markup=ReplyKeyboardRemove()
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик получения фотографий от пользователя"""
    user_id = update.effective_user.id
    
    if not is_user_allowed(user_id):
        await send_access_denied_message(update, context)
        return
    
    # Проверяем, есть ли ожидающая тема для этого пользователя
    if user_id not in pending_requests:
        await update.message.reply_text(
            "❌ Сначала отправьте тему карусели текстом.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    # Проверяем, что image2 загружен
    if not background_image2_url:
        await update.message.reply_text(
            "⚠️ Бот не настроен: отсутствует фоновое изображение image2.\n\n"
            "Пожалуйста, выполните команду /upload_backgrounds для загрузки фона.\n"
            "Или попросите администратора настроить бота.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    if user_id in tasks_queue and not tasks_queue[user_id].done():
        await update.message.reply_text(
            "⏳ Вы уже запустили генерацию. Пожалуйста, дождитесь завершения.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    # Получаем URL изображения
    try:
        photo = update.message.photo[-1]  # Берем самое большое изображение
        file = await context.bot.get_file(photo.file_id)
        image1_url = file.file_path
        if not image1_url.startswith("http"):
            image1_url = f"https://api.telegram.org/file/bot{settings.telegram_token}/{image1_url}"
        
        # Валидация URL
        if not image1_url or not image1_url.strip() or not (image1_url.startswith("http://") or image1_url.startswith("https://")):
            logger.error(f"Невалидный URL image1 от пользователя {user_id}: {image1_url}")
            await update.message.reply_text(
                "❌ Ошибка: не удалось получить валидный URL изображения. Попробуйте отправить изображение еще раз.",
                reply_markup=ReplyKeyboardRemove()
            )
            if user_id in pending_requests:
                del pending_requests[user_id]
            return
        
        logger.info(f"Получено изображение image1 от пользователя {user_id}: {image1_url[:50]}...")
        
        # Сохраняем image1_url и просим указать количество слайдов
        pending_requests[user_id]["image1_url"] = image1_url
        
        await update.message.reply_text(
            "✅ Изображение получено!\n\n"
            "🔢 Укажите, какое количество слайдов для карусели вы хотите получить.\n"
            "(Например: 5, 8, 10)",
            reply_markup=ReplyKeyboardRemove()
        )
                
    except Exception as e:
        logger.error(f"Ошибка получения изображения: {e}")
        await update.message.reply_text(
            "❌ Ошибка при обработке изображения. Попробуйте отправить изображение еще раз.",
            reply_markup=ReplyKeyboardRemove()
        )
        # Удаляем запрос, чтобы пользователь мог начать заново
        if user_id in pending_requests:
            del pending_requests[user_id]

async def generate_carousel(update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str, image1_url: str, slides_count: int):
    """Генерирует карусель с использованием переданного image1_url и количества слайдов"""
    chat_id = update.effective_chat.id
    gemini = GeminiService()
    image_gen = ImageGenService()

    # 1. Генерация JSON с указанным количеством слайдов
    try:
        logger.info(f"Начинаю генерацию JSON для темы: {topic}, слайдов: {slides_count}")
        carousel_data = await gemini.generate_json(topic, GEMINI_SYSTEM_PROMPT, slides_count)
        if not carousel_data:
             await context.bot.send_message(chat_id, "Произошел технический сбой (Gemini). Попробуйте позже.")
             return
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        logger.exception(f"Полный traceback ошибки Gemini:")
        await context.bot.send_message(chat_id, "Ошибка генерации текста. Попробуйте другую тему.")
        return

    await context.bot.send_message(chat_id, "Структура готова! Начинаю генерацию слайдов (это может занять время)...")
    
    slides = carousel_data.get("slides", [])
    if not slides:
        await context.bot.send_message(chat_id, "Ошибка структуры данных (нет слайдов).")
        return

    # 2. Генерация изображений
    for slide in slides:
        slide_num = slide.get("slide_number")
        try:
            # Формируем промпт
            if slide_num == 1:
                title = slide.get("title", "")
                subtitle = slide.get("subtitle", "")
                visual_idea = slide.get("visual_idea", "")
                prompt = get_image_prompt_slide1(title, subtitle, visual_idea)
                # Для первого слайда используем переданный image1_url
                # Проверяем, что URL валидный (не None, не пустая строка, и начинается с http:// или https://)
                if image1_url and image1_url.strip() and (image1_url.startswith("http://") or image1_url.startswith("https://")):
                    img_input = [image1_url]
                    logger.info(f"Слайд 1: используем image1_url от пользователя")
                else:
                    img_input = None
                    logger.warning(f"Слайд 1: image1_url невалиден: {image1_url}")
            elif 2 <= slide_num < slides_count:
                # Промежуточные слайды (2 до предпоследнего)
                title = slide.get("title", "")
                content = slide.get("content", [])
                background_style = slide.get("background_style", "")
                decoration = slide.get("decoration", "")
                prompt = get_image_prompt_slides_2_7(title, content, background_style, decoration)
                # Проверяем, что URL валидный (не None, не пустая строка, и начинается с http:// или https://)
                if background_image2_url and background_image2_url.strip() and (background_image2_url.startswith("http://") or background_image2_url.startswith("https://")):
                    # Проверяем доступность URL
                    is_available = await check_url_availability(background_image2_url)
                    if is_available:
                        img_input = [background_image2_url]
                        logger.info(f"Слайд {slide_num}: используем background_image2_url: {background_image2_url[:80]}...")
                    else:
                        img_input = None
                        logger.error(f"Слайд {slide_num}: background_image2_url недоступен (404 или ошибка): {background_image2_url[:80]}...")
                else:
                    img_input = None
                    logger.warning(f"Слайд {slide_num}: background_image2_url невалиден: {background_image2_url}")
            elif slide_num == slides_count:
                # Последний слайд (с CTA)
                title = slide.get("title", "")
                content = slide.get("content", [])
                call_to_action = slide.get("call_to_action", "")
                background_style = slide.get("background_style", "")
                decoration = slide.get("decoration", "")
                prompt = get_image_prompt_slide8(title, content, call_to_action, background_style, decoration)
                # Проверяем, что URL валидный (не None, не пустая строка, и начинается с http:// или https://)
                if background_image2_url and background_image2_url.strip() and (background_image2_url.startswith("http://") or background_image2_url.startswith("https://")):
                    # Проверяем доступность URL
                    is_available = await check_url_availability(background_image2_url)
                    if is_available:
                        img_input = [background_image2_url]
                        logger.info(f"Слайд {slide_num}: используем background_image2_url: {background_image2_url[:80]}...")
                    else:
                        img_input = None
                        logger.error(f"Слайд {slide_num}: background_image2_url недоступен (404 или ошибка): {background_image2_url[:80]}...")
                else:
                    img_input = None
                    logger.warning(f"Слайд {slide_num}: background_image2_url невалиден: {background_image2_url}")
            else:
                continue

            # Генерируем
            logger.info(f"Генерация слайда {slide_num} для {chat_id}...")
            
            # Попытки генерации
            image_url = None
            for attempt in range(settings.image_gen_max_retries):
                try:
                    # Создаем задачу
                    task_id = await image_gen.generate_image(
                        prompt=prompt,
                        image_input=img_input
                    )
                    
                    # Ждем завершения и получаем URL
                    result_urls = await image_gen.wait_for_result(task_id)
                    if result_urls and len(result_urls) > 0:
                        image_url = result_urls[0]  # Берем первое изображение
                        break
                except Exception as e:
                    logger.warning(f"Попытка {attempt+1} для слайда {slide_num} не удалась: {e}")
                    await asyncio.sleep(2)
            
            if image_url:
                await send_image_to_telegram(context, chat_id, image_url, slide_num)
            else:
                await context.bot.send_message(chat_id, f"⚠️ Не удалось сгенерировать слайд {slide_num}.")

        except Exception as e:
            logger.exception(f"Критическая ошибка на слайде {slide_num}: {e}")
            await context.bot.send_message(chat_id, f"Ошибка обработки слайда {slide_num}.")

    await context.bot.send_message(chat_id, "✅ Генерация карусели завершена!", reply_markup=get_main_keyboard())
    
    # Сохраняем carousel_data для возможной генерации поста
    user_id = update.effective_user.id
    carousel_data_storage[user_id] = carousel_data
    
    # Спрашиваем пользователя о инфографике
    waiting_for_infographic[user_id] = topic
    await context.bot.send_message(
        chat_id,
        "📊 Хотите получить дополнительную инфографику по этой теме?\n\n"
        "Ответьте «да» или «нет».",
        reply_markup=ReplyKeyboardRemove()
    )

async def generate_infographic(update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str):
    """Генерирует инфографику по теме (для режима карусели, без запроса в Gemini)"""
    chat_id = update.effective_chat.id
    image_gen = ImageGenService()
    
    try:
        # Формируем промпт для инфографики
        prompt = get_infographic_prompt(topic)
        
        logger.info(f"Генерация инфографики для темы: {topic}")
        
        # Создаем задачу генерации
        task_id = await image_gen.generate_image(
            prompt=prompt,
            image_input=None,  # Инфографика без референсных изображений
            aspect_ratio="4:5",
            resolution="2K",
            output_format="png"
        )
        
        # Ждем результат
        result_urls = await image_gen.wait_for_result(task_id)
        
        if result_urls and len(result_urls) > 0:
            image_url = result_urls[0]  # Берем первое изображение
            
            # Отправляем инфографику
            sent_successfully = await send_infographic_to_telegram(context, chat_id, image_url)
            if sent_successfully:
                await context.bot.send_message(chat_id, "✅ Инфографика готова!", reply_markup=ReplyKeyboardRemove())
                
                # Спрашиваем про пост
                user_id = update.effective_user.id
                if user_id in carousel_data_storage:
                    waiting_for_post[user_id] = {
                        "topic": topic,
                        "carousel_data": carousel_data_storage[user_id]
                    }
                    await context.bot.send_message(
                        chat_id,
                        "📝 Хотите получить пост для соцсетей на основе этой карусели?\n\n"
                        "Ответьте «да» или «нет».",
                        reply_markup=ReplyKeyboardRemove()
                    )
        else:
            await context.bot.send_message(chat_id, "⚠️ Не удалось сгенерировать инфографику.", reply_markup=ReplyKeyboardRemove())
            
    except Exception as e:
        logger.exception(f"Ошибка генерации инфографики: {e}")
        await context.bot.send_message(chat_id, "❌ Ошибка при генерации инфографики. Попробуйте позже.", reply_markup=ReplyKeyboardRemove())


async def generate_infographic_standalone(update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str):
    """Генерирует инфографику в отдельном режиме: запрос в Gemini -> JSON -> Nana Banana Pro"""
    chat_id = update.effective_chat.id
    gemini = GeminiService()
    image_gen = ImageGenService()
    
    try:
        # 1. Генерация JSON через Gemini
        await context.bot.send_message(chat_id, "⏳ Генерирую структуру инфографики через Gemini...")
        
        logger.info(f"Генерация JSON для инфографики, тема: {topic}")
        
        # Используем специальный промпт для инфографики
        prompt = f"{topic}\n\nСоздай структуру инфографики в формате JSON."
        
        infographic_data = await gemini.generate_json(
            topic=prompt,
            system_prompt=GEMINI_INFographic_SYSTEM_PROMPT,
            slides_count=1,  # Не используется для инфографики, но требуется параметр
            max_retries=3
        )
        
        if not infographic_data:
            await context.bot.send_message(chat_id, "Произошел технический сбой (Gemini). Попробуйте позже.", reply_markup=get_main_keyboard())
            return
        
        # Извлекаем данные из JSON
        captivity_heading = infographic_data.get("captivity_heading", topic)
        tips = infographic_data.get("tips", [])
        
        if not tips or len(tips) < 4:
            logger.warning(f"Недостаточно советов в JSON: {tips}")
            # Используем заглушку
            tips = tips if tips else ["Совет 1", "Совет 2", "Совет 3", "Совет 4"]
            if len(tips) < 4:
                tips.extend(["Совет 3", "Совет 4"][len(tips)-2:])
        
        logger.info(f"Получены данные: заголовок={captivity_heading}, советы={tips}")
        
        # 2. Формируем промпт для Nana Banana Pro
        image_prompt = get_infographic_image_prompt(captivity_heading, tips[:4])  # Берем первые 4 совета
        
        await context.bot.send_message(chat_id, "⏳ Генерирую инфографику...")
        
        # 3. Генерация изображения через Nana Banana Pro
        task_id = await image_gen.generate_image(
            prompt=image_prompt,
            image_input=None,  # Без референсных изображений
            aspect_ratio="4:5",
            resolution="2K",  # 2K для уменьшения размера файла
            output_format="png"
        )
        
        # 4. Ждем результат
        result_urls = await image_gen.wait_for_result(task_id)
        
        if result_urls and len(result_urls) > 0:
            image_url = result_urls[0]  # Берем первое изображение
            
            # Отправляем инфографику
            sent_successfully = await send_infographic_to_telegram(context, chat_id, image_url)
            if sent_successfully:
                await context.bot.send_message(chat_id, "✅ Инфографика готова!", reply_markup=get_main_keyboard())
        else:
            await context.bot.send_message(chat_id, "⚠️ Не удалось сгенерировать инфографику.", reply_markup=get_main_keyboard())
            
    except Exception as e:
        logger.exception(f"Ошибка генерации инфографики: {e}")
        await context.bot.send_message(chat_id, "❌ Ошибка при генерации инфографики. Попробуйте позже.", reply_markup=get_main_keyboard())


async def generate_post_standalone(update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str):
    """Генерирует пост для соцсетей без карусели (отдельный режим)"""
    chat_id = update.effective_chat.id
    gemini = GeminiService()
    
    try:
        # Формируем промпт с темой
        prompt = f"Тема поста: {topic}"
        
        logger.info(f"Генерация поста (без карусели) для темы: {topic}")
        await context.bot.send_message(chat_id, "⏳ Генерирую пост через Gemini...", reply_markup=ReplyKeyboardRemove())
        
        # Генерируем пост через Gemini
        post_text = await gemini.generate_text(
            prompt=prompt,
            system_instruction=POST_WITHOUT_CAROUSEL_SYSTEM_PROMPT,
            temperature=1.0,
            max_retries=3
        )
        
        if not post_text or len(post_text.strip()) < 50:
            await context.bot.send_message(
                chat_id,
                "⚠️ Не удалось сгенерировать пост. Попробуйте позже.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        # Очищаем текст от возможных markdown символов и лишних символов
        # Убираем markdown, если он есть
        post_text = post_text.replace("**", "").replace("__", "").replace("#", "")
        
        # Убираем кавычки в начале и конце, если они есть
        post_text = post_text.strip()
        if post_text.startswith('"') and post_text.endswith('"'):
            post_text = post_text[1:-1]
        if post_text.startswith("'") and post_text.endswith("'"):
            post_text = post_text[1:-1]
        
        # Убираем вводные фразы, если они есть
        intro_phrases = [
            "Конечно, вот пост:",
            "Вот пост:",
            "Вот текст поста:",
            "Вот готовый пост:",
            "Готовый пост:",
        ]
        for phrase in intro_phrases:
            if post_text.startswith(phrase):
                post_text = post_text[len(phrase):].strip()
        
        # Проверяем длину (Telegram ограничение - 4096 символов)
        if len(post_text) > 4096:
            logger.warning(f"Пост слишком длинный ({len(post_text)} символов), обрезаем до 4096")
            post_text = post_text[:4093] + "..."
        
        # Отправляем пост с HTML разметкой
        await context.bot.send_message(
            chat_id,
            post_text,
            parse_mode='HTML',
            reply_markup=ReplyKeyboardRemove()
        )
        await context.bot.send_message(chat_id, "✅ Пост готов!", reply_markup=get_main_keyboard())
        
    except Exception as e:
        logger.exception(f"Ошибка генерации поста: {e}")
        await context.bot.send_message(
            chat_id,
            "❌ Ошибка при генерации поста. Попробуйте позже.",
            reply_markup=get_main_keyboard()
        )


async def generate_post(update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str, carousel_data: dict):
    """Генерирует пост для соцсетей на основе темы и JSON карусели"""
    chat_id = update.effective_chat.id
    gemini = GeminiService()
    
    try:
        # Формируем промпт с темой и JSON
        json_str = json.dumps(carousel_data, ensure_ascii=False, indent=2)
        prompt = f"Тема поста: {topic}\n\nJSON со слайдами: {json_str}"
        
        logger.info(f"Генерация поста для темы: {topic}")
        await context.bot.send_message(chat_id, "⏳ Генерирую пост через Gemini...", reply_markup=ReplyKeyboardRemove())
        
        # Генерируем пост через Gemini
        post_text = await gemini.generate_text(
            prompt=prompt,
            system_instruction=POST_FROM_CAROUSEL_SYSTEM_PROMPT,
            temperature=1.0,
            max_retries=3
        )
        
        if not post_text or len(post_text.strip()) < 50:
            await context.bot.send_message(
                chat_id,
                "⚠️ Не удалось сгенерировать пост. Попробуйте позже.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        # Очищаем текст от возможных markdown символов и лишних символов
        # Убираем markdown, если он есть
        post_text = post_text.replace("**", "").replace("__", "").replace("#", "")
        
        # Убираем кавычки в начале и конце, если они есть
        post_text = post_text.strip()
        if post_text.startswith('"') and post_text.endswith('"'):
            post_text = post_text[1:-1]
        if post_text.startswith("'") and post_text.endswith("'"):
            post_text = post_text[1:-1]
        
        # Убираем вводные фразы, если они есть
        intro_phrases = [
            "Конечно, вот пост:",
            "Вот пост:",
            "Вот текст поста:",
            "Вот готовый пост:",
            "Готовый пост:",
        ]
        for phrase in intro_phrases:
            if post_text.startswith(phrase):
                post_text = post_text[len(phrase):].strip()
        
        # Проверяем длину (Telegram ограничение - 4096 символов)
        if len(post_text) > 4096:
            logger.warning(f"Пост слишком длинный ({len(post_text)} символов), обрезаем до 4096")
            post_text = post_text[:4093] + "..."
        
        # Отправляем пост с HTML разметкой
        await context.bot.send_message(
            chat_id,
            post_text,
            parse_mode='HTML',
            reply_markup=ReplyKeyboardRemove()
        )
        await context.bot.send_message(chat_id, "✅ Пост готов!", reply_markup=ReplyKeyboardRemove())
        
    except Exception as e:
        logger.exception(f"Ошибка генерации поста: {e}")
        await context.bot.send_message(
            chat_id,
            "❌ Ошибка при генерации поста. Попробуйте позже.",
            reply_markup=ReplyKeyboardRemove()
        )


async def send_infographic_to_telegram(context: ContextTypes.DEFAULT_TYPE, chat_id: int, image_url: str):
    """Скачивает и отправляет инфографику"""
    sent_successfully = False
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url, timeout=30.0)
            if response.status_code == 200:
                # Проверяем размер файла
                file_size = len(response.content)
                max_photo_size = 10 * 1024 * 1024  # 10MB для фото
                max_document_size = 50 * 1024 * 1024  # 50MB для документа
                
                if file_size <= max_photo_size:
                    # Если файл меньше 10MB, отправляем как фото
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=response.content,
                        caption="📊 Инфографика"
                    )
                    sent_successfully = True
                elif file_size <= max_document_size:
                    # Если файл больше 10MB, но меньше 50MB, отправляем как документ
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=response.content,
                        filename="infographic.png",
                        caption="📊 Инфографика"
                    )
                    sent_successfully = True
                else:
                    # Файл слишком большой
                    logger.error(f"Файл инфографики слишком большой: {file_size} bytes")
                    await context.bot.send_message(chat_id, "Файл инфографики слишком большой для отправки.")
            else:
                logger.error(f"Ошибка скачивания инфографики: {response.status_code}")
                await context.bot.send_message(chat_id, "Ошибка загрузки инфографики (URL недоступен).")
    except Exception as e:
        logger.exception(f"Ошибка отправки инфографики: {e}")
        # Отправляем сообщение об ошибке только если инфографика не была отправлена
        if not sent_successfully:
            await context.bot.send_message(chat_id, "Ошибка отправки инфографики.")
    
    return sent_successfully


async def send_image_to_telegram(context: ContextTypes.DEFAULT_TYPE, chat_id: int, image_url: str, slide_number: int):
    """Скачивает и отправляет изображение"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url, timeout=30.0)
            if response.status_code == 200:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=response.content,
                    caption=f"Слайд {slide_number}"
                )
            else:
                logger.error(f"Ошибка скачивания изображения: {response.status_code}")
                await context.bot.send_message(chat_id, f"Ошибка загрузки изображения для слайда {slide_number} (URL недоступен).")
    except Exception as e:
        logger.error(f"Ошибка отправки фото: {e}")
        await context.bot.send_message(chat_id, f"Ошибка отправки файла слайда {slide_number}.")
