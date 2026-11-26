from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import (
    Character, CharacterAttribute, Inventory, Item, ActionHistory, GameSession,
    Location, LocationConnection, CombatEncounter, CombatParticipant
)
from src.services import (
    AIService, GameEngineService, CharacterService, InventoryService,
    CombatService, GroupService
)
from src.utils import format_error
import logging
import base64
from io import BytesIO
import html

logger = logging.getLogger(__name__)
router = Router()


class ActionStates(StatesGroup):
    """FSM states for action system"""
    describing_action = State()
    waiting_for_resolution = State()


class CombatStates(StatesGroup):
    """FSM states for combat"""
    in_combat = State()
    choosing_attack = State()


class TravelStates(StatesGroup):
    """FSM states for travel"""
    choosing_destination = State()


@router.message(Command("action"))
async def action_command(message: types.Message, state: FSMContext):
    """Start action sequence"""
    db: Session = SessionLocal()
    try:
        # Get user's default group
        from src.models import Group, GroupMember
        default_group = db.query(Group).join(
            GroupMember
        ).filter(
            GroupMember.user_id == message.from_user.id,
            GroupMember.role == "gm"
        ).first()

        if not default_group:
            await message.answer("❌ Создайте персонажа командой /create_character")
            return

        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id,
            Character.group_id == default_group.id
        ).first()

        if not character:
            await message.answer(
                "❌ У вас нет персонажа\n\n"
                "Создайте персонажа командой /create_character"
            )
            return

        await message.answer(
            f"⚔️ <b>Действие персонажа {character.name}</b>\n\n"
            "Опишите, что вы хотите сделать:\n"
            "(Например: 'Забраться на дерево', 'Атаковать гоблина мечом', 'Использовать магию')"
        )
        await state.set_state(ActionStates.describing_action)

    except Exception as e:
        logger.error(f"Error in action_command: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(ActionStates.describing_action)
async def handle_action_description(message: types.Message, state: FSMContext):
    """Handle action description from player"""
    db: Session = SessionLocal()
    try:
        action_description = message.text.strip()

        if not action_description or len(action_description) < 3:
            await message.answer("❌ Описание действия слишком короткое. Попробуйте снова.")
            return

        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id
        ).first()

        if not character:
            await message.answer(format_error("Персонаж не найден"))
            await state.clear()
            return

        # Get group and world description
        from src.models import Group
        group = db.query(Group).filter(Group.id == character.group_id).first()
        world_desc = group.world_description if group else "Неизвестный мир"

        # Show "thinking" message
        thinking_msg = await message.answer("🤔 ДМ думает...")

        try:
            # Call AI to get action result
            ai_result = await AIService.process_action(
                character_name=character.name,
                action_description=action_description,
                world_setting=world_desc
            )

            # Roll d20
            d20_roll = GameEngineService.roll_d20()
            roll_text = f"🎲 <b>Результат броска:</b> {d20_roll}\n\n"

            result_text = f"""
⚔️ <b>{character.name}</b> выполняет действие

<b>Действие:</b> {action_description}

{roll_text}
<b>Результат:</b>
{ai_result}
"""

            await thinking_msg.delete()
            await message.answer(result_text)
            await state.clear()

        except Exception as ai_error:
            logger.error(f"AI error: {ai_error}")
            await thinking_msg.delete()
            await message.answer(format_error(f"Ошибка ИИ: {str(ai_error)}"))
            await state.clear()

    except Exception as e:
        logger.error(f"Error handling action: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
        await state.clear()
    finally:
        db.close()


@router.message(Command("travel"))
async def travel_command(message: types.Message, state: FSMContext):
    """Travel to a connected location"""
    db: Session = SessionLocal()
    try:
        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id
        ).first()

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        await message.answer(
            "🗺️ <b>Путешествие</b>\n\n"
            "Выберите направление или используйте /location для просмотра текущей локации"
        )

    except Exception as e:
        logger.error(f"Error in travel_command: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("location"))
async def location_command(message: types.Message):
    """Show current location"""
    db: Session = SessionLocal()
    try:
        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id
        ).first()

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        # Get group and world description
        from src.models import Group
        group = db.query(Group).filter(Group.id == character.group_id).first()
        world_desc = group.world_description if group else "Неизвестный мир"

        location_text = f"""
📍 <b>Текущая локация</b>

Ваш персонаж: <b>{character.name}</b>

<b>Мир:</b>
{world_desc}

Используй /action для выполнения действия
"""

        await message.answer(location_text)

    except Exception as e:
        logger.error(f"Error in location_command: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("travel"))
async def travel_command(message: types.Message):
    """Travel command"""
    db: Session = SessionLocal()
    try:
        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id
        ).first()

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        await message.answer(
            "🗺️ <b>Путешествие</b>\n\n"
            "Используйте /location для просмотра текущей локации\n"
            "или /action для выполнения действия"
        )

    except Exception as e:
        logger.error(f"Error in travel_command: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.callback_query(TravelStates.choosing_destination, F.data.startswith("travel_to_"))
async def travel_to_location_callback(query: types.CallbackQuery, state: FSMContext):
    """Travel to selected location"""
    db: Session = SessionLocal()
    try:
        new_location_id = int(query.data.replace("travel_to_", ""))

        # Get character and session
        character = db.query(Character).filter(
            Character.user_id == query.from_user.id,
            Character.group_id == 0
        ).first()

        session = db.query(GameSession).filter(
            GameSession.group_id == character.group_id
        ).first()

        # Move to new location
        GroupService.move_to_location(db, session.id, new_location_id)

        # Get new location info
        location = db.query(Location).filter(Location.id == new_location_id).first()

        # Generate location image description
        ai_service = AIService()
        try:
            # Generate image for location
            image_prompt = f"D&D style fantasy location: {location.name}. {location.description}. Detailed environment, atmospheric lighting, digital art."
            image_b64 = await ai_service.generate_image(prompt=image_prompt)

            travel_text = f"""
🗺️ <b>Вы прибыли в: {location.name}</b>

📍 <b>Описание:</b>
{location.description}

[🖼️ Изображение локации загружается...]
"""
            msg = await query.message.edit_text(travel_text)

            # Send image if generated
            if image_b64:
                try:
                    image_data = base64.b64decode(image_b64)
                    await query.message.answer_photo(
                        photo=image_data,
                        caption=f"🎨 {location.name}"
                    )
                except Exception as e:
                    logger.warning(f"Could not send image: {e}")

        except Exception as e:
            logger.warning(f"Could not generate image: {e}")
            travel_text = f"""
🗺️ <b>Вы прибыли в: {location.name}</b>

📍 <b>Описание:</b>
{location.description}
"""
            await query.message.edit_text(travel_text)

        await state.clear()

    except Exception as e:
        logger.error(f"Error traveling: {e}")
        await query.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("fight"))
