import asyncio
import json
import re
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

def clean_post_text(text: str) -> str:
    """
    Строгая очистка текста поста от markdown символов и лишних элементов.
    Гарантирует, что текст готов для отправки в Telegram с HTML разметкой.
    """
    if not text:
        return ""
    
    # Убираем вводные фразы
    intro_phrases = [
        "Конечно, вот пост:",
        "Вот пост:",
        "Вот текст поста:",
        "Вот готовый пост:",
        "Готовый пост:",
        "Конечно, вот текст:",
        "Вот текст:",
        "Вот готовый текст:",
        "Готовый текст:",
    ]
    text = text.strip()
    for phrase in intro_phrases:
        if text.startswith(phrase):
            text = text[len(phrase):].strip()
    
    # Убираем кавычки в начале и конце
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()
    
    # Разделяем текст на части: HTML теги и обычный текст
    # Это нужно, чтобы не трогать символы внутри HTML тегов
    parts = []
    i = 0
    while i < len(text):
        if text[i] == '<':
            # Нашли начало HTML тега, ищем конец
            tag_end = text.find('>', i)
            if tag_end != -1:
                parts.append(('tag', text[i:tag_end+1]))
                i = tag_end + 1
            else:
                parts.append(('text', text[i]))
                i += 1
        else:
            # Обычный текст, собираем до следующего тега
            text_start = i
            while i < len(text) and text[i] != '<':
                i += 1
            parts.append(('text', text[text_start:i]))
    
    # Обрабатываем только части с типом 'text'
    cleaned_parts = []
    for part_type, part_text in parts:
        if part_type == 'tag':
            cleaned_parts.append(part_text)
        else:
            # Убираем markdown символы из обычного текста
            cleaned = part_text
            
            # Убираем двойные звездочки и подчеркивания (жирный текст markdown)
            cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)
            cleaned = re.sub(r'__([^_]+)__', r'\1', cleaned)
            
            # Убираем одинарные звездочки и подчеркивания (курсив markdown)
            # Только если они окружают текст (не одиночные символы)
            cleaned = re.sub(r'\*([^*\n]+?)\*', r'\1', cleaned)  # *текст* -> текст
            cleaned = re.sub(r'_([^_\n]+?)_', r'\1', cleaned)  # _текст_ -> текст
            
            # Убираем символы # для заголовков (только в начале строки)
            cleaned = re.sub(r'^#+\s+', '', cleaned, flags=re.MULTILINE)
            
            # Убираем символы для списков markdown (-, *, +) в начале строки
            cleaned = re.sub(r'^[\-\*\+]\s+', '', cleaned, flags=re.MULTILINE)
            
            # Убираем оставшиеся одиночные символы * и _ (только если они стоят отдельно)
            # Не трогаем символы внутри слов или чисел
            cleaned = re.sub(r'(?<!\w)\*+(?!\w)', '', cleaned)  # Убираем * только если не часть слова
            cleaned = re.sub(r'(?<!\w)_+(?!\w)', '', cleaned)  # Убираем _ только если не часть слова
            
            cleaned_parts.append(cleaned)
    
    # Собираем обратно
    text = ''.join(cleaned_parts)
    
    # Убираем лишние пробелы и переносы строк в начале/конце
    text = text.strip()
    
    # Убираем множественные пустые строки (оставляем максимум 2 подряд)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    logger.debug(f"Текст после очистки: {text[:200]}...")
    
    return text

from ..config import settings
from ..services.gemini_service import GeminiService
from ..services.image_gen_service import ImageGenService
from ..services.airtable_service import AirtableService
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
from ..utils.watermark import add_watermark

# Глобальные переменные
tasks_queue: Dict[int, asyncio.Task] = {}
background_image2_url: Optional[str] = None  # image2 остается постоянным
pending_requests: Dict[int, Dict[str, any]] = {}  # user_id -> {"topic": str, "image1_url": Optional[str], "slides_count": Optional[int]}
waiting_for_infographic: Dict[int, str] = {}  # user_id -> topic (темы, для которых ждем ответ о инфографике)
waiting_for_post: Dict[int, Dict[str, Any]] = {}  # user_id -> {"topic": str, "carousel_data": dict}
waiting_for_post_topic: Dict[int, bool] = {}  # user_id -> True (ожидаем тему для поста без карусели)
carousel_data_storage: Dict[int, dict] = {}  # user_id -> carousel_data (сохранение JSON карусели)
user_mode: Dict[int, str] = {}  # user_id -> "carousel" или "infographic" (режим работы пользователя)

