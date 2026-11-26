from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import User, Group, Character, CharacterAttribute
from src.services import RulesService
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
    db: Session = SessionLocal()
    try:
        # Get character for this user
        character = db.query(Character).filter(
            Character.user_id == query.from_user.id,
            Character.group_id == 0
        ).first()

        if not character:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧙 Создать персонажа", callback_data="create_character")]
            ])
            await query.message.edit_text(
                "📋 У вас нет персонажа\n\n"
                "Создайте своего персонажа, чтобы начать играть",
                reply_markup=keyboard
            )
            await query.answer()
            return

        # Get character attributes
        attributes = db.query(CharacterAttribute).filter(
            CharacterAttribute.character_id == character.id
        ).first()

        profile_text = f"""
<b>⚔️ {character.name}</b>

<b>📋 Основная информация:</b>
Раса: {character.race}
Класс: {character.class_}
Уровень: {character.level}
Опыт: {character.experience}

<b>❤️ Здоровье:</b>
{character.current_hp}/{character.max_hp} HP

<b>📊 Характеристики:</b>
<b>STR</b> {attributes.strength} ({(attributes.strength-10)//2:+d})
<b>DEX</b> {attributes.dexterity} ({(attributes.dexterity-10)//2:+d})
<b>CON</b> {attributes.constitution} ({(attributes.constitution-10)//2:+d})
<b>INT</b> {attributes.intelligence} ({(attributes.intelligence-10)//2:+d})
<b>WIS</b> {attributes.wisdom} ({(attributes.wisdom-10)//2:+d})
<b>CHA</b> {attributes.charisma} ({(attributes.charisma-10)//2:+d})
"""

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📦 Инвентарь", callback_data="show_inventory"),
                InlineKeyboardButton(text="🗡️ Способности", callback_data="show_abilities")
            ]
        ])

        await query.message.edit_text(profile_text, reply_markup=keyboard)
        await query.answer()

    except Exception as e:
        logger.error(f"Error in show_profile_callback: {e}")
        await query.answer(f"❌ Ошибка: {str(e)}")
    finally:
        db.close()


@router.callback_query(F.data == "create_character")
async def create_character_callback(query: types.CallbackQuery, state: FSMContext):
    """Start character creation"""
    from src.handlers.character import CharacterCreationStates
    db = SessionLocal()
    try:
        # Get available races
        races = RulesService.get_all_races()

        # Create keyboard with race options
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=race, callback_data=f"race_{race}")]
            for race in races
        ])

        await query.message.edit_text(
            "🧙 <b>Создание персонажа</b>\n\n"
            "Шаг 1: <b>Выбери расу</b>",
            reply_markup=keyboard
        )
        await state.set_state(CharacterCreationStates.choosing_race)
        await query.answer()
    finally:
        db.close()


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
