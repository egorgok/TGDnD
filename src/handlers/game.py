from aiogram import Router, types
from aiogram.filters import Command
import logging

logger = logging.getLogger(__name__)
router = Router()

@router.message(Command("action"))
async def action_command(message: types.Message):
    """Take action in the game"""
    await message.answer("⚔️ Выполнение действия (в разработке)")
