from aiogram import Router, types
from aiogram.filters import Command
import logging

logger = logging.getLogger(__name__)
router = Router()

@router.message(Command("create_character"))
async def create_character_command(message: types.Message):
    """Create a new character"""
    await message.answer("🧙 Создание персонажа (в разработке)")
