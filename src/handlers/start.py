from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import (
    User, Character, CharacterAttribute, CharacterAbility, CharacterSpell,
    Inventory, Item, Group, GroupMember,
    ActionHistory, GameSession, CombatEncounter, CampaignNPC, CharacterCurrency
)
from src.services import RulesService, GameEngineService
from src.services.character_service import xp_for_next_level
from src.services.inventory_service import InventoryService
from src.services.open5e_localization import localized_feature_description, localized_item_type
import logging

logger = logging.getLogger(__name__)
router = Router()


class MenuStates(StatesGroup):
    entering_setting_style = State()


def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile")],
        [InlineKeyboardButton(text="⚔️ Действие", callback_data="menu_action")],
        [InlineKeyboardButton(text="👥 Знакомые", callback_data="menu_acquaintances")],
        [InlineKeyboardButton(text="🌍 Пересобрать сеттинг", callback_data="reset_setting")],
    ])


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

        welcome_text = """
🎲 <b>Добро пожаловать в D&D RPG Bot!</b>

Выберите действие в меню ниже.
"""

        await message.answer(welcome_text, reply_markup=main_menu_keyboard())

    except Exception as e:
        logger.error(f"Error in start_command: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
    finally:
        db.close()


@router.message(Command("menu"))
async def menu_command(message: types.Message):
    """Show the main game menu."""
    await message.answer(
        "🎮 <b>Главное меню</b>\n\n"
        "Выберите действие:",
        reply_markup=main_menu_keyboard()
    )


@router.callback_query(F.data == "main_menu")
async def main_menu_callback(query: types.CallbackQuery, state: FSMContext):
    """Return to the main game menu."""
    await state.clear()
    await query.message.edit_text(
        "🎮 <b>Главное меню</b>\n\n"
        "Выберите действие:",
        reply_markup=main_menu_keyboard()
    )
    await query.answer()


@router.callback_query(F.data == "menu_profile")
async def menu_profile_callback(query: types.CallbackQuery, state: FSMContext):
    """Show the profile if a character exists; otherwise start character creation."""
    db: Session = SessionLocal()
    try:
        character = db.query(Character).filter(
            Character.user_id == query.from_user.id
        ).first()
        if character:
            await show_profile_callback(query)
        else:
            from src.handlers.character import create_character_callback
            await create_character_callback(query, state)
    finally:
        db.close()


@router.callback_query(F.data == "menu_action")
async def menu_action_callback(query: types.CallbackQuery, state: FSMContext):
    """Start the existing /action input flow from the menu."""
    db: Session = SessionLocal()
    try:
        character = db.query(Character).filter(
            Character.user_id == query.from_user.id
        ).first()
        if not character:
            await query.answer("Сначала создайте персонажа через «Профиль».", show_alert=True)
            return

        await query.message.answer(
            f"⚔️ <b>Действие персонажа {character.name}</b>\n\n"
            "Опишите, что хотите сделать:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
            ])
        )
        from src.handlers.game import ActionStates
        await state.set_state(ActionStates.describing_action)
        await query.answer()
    finally:
        db.close()


@router.callback_query(F.data == "reset_setting")
async def reset_setting_callback(query: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data="confirm_setting_reset"),
            InlineKeyboardButton(text="❌ Нет", callback_data="cancel_setting_reset"),
        ],
        [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
    ])
    await query.message.edit_text(
        "⚠️ <b>Пересобрать сеттинг?</b>\n\n"
        "Персонаж и история текущего приключения будут удалены. Продолжить?",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data == "cancel_setting_reset")
async def cancel_setting_reset_callback(query: types.CallbackQuery):
    await query.message.edit_text(
        "Пересборка отменена.",
        reply_markup=main_menu_keyboard()
    )
    await query.answer()


