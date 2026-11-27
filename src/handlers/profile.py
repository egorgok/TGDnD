from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import (
    User, Group, Character, CharacterAttribute, CharacterAbility, CharacterSpell,
    Inventory, Item, GroupMember, CharacterCurrency
)
from src.services import CharacterService
from src.services import GameEngineService
from src.services.open5e_localization import localized_feature_description
from src.services.inventory_service import InventoryService
from src.services.character_service import xp_for_next_level
from src.services.campaign_context import resolve_group, resolve_character
from src.utils import format_error
import logging

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("profile"))
async def profile_command(message: types.Message):
    """Show character profile"""
    db: Session = SessionLocal()
    try:
        default_group = resolve_group(db, message.from_user.id, message.chat)

        if not default_group:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧙 Создать персонажа", callback_data="create_character")],
                [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
            ])
            await message.answer(
                "📋 У вас нет персонажа\n\n"
                "Создайте своего персонажа, чтобы начать играть",
                reply_markup=keyboard
            )
            return

        # Get character for this user
        character = resolve_character(db, message.from_user.id, message.chat)

        if not character:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧙 Создать персонажа", callback_data="create_character")],
                [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
            ])
            await message.answer(
                "📋 У вас нет персонажа\n\n"
                "Создайте своего персонажа, чтобы начать играть",
                reply_markup=keyboard
            )
            return

        # Get character attributes
        attributes = db.query(CharacterAttribute).filter(
            CharacterAttribute.character_id == character.id
        ).first()
        armor_class = GameEngineService.calculate_armor_class(character, attributes, db)

        # Get inventory
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
        xp_target = xp_for_next_level(character.level)

        # Format profile
        profile_text = f"""
<b>⚔️ {character.name}</b>

<b>📋 Основная информация:</b>
Раса: {character.race}
Класс: {character.class_}
Уровень: {character.level}
Опыт: {character.experience or 0}{f" / {xp_target}" if xp_target else " (максимум)"}

<b>❤️ Здоровье:</b>
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

<b>🎒 Инвентарь:</b>
"""

        if inventory:
            for row in inventory:
                item = row["item"]
                if item:
                    marker = "✓" if row["is_equipped"] else "•"
                    profile_text += f"\n{marker} {item.name} x{row['quantity']}"
        else:
            profile_text += "\nПуст"

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
                marker = "✓" if not (spell.slots_used or 0) else "•"
                level = "заговор" if spell.spell_level == 0 else f"{spell.spell_level} ур."
                profile_text += f"\n{marker} {spell.spell_name} ({level})"

        # Create action buttons
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Удалить персонажа", callback_data="delete_character")],
            [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
        ])

        await message.answer(profile_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error in profile_command: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.callback_query(F.data == "show_inventory")
async def show_inventory_callback(query: types.CallbackQuery):
    """Show full inventory"""
    db: Session = SessionLocal()
    try:
        character = db.query(Character).filter(
            Character.user_id == query.from_user.id,
            Character.group_id.in_(
                db.query(GroupMember.group_id).filter(
                    GroupMember.user_id == query.from_user.id
                )
            )
        ).first()

        if not character:
            await query.answer("❌ Персонаж не найден")
            return

        inventory = InventoryService.get_inventory(db, character.id)

        inventory_text = "<b>🎒 Полный инвентарь</b>\n\n"

        if inventory:
            total_weight = 0
            for row in inventory:
                item = row["item"]
                if not item:
                    continue
                quantity = row["quantity"]
                equipped = "✓ " if row["is_equipped"] else "  "
                weight = float(item.weight or 0) * quantity
                total_weight += weight
                inventory_text += (
                    f"{equipped}<b>{item.name}</b> x{quantity}\n"
                    f"   Вес: {weight:.1f} фунтов\n"
                    f"   {item.description or 'Нет описания'}\n\n"
                )
            inventory_text += f"\n<b>Общий вес:</b> {total_weight:.1f} фунтов"
        else:
            inventory_text += "Инвентарь пуст"

        await query.message.edit_text(inventory_text)
        await query.answer()

    except Exception as e:
        logger.error(f"Error in show_inventory_callback: {e}")
        await query.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.callback_query(F.data == "show_abilities")
async def show_abilities_callback(query: types.CallbackQuery):
    """Show character abilities and spells"""
    db: Session = SessionLocal()
    try:
        from src.models import CharacterAbility, CharacterSpell

        character = db.query(Character).filter(
            Character.user_id == query.from_user.id,
            Character.group_id.in_(
                db.query(GroupMember.group_id).filter(
                    GroupMember.user_id == query.from_user.id
                )
            )
        ).first()

        if not character:
            await query.answer("❌ Персонаж не найден")
            return

        abilities = db.query(CharacterAbility).filter(
            CharacterAbility.character_id == character.id
        ).all()

        spells = db.query(CharacterSpell).filter(
            CharacterSpell.character_id == character.id
        ).all()

        abilities_text = "<b>🗡️ Способности и заклинания</b>\n\n"

        if abilities:
            abilities_text += "<b>Способности:</b>\n"
            for ability in abilities:
                tag = "🧬 Расовая" if ability.is_racial else "🎯 Классовая"
                abilities_text += f"\n{tag}: <b>{ability.ability_name}</b>\n"
                if ability.ability_description:
                    abilities_text += f"{ability.ability_description}\n"

        if spells:
            abilities_text += "\n\n<b>Заклинания:</b>\n"
            for spell in spells:
                marker = "✓" if not (spell.slots_used or 0) else "•"
                abilities_text += (
                    f"\n{marker} <b>{spell.spell_name}</b> (ур. {spell.spell_level})\n"
                )
        else:
            if abilities:
                abilities_text += "\n\nНет заклинаний"

        if not abilities and not spells:
            abilities_text += "Нет способностей или заклинаний"

        await query.message.edit_text(abilities_text)
        await query.answer()

    except Exception as e:
        logger.error(f"Error in show_abilities_callback: {e}")
        await query.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.callback_query(F.data == "delete_character")
async def delete_character_callback(query: types.CallbackQuery):
    """Confirm character deletion"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Удалить персонажа", callback_data="confirm_delete"),
            InlineKeyboardButton(text="✅ Оставить", callback_data="cancel_delete")
        ]
    ])

    await query.message.edit_text(
        "⚠️ <b>Вы уверены?</b>\n\n"
        "Это действие удалит вашего персонажа и всё его снаряжение навсегда.",
        reply_markup=keyboard
    )
    await query.answer()


@router.callback_query(F.data == "confirm_delete")
async def confirm_delete_callback(query: types.CallbackQuery):
    """Delete character"""
    db: Session = SessionLocal()
    try:
        character = db.query(Character).filter(
            Character.user_id == query.from_user.id,
            Character.group_id.in_(
                db.query(GroupMember.group_id).filter(
                    GroupMember.user_id == query.from_user.id
                )
            )
        ).first()

        if not character:
            await query.answer("❌ Персонаж не найден")
            return

        char_name = character.name
        CharacterService.delete_character(db, character.id)

        await query.message.edit_text(
            f"✅ Персонаж <b>{char_name}</b> удален"
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error in confirm_delete_callback: {e}")
        await query.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.callback_query(F.data == "cancel_delete")
async def cancel_delete_callback(query: types.CallbackQuery):
    """Cancel character deletion"""
    await query.message.edit_text(
        "✅ Удаление отменено"
    )
    await query.answer()
