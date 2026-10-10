import asyncio
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

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

    async def ip_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is None:
            return
        import urllib.request

        try:
            ip = await asyncio.to_thread(
                lambda: urllib.request.urlopen("https://api.ipify.org", timeout=5).read(64).decode("ascii")
            )
            await message.reply_text(f"Global IP: {ip.strip()}")
        except Exception:
            await message.reply_text("Could not determine global IP.")

    async def status_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is None:
            return
        programs = manager.list_programs()
        lines = [f"{'RUNNING' if item['running'] else 'STOPPED'}  {item['name']}" for item in programs]
        await message.reply_text("\n".join(lines) if lines else "No programs configured.")

    async def control_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is None:
            return
        args = context.args
        if args is None or len(args) != 1:
            await message.reply_text("Usage: /start <program-id> or /stop <program-id>")
            return
        if message.text is None:
            return
        program_id = args[0]
        action = message.text.split(maxsplit=1)[0].lstrip("/").split("@", 1)[0]
        try:
            result = await asyncio.to_thread(getattr(manager, action), program_id)
            running = result.get("running", False)
            await message.reply_text(f"{program_id}: {'running' if running else 'stopped'}")
        except (KeyError, RuntimeError, ValueError) as error:
            await message.reply_text(str(error))

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("ip", ip_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("start", control_command))
    application.add_handler(CommandHandler("stop", control_command))
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