@router.callback_query(F.data == "confirm_setting_reset")
async def confirm_setting_reset_callback(query: types.CallbackQuery, state: FSMContext):
    db: Session = SessionLocal()
    try:
        group = db.query(Group).join(GroupMember).filter(
            Group.id == query.from_user.id,
            GroupMember.user_id == query.from_user.id,
            GroupMember.role == "gm"
        ).first()
        if not group:
            await query.answer("Личная кампания не найдена.", show_alert=True)
            return

        characters = db.query(Character).filter(
            Character.user_id == query.from_user.id,
            Character.group_id == group.id
        ).all()
        for character in characters:
            db.query(ActionHistory).filter(
                ActionHistory.character_id == character.id
            ).delete(synchronize_session=False)
            db.delete(character)
        db.query(CampaignNPC).filter(
            CampaignNPC.group_id == group.id
        ).delete(synchronize_session=False)
        session = db.query(GameSession).filter(GameSession.group_id == group.id).first()
        if session:
            session.current_location_id = None
            session.turn_order = None
            session.combat_active = False
            db.query(CombatEncounter).filter(
                CombatEncounter.session_id == session.id
            ).update({CombatEncounter.is_active: False}, synchronize_session=False)
        db.commit()

        await state.set_state(MenuStates.entering_setting_style)
        await state.update_data(setting_group_id=group.id)
        await query.message.edit_text(
            "🧹 Персонаж и история приключения удалены.\n\n"
            "В каком стиле создать новый мир? Опишите его своими словами: "
            "например, классическое фэнтези, киберпанк, космическая опера "
            "или любой другой стиль. ДМ будет придерживаться выбранного стиля.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
            ])
        )
        await query.answer()
    except Exception:
        db.rollback()
        logger.exception("Failed to reset personal setting")
        await query.answer("Не удалось пересобрать сеттинг.", show_alert=True)
    finally:
        db.close()


@router.message(MenuStates.entering_setting_style, F.text)
async def save_personal_setting_style(message: types.Message, state: FSMContext):
    style = (message.text or "").strip()
    if len(style) < 3:
        await message.answer("Опишите стиль чуть подробнее (минимум 3 символа).")
        return
    if len(style) > 1500:
        await message.answer("Описание слишком длинное. Максимум 1500 символов.")
        return

    data = await state.get_data()
    group_id = data.get("setting_group_id")
    db: Session = SessionLocal()
    try:
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            await message.answer("Личная кампания не найдена. Откройте /menu и попробуйте снова.")
            await state.clear()
            return

        group.world_description = (
            f"Обязательный стиль сеттинга: {style}. "
            "Строго придерживайся этого жанра, атмосферы, уровня технологий и "
            "правил мира во всех сценах. Не меняй сеттинг без явного решения игрока."
        )
        db.commit()
        await state.clear()
        await message.answer(
            "✅ Стиль мира сохранён. ДМ будет придерживаться его.\n\n"
            "Теперь можно создать нового персонажа:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧙 Создать персонажа", callback_data="create_character")],
                [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")],
            ])
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to save personal setting style")
        await message.answer("Не удалось сохранить стиль. Попробуйте ещё раз.")
    finally:
        db.close()


@router.callback_query(F.data == "action_menu")
async def action_menu_callback(query: types.CallbackQuery):
    """Action menu"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ Выполнить действие", switch_inline_query_current_chat="/action ")],
        [InlineKeyboardButton(text="🗺️ Путешествовать", callback_data="travel_menu")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
    ])
    await query.message.edit_text("⚔️ <b>Действия в игре</b>", reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data == "travel_menu")
async def travel_menu_callback(query: types.CallbackQuery):
    """Travel menu"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚶 Путешествовать", switch_inline_query_current_chat="/travel ")],
        [InlineKeyboardButton(text="📍 Текущая локация", switch_inline_query_current_chat="/location ")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="action_menu")],
    ])
    await query.message.edit_text("🗺️ <b>Путешествие</b>", reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data == "inventory_menu")