async def fight_command(message: types.Message, state: FSMContext):
    """Fight command - removed, ДМ решает когда боевой раунд"""
    await message.answer(
        "⚔️ <b>Боевая система</b>\n\n"
        "Боевые раунды назначает Гейм-Мастер!\n"
        "Используйте /action для выполнения действий в бою"
    )
    db: Session = SessionLocal()
    try:
        # Get user's default group
        from src.models import Group, GroupMember
        default_group = db.query(Group).join(
            GroupMember
        ).filter(
            GroupMember.user_id == message.from_user.id,
            GroupMember.role == "gm"
        ).first()

        if not default_group:
            await message.answer(format_error("Создайте персонажа командой /create_character"))
            return

        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id,
            Character.group_id == default_group.id
        ).first()

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        # Get session
        session = db.query(GameSession).filter(
            GameSession.group_id == default_group.id
        ).first()

        if not session:
            await message.answer("❌ Сессия не активна")
            return

        # Create combat encounter
        encounter = CombatService.start_combat(
            db=db,
            session_id=session.id,
            location_id=session.current_location_id,
            enemy_name="Враг",
            enemy_hp=20,
            enemy_ac=12
        )

        # Roll initiative
        attributes = db.query(CharacterAttribute).filter(
            CharacterAttribute.character_id == character.id
        ).first()

        dex_modifier = (attributes.dexterity - 10) // 2
        player_initiative = CombatService.roll_initiative(dex_modifier)
        enemy_initiative = CombatService.roll_initiative(0)

        # Add participants
        CombatService.add_participant(db, encounter.id, character.id, player_initiative, True)

        # Determine turn order
        player_first = player_initiative > enemy_initiative

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚔️ Атаковать", callback_data=f"attack_in_{encounter.id}")]
        ])

        combat_text = f"""
⚔️ <b>БОЕВОЕ СРАЖЕНИЕ!</b>

Инициатива:
Вы: {player_initiative}
Враг: {enemy_initiative}

{'🎯 Ваш ход!' if player_first else '⚠️ Враг атакует первым!'}

Враг HP: 20/20
Ваш HP: {character.current_hp}/{character.max_hp}
"""

        await message.answer(combat_text, reply_markup=keyboard)
        await state.set_state(CombatStates.in_combat)
        await state.update_data(encounter_id=encounter.id, character_id=character.id)

    except Exception as e:
        logger.error(f"Error starting combat: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.callback_query(CombatStates.in_combat, F.data.startswith("attack_in_"))
async def attack_in_combat_callback(query: types.CallbackQuery, state: FSMContext):
    """Player attacks in combat"""
    db: Session = SessionLocal()
    try:
        data = await state.get_data()
        encounter_id = data.get("encounter_id")
        character_id = data.get("character_id")

        # Get encounter and character
        encounter = db.query(CombatEncounter).filter(
            CombatEncounter.id == encounter_id
        ).first()

        character = db.query(Character).filter(
            Character.id == character_id
        ).first()

        attributes = db.query(CharacterAttribute).filter(
            CharacterAttribute.character_id == character_id
        ).first()

        # Resolve attack
        attack_bonus = (attributes.strength - 10) // 2 + 2  # +2 for proficiency
        attack_result = CombatService.resolve_attack(
            attacker_bonus=attack_bonus,
            target_ac=encounter.enemy_ac,
            weapon_damage_dice="d8"
        )

        # Apply damage to enemy
        new_enemy_hp, is_dead = CombatService.apply_damage_to_enemy(
            db, encounter_id, attack_result['damage']
        )

        # Enemy counterattack
        enemy_damage = GameEngineService.roll_dice("d6")
        GameEngineService.apply_damage(character, enemy_damage, db)

        # Generate combat image
        ai_service = AIService()
        image_b64 = None
        try:
            character_name = character.name
            enemy_hp_pct = (new_enemy_hp / 20) * 100
            combat_image_prompt = (
                f"D&D 5e fantasy combat scene: {character_name} ({character.race} {character.class_}) "
                f"fighting an enemy in a dungeon. Dynamic action pose, detailed armor and weapons, "
                f"atmospheric lighting, dramatic battle scene, digital art, high quality."
            )
            image_b64 = await ai_service.generate_image(prompt=combat_image_prompt)
        except Exception as e:
            logger.warning(f"Could not generate combat image: {e}")

        # Format response
        combat_text = f"""
⚔️ <b>Раунд боя</b>

<b>Ваша атака:</b>
{attack_result['description']}

<b>Контратака врага:</b>
Враг наносит {enemy_damage} урона!

<b>Статус:</b>
Враг HP: {new_enemy_hp}/20
Ваш HP: {character.current_hp}/{character.max_hp}
"""

        if is_dead:
            combat_text += "\n\n🎉 <b>ПОБЕДА!</b> Враг побежден!"
            await query.message.edit_text(combat_text)
            if image_b64:
                try:
                    image_data = base64.b64decode(image_b64)
                    await query.message.answer_photo(photo=image_data, caption="🎨 Финальный удар!")
                except:
                    pass
            CombatService.end_combat(db, encounter_id)
            await state.clear()
        elif character.current_hp <= 0:
            combat_text += f"\n\n💀 <b>Поражение!</b> {character.name} повержен!"
            await query.message.edit_text(combat_text)
            if image_b64:
                try:
                    image_data = base64.b64decode(image_b64)
                    await query.message.answer_photo(photo=image_data, caption="💀 Последний момент...")
                except:
                    pass
            CombatService.end_combat(db, encounter_id)
            await state.clear()
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚔️ Атаковать", callback_data=f"attack_in_{encounter_id}")]
            ])
            await query.message.edit_text(combat_text, reply_markup=keyboard)
            if image_b64:
                try:
                    image_data = base64.b64decode(image_b64)
                    await query.message.answer_photo(photo=image_data, caption="⚔️ Боевая сцена")
                except:
                    pass

    except Exception as e:
        logger.error(f"Error attacking: {e}")
        await query.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("location"))
