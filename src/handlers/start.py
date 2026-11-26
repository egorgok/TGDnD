from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import User, Group
import logging

logger = logging.getLogger(__name__)
router = Router()

@router.message(Command("start"))
async def start_command(message: types.Message):
    """Handle /start command"""
    db: Session = SessionLocal()
    try:
        # Create or update user
        user = db.query(User).filter(User.id == message.from_user.id).first()
        if not user:
            user = User(
                id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name
            )
            db.add(user)
            db.commit()
            logger.info(f"New user created: {message.from_user.id}")

        # Create keyboard with main commands
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧙 Создать персонажа", callback_data="create_character")],
            [InlineKeyboardButton(text="📋 Мой профиль", callback_data="show_profile")],
            [InlineKeyboardButton(text="🗺️ Присоединиться к группе", callback_data="join_group")],
            [InlineKeyboardButton(text="⚔️ Действие", callback_data="take_action")],
        ])

        welcome_text = """
🎲 <b>Добро пожаловать в D&D RPG Bot!</b>

Это телеграм-бот для ролевых игр в стиле Dungeons & Dragons.

<b>Как начать:</b>
1. Создайте своего персонажа (раса, класс, имя)
2. Присоединитесь к группе или создайте свою
3. Начните приключение!

<b>Основные команды:</b>
/start - Главное меню
/profile - Ваш профиль
/create_character - Создать нового персонажа
/action - Выполнить действие

Нажимайте кнопки ниже для начала!
"""

        await message.answer(welcome_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error in start_command: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
    finally:
        db.close()


@router.callback_query(F.data == "show_profile")
async def show_profile_callback(query: types.CallbackQuery):
    """Show user profile"""
    await query.answer()
    await query.message.answer("📋 Ваш профиль (функционал в разработке)")


@router.callback_query(F.data == "create_character")
async def create_character_callback(query: types.CallbackQuery):
    """Start character creation"""
    await query.answer()
    await query.message.answer("🧙 Создание персонажа (функционал в разработке)")


@router.callback_query(F.data == "join_group")
async def join_group_callback(query: types.CallbackQuery):
    """Join group"""
    await query.answer()
    await query.message.answer("🗺️ Присоединение к группе (функционал в разработке)")


@router.callback_query(F.data == "take_action")
async def take_action_callback(query: types.CallbackQuery):
    """Take action in game"""
    await query.answer()
    await query.message.answer("⚔️ Выполнение действия (функционал в разработке)")
