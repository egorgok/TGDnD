from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import Group, GroupMember, User, GameSession, Location, Character
from src.services import GroupService
from src.utils import format_error, format_success
import logging

logger = logging.getLogger(__name__)
router = Router()


class GroupCreationStates(StatesGroup):
    """FSM states for group creation"""
    entering_title = State()
    entering_world_prompt = State()
    confirming = State()


class GroupJoinStates(StatesGroup):
    """FSM states for joining group"""
    entering_code = State()


@router.message(Command("create_group"))
async def create_group_command(message: types.Message, state: FSMContext):
    """Start group creation"""
    db: Session = SessionLocal()
    try:
        await message.answer(
            "🗺️ <b>Создание новой группы/кампании</b>\n\n"
            "Шаг 1: Введите название группы\n"
            "(Например: 'Забытые подземелья', 'Королевство Аулерона')"
        )
        await state.set_state(GroupCreationStates.entering_title)
    finally:
        db.close()


@router.message(GroupCreationStates.entering_title)
async def handle_group_title(message: types.Message, state: FSMContext):
    """Handle group title input"""
    title = message.text.strip()

    if not title or len(title) < 3 or len(title) > 100:
        await message.answer("❌ Название должно быть от 3 до 100 символов")
        return

    await state.update_data(title=title)
    await message.answer(
        f"<b>Название:</b> {title}\n\n"
        "Шаг 2: Опишите мир кампании\n"
        "(Например: 'Средневековое фэнтези с магией и драконами', 'Киберпанк 2250')"
    )
    await state.set_state(GroupCreationStates.entering_world_prompt)


@router.message(GroupCreationStates.entering_world_prompt)
async def handle_world_prompt(message: types.Message, state: FSMContext):
    """Handle world description input"""
    world_prompt = message.text.strip()

    if not world_prompt or len(world_prompt) < 10:
        await message.answer("❌ Описание должно быть не менее 10 символов")
        return

    data = await state.get_data()
    title = data.get("title")

    await state.update_data(world_prompt=world_prompt)

    confirmation_text = f"""
🗺️ <b>Подтверждение группы</b>

<b>Название:</b> {title}
<b>Описание мира:</b>
{world_prompt}

Создать группу?
"""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Создать", callback_data="confirm_create_group"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_create_group")
        ]
    ])

    await message.answer(confirmation_text, reply_markup=keyboard)
    await state.set_state(GroupCreationStates.confirming)


@router.callback_query(GroupCreationStates.confirming, F.data == "confirm_create_group")
async def confirm_create_group_callback(query: types.CallbackQuery, state: FSMContext):
    """Create group"""
    db: Session = SessionLocal()
    try:
        data = await state.get_data()
        title = data.get("title")
        world_prompt = data.get("world_prompt")

        # Use Telegram group ID or create unique ID
        group_id = query.message.chat.id if query.message.chat.type == "group" else query.from_user.id * 1000 + 1

        # Create group
        group = GroupService.create_group(
            db=db,
            group_id=group_id,
            title=title,
            gm_prompt=world_prompt,
            created_by_user_id=query.from_user.id
        )

        # Create starting location
        start_location = GroupService.create_location(
            db=db,
            group_id=group_id,
            name="Начало приключения",
            description=f"Вы начинаете своё приключение в мире: {world_prompt}",
            location_type="starting"
        )

        # Initialize game session
        game_session = GroupService.initialize_game_session(
            db=db,
            group_id=group_id,
            starting_location_id=start_location.id
        )

        await query.message.edit_text(
            format_success(f"✅ Группа <b>{title}</b> создана!\n\n")
            + f"ID группы: <code>{group_id}</code>\n"
            + "Используй /join_group для присоединения других игроков"
        )
        await state.clear()

    except Exception as e:
        logger.error(f"Error creating group: {e}")
        await query.message.edit_text(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()

    await query.answer()


@router.callback_query(GroupCreationStates.confirming, F.data == "cancel_create_group")
async def cancel_create_group_callback(query: types.CallbackQuery, state: FSMContext):
    """Cancel group creation"""
    await query.message.edit_text("❌ Создание группы отменено")
    await state.clear()
    await query.answer()


@router.message(Command("join_group"))
async def join_group_command(message: types.Message, state: FSMContext):
    """Start group joining process"""
    await message.answer(
        "🗺️ <b>Присоединение к группе</b>\n\n"
        "Введите ID группы (его даст вам создатель группы):"
    )
    await state.set_state(GroupJoinStates.entering_code)


@router.message(GroupJoinStates.entering_code)
async def handle_group_code(message: types.Message, state: FSMContext):
    """Handle group ID input"""
    db: Session = SessionLocal()
    try:
        group_id_text = message.text.strip()

        try:
            group_id = int(group_id_text)
        except ValueError:
            await message.answer("❌ ID должен быть числом")
            return

        # Check if group exists
        group = db.query(Group).filter(Group.id == group_id).first()

        if not group:
            await message.answer("❌ Группа не найдена. Проверьте ID")
            return

        # Add user to group
        member = GroupService.add_member(
            db=db,
            group_id=group_id,
            user_id=message.from_user.id,
            role="player"
        )

        if member:
            await message.answer(
                format_success(f"✅ Вы присоединились к группе <b>{group.title}</b>!")
                + f"\n\nОписание мира:\n{group.gm_prompt}"
            )
        else:
            await message.answer("❌ Не удалось присоединиться к группе")

        await state.clear()

    except Exception as e:
        logger.error(f"Error joining group: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("group_info"))
async def group_info_command(message: types.Message):
    """Show current group information"""
    db: Session = SessionLocal()
    try:
        # Get user's groups
        memberships = db.query(GroupMember).filter(
            GroupMember.user_id == message.from_user.id
        ).all()

        if not memberships:
            await message.answer(
                "❌ Вы не состоите ни в одной группе\n\n"
                "Используйте /create_group или /join_group"
            )
            return

        for membership in memberships:
            group = db.query(Group).filter(Group.id == membership.group_id).first()
            members = db.query(GroupMember).filter(
                GroupMember.group_id == membership.group_id
            ).all()

            members_count = len(members)
            player_count = len([m for m in members if m.role == "player"])

            info_text = f"""
<b>🗺️ {group.title}</b>

<b>ID:</b> <code>{group.id}</code>
<b>Роль:</b> {membership.role}
<b>Участники:</b> {members_count} (игроков: {player_count})

<b>Описание мира:</b>
{group.gm_prompt}
"""

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎮 Начать", callback_data=f"start_session_{group.id}")]
            ])

            await message.answer(info_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error getting group info: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.callback_query(F.data.startswith("start_session_"))
async def start_session_callback(query: types.CallbackQuery):
    """Start a game session in group"""
    db: Session = SessionLocal()
    try:
        group_id = int(query.data.replace("start_session_", ""))

        # Get game session
        session = db.query(GameSession).filter(
            GameSession.group_id == group_id
        ).first()

        if not session or not session.current_location_id:
            await query.answer("❌ Ошибка: сессия не инициализирована")
            return

        location = db.query(Location).filter(
            Location.id == session.current_location_id
        ).first()

        if not location:
            await query.answer("❌ Ошибка: локация не найдена")
            return

        session_text = f"""
🎮 <b>Сессия началась!</b>

📍 <b>Локация:</b> {location.name}
<b>Описание:</b> {location.description}

Используйте /action для выполнения действий
"""

        await query.message.edit_text(session_text)
        await query.answer()

    except Exception as e:
        logger.error(f"Error starting session: {e}")
        await query.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()