async def location_command(message: types.Message):
    """Show current location"""
    db: Session = SessionLocal()
    try:
        # Get user's default group
        from src.models import Group, GroupMember
        default_group = db.query(Group).join(
            GroupMember
        ).filter(
            GroupMember.user_id == message.from_user.id,
            GroupMember.role == "gm"
        ).first()

        if not default_group:
            await message.answer(format_error("Создайте персонажа командой /create_character"))
            return

        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id,
            Character.group_id == default_group.id
        ).first()

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        # Get current session and location
        session = db.query(GameSession).filter(
            GameSession.group_id == default_group.id
        ).first()

        if not session or not session.current_location_id:
            await message.answer("❌ Сессия не активна")
            return

        location = db.query(Location).filter(
            Location.id == session.current_location_id
        ).first()

        # Get connected locations
        connections = GroupService.get_connected_locations(db, session.current_location_id)

        location_text = f"""
📍 <b>{location.name}</b>

{location.description}

<b>Возможные направления:</b>
"""
        for connection in connections:
            location_text += f"\n• {connection[1].name}"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚶 Путешествовать", callback_data="travel_command")]
        ])

        await message.answer(location_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error getting location: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("inventory"))
async def inventory_command(message: types.Message):
    """Show and manage inventory"""
    db: Session = SessionLocal()
    try:
        # Get user's default group
        from src.models import Group, GroupMember
        default_group = db.query(Group).join(
            GroupMember
        ).filter(
            GroupMember.user_id == message.from_user.id,
            GroupMember.role == "gm"
        ).first()

        if not default_group:
            await message.answer(format_error("Создайте персонажа командой /create_character"))
            return

        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id,
            Character.group_id == default_group.id
        ).first()

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        # Get inventory
        inventory_items = db.query(Inventory).filter(
            Inventory.character_id == character.id
        ).all()

        inventory_text = f"<b>🎒 Инвентарь {character.name}</b>\n\n"

        if inventory_items:
            total_weight = 0
            for idx, inv in enumerate(inventory_items, 1):
                item = db.query(Item).filter(Item.id == inv.item_id).first()
                equipped = "✓ " if inv.is_equipped else "  "
                weight = float(item.weight or 0) * inv.quantity
                total_weight += weight
                inventory_text += (
                    f"{idx}. {equipped}<b>{item.name}</b> x{inv.quantity}\n"
                    f"   Вес: {weight:.1f} фн | Тип: {item.item_type}\n"
                )
            inventory_text += f"\n<b>Общий вес:</b> {total_weight:.1f} фунтов"
        else:
            inventory_text += "Инвентарь пуст"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 Снять предмет", callback_data="unequip_item")],
            [InlineKeyboardButton(text="🎯 Экипировать", callback_data="equip_item")]
        ])

        await message.answer(inventory_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error showing inventory: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("help"))
async def help_command(message: types.Message):
    """Show help with available commands"""
    help_text = """
🎲 <b>D&D RPG Bot - Справка</b>

<b>📋 Персонаж:</b>
/create_character - Создать нового персонажа
/profile - Показать профиль персонажа
/inventory - Управлять инвентарём

<b>🎮 Игра:</b>
/action [описание] - Выполнить действие (с d20 проверкой)
/location - Показать текущую локацию
/travel - Путешествовать в соседние локации
/fight - Начать боевое сражение

<b>🗺️ Группа:</b>
/create_group - Создать новую группу/кампанию
/join_group - Присоединиться к группе
/group_info - Информация о группе

<b>⚙️ Система:</b>
/help - Эта справка
/start - Главное меню

<b>Система D&D:</b>
• Характеристики: STR, DEX, CON, INT, WIS, CHA
• Броски: d20 для проверок, другие кубики для урона
• Крит-успех (20) и крит-провал (1)
• Сложность (DC) зависит от действия

Используйте /start для меню!
"""
    await message.answer(help_text)
