import asyncio
import ipaddress
import os
import socket
import subprocess
import urllib.request

from fastapi import HTTPException
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.programs import ProgramManager

REBOOT_HELPER = "/usr/local/sbin/rpi-utils-reboot"

HELP_MESSAGE = (
    "I didn't understand that. Available commands:\n"
    "/help — show this help message\n"
    "/ip — show the device's global and local IP addresses\n"
    "/status — list running programs\n"
    "/start <program-id> — start a program\n"
    "/stop <program-id> — stop a program\n"
    "/restart — restart the Raspberry Pi\n"
    "/update — install the latest published release and system upgrades"
)


def get_local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.connect(("8.8.8.8", 80))
        address = connection.getsockname()[0]
    if not ipaddress.ip_address(address).is_private:
        raise OSError("The selected network address is not private")
    return address


def get_global_ip() -> str:
    with urllib.request.urlopen("https://api.ipify.org", timeout=5) as response:
        return response.read(64).decode("ascii").strip()


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

    async def help_message(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is not None:
            await message.reply_text(HELP_MESSAGE)

    async def ip_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is None:
            return
        results = await asyncio.gather(
            asyncio.to_thread(get_global_ip),
            asyncio.to_thread(get_local_ip),
            return_exceptions=True,
        )
        global_ip = str(results[0]) if isinstance(results[0], str) else "Unavailable"
        local_ip = str(results[1]) if isinstance(results[1], str) else "Unavailable"
        await message.reply_text(f"Global IP: {global_ip}\nLocal IP: {local_ip}")

    async def status_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is None:
            return
        programs = manager.list_programs()
        lines = [f"RUNNING  {item['name']}" for item in programs if item["running"]]
        await message.reply_text("\n".join(lines) if lines else "No programs are running.")

    async def restart_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is None:
            return
        await message.reply_text("Restarting the Raspberry Pi now.")
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["sudo", "-n", REBOOT_HELPER],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            await message.reply_text(f"Could not restart the Raspberry Pi: {error}")
            return
        if result.returncode:
            output = (result.stdout + result.stderr).strip()
            await message.reply_text(f"Could not restart the Raspberry Pi: {output or 'reboot command failed'}")

    async def update_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is None:
            return
        await message.reply_text("Starting the latest release and system package update.")
        try:
            from app.main import update as update_device

            result = await update_device()
        except HTTPException as error:
            await message.reply_text(f"Update failed: {error.detail}")
            return
        except (OSError, subprocess.SubprocessError) as error:
            await message.reply_text(f"Update failed: {error}")
            return
        await message.reply_text(result["message"])

    async def control_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await authorized(update):
            return
        message = update.effective_message
        if message is None:
            return
        args = context.args
        if args is None or len(args) != 1:
            await message.reply_text(HELP_MESSAGE)
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
            await message.reply_text(f"{error}\n\n{HELP_MESSAGE}")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("help", help_message))
    application.add_handler(CommandHandler("ip", ip_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("start", control_command))
    application.add_handler(CommandHandler("stop", control_command))
    application.add_handler(CommandHandler("restart", restart_command))
    application.add_handler(CommandHandler("update", update_command))
    application.add_handler(MessageHandler(filters.ALL, help_message))
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