from aiogram import Router, types, F, Dispatcher
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import Character, User
from src.services import CharacterService, RulesService
from src.utils import format_character_sheet, format_error, format_success
import logging

logger = logging.getLogger(__name__)
router = Router()


class CharacterCreationStates(StatesGroup):
    """FSM states for character creation"""
    choosing_race = State()
    choosing_class = State()
    choosing_name = State()
    distributing_attributes = State()
    confirming = State()


@router.message(Command("create_character"))
async def create_character_command(message: types.Message, state: FSMContext):
    """Start character creation process"""
    db: Session = SessionLocal()
    try:
        # Check if user already has character in this group
        # For now, just proceed with creation
        user = db.query(User).filter(User.id == message.from_user.id).first()
        if not user:
            user = User(
                id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name
            )
            db.add(user)
            db.commit()

        # Get available races
        races = RulesService.get_all_races()

        # Create keyboard with race options
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=race, callback_data=f"race_{race}")]
            for race in races
        ])

        await message.answer(
            "🧙 <b>Создание персонажа</b>\n\n"
            "Шаг 1: <b>Выбери расу</b>",
            reply_markup=keyboard
        )
        await state.set_state(CharacterCreationStates.choosing_race)

    except Exception as e:
        logger.error(f"Error in create_character_command: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.callback_query(CharacterCreationStates.choosing_race, F.data.startswith("race_"))
async def choose_race_callback(query: types.CallbackQuery, state: FSMContext):
    """Handle race selection"""
    race = query.data.replace("race_", "")

    # Validate race
    if not RulesService.validate_race(race):
        await query.answer("❌ Недействительная раса")
        return

    # Store race in context
    await state.update_data(race=race)

    # Get available classes
    classes = RulesService.get_all_classes()

    # Create keyboard with class options
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=cls, callback_data=f"class_{cls}")]
        for cls in classes
    ])

    await query.message.edit_text(
        f"🧙 <b>Создание персонажа</b>\n\n"
        f"<b>Раса:</b> {race}\n\n"
        f"Шаг 2: <b>Выбери класс</b>",
        reply_markup=keyboard
    )
    await query.answer()
    await state.set_state(CharacterCreationStates.choosing_class)


@router.callback_query(CharacterCreationStates.choosing_class, F.data.startswith("class_"))
async def choose_class_callback(query: types.CallbackQuery, state: FSMContext):
    """Handle class selection"""
    class_name = query.data.replace("class_", "")

    # Validate class
    if not RulesService.validate_class(class_name):
        await query.answer("❌ Недействительный класс")
        return

    # Store class in context
    data = await state.get_data()
    race = data.get("race")

    await state.update_data(class_=class_name)

    await query.message.edit_text(
        f"🧙 <b>Создание персонажа</b>\n\n"
        f"<b>Раса:</b> {race}\n"
        f"<b>Класс:</b> {class_name}\n\n"
        f"Шаг 3: <b>Введи имя персонажа</b>\n\n"
        f"(Просто напишите имя в чат)",
        reply_markup=None
    )
    await query.answer()
    await state.set_state(CharacterCreationStates.choosing_name)


@router.message(CharacterCreationStates.choosing_name)
async def choose_name_message(message: types.Message, state: FSMContext):
    """Handle character name input"""
    name = message.text.strip()

    if not name or len(name) < 2 or len(name) > 50:
        await message.answer("❌ Имя должно быть от 2 до 50 символов")
        return

    data = await state.get_data()
    race = data.get("race")
    class_name = data.get("class_")

    await state.update_data(name=name)

    # Generate base attributes (standard array: 15, 14, 13, 12, 10, 8)
    base_attributes = {
        "str": 15,
        "dex": 14,
        "con": 13,
        "int": 12,
        "wis": 10,
        "cha": 8
    }

    # Apply racial modifiers
    final_attributes = RulesService.apply_ability_modifiers(base_attributes, race)

    await state.update_data(attributes=final_attributes)

    # Show confirmation
    race_info = RulesService.get_race_info(race)
    class_info = RulesService.get_class_info(class_name)
    starting_hp = class_info["starting_hp"] + (final_attributes["con"] - 10) // 2

    confirmation_text = f"""
🧙 <b>Подтверждение персонажа</b>

<b>Имя:</b> {name}
<b>Раса:</b> {race}
<b>Класс:</b> {class_name}

<b>📊 Характеристики:</b>
STR: {final_attributes['str']} ({(final_attributes['str']-10)//2:+d})
DEX: {final_attributes['dex']} ({(final_attributes['dex']-10)//2:+d})
CON: {final_attributes['con']} ({(final_attributes['con']-10)//2:+d})
INT: {final_attributes['int']} ({(final_attributes['int']-10)//2:+d})
WIS: {final_attributes['wis']} ({(final_attributes['wis']-10)//2:+d})
CHA: {final_attributes['cha']} ({(final_attributes['cha']-10)//2:+d})

<b>❤️ HP:</b> {starting_hp}

Всё правильно?
"""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Создать", callback_data="confirm_create"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_create")
        ]
    ])

    await message.answer(confirmation_text, reply_markup=keyboard)
    await state.set_state(CharacterCreationStates.confirming)


@router.callback_query(CharacterCreationStates.confirming, F.data == "confirm_create")
async def confirm_create_callback(query: types.CallbackQuery, state: FSMContext):
    """Create character after confirmation"""
    db: Session = SessionLocal()
    try:
        data = await state.get_data()
        name = data.get("name")
        race = data.get("race")
        class_name = data.get("class_")
        attributes = data.get("attributes")

        class_info = RulesService.get_class_info(class_name)
        con_modifier = (attributes["con"] - 10) // 2
        starting_hp = class_info["starting_hp"] + con_modifier

        # Create character (using group_id = 0 for now, will be updated in group functionality)
        character = CharacterService.create_character(
            db=db,
            user_id=query.from_user.id,
            group_id=0,  # Default group
            name=name,
            race=race,
            class_=class_name,
            attributes=attributes,
            hp=starting_hp
        )

        if character:
            await query.message.edit_text(
                format_success(f"✅ Персонаж {name} создан успешно!\n\n")
                + f"Используй /profile для просмотра профиля"
            )
            await state.clear()
        else:
            await query.message.edit_text(format_error("Персонаж с этим именем уже существует"))

    except Exception as e:
        logger.error(f"Error creating character: {e}")
        await query.message.edit_text(format_error(f"Ошибка при создании: {str(e)}"))
    finally:
        db.close()

    await query.answer()


@router.callback_query(CharacterCreationStates.confirming, F.data == "cancel_create")
async def cancel_create_callback(query: types.CallbackQuery, state: FSMContext):
    """Cancel character creation"""
    await query.message.edit_text("❌ Создание персонажа отменено")
    await state.clear()
    await query.answer()
