from aiogram import Router, types, F, Dispatcher
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import Character, User, Group, GroupMember, DnDRules
from src.services import CharacterService, RulesService, GroupService
from src.services.open5e_rules_service import (
    grant_starting_class_data,
    grant_starting_species_data,
    grant_starting_background_data,
    open5e_class_is_available
)
from src.utils import format_error, format_success
import logging
import re

logger = logging.getLogger(__name__)
router = Router()


class CharacterCreationStates(StatesGroup):
    """FSM states for character creation"""
    choosing_race = State()
    choosing_class = State()
    choosing_name = State()
    choosing_background = State()
    choosing_background_abilities = State()
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
    if message.chat.type in {"group", "supergroup"}:
        await message.answer(
            "❌ Персонажей можно создавать только в личном чате с ботом.\n"
            "Откройте личный чат и выберите нужный сеттинг через /menu."
        )
        return
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
            "Шаг 1: <b>Выбери вид персонажа (правила 2024)</b>",
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
    """Handle 2024 species selection."""
    race = query.data.replace("race_", "")

    # Validate race
    if not RulesService.validate_race(race):
        await query.answer("❌ Недействительный вид")
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
        f"<b>Вид:</b> {race}\n\n"
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
        f"<b>Вид:</b> {race}\n"
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
    db: Session = SessionLocal()
    try:
        backgrounds = [
            record for (record,) in db.query(DnDRules.rule_data).filter(
                DnDRules.rule_category == "srd2024_backgrounds"
            ).all()
            if isinstance(record, dict)
        ]
        backgrounds.sort(
            key=lambda record: str(
                (record.get("localized_ru") or {}).get("name")
                or record.get("name") or ""
            ).casefold()
        )
        if not backgrounds:
            await message.answer(
                "❌ Предыстории SRD 2024 ещё не загружены. "
                "Перезапустите бота и повторите создание персонажа."
            )
            await state.clear()
            return
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=str(
                        (background.get("localized_ru") or {}).get("name")
                        or background.get("name")
                    ),
                    callback_data=f"background_{background.get('key') or background.get('index')}"
                )
            ]
            for background in backgrounds
        ])
        await message.answer(
            f"🧙 <b>Создание персонажа</b>\n\n"
            f"<b>Вид:</b> {race}\n<b>Класс:</b> {class_name}\n"
            f"<b>Имя:</b> {name}\n\n"
            "Шаг 4: выберите предысторию SRD 2024.",
            reply_markup=keyboard
        )
        await state.set_state(CharacterCreationStates.choosing_background)
    finally:
        db.close()


@router.callback_query(
    CharacterCreationStates.choosing_background,
    F.data.startswith("background_")
)
async def choose_background_callback(query: types.CallbackQuery, state: FSMContext):
    background_key = query.data.removeprefix("background_")
    db: Session = SessionLocal()
    try:
        background = next(
            (
                record for (record,) in db.query(DnDRules.rule_data).filter(
                    DnDRules.rule_category == "srd2024_backgrounds"
                ).all()
                if isinstance(record, dict)
                and str(record.get("key") or record.get("index")) == background_key
            ),
            None
        )
        if not background:
            await query.answer("Предыстория не найдена", show_alert=True)
            return
        localized = background.get("localized_ru") or {}
        name = str(localized.get("name") or background.get("name") or background_key)
        allowed_scores = localized.get("ability_scores") or background.get("ability_scores") or []
        score_names = []
        display_by_key = {
            "str": "СИЛ", "dex": "ЛОВ", "con": "ТЕЛ",
            "int": "ИНТ", "wis": "МУД", "cha": "ХАР",
        }
        score_keys = []
        for score in allowed_scores:
            if not isinstance(score, dict):
                continue
            key = str(score.get("index") or "").casefold()
            if key not in {"str", "dex", "con", "int", "wis", "cha"}:
                continue
            score_keys.append(key)
            score_names.append(display_by_key[key])
        example_two = (
            f"{display_by_key[score_keys[0]]}+2; {display_by_key[score_keys[1]]}+1"
            if len(score_keys) >= 2 else "СИЛ+2; ЛОВ+1"
        )
        example_three = (
            "; ".join(f"{display_by_key[key]}+1" for key in score_keys[:3])
            if len(score_keys) >= 3 else "СИЛ+1; ЛОВ+1; ТЕЛ+1"
        )
        await state.update_data(
            background_key=background_key,
            background=name,
            background_allowed_scores=score_keys
        )
        await query.message.edit_text(
            f"<b>Предыстория:</b> {name}\n\n"
            "<b>Шаг 4: бонусы характеристик от предыстории</b>\n"
            "Распределите бонусы одним из способов: <b>+2 и +1</b> "
            "между двумя характеристиками или <b>+1 +1 +1</b> "
            "между тремя разными.\n"
            f"Доступные характеристики: <b>{', '.join(score_names)}</b>.\n"
            f"Примеры: <code>{example_two}</code> / <code>{example_three}</code>"
        )
        await query.answer()
        await state.set_state(CharacterCreationStates.choosing_background_abilities)
    finally:
        db.close()


