import asyncio
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.commands import COMMANDS, CommandError, help_message, run_command
from app.programs import ProgramManager


def allowed_chat_ids() -> set[int]:
    return {
        int(value.strip())
        for value in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
        if value.strip().lstrip("-").isdigit()
    }


async def telegram_polling(manager: ProgramManager) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    allowed = allowed_chat_ids()
    if not token or not allowed:
        return

    async def authorized(update: Update) -> bool:
        return bool(update.effective_chat and update.effective_chat.id in allowed)

    async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is None or message.text is None:
            return
        name = message.text.split(maxsplit=1)[0].lstrip("/").split("@", 1)[0]
        try:
            result = await run_command(name, manager, context.args)
        except CommandError as error:
            await message.reply_text(f"{error}\n\n{help_message()}")
            return
        await message.reply_text(result["message"])

    async def handle_unrecognized(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is not None:
            await message.reply_text(help_message())

    application = Application.builder().token(token).build()
    for command in COMMANDS:
        application.add_handler(CommandHandler(command["name"], handle_command))
    application.add_handler(MessageHandler(filters.ALL, handle_unrecognized))
    await application.initialize()
    await application.start()
    if application.updater is not None:
        await application.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        if application.updater is not None:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()
