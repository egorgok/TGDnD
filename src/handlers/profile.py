from aiogram import Router, types
from aiogram.filters import Command
import logging

logger = logging.getLogger(__name__)
router = Router()

@router.message(Command("profile"))
async def profile_command(message: types.Message):
    """Show character profile"""
    await message.answer("📋 Профиль персонажа (в разработке)")
