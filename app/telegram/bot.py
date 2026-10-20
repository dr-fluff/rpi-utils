import asyncio
import logging
import os

from telegram import BotCommand, Update
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from app.commands import COMMANDS, CommandError, help_message, run_command
from app.custom_commands import CustomCommandStore
from app.programs import ProgramManager

logger = logging.getLogger(__name__)


def allowed_chat_ids() -> set[int]:
    return {
        int(value.strip())
        for value in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
        if value.strip().lstrip("-").isdigit()
    }


async def deliver_notifications(
    application: Application,
    notification_queue: asyncio.Queue,
    chat_ids: set[int],
) -> None:
    while True:
        message, result = await notification_queue.get()
        try:
            success = True

            for chat_id in sorted(chat_ids):
                try:
                    await application.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                    )
                except (TelegramError, OSError):
                    logger.exception(
                        "Could not deliver queued notification to chat %s",
                        chat_id,
                    )
                    success = False

            if not result.done():
                result.set_result(success)
        finally:
            notification_queue.task_done()


async def telegram_polling(
    manager: ProgramManager,
    custom_commands: CustomCommandStore,
    notification_queue: asyncio.Queue,
) -> None:
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
            result = await run_command(
                name,
                manager,
                context.args,
                custom_commands,
                source="telegram",
            )
        except CommandError as error:
            await message.reply_text(f"{error}\n\n{help_message(custom_commands)}")
            return
        await message.reply_text(result["message"])

    async def handle_unrecognized(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is not None:
            await message.reply_text(help_message(custom_commands))

    application = Application.builder().token(token).build()
    application.add_handler(MessageHandler(filters.COMMAND, handle_command))
    application.add_handler(MessageHandler(filters.ALL, handle_unrecognized))
    await application.initialize()
    await application.start()
    if application.updater is not None:
        await application.updater.start_polling()
    menu_task = asyncio.create_task(sync_command_menu(application, custom_commands))
    notification_task = asyncio.create_task(
    deliver_notifications(application, notification_queue, allowed)
)
    try:
        await asyncio.Event().wait()
    finally:
        menu_task.cancel()
        notification_task.cancel()
        await asyncio.gather(notification_task, return_exceptions=True)
        await asyncio.gather(menu_task, return_exceptions=True)
        if application.updater is not None:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()


async def sync_command_menu(
    application: Application,
    custom_commands: CustomCommandStore,
) -> None:
    while True:
        try:
            commands = [
                BotCommand(command=command["name"], description=command["description"][:256])
                for command in COMMANDS
            ]
            commands.extend(
                BotCommand(command=command["name"], description=command["description"][:256])
                for command in custom_commands.list()
                if command["telegram_enabled"] and not command["requires_confirmation"]
            )
            await application.bot.set_my_commands(commands)
        except (OSError, TelegramError, RuntimeError):
            logger.exception("Could not synchronize Telegram command menu")
        await asyncio.sleep(60)
