"""Главный файл для запуска бота"""
import asyncio
import sys
import logging
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from loguru import logger

from app.config import settings
from app.handlers.user_handlers import (
    start_command,
    help_command,
    upload_backgrounds_command,
    handle_message,
    handle_photo,
    set_background_urls,
    background_image2_url
)
from app.utils.background_utils import save_background_urls, load_background_urls

# Настройка логирования для python-telegram-bot (он использует logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def upload_backgrounds_on_startup(bot):
    """Загружает image2 при старте бота (если задан ADMIN_CHAT_ID)"""
    if not settings.admin_chat_id:
        return False
        
    try:
        chat_id = int(settings.admin_chat_id)
        logger.info(f"Автоматическая загрузка image2 в чат {chat_id}...")
        
        if not settings.image2_path.exists():
            logger.error("Файл image2.jpg не найден!")
            return False

        # Image 2 (image1 теперь запрашивается у пользователя каждый раз)
        with open(settings.image2_path, "rb") as f:
            msg2 = await bot.send_photo(chat_id=chat_id, photo=f)
            file2 = await bot.get_file(msg2.photo[-1].file_id)
            url2 = file2.file_path
            if not url2.startswith("http"):
                url2 = f"https://api.telegram.org/file/bot{settings.telegram_token}/{url2}"
        
        # Устанавливаем только image2 (image1 больше не нужен глобально)
        set_background_urls("", url2)  # Передаем пустую строку для url1, так как он больше не используется
        save_background_urls("", url2)  # Сохраняем только url2
        
        logger.info(f"✅ Фоновое изображение image2 успешно загружено и сохранено!")
        logger.info(f"URL 2: {url2[:60]}...")
        return True
        
    except Exception as e:
        logger.error(f"❌ Не удалось автоматически загрузить image2: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

async def post_init(application):
    """Действия после инициализации"""
    pass  # Оставляем пустым, загрузка фонов переехала в main()

async def main():
    """Главная функция"""
    # Настройка Loguru
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
        level="INFO"
    )
    
    logger.info("Инициализация бота...")
    
    try:
        application = ApplicationBuilder().token(settings.telegram_token).post_init(post_init).build()
        
        # Регистрация обработчиков
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("upload_backgrounds", upload_backgrounds_command))
        
        # Обработчик текстовых сообщений (исключая команды)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Обработчик фотографий
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        
        logger.info("Запуск polling...")
        
        # Инициализация приложения
        await application.initialize()
        await application.start()
        
        # Установка команд меню после инициализации
        commands = [
            BotCommand("start", "Начать работу с ботом"),
            BotCommand("help", "Справка по использованию"),
            BotCommand("upload_backgrounds", "Загрузить фоновые изображения (админ)"),
        ]
        await application.bot.set_my_commands(commands)
        logger.info("✅ Команды меню установлены.")
        
        # Загрузка фонового изображения image2 из файла или Telegram
        logger.info("Проверка фонового изображения image2...")
        saved_urls = load_background_urls()
        if saved_urls:
            url1, url2 = saved_urls
            # Используем только url2 (url1 больше не нужен, так как запрашивается у пользователя)
            if url2:
                set_background_urls("", url2)
                logger.info("✅ Используется сохранённый URL фонового изображения image2")
        elif settings.admin_chat_id:
            logger.info("Сохранённого URL нет, пытаемся загрузить через Telegram...")
            # Вызываем логику загрузки image2
            await upload_backgrounds_on_startup(application.bot)
        
        await application.updater.start_polling()
        
        # Финальная проверка статуса
        from app.handlers.user_handlers import background_image2_url
        if background_image2_url:
            logger.info("✅ Бот запущен и готов к работе! Фоновое изображение image2 загружено.")
        else:
            logger.warning("⚠️ Бот запущен, но фоновое изображение image2 НЕ загружено!")
            logger.warning("⚠️ Используйте /upload_backgrounds для загрузки фона.")
        
        logger.info("✅ Бот ждет сообщений...")
        
        # Держим бота запущенным
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("Остановка бота...")
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
        
    except Exception as e:
        logger.exception(f"Критическая ошибка при запуске: {e}")

if __name__ == "__main__":
    asyncio.run(main())
