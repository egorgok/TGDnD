from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import Character, CharacterAttribute, Inventory, Item, ActionHistory, GameSession, Location
from src.services import AIService, GameEngineService, CharacterService, InventoryService
from src.utils import format_error
import logging

logger = logging.getLogger(__name__)
router = Router()


class ActionStates(StatesGroup):
    """FSM states for action system"""
    describing_action = State()
    waiting_for_resolution = State()


@router.message(Command("action"))
async def action_command(message: types.Message, state: FSMContext):
    """Start action sequence"""
    db: Session = SessionLocal()
    try:
        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id,
            Character.group_id == 0
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

        # Get character and all related data
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id,
            Character.group_id == 0
        ).first()

        if not character:
            await message.answer(format_error("Персонаж не найден"))
            await state.clear()
            return

        # Get character data
        attributes = db.query(CharacterAttribute).filter(
            CharacterAttribute.character_id == character.id
        ).first()

        inventory = db.query(Inventory).filter(
            Inventory.character_id == character.id
        ).all()

        inventory_list = []
        for inv in inventory:
            item = db.query(Item).filter(Item.id == inv.item_id).first()
            inventory_list.append(f"{item.name} x{inv.quantity}")

        # Get game session and location
        game_session = db.query(GameSession).filter(
            GameSession.group_id == character.group_id
        ).first()

        location_name = "Unknown Location"
        if game_session and game_session.current_location_id:
            location = db.query(Location).filter(
                Location.id == game_session.current_location_id
            ).first()
            if location:
                location_name = location.name

        # Build character data for AI
        character_data = {
            "name": character.name,
            "race": character.race,
            "class": character.class_,
            "level": character.level,
            "current_hp": character.current_hp,
            "max_hp": character.max_hp,
            "attributes": {
                "str": attributes.strength,
                "dex": attributes.dexterity,
                "con": attributes.constitution,
                "int": attributes.intelligence,
                "wis": attributes.wisdom,
                "cha": attributes.charisma,
            },
            "inventory": inventory_list,
            "location": location_name
        }

        # Store in state for later use
        await state.update_data(
            character_id=character.id,
            action_description=action_description,
            character_data=character_data
        )

        # Send "thinking" message
        thinking_msg = await message.answer(
            "🤔 <b>Гейм-мастер обдумывает действие...</b>\n\n"
            "⏳ Пожалуйста, подождите..."
        )

        # Call AI service to validate action
        ai_service = AIService()

        # Simple GM prompt for now
        gm_prompt = "You are a D&D 5e Game Master. Evaluate if the player action is possible and describe the consequences."

        world_context = f"Current location: {location_name}. Character: {character.name} ({character.race} {character.class_})"

        try:
            action_validation = await ai_service.validate_action(
                character_data=character_data,
                action_description=action_description,
                world_context=world_context,
                gm_prompt=gm_prompt
            )

            # Roll d20 for the action
            dice_roll = GameEngineService.roll_d20()

            # Determine DC (difficulty class) based on action type
            dc = 15  # Medium difficulty by default

            # Get relevant ability modifier
            ability_mod = 0
            if "strength" in action_description.lower() or "push" in action_description.lower():
                ability_mod = (attributes.strength - 10) // 2
            elif "dexterity" in action_description.lower() or "dodge" in action_description.lower() or "climb" in action_description.lower():
                ability_mod = (attributes.dexterity - 10) // 2
            elif "constitution" in action_description.lower():
                ability_mod = (attributes.constitution - 10) // 2
            elif "intelligence" in action_description.lower() or "investigate" in action_description.lower():
                ability_mod = (attributes.intelligence - 10) // 2
            elif "wisdom" in action_description.lower() or "perception" in action_description.lower():
                ability_mod = (attributes.wisdom - 10) // 2
            elif "charisma" in action_description.lower() or "persuade" in action_description.lower():
                ability_mod = (attributes.charisma - 10) // 2

            # Resolve action
            roll_result = GameEngineService.resolve_action(
                character=character,
                difficulty_class=dc,
                ability_modifier=ability_mod
            )

            # Format response
            response_text = f"""
<b>⚔️ Результат действия</b>

<b>Действие:</b> {action_description}

<b>🎲 Бросок:</b>
{roll_result['description']}
Всего: {roll_result['total']} (DC {dc})

<b>📖 Описание ГМ:</b>
{action_validation.get('narrative', 'Действие выполнено.')}
"""

            # Edit thinking message with result
            await thinking_msg.edit_text(response_text)

            # Log action to history
            action_log = ActionHistory(
                group_id=character.group_id,
                character_id=character.id,
                action_description=action_description,
                ai_response=action_validation.get('narrative', ''),
                dice_roll=dice_roll,
                success=roll_result['success'],
                consequences=str(roll_result)
            )
            db.add(action_log)
            db.commit()

            # Apply consequences based on success
            if roll_result['critical'] == 'failure':
                # Critical failure - take damage
                GameEngineService.apply_damage(character, 5, db)
                await message.answer(
                    "💀 <b>Критический провал!</b>\n\n"
                    f"{character.name} получает 5 урона!\n"
                    f"HP: {character.current_hp}/{character.max_hp}"
                )
            elif roll_result['critical'] == 'success':
                # Critical success
                await message.answer("🎉 <b>Критический успех!</b>\n\nДействие удалось идеально!")

            await state.clear()

        except Exception as e:
            logger.error(f"Error calling AI service: {e}")
            await thinking_msg.edit_text(
                format_error(f"Ошибка при обработке действия: {str(e)}")
            )
            await state.clear()

    except Exception as e:
        logger.error(f"Error in handle_action_description: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
        await state.clear()
    finally:
        db.close()
