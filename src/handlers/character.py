from aiogram import Router, types, F, Dispatcher
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import Character, User, Group, GroupMember
from src.services import CharacterService, RulesService, GroupService
from src.services.open5e_rules_service import (
    grant_starting_class_data,
    open5e_class_is_available
)
from src.utils import format_error, format_success
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


def get_or_create_default_group(db: Session, user_id: int) -> Group:
    """Get or create default group for user"""
    group = db.query(Group).join(
        GroupMember
    ).filter(
        GroupMember.user_id == user_id,
        GroupMember.role == "gm"
    ).first()

    if group:
        if not group.world_description:
            group.world_description = (
                "Классическое средневековое фэнтези: королевства, деревни, замки, "
                "дороги, мечи, луки и магия. Сохраняй средневековый уровень "
                "технологий; не вводи современную технику или научную фантастику, "
                "если игрок явно не пересобрал сеттинг."
            )
            db.commit()
        return group

    # Create default group
    default_group_id = user_id
    group = Group(
        id=default_group_id,
        title=f"Персональная кампания",
        group_type="personal",
        gm_prompt="Одиночная кампания",
        world_description=(
            "Классическое средневековое фэнтези: королевства, деревни, замки, "
            "дороги, мечи, луки и магия. Сохраняй исторический средневековый "
            "уровень технологий; не вводи современную технику или научную "
            "фантастику, если игрок явно не пересобрал сеттинг."
        )
    )
    db.add(group)
    db.flush()

    member = GroupMember(
        group_id=default_group_id,
        user_id=user_id,
        role="gm"
    )
    db.add(member)
    db.commit()

    return group


@router.callback_query(F.data == "create_character")
async def create_character_callback(query: types.CallbackQuery, state: FSMContext):
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == query.from_user.id).first()
        if user and user.active_group_id:
            membership = db.query(GroupMember).filter(
                GroupMember.group_id == user.active_group_id,
                GroupMember.user_id == user.id
            ).first()
            if membership:
                await state.update_data(group_id=user.active_group_id)
    finally:
        db.close()
    await query.answer()
    msg = query.message.model_copy(update={"from_user": query.from_user})
    await create_character_command(msg, state)


@router.callback_query(F.data.startswith("create_character_group_"))
async def create_group_character_callback(query: types.CallbackQuery, state: FSMContext):
    """Start character creation inside the selected shared campaign."""
    try:
        group_id = int(query.data.removeprefix("create_character_group_"))
    except ValueError:
        await query.answer("Некорректная группа.", show_alert=True)
        return
    await state.update_data(group_id=group_id)
    await query.answer()
    msg = query.message.model_copy(update={"from_user": query.from_user})
    await create_character_command(msg, state)


@router.message(Command("create_character"))
async def create_character_command(message: types.Message, state: FSMContext):
    """Start character creation process"""
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == message.from_user.id).first()
        if not user:
            user = User(
                id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name
            )
            db.add(user)
            db.commit()

        # Use a preselected shared campaign when creation was opened from
        # the setting selector; otherwise use the personal campaign.
        state_data = await state.get_data()
        selected_group_id = state_data.get("group_id")
        current_user = db.query(User).filter(User.id == message.from_user.id).first()
        if not selected_group_id and current_user and current_user.active_group_id:
            active_membership = db.query(GroupMember).filter(
                GroupMember.group_id == current_user.active_group_id,
                GroupMember.user_id == current_user.id
            ).first()
            if active_membership:
                selected_group_id = current_user.active_group_id
        group = None
        if selected_group_id:
            group = db.query(Group).join(GroupMember).filter(
                Group.id == selected_group_id,
                GroupMember.group_id == selected_group_id,
                GroupMember.user_id == message.from_user.id
            ).first()
        if group is None:
            group = get_or_create_default_group(db, message.from_user.id)

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
        await state.update_data(group_id=group.id)

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

    # Show attribute distribution screen
    attributes_text = f"""
🧙 <b>Создание персонажа</b>

<b>Раса:</b> {race}
<b>Класс:</b> {class_name}
<b>Имя:</b> {name}

Шаг 4: <b>Распределение характеристик</b>

Вы берете готовый набор чисел:
<b>15, 14, 13, 12, 10, 8</b>

И распределяете их между характеристиками:
• <b>STR</b> (Сила) - боевые атаки, прыжки
• <b>DEX</b> (Ловкость) - инициатива, защита
• <b>CON</b> (Телосложение) - здоровье
• <b>INT</b> (Интеллект) - знания, логика
• <b>WIS</b> (Мудрость) - восприятие, магия
• <b>CHA</b> (Харизма) - общение, влияние

Напишите 6 чисел через запятую в порядке:
STR, DEX, CON, INT, WIS, CHA

Пример: 15, 14, 13, 12, 10, 8
"""

    await message.answer(attributes_text)
    await state.set_state(CharacterCreationStates.distributing_attributes)


