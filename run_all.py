"""
Запускает Lilu bot и Suppliers bot одновременно в одном процессе.
Используется как точка входа на Railway вместо bot.py.
"""

import asyncio
import logging
import os
import signal
import sys

logger = logging.getLogger(__name__)


def run_migrations():
    try:
        from migrate_suppliers import run_migration
        run_migration()
    except Exception as e:
        logger.error(f"Migration error: {e}")


async def run_lilu():
    """Запуск Lilu бота через async API."""
    import importlib
    import bot as lilu_module

    from telegram.ext import Application

    app = (
        Application.builder()
        .token(lilu_module.BOT_TOKEN)
        .build()
    )

    # Регистрируем все хендлеры из bot.py
    from telegram.ext import (
        CommandHandler, MessageHandler, CallbackQueryHandler, filters
    )

    app.add_handler(CommandHandler("start",  lilu_module.start))
    app.add_handler(CommandHandler("clear",  lilu_module.clear_cmd))
    app.add_handler(CommandHandler("mode",   lilu_module.mode_cmd))
    app.add_handler(CommandHandler("mail",   lilu_module.mail_cmd))
    app.add_handler(CommandHandler("memory", lilu_module.memory_cmd))
    app.add_handler(CallbackQueryHandler(lilu_module.set_mode, pattern="^mode_"))
    app.add_handler(MessageHandler(filters.VOICE, lilu_module.handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, lilu_module.handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lilu_module.handle_text))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Lilu bot started")
    return app


async def run_suppliers():
    """Запуск Suppliers бота через async API."""
    import suppliers_bot as sb

    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        CallbackQueryHandler, filters
    )

    sb.init_db()
    run_migrations()

    app = Application.builder().token(sb.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", sb.cmd_start))
    app.add_handler(CallbackQueryHandler(sb.on_callback))
    app.add_handler(MessageHandler(filters.PHOTO, sb.on_photo))
    app.add_handler(MessageHandler(filters.VOICE, sb.on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, sb.on_text))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Suppliers bot started")
    return app


async def main():
    logging.basicConfig(level=logging.INFO)

    apps = []

    # Запускаем Lilu если токен задан
    lilu_token = os.getenv("BOT_TOKEN")
    suppliers_token = os.getenv("SUPPLIERS_BOT_TOKEN")

    if lilu_token:
        try:
            app = await run_lilu()
            apps.append(app)
        except Exception as e:
            logger.error(f"Lilu bot failed to start: {e}")

    if suppliers_token:
        try:
            app = await run_suppliers()
            apps.append(app)
        except Exception as e:
            logger.error(f"Suppliers bot failed to start: {e}")

    if not apps:
        logger.error("No bots started — check BOT_TOKEN and SUPPLIERS_BOT_TOKEN")
        sys.exit(1)

    logger.info(f"Running {len(apps)} bot(s). Press Ctrl+C to stop.")

    # Ждём сигнал завершения
    stop = asyncio.Event()

    def _signal_handler():
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass  # Windows

    await stop.wait()

    # Graceful shutdown
    for app in apps:
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception as e:
            logger.error(f"Shutdown error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