@router.message(CharacterCreationStates.choosing_background_abilities)
async def choose_background_abilities_message(message: types.Message, state: FSMContext):
    data = await state.get_data()
    allowed = set(data.get("background_allowed_scores") or [])
    aliases = {
        "сила": "str", "сил": "str", "str": "str", "strength": "str",
        "ловкость": "dex", "лов": "dex", "dex": "dex", "dexterity": "dex",
        "телосложение": "con", "тел": "con", "con": "con", "constitution": "con",
        "интеллект": "int", "инт": "int", "int": "int", "intelligence": "int",
        "мудрость": "wis", "муд": "wis", "мдр": "wis", "wis": "wis", "wisdom": "wis",
        "харизма": "cha", "хар": "cha", "cha": "cha", "charisma": "cha",
    }
    parsed = re.findall(
        r"([a-zа-яё]+)\s*\+\s*([12])",
        (message.text or "").casefold()
    )
    bonuses = {}
    for label, amount in parsed:
        score = aliases.get(label)
        if not score or score not in allowed or score in bonuses:
            await message.answer(
                "❌ Выберите характеристики только из списка предыстории, "
                "без повторов. Пишите русские обозначения, например "
                "<code>МУД+2; ИНТ+1</code>."
            )
            return
        bonuses[score] = int(amount)
    valid = (
        (len(bonuses) == 2 and sorted(bonuses.values()) == [1, 2])
        or (len(bonuses) == 3 and all(amount == 1 for amount in bonuses.values()))
    )
    if not valid:
        await message.answer(
            "❌ Нужно распределить ровно +3: два значения +2 и +1 "
            "или три разных значения по +1. Например: "
            "<code>ЛОВ+2; ТЕЛ+1</code> или <code>ЛОВ+1; ТЕЛ+1; ИНТ+1</code>."
        )
        return
    await state.update_data(background_bonuses=bonuses)
    race = data.get("race")
    class_name = data.get("class_")
    name = data.get("name")
    await message.answer(
        f"🧙 <b>Создание персонажа</b>\n\n"
        f"<b>Вид:</b> {race}\n<b>Класс:</b> {class_name}\n"
        f"<b>Предыстория:</b> {data.get('background')}\n<b>Имя:</b> {name}\n\n"
        "<b>Шаг 5: распределение характеристик</b>\n"
        "Распределите готовый набор чисел "
        "<b>15, 14, 13, 12, 10, 8</b>.\n"
        "Введите 6 чисел через запятую в порядке "
        "<b>СИЛ, ЛОВ, ТЕЛ, ИНТ, МУД, ХАР</b>.\n"
        "Пример: <code>15, 14, 13, 12, 10, 8</code>."
    )
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
        background = data.get("background")
        background_bonuses = data.get("background_bonuses") or {}

        # 2024 species do not grant fixed ability increases; the background does.
        final_attributes = RulesService.apply_ability_modifiers(attributes, race)
        for score, amount in background_bonuses.items():
            if score in final_attributes:
                final_attributes[score] = min(20, final_attributes[score] + int(amount))

        await state.update_data(attributes=final_attributes)

        # Show confirmation
        race_info = RulesService.get_race_info(race)
        class_info = RulesService.get_class_info(class_name)
        con_mod = (final_attributes["con"] - 10) // 2
        starting_hp = class_info["starting_hp"] + con_mod + (race_info.get("hp_bonus", 0) if race_info else 0)

        confirmation_text = f"""
🧙 <b>Подтверждение персонажа</b>

<b>Имя:</b> {name}
<b>Вид:</b> {race}
<b>Класс:</b> {class_name}
<b>Предыстория:</b> {background}

<b>📊 Характеристики (с бонусами предыстории 2024):</b>
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
        await message.answer(
            "❌ Введите 6 чисел через запятую в порядке "
            "СИЛ, ЛОВ, ТЕЛ, ИНТ, МУД, ХАР.\n"
            "Пример: <code>15, 14, 13, 12, 10, 8</code>."
        )


@router.callback_query(CharacterCreationStates.confirming, F.data == "confirm_create")
async def confirm_create_callback(query: types.CallbackQuery, state: FSMContext):
    """Create character after confirmation"""
    db: Session = SessionLocal()
    try:
        data = await state.get_data()
        name = data.get("name")
        race = data.get("race")
        class_name = data.get("class_")
        background = data.get("background")
        background_key = data.get("background_key")
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
                "❌ Данные класса из SRD 2024 ещё не загружены.\n\n"
                "Перезапустите бота, дождитесь завершения импорта базы, "
                "затем нажмите «Создать» ещё раз. Персонаж пока не создан."
            )
            await query.answer()
            return

        class_info = RulesService.get_class_info(class_name)
        con_modifier = (attributes["con"] - 10) // 2
        species_info = RulesService.get_race_info(race)
        starting_hp = (
            class_info["starting_hp"]
            + con_modifier
            + (species_info.get("hp_bonus", 0) if species_info else 0)
        )

        character = CharacterService.create_character(
            db=db,
            user_id=query.from_user.id,
            group_id=selected_group_id,
            name=name,
            race=race,
            class_=class_name,
            attributes=attributes,
            hp=starting_hp,
            background=background,
        )

        if character:
            user = db.query(User).filter(User.id == query.from_user.id).first()
            if user:
                user.active_group_id = selected_group_id
                db.commit()
            starting_data = grant_starting_class_data(db, character)
            species_features = grant_starting_species_data(db, character)
            background_data = grant_starting_background_data(
                db, character, background_key
            )
            await query.message.edit_text(
                format_success(f"✅ Персонаж {name} создан успешно!\n\n")
                + (
                    f"Стартовые особенности: {starting_data['features']}\n"
                    f"Особенности вида: {species_features}\n"
                    f"Особенности предыстории: {background_data['features']}\n"
                    f"Предметы: {starting_data['items']}\n"
                    f"Предметы предыстории: {background_data['items']}\n"
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