async def inventory_menu_callback(query: types.CallbackQuery):
    """Inventory menu"""
    db: Session = SessionLocal()
    try:
        # Get character for this user
        character = db.query(Character).filter(
            Character.user_id == query.from_user.id
        ).first()

        if not character:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧙 Создать персонажа", callback_data="create_character")],
                [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
            ])
            await query.message.edit_text(
                "❌ У вас нет персонажа\n\n"
                "Создайте персонажа командой /create_character",
                reply_markup=keyboard
            )
            await query.answer()
            return

        # Get inventory
        inventory_items = db.query(Inventory).filter(
            Inventory.character_id == character.id
        ).all()
        grouped_inventory = {}
        for inv in inventory_items:
            grouped_inventory.setdefault(inv.item_id, []).append(inv)

        inventory_text = f"<b>🎒 Инвентарь {character.name}</b>\n\n"

        if grouped_inventory:
            total_weight = 0
            for idx, (item_id, rows) in enumerate(grouped_inventory.items(), 1):
                inv = rows[0]
                quantity = sum(max(0, row.quantity or 0) for row in rows)
                equipped_state = any(row.is_equipped for row in rows)
                item = db.query(Item).filter(Item.id == item_id).first()
                if item:
                    equipped = "✓ " if equipped_state else "  "
                    weight = float(item.weight or 0) * quantity
                    total_weight += weight
                    inventory_text += (
                        f"{idx}. {equipped}<b>{item.name}</b> x{quantity}\n"
                        f"   Вес: {weight:.1f} фн | Тип: {localized_item_type(item.item_type)}\n"
                    )
            inventory_text += f"\n<b>Общий вес:</b> {total_weight:.1f} фунтов"
        else:
            inventory_text += "Инвентарь пуст"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
        ])

        await query.message.edit_text(inventory_text, reply_markup=keyboard)
        await query.answer()

    except Exception as e:
        logger.error(f"Error in inventory_menu_callback: {e}")
        await query.answer(f"❌ Ошибка: {str(e)}")
    finally:
        db.close()


@router.callback_query(F.data == "help_menu")
async def help_menu_callback(query: types.CallbackQuery):
    """Help menu"""
    help_text = """
🎲 <b>D&D RPG Bot - Справка</b>

<b>📋 Персонаж:</b>
/create_character - Создать персонажа
/profile - Показать профиль персонажа
/inventory - Управлять инвентарём

<b>🎮 Игра:</b>
/action [описание] - Выполнить действие
/location - Показать локацию
/travel - Путешествовать

<b>⚙️ Система:</b>
/menu - Главное меню
/help - Эта справка
"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
    ])
    await query.message.edit_text(help_text, reply_markup=keyboard)
    await query.answer()


@router.callback_query(F.data == "show_profile")
async def show_profile_callback(query: types.CallbackQuery):
    """Show user profile"""
    db: Session = SessionLocal()
    try:
        # Get character for this user
        character = db.query(Character).filter(
            Character.user_id == query.from_user.id
        ).first()

        if not character:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧙 Создать персонажа", callback_data="create_character")],
                [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
            ])
            await query.message.edit_text(
                "❌ У вас нет персонажа\n\n"
                "Создайте персонажа командой /create_character",
                reply_markup=keyboard
            )
            await query.answer()
            return

        # Get character attributes
        attributes = db.query(CharacterAttribute).filter(
            CharacterAttribute.character_id == character.id
        ).first()
        armor_class = GameEngineService.calculate_armor_class(character, attributes, db)
        inventory = InventoryService.get_inventory(db, character.id)
        abilities = db.query(CharacterAbility).filter(
            CharacterAbility.character_id == character.id,
            CharacterAbility.is_class.is_(True)
        ).all()
        spells = db.query(CharacterSpell).filter(
            CharacterSpell.character_id == character.id
        ).order_by(CharacterSpell.spell_level, CharacterSpell.spell_name).all()
        purse = db.query(CharacterCurrency).filter(
            CharacterCurrency.character_id == character.id
        ).first()
        if purse is None:
            purse = CharacterCurrency(character_id=character.id)
            db.add(purse)
            db.commit()
        hp_status = "☠️ Погиб" if character.current_hp <= 0 else "❤️ Здоровье"

        profile_text = f"""