@router.message(CharacterCreationStates.distributing_attributes)
async def distribute_attributes_message(message: types.Message, state: FSMContext):
    """Handle attribute distribution"""
    text = message.text.strip()

    try:
        # Parse attributes
        attr_values = [int(x.strip()) for x in text.split(",")]

        if len(attr_values) != 6:
            await message.answer("❌ Нужно ровно 6 чисел через запятую")
            return

        # Check if it's the correct array
        if sorted(attr_values, reverse=True) != [15, 14, 13, 12, 10, 8]:
            await message.answer(
                "❌ Числа должны быть: 15, 14, 13, 12, 10, 8\n"
                "(в любом порядке)"
            )
            return

        attributes = {
            "str": attr_values[0],
            "dex": attr_values[1],
            "con": attr_values[2],
            "int": attr_values[3],
            "wis": attr_values[4],
            "cha": attr_values[5],
        }

        data = await state.get_data()
        race = data.get("race")
        class_name = data.get("class_")
        name = data.get("name")

        # Apply racial modifiers
        final_attributes = RulesService.apply_ability_modifiers(attributes, race)

        await state.update_data(attributes=final_attributes)

        # Show confirmation
        race_info = RulesService.get_race_info(race)
        class_info = RulesService.get_class_info(class_name)
        con_mod = (final_attributes["con"] - 10) // 2
        starting_hp = class_info["starting_hp"] + con_mod

        confirmation_text = f"""
🧙 <b>Подтверждение персонажа</b>

<b>Имя:</b> {name}
<b>Раса:</b> {race}
<b>Класс:</b> {class_name}

<b>📊 Характеристики (с модификаторами расы):</b>
STR: {final_attributes['str']} ({(final_attributes['str']-10)//2:+d})
DEX: {final_attributes['dex']} ({(final_attributes['dex']-10)//2:+d})
CON: {final_attributes['con']} ({con_mod:+d})
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

    except ValueError:
        await message.answer("❌ Введите 6 чисел через запятую\nПример: 15, 14, 13, 12, 10, 8")


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
        selected_group_id = data.get("group_id")

        if not selected_group_id:
            await query.message.edit_text(
                format_error("Сеттинг потерян. Вернитесь в меню и выберите сеттинг заново.")
            )
            await state.clear()
            await query.answer()
            return

        membership = db.query(GroupMember).filter(
            GroupMember.group_id == selected_group_id,
            GroupMember.user_id == query.from_user.id
        ).first()
        selected_group = db.query(Group).filter(Group.id == selected_group_id).first()
        if not membership or not selected_group:
            await query.message.edit_text(
                format_error("Вы больше не состоите в выбранном сеттинге.")
            )
            await state.clear()
            await query.answer()
            return

        if not open5e_class_is_available(db, class_name):
            await query.message.edit_text(
                "❌ Стартовые данные Open5e ещё не загружены.\n\n"
                "Перезапустите бота, дождитесь завершения синхронизации правил "
                "и нажмите «Создать» ещё раз. Персонаж пока не создан."
            )
            await query.answer()
            return

        class_info = RulesService.get_class_info(class_name)
        con_modifier = (attributes["con"] - 10) // 2
        starting_hp = class_info["starting_hp"] + con_modifier

        character = CharacterService.create_character(
            db=db,
            user_id=query.from_user.id,
            group_id=selected_group_id,
            name=name,
            race=race,
            class_=class_name,
            attributes=attributes,
            hp=starting_hp
        )

        if character:
            user = db.query(User).filter(User.id == query.from_user.id).first()
            if user:
                user.active_group_id = selected_group_id
                db.commit()
            starting_data = grant_starting_class_data(db, character)
            await query.message.edit_text(
                format_success(f"✅ Персонаж {name} создан успешно!\n\n")
                + (
                    f"Стартовые особенности: {starting_data['features']}\n"
                    f"Предметы: {starting_data['items']}\n"
                    f"Заклинания: {starting_data['spells']}\n\n"
                    "Используй /profile для просмотра листа персонажа"
                )
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
