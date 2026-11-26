from aiogram import Router, types
from aiogram.filters import Command
import logging

logger = logging.getLogger(__name__)
router = Router()

@router.message(Command("create_group"))
async def create_group_command(message: types.Message):
    """Create a new group/campaign"""
    await message.answer("🗺️ Создание группы (в разработке)")