<b>⚔️ {character.name}</b>

<b>📋 Основная информация:</b>
Раса: {character.race}
Класс: {character.class_}
Уровень: {character.level}
Опыт: {character.experience or 0}{f" / {xp_for_next_level(character.level)}" if xp_for_next_level(character.level) else " (максимум)"}

<b>{hp_status}:</b>
{character.current_hp}/{character.max_hp} HP
<b>🛡️ Класс защиты:</b> {armor_class}
<b>💰 Кошелёк:</b> {purse.gold} зм · {purse.silver} см · {purse.copper} мм

<b>📊 Характеристики:</b>
<b>STR</b> {attributes.strength} ({(attributes.strength-10)//2:+d})
<b>DEX</b> {attributes.dexterity} ({(attributes.dexterity-10)//2:+d})
<b>CON</b> {attributes.constitution} ({(attributes.constitution-10)//2:+d})
<b>INT</b> {attributes.intelligence} ({(attributes.intelligence-10)//2:+d})
<b>WIS</b> {attributes.wisdom} ({(attributes.wisdom-10)//2:+d})
<b>CHA</b> {attributes.charisma} ({(attributes.charisma-10)//2:+d})
"""
        profile_text += "\n<b>🎒 Снаряжение:</b>"
        if inventory:
            for entry in inventory:
                item = entry["item"]
                if item:
                    profile_text += f"\n• {item.name} ×{entry['quantity']}"
        else:
            profile_text += "\nПусто"
        if abilities:
            profile_text += "\n\n<b>✨ Особенности класса:</b>"
            for ability in abilities:
                profile_text += f"\n• <b>{ability.ability_name}</b>"
                description = localized_feature_description(
                    ability.ability_name,
                    ability.ability_description or ""
                )
                if description:
                    profile_text += f"\n  {description}"
        if spells:
            profile_text += "\n\n<b>🔮 Заклинания:</b>"
            for spell in spells:
                level = "заговор" if spell.spell_level == 0 else f"{spell.spell_level} ур."
                profile_text += f"\n• {spell.spell_name} ({level})"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
        ])

        await query.message.edit_text(profile_text, reply_markup=keyboard)
        await query.answer()

    except Exception as e:
        logger.error(f"Error in show_profile_callback: {e}")
        await query.answer(f"❌ Ошибка: {str(e)}")
    finally:
        db.close()


@router.message(Command("set_setting"))
async def set_setting_command(message: types.Message, state: FSMContext):
    """Set campaign world setting/description"""
    await message.answer(
        "🗺️ <b>Установка сеттинга кампании</b>\n\n"
        "Опишите мир вашей кампании:\n"
        "(Например: 'Средневековое фэнтези с магией, королевства, драконы, подземелья')"
    )
    await state.set_state("setting_description")


@router.message(F.text, lambda msg: msg.text)
async def handle_setting_description(message: types.Message, state: FSMContext):
    """Handle campaign setting description"""
    current_state = await state.get_state()

    if current_state != "setting_description":
        return

    db: Session = SessionLocal()
    try:
        setting_text = message.text.strip()

        if not setting_text or len(setting_text) < 10:
            await message.answer("❌ Описание слишком короткое. Минимум 10 символов")
            return

        # Get or create group for user
        from src.models import Group
        group = db.query(Group).filter(Group.id == message.from_user.id).first()

        if not group:
            group = Group(
                id=message.from_user.id,
                title=f"Кампания {message.from_user.first_name}",
                world_description=setting_text
            )
            db.add(group)
        else:
            group.world_description = setting_text

        db.commit()

        await message.answer(
            f"✅ Сеттинг кампании установлен!\n\n"
            f"Описание:\n{setting_text}"
        )
        await state.clear()

    except Exception as e:
        logger.error(f"Error setting campaign: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
    finally:
        db.close()