# Контекст для регенерации слайдов
regeneration_context: Dict[int, Dict[str, Any]] = {}  # user_id -> контекст регенерации
waiting_for_regenerate_decision: Dict[int, bool] = {}  # user_id -> True (ждем ответ "да/нет" о регенерации слайда)
waiting_for_slide_number: Dict[int, bool] = {}  # user_id -> True (ждем номер слайда для регенерации)
waiting_for_edited_prompt: Dict[int, int] = {}  # user_id -> slide_number (ждем отредактированный промпт для слайда)
waiting_for_airtable_update: Dict[int, int] = {}  # user_id -> slide_number (ждем "+" после изменения промпта слайда в Airtable)
waiting_for_infographic_regenerate_decision: Dict[int, bool] = {}  # user_id -> True (ждем ответ "да/нет" о регенерации инфографики)
waiting_for_infographic_airtable_update: Dict[int, bool] = {}  # user_id -> True (ждем "+" после изменения промпта инфографики в Airtable)
waiting_for_edited_infographic_prompt: Dict[int, bool] = {}  # user_id -> True (ждем отредактированный промпт для standalone инфографики)
waiting_for_post_regenerate_decision: Dict[int, bool] = {}  # user_id -> True (ждем ответ "да/нет" о регенерации поста)
waiting_for_post_airtable_update: Dict[int, bool] = {}  # user_id -> True (ждем "+" после изменения текста поста в Airtable)

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

    # Проверяем, ожидаем ли мы решение о регенерации слайда
    if user_id in waiting_for_regenerate_decision:
        logger.info(f"[USER {user_id}] Обработка решения о регенерации слайда. Ответ: {text}")
        text_lower = text.lower().strip()
        
        if text_lower in ["да", "yes", "y", "ок", "хочу", "создай"]:
            # Пользователь хочет переделать слайд
            logger.info(f"[USER {user_id}] Пользователь хочет переделать слайд. Переход в состояние waiting_for_slide_number")
            waiting_for_regenerate_decision.pop(user_id)
            waiting_for_slide_number[user_id] = True
            
            slides_count = regeneration_context[user_id]["slides_count"]
            await update.message.reply_text(
                f"Какой слайд вы хотите переделать?\n\n"
                f"Напишите цифру от 1 до {slides_count}.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        elif text_lower in ["нет", "no", "n", "не хочу", "не надо"]:
            # Пользователь не хочет переделывать - спрашиваем про инфографику
            logger.info(f"[USER {user_id}] Пользователь не хочет переделывать слайд. Спрашиваем про инфографику")
            waiting_for_regenerate_decision.pop(user_id)
            topic = regeneration_context[user_id]["topic"]
            waiting_for_infographic[user_id] = topic
            
            await update.message.reply_text(
                "Хорошо! Если понадобится переделать слайд, просто напишите «да» после следующей генерации.\n\n"
                "📊 Хотите получить дополнительную инфографику по этой теме?\n\n"
                "Ответьте «да» или «нет».",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        else:
            # Непонятный ответ, уточняем
            logger.warning(f"[USER {user_id}] Непонятный ответ о регенерации слайда: {text}")
            await update.message.reply_text(
                "Пожалуйста, ответьте «да» или «нет».",
                reply_markup=ReplyKeyboardRemove()
            )
            return

    # Проверяем, ожидаем ли мы номер слайда для регенерации
    if user_id in waiting_for_slide_number:
        logger.info(f"[USER {user_id}] Получен номер слайда для регенерации: {text}")
        try:
            slide_num = int(text.strip())
            slides_count = regeneration_context[user_id]["slides_count"]
            
            if slide_num < 1 or slide_num > slides_count:
                logger.warning(f"[USER {user_id}] Неверный номер слайда: {slide_num} (должен быть от 1 до {slides_count})")
                await update.message.reply_text(
                    f"❌ Номер слайда должен быть от 1 до {slides_count}.\n\n"
                    f"Напишите цифру от 1 до {slides_count}.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            
            # Проверяем, что Record ID есть в контексте
            record_id = regeneration_context[user_id].get("airtable_record_id")
            if not record_id:
                logger.error(f"[USER {user_id}] Record ID не найден в контексте для слайда {slide_num}")
                await update.message.reply_text(
                    f"❌ Record ID не найден. Невозможно прочитать промпт из Airtable.",
                    reply_markup=ReplyKeyboardRemove()
                )
                waiting_for_slide_number.pop(user_id)
                return
            
            # Просим пользователя изменить промпт в Airtable
            logger.info(f"[USER {user_id}] Переход в состояние waiting_for_airtable_update для слайда {slide_num}. Record ID: {record_id}")
            waiting_for_slide_number.pop(user_id)
            waiting_for_airtable_update[user_id] = slide_num
            
            await update.message.reply_text(
                f"📝 Измените промпт для генерации слайда {slide_num} в таблице Airtable.\n\n"
                f"Когда сделаете это, напишите «+» в чат.",
                reply_markup=ReplyKeyboardRemove()
            )
            
        except ValueError:
            logger.warning(f"[USER {user_id}] Неверный формат номера слайда: {text}")
            await update.message.reply_text(
                "❌ Пожалуйста, напишите цифру (номер слайда).",
                reply_markup=ReplyKeyboardRemove()
            )
        return
    
    # Проверяем, ожидаем ли мы "+" после изменения промпта в Airtable
    if user_id in waiting_for_airtable_update:
        slide_num = waiting_for_airtable_update.get(user_id)
        logger.info(f"[USER {user_id}] Ожидание '+' для слайда {slide_num}. Получено: {text}")
        
        if text.strip() == "+":
            slide_num = waiting_for_airtable_update.pop(user_id)
            record_id = regeneration_context[user_id].get("airtable_record_id")
            
            logger.info(f"[USER {user_id}] Получен '+'. Начинаю чтение промпта для слайда {slide_num} из Airtable. Record ID: {record_id}")
            
            if not record_id:
                logger.error(f"[USER {user_id}] Record ID не найден в контексте")
                await update.message.reply_text(
                    "❌ Record ID не найден. Невозможно прочитать промпт из Airtable.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            
            # Читаем промпт из Airtable
            try:
                if settings.airtable_api_token and settings.airtable_base_id and settings.airtable_table_id:
                    logger.info(f"[USER {user_id}] Читаю промпт для слайда {slide_num} из Airtable...")
                    airtable = AirtableService()
                    prompt = airtable.get_slide_prompt(record_id, slide_num)
                    
                    if not prompt:
                        logger.warning(f"[USER {user_id}] Промпт для слайда {slide_num} не найден в Airtable")
                        await update.message.reply_text(
                            f"❌ Не удалось прочитать промпт для слайда {slide_num} из Airtable. "
                            f"Убедитесь, что промпт заполнен в таблице.",
                            reply_markup=ReplyKeyboardRemove()
                        )
                        return
                    
                    logger.info(f"[USER {user_id}] Промпт для слайда {slide_num} успешно прочитан из Airtable. Длина: {len(prompt)} символов")
                    # Регенерируем слайд с промптом из Airtable
                    await regenerate_slide_from_airtable(update, context, slide_num, prompt, record_id)
                else:
                    logger.error(f"[USER {user_id}] Airtable не настроен (отсутствуют настройки)")
                    await update.message.reply_text(
                        "❌ Airtable не настроен. Невозможно прочитать промпт.",
                        reply_markup=ReplyKeyboardRemove()
                    )
            except Exception as e:
                logger.error(f"[USER {user_id}] Ошибка чтения промпта из Airtable: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await update.message.reply_text(
                    f"❌ Ошибка при чтении промпта из Airtable: {e}",
                    reply_markup=ReplyKeyboardRemove()
                )
        else:
            logger.warning(f"[USER {user_id}] Получен неверный ответ вместо '+': {text}")
            await update.message.reply_text(
                "Пожалуйста, напишите «+» после изменения промпта в Airtable.",
                reply_markup=ReplyKeyboardRemove()
            )
        return

    # Проверяем, ожидаем ли мы отредактированный промпт
    if user_id in waiting_for_edited_prompt:
        slide_num = waiting_for_edited_prompt.pop(user_id)
        edited_prompt = text.strip()
        
        if not edited_prompt:
            await update.message.reply_text(
                "❌ Промпт не может быть пустым. Пожалуйста, отправьте отредактированный промпт.",
                reply_markup=ReplyKeyboardRemove()
            )
            waiting_for_edited_prompt[user_id] = slide_num
            return
        
        # Регенерируем слайд
        await regenerate_slide(update, context, slide_num, edited_prompt)
        return

    # Проверяем, ожидаем ли мы решение о регенерации инфографики
    if user_id in waiting_for_infographic_regenerate_decision:
        logger.info(f"[USER {user_id}] Обработка решения о регенерации инфографики. Ответ: {text}")
        text_lower = text.lower().strip()
        
        if text_lower in ["да", "yes", "y", "ок", "хочу", "создай"]:
            # Пользователь хочет переделать инфографику
            waiting_for_infographic_regenerate_decision.pop(user_id)
            
            record_id = regeneration_context.get(user_id, {}).get("airtable_record_id")
            
            if record_id:
                # Есть запись в Airtable - используем стандартный процесс
                logger.info(f"[USER {user_id}] Пользователь хочет переделать инфографику. Переход в состояние waiting_for_infographic_airtable_update. Record ID: {record_id}")
                waiting_for_infographic_airtable_update[user_id] = True
                await update.message.reply_text(
                    "📝 Измените промпт для генерации инфографики в таблице Airtable (столбец Prompt_infografic).\n\n"
                    "Когда сделаете это, напишите «+» в чат.",
                    reply_markup=ReplyKeyboardRemove()
                )
            else:
                # Нет записи в Airtable (standalone режим) - используем промпт из контекста
                logger.info(f"[USER {user_id}] Пользователь хочет переделать инфографику (standalone режим, без Airtable)")
                infographic_prompt = regeneration_context.get(user_id, {}).get("infographic_prompt")
                if not infographic_prompt:
                    logger.error(f"[USER {user_id}] Промпт инфографики не найден в контексте")
                    await update.message.reply_text(
                        "❌ Промпт инфографики не найден в контексте. Невозможно переделать инфографику.",
                        reply_markup=ReplyKeyboardRemove()
                    )
                    return
                
                # Отправляем промпт для редактирования
                # Если промпт длинный, разбиваем на части
                if len(infographic_prompt) > 4000:
                    # Отправляем по частям
                    chunks = [infographic_prompt[i:i+4000] for i in range(0, len(infographic_prompt), 4000)]
                    for i, chunk in enumerate(chunks):
                        await update.message.reply_text(
                            f"📝 Промпт для редактирования (часть {i+1} из {len(chunks)}):\n\n"
                            f"```\n{chunk}\n```",
                            reply_markup=ReplyKeyboardRemove(),
                            parse_mode="Markdown"
                        )
                    await update.message.reply_text(
                        "Скопируйте весь промпт выше, отредактируйте и отправьте новый.",
                        reply_markup=ReplyKeyboardRemove()
                    )
                else:
                    await update.message.reply_text(
                        f"📝 Отредактируйте промпт для генерации инфографики и отправьте его:\n\n"
                        f"```\n{infographic_prompt}\n```\n\n"
                        f"Скопируйте промпт выше, отредактируйте и отправьте новый.",
                        reply_markup=ReplyKeyboardRemove(),
                        parse_mode="Markdown"
                    )
                # Сохраняем состояние ожидания отредактированного промпта
                waiting_for_edited_infographic_prompt[user_id] = True
            return
        elif text_lower in ["нет", "no", "n", "не хочу", "не надо"]:
            # Пользователь не хочет переделывать инфографику - спрашиваем про пост
            logger.info(f"[USER {user_id}] Пользователь не хочет переделывать инфографику. Спрашиваем про пост")
            waiting_for_infographic_regenerate_decision.pop(user_id)
            topic = regeneration_context.get(user_id, {}).get("topic")
            if user_id in carousel_data_storage:
                waiting_for_post[user_id] = {
                    "topic": topic,
                    "carousel_data": carousel_data_storage[user_id]
                }
                await update.message.reply_text(
                    "Хорошо! Если понадобится переделать инфографику, просто напишите «да» после следующей генерации.\n\n"
                    "📝 Хотите получить пост для соцсетей на основе этой карусели?\n\n"
                    "Ответьте «да» или «нет».",
                    reply_markup=ReplyKeyboardRemove()
                )
            else:
                await update.message.reply_text(
                    "Хорошо! Если понадобится переделать инфографику, просто напишите «да» после следующей генерации.",
                    reply_markup=ReplyKeyboardRemove()
                )
            return
        else:
            logger.warning(f"[USER {user_id}] Непонятный ответ о регенерации инфографики: {text}")
            await update.message.reply_text(
                "Пожалуйста, ответьте «да» или «нет».",
                reply_markup=ReplyKeyboardRemove()
            )
            return
    
    # Проверяем, ожидаем ли мы отредактированный промпт для standalone инфографики
    if user_id in waiting_for_edited_infographic_prompt:
        logger.info(f"[USER {user_id}] Получен отредактированный промпт для standalone инфографики. Длина: {len(text)} символов")
        waiting_for_edited_infographic_prompt.pop(user_id)
        
        # Получаем параметры из контекста
        infographic_params = regeneration_context.get(user_id, {}).get("infographic_params")
        if not infographic_params:
            logger.error(f"[USER {user_id}] Параметры генерации инфографики не найдены в контексте")
            await update.message.reply_text(
                "❌ Параметры генерации не найдены. Невозможно переделать инфографику.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        # Регенерируем инфографику с отредактированным промптом
        try:
            image_gen = ImageGenService()
            await update.message.reply_text("⏳ Переделываю инфографику с новым промптом...", reply_markup=ReplyKeyboardRemove())
            
            task_id = await image_gen.generate_image(
                prompt=text,
                image_input=infographic_params.get("image_input"),
                aspect_ratio=infographic_params.get("aspect_ratio", "4:5"),
                resolution=infographic_params.get("resolution", "2K"),
                output_format=infographic_params.get("output_format", "png")
            )
            
            result_urls = await image_gen.wait_for_result(task_id)
            
            if result_urls and len(result_urls) > 0:
                image_url = result_urls[0]
                sent_successfully = await send_infographic_to_telegram(context, update.effective_chat.id, image_url)
                
                if sent_successfully:
                    # Обновляем промпт в контексте
                    regeneration_context[user_id]["infographic_prompt"] = text
                    
                    logger.info(f"[USER {user_id}] ✅ Инфографика успешно переделана с новым промптом")
                    await update.message.reply_text(
                        "✅ Инфографика переделана!",
                        reply_markup=ReplyKeyboardRemove()
                    )
                    
                    # Спрашиваем, хочет ли пользователь переделать еще раз
                    waiting_for_infographic_regenerate_decision[user_id] = True
                    await update.message.reply_text(
                        "🔄 Хотите переделать инфографику еще раз?\n\n"
                        "Ответьте «да» или «нет».",
                        reply_markup=ReplyKeyboardRemove()
                    )
                else:
                    logger.error(f"[USER {user_id}] ❌ Не удалось отправить инфографику")
                    await update.message.reply_text("❌ Не удалось отправить инфографику.")
            else:
                logger.error(f"[USER {user_id}] ❌ Не удалось сгенерировать изображение инфографики")
                await update.message.reply_text("❌ Не удалось переделать инфографику. Попробуйте позже.")
            
            await image_gen.close()
        except Exception as e:
            logger.exception(f"Ошибка регенерации standalone инфографики: {e}")
            await update.message.reply_text("❌ Ошибка при регенерации инфографики.")
        return
    
    # Проверяем, ожидаем ли мы "+" после изменения промпта инфографики в Airtable
    if user_id in waiting_for_infographic_airtable_update:
        logger.info(f"[USER {user_id}] Ожидание '+' для инфографики. Получено: {text}")
        
        if text.strip() == "+":
            waiting_for_infographic_airtable_update.pop(user_id)
            record_id = regeneration_context.get(user_id, {}).get("airtable_record_id")
            
            logger.info(f"[USER {user_id}] Получен '+'. Начинаю чтение промпта инфографики из Airtable. Record ID: {record_id}")
            
            if not record_id:
                logger.error(f"[USER {user_id}] Record ID не найден в контексте для инфографики")
                await update.message.reply_text(
                    "❌ Record ID не найден. Невозможно прочитать промпт из Airtable.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            
            # Читаем промпт из Airtable
            try:
                if settings.airtable_api_token and settings.airtable_base_id and settings.airtable_table_id:
                    logger.info(f"[USER {user_id}] Читаю промпт инфографики из Airtable...")
                    airtable = AirtableService()
                    record = airtable.get_record_by_id(record_id)
                    
                    if not record:
                        logger.error(f"[USER {user_id}] Не удалось прочитать запись {record_id} из Airtable")
                        await update.message.reply_text(
                            "❌ Не удалось прочитать запись из Airtable.",
                            reply_markup=ReplyKeyboardRemove()
                        )
                        return
                    
                    prompt = record.get("fields", {}).get("Prompt_infografic")
                    if not prompt:
                        logger.warning(f"[USER {user_id}] Промпт для инфографики не найден в записи {record_id}")
                        await update.message.reply_text(
                            "❌ Промпт для инфографики не найден в Airtable. Убедитесь, что промпт заполнен в таблице.",
                            reply_markup=ReplyKeyboardRemove()
                        )
                        return
                    
                    logger.info(f"[USER {user_id}] Промпт инфографики успешно прочитан из Airtable. Длина: {len(prompt)} символов")
                    # Регенерируем инфографику с промптом из Airtable
                    await regenerate_infographic_from_airtable(update, context, prompt, record_id)
                else:
                    logger.error(f"[USER {user_id}] Airtable не настроен (отсутствуют настройки)")
                    await update.message.reply_text(
                        "❌ Airtable не настроен. Невозможно прочитать промпт.",
                        reply_markup=ReplyKeyboardRemove()
                    )
            except Exception as e:
                logger.error(f"[USER {user_id}] Ошибка чтения промпта инфографики из Airtable: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await update.message.reply_text(
                    f"❌ Ошибка при чтении промпта из Airtable: {e}",
                    reply_markup=ReplyKeyboardRemove()
                )
        else:
            logger.warning(f"[USER {user_id}] Получен неверный ответ вместо '+' для инфографики: {text}")
            await update.message.reply_text(
                "Пожалуйста, напишите «+» после изменения промпта в Airtable.",
                reply_markup=ReplyKeyboardRemove()
            )
        return
    
    # Проверяем, ожидаем ли мы решение о регенерации поста
    if user_id in waiting_for_post_regenerate_decision:
        logger.info(f"[USER {user_id}] Обработка решения о регенерации поста. Ответ: {text}")
        text_lower = text.lower().strip()
        
        if text_lower in ["да", "yes", "y", "ок", "хочу", "создай"]:
            # Пользователь хочет переделать пост
            logger.info(f"[USER {user_id}] Пользователь хочет переделать пост. Переход в состояние waiting_for_post_airtable_update")
            waiting_for_post_regenerate_decision.pop(user_id)
            waiting_for_post_airtable_update[user_id] = True
            
            record_id = regeneration_context.get(user_id, {}).get("airtable_record_id")
            if not record_id:
                logger.error(f"[USER {user_id}] Record ID не найден в контексте для поста")
                await update.message.reply_text(
                    "❌ Record ID не найден. Невозможно прочитать текст из Airtable.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            
            logger.info(f"[USER {user_id}] Прошу изменить текст поста в Airtable. Record ID: {record_id}")
            await update.message.reply_text(
                "📝 Измените текст поста в таблице Airtable (столбец Post_text).\n\n"
                "Когда сделаете это, напишите «+» в чат.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        elif text_lower in ["нет", "no", "n", "не хочу", "не надо"]:
            # Пользователь не хочет переделывать пост
            logger.info(f"[USER {user_id}] Пользователь не хочет переделывать пост")
            waiting_for_post_regenerate_decision.pop(user_id)
            await update.message.reply_text(
                "Хорошо! Если понадобится переделать пост, просто напишите «да» после следующей генерации.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        else:
            logger.warning(f"[USER {user_id}] Непонятный ответ о регенерации поста: {text}")
            await update.message.reply_text(
                "Пожалуйста, ответьте «да» или «нет».",
                reply_markup=ReplyKeyboardRemove()
            )
            return
    
    # Проверяем, ожидаем ли мы "+" после изменения текста поста в Airtable
    if user_id in waiting_for_post_airtable_update:
        logger.info(f"[USER {user_id}] Ожидание '+' для поста. Получено: {text}")
        
        if text.strip() == "+":
            waiting_for_post_airtable_update.pop(user_id)
            record_id = regeneration_context.get(user_id, {}).get("airtable_record_id")
            
            logger.info(f"[USER {user_id}] Получен '+'. Начинаю чтение текста поста из Airtable. Record ID: {record_id}")
            
            if not record_id:
                logger.error(f"[USER {user_id}] Record ID не найден в контексте для поста")
                await update.message.reply_text(
                    "❌ Record ID не найден. Невозможно прочитать текст из Airtable.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            
            # Читаем текст поста из Airtable
            try:
                if settings.airtable_api_token and settings.airtable_base_id and settings.airtable_table_id:
                    logger.info(f"[USER {user_id}] Читаю текст поста из Airtable...")
                    airtable = AirtableService()
                    record = airtable.get_record_by_id(record_id)
                    
                    if not record:
                        logger.error(f"[USER {user_id}] Не удалось прочитать запись {record_id} из Airtable")
                        await update.message.reply_text(
                            "❌ Не удалось прочитать запись из Airtable.",
                            reply_markup=ReplyKeyboardRemove()
                        )
                        return
                    
                    post_text = record.get("fields", {}).get("Post_text")
                    if not post_text:
                        logger.warning(f"[USER {user_id}] Текст поста не найден в записи {record_id}")
                        await update.message.reply_text(
                            "❌ Текст поста не найден в Airtable. Убедитесь, что текст заполнен в таблице.",
                            reply_markup=ReplyKeyboardRemove()
                        )
                        return
                    
                    logger.info(f"[USER {user_id}] Текст поста успешно прочитан из Airtable. Длина: {len(post_text)} символов")
                    # Отправляем обновленный пост
                    chat_id = update.effective_chat.id
                    await context.bot.send_message(
                        chat_id,
                        post_text,
                        parse_mode='HTML',
                        reply_markup=ReplyKeyboardRemove()
                    )
                    await context.bot.send_message(
                        chat_id,
                        "✅ Пост обновлен из Airtable!",
                        reply_markup=ReplyKeyboardRemove()
                    )
                    logger.info(f"[USER {user_id}] Пост успешно отправлен пользователю")
                else:
                    logger.error(f"[USER {user_id}] Airtable не настроен (отсутствуют настройки)")
                    await update.message.reply_text(
                        "❌ Airtable не настроен. Невозможно прочитать текст.",
                        reply_markup=ReplyKeyboardRemove()
                    )
            except Exception as e:
                logger.error(f"[USER {user_id}] Ошибка чтения текста поста из Airtable: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await update.message.reply_text(
                    f"❌ Ошибка при чтении текста из Airtable: {e}",
                    reply_markup=ReplyKeyboardRemove()
                )
        else:
            logger.warning(f"[USER {user_id}] Получен неверный ответ вместо '+' для поста: {text}")
            await update.message.reply_text(
                "Пожалуйста, напишите «+» после изменения текста в Airtable.",
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
    user_id = update.effective_user.id
    gemini = GeminiService()
    image_gen = ImageGenService()

    # Очищаем старый контекст регенерации при новой генерации
    if user_id in regeneration_context:
        del regeneration_context[user_id]

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
    logger.info(f"[USER {user_id}] Получено слайдов из JSON: {len(slides)}")
    if not slides:
        logger.error(f"[USER {user_id}] ❌ Ошибка: нет слайдов в JSON")
        await context.bot.send_message(chat_id, "Ошибка структуры данных (нет слайдов).")
        return

    # Инициализируем контекст регенерации
    regeneration_context[user_id] = {
        "carousel_data": carousel_data,
        "slides_prompts": {},  # Промпты из JSON от Гемини (visual_idea, background_style, decoration)
        "slides_data": {},  # Полные данные слайда из JSON для формирования системного промпта
        "slides_params": {},
        "slides_images": {},  # URL изображений слайдов {номер_слайда: url}
        "image1_url": image1_url,
        "background_image2_url": background_image2_url,
        "slides_count": slides_count,
        "topic": topic,
        "airtable_record_id": None  # Record ID в Airtable (будет заполнен после создания записи)
    }

    # 2. Генерация изображений
    logger.info(f"[USER {user_id}] Начинаю генерацию {len(slides)} слайдов...")
    for slide in slides:
        slide_num = slide.get("slide_number")
        logger.info(f"[USER {user_id}] ========== Обработка слайда {slide_num} ==========")
        try:
            # Формируем промпт
            if slide_num == 1:
                title = slide.get("title", "")
                subtitle = slide.get("subtitle", "")
                visual_idea = slide.get("visual_idea", "")
                prompt = get_image_prompt_slide1(title, subtitle, visual_idea)
                
                # Сохраняем полный промпт для Nana Banana и данные из JSON для регенерации
                regeneration_context[user_id]["slides_prompts"][slide_num] = prompt
                regeneration_context[user_id]["slides_data"][slide_num] = {
                    "title": title,
                    "subtitle": subtitle,
                    "visual_idea": visual_idea,
                    "type": "cover"
                }
                
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
                prompt = get_image_prompt_slides_2_7(title, content, background_style)
                
                # Сохраняем полный промпт для Nana Banana и данные из JSON для регенерации
                regeneration_context[user_id]["slides_prompts"][slide_num] = prompt
                regeneration_context[user_id]["slides_data"][slide_num] = {
                    "title": title,
                    "content": content,
                    "background_style": background_style
                }
                
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
                prompt = get_image_prompt_slide8(title, content, call_to_action, background_style)
                
                # Сохраняем полный промпт для Nana Banana и данные из JSON для регенерации
                regeneration_context[user_id]["slides_prompts"][slide_num] = prompt
                regeneration_context[user_id]["slides_data"][slide_num] = {
                    "title": title,
                    "content": content,
                    "call_to_action": call_to_action,
                    "background_style": background_style,
                    "type": "final"
                }
                
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

            # Сохраняем параметры для возможной регенерации
            regeneration_context[user_id]["slides_params"][slide_num] = {
                "image_input": img_input,
                "aspect_ratio": "4:5",
                "resolution": "2K",
                "output_format": "png"
            }

            # Генерируем
            logger.info(f"[USER {user_id}] Генерация слайда {slide_num} для {chat_id}...")
            logger.info(f"[USER {user_id}] ===== ПРОМПТ ДЛЯ СЛАЙДА {slide_num} (полный) =====")
            logger.info(f"[USER {user_id}] {prompt}")
            logger.info(f"[USER {user_id}] ===== КОНЕЦ ПРОМПТА ДЛЯ СЛАЙДА {slide_num} =====")
            logger.debug(f"[USER {user_id}] image_input для слайда {slide_num}: {img_input}")
            
            # Попытки генерации
            image_url = None
            for attempt in range(settings.image_gen_max_retries):
                try:
                    logger.info(f"[USER {user_id}] Попытка {attempt+1}/{settings.image_gen_max_retries} генерации слайда {slide_num}...")
                    # Создаем задачу
                    task_id = await image_gen.generate_image(
                        prompt=prompt,
                        image_input=img_input
                    )
                    logger.info(f"[USER {user_id}] Слайд {slide_num}: создана задача {task_id}, ждем результат...")
                    
                    # Ждем завершения и получаем URL
                    result_urls = await image_gen.wait_for_result(task_id)
                    logger.info(f"[USER {user_id}] Слайд {slide_num}: получены результаты, количество URL: {len(result_urls) if result_urls else 0}")
                    
                    if result_urls and len(result_urls) > 0:
                        image_url = result_urls[0]  # Берем первое изображение
                        logger.info(f"[USER {user_id}] ✅ Слайд {slide_num}: URL получен: {image_url[:80]}...")
                        break
                    else:
                        logger.warning(f"[USER {user_id}] ⚠️ Слайд {slide_num}: result_urls пуст или не содержит URL")
                except Exception as e:
                    logger.error(f"[USER {user_id}] ❌ Попытка {attempt+1} для слайда {slide_num} не удалась: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    await asyncio.sleep(2)
            
            if image_url:
                # Сохраняем URL изображения в контекст для Airtable
                regeneration_context[user_id]["slides_images"][slide_num] = image_url
                logger.info(f"[USER {user_id}] URL изображения слайда {slide_num} сохранен в контекст")
                
                logger.info(f"[USER {user_id}] Слайд {slide_num}: отправляю в Telegram...")
                try:
                    await send_image_to_telegram(context, chat_id, image_url, slide_num, slides_count)
                    logger.info(f"[USER {user_id}] ✅ Слайд {slide_num}: успешно отправлен в Telegram")
                except Exception as e:
                    logger.error(f"[USER {user_id}] ❌ Слайд {slide_num}: ошибка при отправке в Telegram: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    await context.bot.send_message(chat_id, f"⚠️ Не удалось отправить слайд {slide_num}.")
            else:
                logger.error(f"[USER {user_id}] ❌ Слайд {slide_num}: image_url не получен после всех попыток")
                await context.bot.send_message(chat_id, f"⚠️ Не удалось сгенерировать слайд {slide_num}.")

        except Exception as e:
            logger.exception(f"[USER {user_id}] ❌ Критическая ошибка на слайде {slide_num}: {e}")
            await context.bot.send_message(chat_id, f"Ошибка обработки слайда {slide_num}.")
        
        logger.info(f"[USER {user_id}] ========== Слайд {slide_num} обработан ==========")

    logger.info(f"[USER {user_id}] ✅ Генерация всех слайдов завершена. Всего слайдов: {len(slides)}")
    await context.bot.send_message(chat_id, "✅ Генерация карусели завершена!", reply_markup=get_main_keyboard())
    
    # Сохраняем carousel_data для возможной генерации поста
    carousel_data_storage[user_id] = carousel_data
    
    # Создаем запись в Airtable
    logger.info(f"[USER {user_id}] Начинаю создание записи в Airtable для темы: {topic}, слайдов: {slides_count}")
    try:
        if settings.airtable_api_token and settings.airtable_base_id and settings.airtable_table_id:
            logger.info(f"[USER {user_id}] Airtable настроен. Создаю запись...")
            airtable = AirtableService()
            logger.info(f"[USER {user_id}] Количество промптов: {len(regeneration_context[user_id]['slides_prompts'])}, количество изображений: {len(regeneration_context[user_id]['slides_images'])}")
            record_id = airtable.create_carousel_record(
                topic=topic,
                slides_count=slides_count,
                image1_url=image1_url,
                slides_prompts=regeneration_context[user_id]["slides_prompts"],
                slides_images=regeneration_context[user_id]["slides_images"]
            )
            # Сохраняем Record ID в контекст для последующего использования
            regeneration_context[user_id]["airtable_record_id"] = record_id
            logger.info(f"[USER {user_id}] ✅ Запись успешно создана в Airtable с Record ID: {record_id}")
        else:
            logger.warning(f"[USER {user_id}] ⚠️ Airtable не настроен (отсутствуют настройки), пропускаем создание записи")
    except Exception as e:
        logger.error(f"[USER {user_id}] ❌ Ошибка создания записи в Airtable: {e}")
        import traceback
        logger.error(traceback.format_exc())
        # Не прерываем процесс, если Airtable недоступен
    
    # Спрашиваем пользователя о регенерации слайдов
    waiting_for_regenerate_decision[user_id] = True
    await context.bot.send_message(
        chat_id,
        "🔄 Хотите переделать какой-то слайд?\n\n"
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
                # Обновляем запись в Airtable
                user_id = update.effective_user.id
                record_id = regeneration_context.get(user_id, {}).get("airtable_record_id")
                logger.info(f"[USER {user_id}] Обновляю инфографику в Airtable. Record ID: {record_id}")
                if record_id and settings.airtable_api_token:
                    try:
                        airtable = AirtableService()
                        airtable.update_infographic_image(record_id, image_url, prompt=prompt)
                        logger.info(f"[USER {user_id}] ✅ Инфографика успешно обновлена в Airtable для записи {record_id}")
                    except Exception as e:
                        logger.error(f"[USER {user_id}] ❌ Ошибка обновления инфографики в Airtable: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                else:
                    logger.warning(f"[USER {user_id}] ⚠️ Record ID или Airtable настройки отсутствуют, пропускаю обновление инфографики")
                
                await context.bot.send_message(chat_id, "✅ Инфографика готова!", reply_markup=ReplyKeyboardRemove())
                
                # Спрашиваем, хочет ли пользователь переделать инфографику
                waiting_for_infographic_regenerate_decision[user_id] = True
                await context.bot.send_message(
                    chat_id,
                    "🔄 Хотите переделать инфографику?\n\n"
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
                user_id = update.effective_user.id
                
                # Сохраняем контекст для регенерации
                if user_id not in regeneration_context:
                    regeneration_context[user_id] = {}
                
                regeneration_context[user_id]["infographic_prompt"] = image_prompt
                regeneration_context[user_id]["infographic_params"] = {
                    "aspect_ratio": "4:5",
                    "resolution": "2K",
                    "output_format": "png",
                    "image_input": None
                }
                regeneration_context[user_id]["topic"] = topic
                logger.info(f"[USER {user_id}] Сохранен контекст для регенерации standalone инфографики")
                
                await context.bot.send_message(chat_id, "✅ Инфографика готова!", reply_markup=ReplyKeyboardRemove())
                
                # Спрашиваем, хочет ли пользователь переделать инфографику
                waiting_for_infographic_regenerate_decision[user_id] = True
                logger.info(f"[USER {user_id}] Переход в состояние waiting_for_infographic_regenerate_decision (standalone)")
                await context.bot.send_message(
                    chat_id,
                    "🔄 Хотите переделать инфографику?\n\n"
                    "Ответьте «да» или «нет».",
                    reply_markup=ReplyKeyboardRemove()
                )
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
        
        # Строгая очистка текста от markdown символов и лишних элементов
        post_text = clean_post_text(post_text)
        
        if not post_text or len(post_text.strip()) < 50:
            await context.bot.send_message(
                chat_id,
                "⚠️ После очистки текст поста оказался слишком коротким. Попробуйте позже.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
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
        
        # Строгая очистка текста от markdown символов и лишних элементов
        post_text = clean_post_text(post_text)
        
        if not post_text or len(post_text.strip()) < 50:
            await context.bot.send_message(
                chat_id,
                "⚠️ После очистки текст поста оказался слишком коротким. Попробуйте позже.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
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
        
        # Обновляем запись в Airtable
        user_id = update.effective_user.id
        record_id = regeneration_context.get(user_id, {}).get("airtable_record_id")
        logger.info(f"[USER {user_id}] Обновляю текст поста в Airtable. Record ID: {record_id}")
        if record_id and settings.airtable_api_token:
            try:
                airtable = AirtableService()
                airtable.update_post_text(record_id, post_text)
                logger.info(f"[USER {user_id}] ✅ Текст поста успешно обновлен в Airtable для записи {record_id}")
            except Exception as e:
                logger.error(f"[USER {user_id}] ❌ Ошибка обновления поста в Airtable: {e}")
                import traceback
                logger.error(traceback.format_exc())
        else:
            logger.warning(f"[USER {user_id}] ⚠️ Record ID или Airtable настройки отсутствуют, пропускаю обновление поста")
        
        await context.bot.send_message(chat_id, "✅ Пост готов!", reply_markup=ReplyKeyboardRemove())
        
        # Спрашиваем, хочет ли пользователь переделать пост
        waiting_for_post_regenerate_decision[user_id] = True
        await context.bot.send_message(
            chat_id,
            "🔄 Хотите переделать пост?\n\n"
            "Ответьте «да» или «нет».",
            reply_markup=ReplyKeyboardRemove()
        )
        
    except Exception as e:
        logger.exception(f"Ошибка генерации поста: {e}")
        await context.bot.send_message(
            chat_id,
            "❌ Ошибка при генерации поста. Попробуйте позже.",
            reply_markup=ReplyKeyboardRemove()
        )


async def send_prompt_for_editing(update: Update, context: ContextTypes.DEFAULT_TYPE, slide_num: int, prompt: str):
    """Отправляет промпт пользователю для редактирования, разбивая на части если нужно"""
    chat_id = update.effective_chat.id
    max_length = 4000  # Telegram ограничение на длину сообщения
    
    if len(prompt) <= max_length:
        # Промпт помещается в одно сообщение
        # Используем формат кода без parse_mode для безопасности
        message_text = f"📝 Текущий промпт для слайда {slide_num}:\n\n"
        message_text += f"```\n{prompt}\n```\n\n"
        message_text += "Скопируйте промпт выше, отредактируйте и отправьте новый:"
        
        await context.bot.send_message(
            chat_id,
            message_text,
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        # Промпт нужно разбить на части
        parts = []
        current_part = ""
        lines = prompt.split('\n')
        
        for line in lines:
            if len(current_part) + len(line) + 1 > max_length - 100:  # Оставляем запас
                if current_part:
                    parts.append(current_part)
                current_part = line + '\n'
            else:
                current_part += line + '\n'
        
        if current_part:
            parts.append(current_part)
        
        total_parts = len(parts)
        
        # Отправляем первую часть с инструкцией
        message_text = f"📝 Промпт для слайда {slide_num} (часть 1/{total_parts}):\n\n"
        message_text += f"```\n{parts[0]}```"
        
        await context.bot.send_message(
            chat_id,
            message_text,
            reply_markup=ReplyKeyboardRemove()
        )
        
        # Отправляем остальные части
        for i, part in enumerate(parts[1:], start=2):
            message_text = f"📝 Промпт для слайда {slide_num} (часть {i}/{total_parts}):\n\n"
            message_text += f"```\n{part}```"
            
            await context.bot.send_message(
                chat_id,
                message_text,
                reply_markup=ReplyKeyboardRemove()
            )
        
        # Отправляем инструкцию
        await context.bot.send_message(
            chat_id,
            f"Скопируйте все части промпта выше, объедините их, отредактируйте и отправьте новый промпт:",
            reply_markup=ReplyKeyboardRemove()
        )


async def regenerate_slide(update: Update, context: ContextTypes.DEFAULT_TYPE, slide_num: int, new_prompt: str):
    """Регенерирует слайд с новым промптом из JSON, используя сохраненные параметры"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if user_id not in regeneration_context:
        await context.bot.send_message(chat_id, "❌ Контекст регенерации не найден. Начните новую генерацию карусели.")
        return
    
    if slide_num not in regeneration_context[user_id]["slides_params"]:
        await context.bot.send_message(chat_id, f"❌ Параметры для слайда {slide_num} не найдены.")
        return
    
    if slide_num not in regeneration_context[user_id]["slides_data"]:
        await context.bot.send_message(chat_id, f"❌ Данные для слайда {slide_num} не найдены.")
        return
    
    # Получаем сохраненные параметры и данные слайда
    params = regeneration_context[user_id]["slides_params"][slide_num]
    slide_data = regeneration_context[user_id]["slides_data"][slide_num]
    slides_count = regeneration_context[user_id]["slides_count"]
    
    await context.bot.send_message(
        chat_id,
        f"🔄 Регенерирую слайд {slide_num} с новым промптом...",
        reply_markup=ReplyKeyboardRemove()
    )
    
    image_gen = ImageGenService()
    
    try:
        # Формируем системный промпт из отредактированного промпта из JSON
        if slide_num == 1:
            # Для первого слайда используем отредактированный промпт как visual_idea
            title = slide_data.get("title", "")
            subtitle = slide_data.get("subtitle", "")
            visual_idea = new_prompt.strip()
            system_prompt = get_image_prompt_slide1(title, subtitle, visual_idea)
            # Обновляем данные в контексте
            regeneration_context[user_id]["slides_data"][slide_num]["visual_idea"] = visual_idea
        elif 2 <= slide_num < slides_count:
            # Для промежуточных слайдов используем отредактированный промпт как background_style
            title = slide_data.get("title", "")
            content = slide_data.get("content", [])
            background_style = new_prompt.strip()
            system_prompt = get_image_prompt_slides_2_7(title, content, background_style)
            # Обновляем данные в контексте
            regeneration_context[user_id]["slides_data"][slide_num]["background_style"] = background_style
        elif slide_num == slides_count:
            # Для последнего слайда используем отредактированный промпт как background_style
            title = slide_data.get("title", "")
            content = slide_data.get("content", [])
            call_to_action = slide_data.get("call_to_action", "")
            background_style = new_prompt.strip()
            system_prompt = get_image_prompt_slide8(title, content, call_to_action, background_style)
            # Обновляем данные в контексте
            regeneration_context[user_id]["slides_data"][slide_num]["background_style"] = background_style
        else:
            await context.bot.send_message(chat_id, f"❌ Неверный номер слайда: {slide_num}.")
            return
        
        # Обновляем промпт из JSON в контексте
        regeneration_context[user_id]["slides_prompts"][slide_num] = new_prompt
        
        # Генерируем с новым системным промптом
        image_url = None
        for attempt in range(settings.image_gen_max_retries):
            try:
                task_id = await image_gen.generate_image(
                    prompt=system_prompt,
                    image_input=params["image_input"],
                    aspect_ratio=params["aspect_ratio"],
                    resolution=params["resolution"],
                    output_format=params["output_format"]
                )
                logger.info(f"Регенерация слайда {slide_num}: создана задача {task_id}")
                
                result_urls = await image_gen.wait_for_result(task_id)
                logger.info(f"Регенерация слайда {slide_num}: получены результаты")
                
                if result_urls and len(result_urls) > 0:
                    image_url = result_urls[0]
                    break
            except Exception as e:
                logger.error(f"Попытка {attempt+1} регенерации слайда {slide_num} не удалась: {e}")
                await asyncio.sleep(2)
        
        if image_url:
            # Обновляем изображение в Airtable
            record_id = regeneration_context[user_id].get("airtable_record_id")
            if record_id and settings.airtable_api_token:
                try:
                    airtable = AirtableService()
                    airtable.update_slide_image(record_id, slide_num, image_url)
                    logger.info(f"Изображение слайда {slide_num} обновлено в Airtable")
                except Exception as e:
                    logger.error(f"Ошибка обновления изображения в Airtable: {e}")
            
            # Обновляем URL изображения в контексте
            regeneration_context[user_id]["slides_images"][slide_num] = image_url
            
            # Отправляем новый слайд
            await send_image_to_telegram(context, chat_id, image_url, slide_num, slides_count)
            await context.bot.send_message(
                chat_id,
                f"✅ Слайд {slide_num} переделан!\n\n"
                f"🔄 Хотите переделать еще какой-то слайд?\n\n"
                f"Ответьте «да» или «нет».",
                reply_markup=ReplyKeyboardRemove()
            )
            # Возвращаем в состояние ожидания решения о регенерации
            waiting_for_regenerate_decision[user_id] = True
        else:
            await context.bot.send_message(chat_id, f"❌ Не удалось переделать слайд {slide_num}. Попробуйте позже.")
    
    except Exception as e:
        logger.exception(f"Ошибка регенерации слайда {slide_num}: {e}")
        await context.bot.send_message(chat_id, f"❌ Ошибка при регенерации слайда {slide_num}.")
    finally:
        await image_gen.close()


async def regenerate_slide_from_airtable(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE, 
    slide_num: int, 
    prompt: str,
    record_id: str
):
    """Регенерирует слайд с промптом из Airtable, используя сохраненные параметры"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    logger.info(f"[USER {user_id}] Начинаю регенерацию слайда {slide_num} из Airtable. Record ID: {record_id}")
    
    if user_id not in regeneration_context:
        logger.error(f"[USER {user_id}] Контекст регенерации не найден")
        await context.bot.send_message(chat_id, "❌ Контекст регенерации не найден. Начните новую генерацию карусели.")
        return
    
    if slide_num not in regeneration_context[user_id]["slides_params"]:
        logger.error(f"[USER {user_id}] Параметры для слайда {slide_num} не найдены")
        await context.bot.send_message(chat_id, f"❌ Параметры для слайда {slide_num} не найдены.")
        return
    
    # Получаем сохраненные параметры
    params = regeneration_context[user_id]["slides_params"][slide_num]
    slides_count = regeneration_context[user_id]["slides_count"]
    
    logger.info(f"[USER {user_id}] Параметры слайда {slide_num} получены. Использую промпт напрямую из Airtable...")
    
    await context.bot.send_message(
        chat_id,
        f"🔄 Регенерирую слайд {slide_num} с промптом из Airtable...",
        reply_markup=ReplyKeyboardRemove()
    )
    
    image_gen = ImageGenService()
    
    try:
        # Используем промпт из Airtable напрямую (это уже полный промпт для Nana Banana)
        # Обновляем промпт в контексте
        regeneration_context[user_id]["slides_prompts"][slide_num] = prompt
        
        logger.info(f"[USER {user_id}] Использую промпт из Airtable напрямую (длина: {len(prompt)} символов)")
        
        # Генерируем с промптом из Airtable
        image_url = None
        for attempt in range(settings.image_gen_max_retries):
            try:
                task_id = await image_gen.generate_image(
                    prompt=prompt,
                    image_input=params["image_input"],
                    aspect_ratio=params["aspect_ratio"],
                    resolution=params["resolution"],
                    output_format=params["output_format"]
                )
                logger.info(f"Регенерация слайда {slide_num} из Airtable: создана задача {task_id}")
                
                result_urls = await image_gen.wait_for_result(task_id)
                logger.info(f"Регенерация слайда {slide_num} из Airtable: получены результаты")
                
                if result_urls and len(result_urls) > 0:
                    image_url = result_urls[0]
                    break
            except Exception as e:
                logger.error(f"Попытка {attempt+1} регенерации слайда {slide_num} не удалась: {e}")
                await asyncio.sleep(2)
        
        if image_url:
            logger.info(f"[USER {user_id}] Изображение слайда {slide_num} успешно сгенерировано. URL: {image_url[:80]}...")
            # Обновляем изображение в Airtable
            try:
                logger.info(f"[USER {user_id}] Обновляю изображение слайда {slide_num} в Airtable...")
                airtable = AirtableService()
                airtable.update_slide_image(record_id, slide_num, image_url)
                logger.info(f"[USER {user_id}] ✅ Изображение слайда {slide_num} успешно обновлено в Airtable")
            except Exception as e:
                logger.error(f"[USER {user_id}] ❌ Ошибка обновления изображения слайда {slide_num} в Airtable: {e}")
                import traceback
                logger.error(traceback.format_exc())
            
            # Обновляем URL изображения в контексте
            regeneration_context[user_id]["slides_images"][slide_num] = image_url
            
            # Отправляем новый слайд
            logger.info(f"[USER {user_id}] Отправляю слайд {slide_num} пользователю...")
            await send_image_to_telegram(context, chat_id, image_url, slide_num, slides_count)
            logger.info(f"[USER {user_id}] ✅ Слайд {slide_num} успешно отправлен пользователю")
            await context.bot.send_message(
                chat_id,
                f"✅ Слайд {slide_num} переделан с промптом из Airtable!\n\n"
                f"🔄 Хотите переделать еще какой-то слайд?\n\n"
                f"Ответьте «да» или «нет».",
                reply_markup=ReplyKeyboardRemove()
            )
            # Возвращаем в состояние ожидания решения о регенерации
            waiting_for_regenerate_decision[user_id] = True
            logger.info(f"[USER {user_id}] Переход в состояние waiting_for_regenerate_decision")
        else:
            logger.error(f"[USER {user_id}] ❌ Не удалось сгенерировать изображение для слайда {slide_num}")
            await context.bot.send_message(chat_id, f"❌ Не удалось переделать слайд {slide_num}. Попробуйте позже.")
    
    except Exception as e:
        logger.exception(f"Ошибка регенерации слайда {slide_num} из Airtable: {e}")
        await context.bot.send_message(chat_id, f"❌ Ошибка при регенерации слайда {slide_num}.")
    finally:
        await image_gen.close()


async def regenerate_infographic_from_airtable(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    record_id: str
):
    """Регенерирует инфографику с промптом из Airtable"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    image_gen = ImageGenService()
    
    logger.info(f"[USER {user_id}] Начинаю регенерацию инфографики из Airtable. Record ID: {record_id}, длина промпта: {len(prompt)} символов")
    
    await context.bot.send_message(
        chat_id,
        "🔄 Регенерирую инфографику с промптом из Airtable...",
        reply_markup=ReplyKeyboardRemove()
    )
    
    try:
        # Генерируем инфографику с промптом из Airtable
        image_url = None
        for attempt in range(settings.image_gen_max_retries):
            try:
                task_id = await image_gen.generate_image(
                    prompt=prompt,
                    image_input=None,  # Инфографика без референсных изображений
                    aspect_ratio="4:5",
                    resolution="2K",
                    output_format="png"
                )
                logger.info(f"Регенерация инфографики из Airtable: создана задача {task_id}")
                
                result_urls = await image_gen.wait_for_result(task_id)
                logger.info(f"Регенерация инфографики из Airtable: получены результаты")
                
                if result_urls and len(result_urls) > 0:
                    image_url = result_urls[0]
                    break
            except Exception as e:
                logger.error(f"Попытка {attempt+1} регенерации инфографики не удалась: {e}")
                await asyncio.sleep(2)
        
        if image_url:
            logger.info(f"[USER {user_id}] Изображение инфографики успешно сгенерировано. URL: {image_url[:80]}...")
            # Обновляем изображение в Airtable
            try:
                logger.info(f"[USER {user_id}] Обновляю изображение инфографики в Airtable...")
                airtable = AirtableService()
                airtable.update_infographic_image(record_id, image_url, prompt=prompt)
                logger.info(f"[USER {user_id}] ✅ Изображение инфографики успешно обновлено в Airtable")
            except Exception as e:
                logger.error(f"[USER {user_id}] ❌ Ошибка обновления изображения инфографики в Airtable: {e}")
                import traceback
                logger.error(traceback.format_exc())
            
            # Отправляем инфографику
            logger.info(f"[USER {user_id}] Отправляю инфографику пользователю...")
            sent_successfully = await send_infographic_to_telegram(context, chat_id, image_url)
            if sent_successfully:
                logger.info(f"[USER {user_id}] ✅ Инфографика успешно отправлена пользователю")
                await context.bot.send_message(
                    chat_id,
                    "✅ Инфографика переделана с промптом из Airtable!",
                    reply_markup=ReplyKeyboardRemove()
                )
                
                # Спрашиваем, хочет ли пользователь переделать еще раз
                waiting_for_infographic_regenerate_decision[user_id] = True
                logger.info(f"[USER {user_id}] Переход в состояние waiting_for_infographic_regenerate_decision")
                await context.bot.send_message(
                    chat_id,
                    "🔄 Хотите переделать инфографику еще раз?\n\n"
                    "Ответьте «да» или «нет».",
                    reply_markup=ReplyKeyboardRemove()
                )
            else:
                logger.error(f"[USER {user_id}] ❌ Не удалось отправить инфографику")
                await context.bot.send_message(chat_id, "❌ Не удалось отправить инфографику.")
        else:
            logger.error(f"[USER {user_id}] ❌ Не удалось сгенерировать изображение инфографики")
            await context.bot.send_message(chat_id, "❌ Не удалось переделать инфографику. Попробуйте позже.")
    
    except Exception as e:
        logger.exception(f"Ошибка регенерации инфографики из Airtable: {e}")
        await context.bot.send_message(chat_id, "❌ Ошибка при регенерации инфографики.")
    finally:
        await image_gen.close()


async def send_infographic_to_telegram(context: ContextTypes.DEFAULT_TYPE, chat_id: int, image_url: str):
    """Скачивает и отправляет инфографику"""
    sent_successfully = False
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url, timeout=30.0)
            if response.status_code == 200:
                # Для инфографики не накладываем водяной знак
                image_with_watermark = response.content
                
                # Проверяем размер файла
                file_size = len(image_with_watermark)
                max_photo_size = 10 * 1024 * 1024  # 10MB для фото
                max_document_size = 50 * 1024 * 1024  # 50MB для документа
                
                if file_size <= max_photo_size:
                    # Если файл меньше 10MB, отправляем как фото
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=image_with_watermark,
                        caption="📊 Инфографика"
                    )
                    sent_successfully = True
                elif file_size <= max_document_size:
                    # Если файл больше 10MB, но меньше 50MB, отправляем как документ
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=image_with_watermark,
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


async def send_image_to_telegram(
    context: ContextTypes.DEFAULT_TYPE, 
    chat_id: int, 
    image_url: str, 
    slide_number: int,
    slides_count: int
):
    """
    Скачивает, накладывает водяной знак и отправляет изображение.
    
    Логика размещения логотипа:
    - Слайд 1: левый верхний угол (светлый логотип)
    - Слайды 2 до предпоследнего: левый нижний угол (обычный логотип)
    - Последний слайд: без логотипа
    """
    try:
        logger.info(f"send_image_to_telegram: начинаю скачивание слайда {slide_number}, URL: {image_url[:80]}...")
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url, timeout=30.0)
            logger.info(f"send_image_to_telegram: слайд {slide_number}, статус ответа: {response.status_code}, размер: {len(response.content)} bytes")
            
            if response.status_code == 200:
                # Определяем параметры водяного знака в зависимости от номера слайда
                if slide_number == 1:
                    # Первый слайд: левый верхний угол, светлый логотип
                    position = "top-left"
                    is_light = True
                elif slide_number < slides_count:
                    # Слайды 2 до предпоследнего: правый нижний угол, обычный логотип
                    position = "bottom-right"
                    is_light = False
                else:
                    # Последний слайд: без логотипа
                    position = None
                    is_light = False
                
                logger.info(f"send_image_to_telegram: слайд {slide_number}, позиция логотипа: {position}, светлый: {is_light}")
                
                # Накладываем водяной знак (логотип) если нужно
                if position is not None:
                    logger.info(f"send_image_to_telegram: слайд {slide_number}, накладываю водяной знак...")
                    image_with_watermark = await add_watermark(
                        response.content, 
                        position=position, 
                        is_light=is_light
                    )
                    logger.info(f"send_image_to_telegram: слайд {slide_number}, водяной знак наложен, размер: {len(image_with_watermark)} bytes")
                else:
                    image_with_watermark = response.content
                    logger.info(f"send_image_to_telegram: слайд {slide_number}, водяной знак не требуется")
                
                logger.info(f"send_image_to_telegram: слайд {slide_number}, отправляю в Telegram...")
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=image_with_watermark,
                    caption=f"Слайд {slide_number}"
                )
                logger.info(f"send_image_to_telegram: слайд {slide_number}, успешно отправлен")
            else:
                logger.error(f"Ошибка скачивания изображения для слайда {slide_number}: статус {response.status_code}")
                await context.bot.send_message(chat_id, f"Ошибка загрузки изображения для слайда {slide_number} (URL недоступен).")
    except Exception as e:
        logger.error(f"Ошибка отправки фото слайда {slide_number}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await context.bot.send_message(chat_id, f"Ошибка отправки файла слайда {slide_number}.")
