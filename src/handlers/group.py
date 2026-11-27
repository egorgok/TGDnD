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


@router.my_chat_member(F.chat.type.in_(["group", "supergroup"]))
async def bot_membership_changed(update: types.ChatMemberUpdated):
    """Remove a campaign from every user's selector when its Telegram group disappears."""
    new_status = update.new_chat_member.status
    db: Session = SessionLocal()
    try:
        if new_status in {"member", "administrator"}:
            group = db.query(Group).filter(Group.id == update.chat.id).first()
            if group is None:
                group = Group(
                    id=update.chat.id,
                    title=update.chat.title or "Групповой сеттинг",
                    group_type="campaign",
                    gm_prompt="Групповая кампания",
                    world_description=None,
                )
                db.add(group)
                db.flush()
            # The user who added the bot is an initial campaign member.
            user_id = update.from_user.id if update.from_user else None
            if user_id:
                user = db.query(User).filter(User.id == user_id).first()
                if user is None:
                    user = User(id=user_id, username=update.from_user.username,
                                full_name=update.from_user.full_name)
                    db.add(user)
                    db.flush()
                if not db.query(GroupMember.id).filter(
                    GroupMember.group_id == group.id,
                    GroupMember.user_id == user_id
                ).first():
                    db.add(GroupMember(group_id=group.id, user_id=user_id, role="gm"))
            db.commit()
            logger.info("Registered Telegram campaign %s (%s)", update.chat.id, group.title)
            return

        if new_status not in {"left", "kicked"}:
            return

        group = db.query(Group).filter(Group.id == update.chat.id).first()
        if not group:
            return

        # Explicitly clear active selectors; database FK ON DELETE SET NULL is
        # also configured, but this keeps legacy databases safe.
        db.query(User).filter(User.active_group_id == group.id).update(
            {User.active_group_id: None},
            synchronize_session=False
        )
        db.delete(group)
        db.commit()
        logger.info(
            "Removed campaign %s (%s) after bot left Telegram chat",
            update.chat.id,
            update.chat.title
        )
    except Exception:
        db.rollback()
        logger.exception("Could not remove campaign after bot was removed from chat")
    finally:
        db.close()


@router.message(F.chat.type.in_(["group", "supergroup"]))
async def group_message_handler(message: types.Message, state: FSMContext):
    """Handle messages in group chats"""
    # Bot added to group
    if message.new_chat_members:
        for member in message.new_chat_members:
            if member.is_bot:
                db: Session = SessionLocal()
                try:
                    group = db.query(Group).filter(Group.id == message.chat.id).first()
                    if group is None:
                        group = Group(
                            id=message.chat.id,
                            title=message.chat.title or "Групповой сеттинг",
                            group_type="campaign",
                            gm_prompt="Групповая кампания",
                            world_description=None,
                        )
                        db.add(group)
                        db.flush()
                    creator = db.query(User).filter(
                        User.id == message.from_user.id
                    ).first()
                    if creator is None:
                        creator = User(
                            id=message.from_user.id,
                            username=message.from_user.username,
                            full_name=message.from_user.full_name,
                        )
                        db.add(creator)
                        db.flush()
                    if not db.query(GroupMember).filter(
                        GroupMember.group_id == group.id,
                        GroupMember.user_id == creator.id
                    ).first():
                        db.add(GroupMember(
                            group_id=group.id,
                            user_id=creator.id,
                            role="gm"
                        ))
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("Failed to register Telegram group campaign")
                finally:
                    db.close()
                await message.answer(
                    "🗺️ <b>Добро пожаловать в D&D RPG Bot!</b>\n\n"
                    "Этот чат зарегистрирован как групповой сеттинг.\n"
                    "Откройте личный чат с ботом и выберите его в меню. "
                    "Первый игрок задаст описание мира.\n\n"
                    "Для ручной настройки также доступна команда /setup_campaign"
                )
                break

    # Telegram sends this event when a new participant joins the chat.
    for member in message.new_chat_members or []:
        if member.is_bot:
            continue
        db: Session = SessionLocal()
        try:
            group = db.query(Group).filter(Group.id == message.chat.id).first()
            if group:
                user = db.query(User).filter(User.id == member.id).first()
                if user is None:
                    user = User(
                        id=member.id,
                        username=member.username,
                        full_name=member.full_name,
                    )
                    db.add(user)
                    db.flush()
                if not db.query(GroupMember).filter(
                    GroupMember.group_id == group.id,
                    GroupMember.user_id == member.id
                ).first():
                    db.add(GroupMember(
                        group_id=group.id,
                        user_id=member.id,
                        role="player"
                    ))
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to register new Telegram group member")
        finally:
            db.close()


@router.message(Command("setup_campaign"))
async def setup_campaign_command(message: types.Message, state: FSMContext):
    """Setup campaign in group chat"""
    # Only works in group chats
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("❌ Эта команда работает только в групповых чатах")
        return

    db: Session = SessionLocal()
    try:
        logger.info(f"setup_campaign called in chat {message.chat.id} by user {message.from_user.id}")

        # Check if campaign already exists for this chat
        existing_group = db.query(Group).filter(Group.id == message.chat.id).first()
        if existing_group:
            await message.answer(f"ℹ️ Кампания <b>{existing_group.title}</b> уже настроена")
            return

        await message.answer(
            "🗺️ <b>Настройка кампании</b>\n\n"
            "Шаг 1: Введите название кампании\n"
            "(Например: 'Забытые подземелья', 'Королевство Аулерона')"
        )
        await state.set_state(GroupCreationStates.entering_title)
        # Store that we're in a group
        await state.update_data(is_group_chat=True, group_chat_id=message.chat.id, gm_user_id=message.from_user.id)
        logger.info(f"State set to entering_title for group {message.chat.id}")

    except Exception as e:
        logger.error(f"Error in setup_campaign: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {str(e)}")
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

Создать группу и групповой чат в Telegram?
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
    """Create group and Telegram chat"""
    db: Session = SessionLocal()
    try:
        data = await state.get_data()
        title = data.get("title")
        world_prompt = data.get("world_prompt")
        is_group_chat = data.get("is_group_chat", False)
        group_chat_id = data.get("group_chat_id")
        gm_user_id = data.get("gm_user_id")

        logger.info(f"Creating group: title={title}, is_group_chat={is_group_chat}, group_chat_id={group_chat_id}")

        # Use Telegram group ID or create unique ID
        if is_group_chat and group_chat_id:
            group_id = group_chat_id
            await query.message.answer(f"🗺️ <b>Создание кампании...</b>")
        else:
            # Personal group creation (deprecated flow, but keep for compatibility)
            group_id = abs(hash(f"{query.from_user.id}_{title}")) % (10**9)

        # Create group in DB
        group = GroupService.create_group(
            db=db,
            group_id=group_id,
            title=title,
            gm_prompt=world_prompt,
            created_by_user_id=gm_user_id or query.from_user.id
        )

        logger.info(f"Group created: id={group.id}, title={group.title}")

        # Add creator as GM
        member = GroupService.add_member(
            db=db,
            group_id=group_id,
            user_id=gm_user_id or query.from_user.id,
            role="gm"
        )

        logger.info(f"Creator added as GM: user_id={gm_user_id or query.from_user.id}")

        # Create starting location
        start_location = GroupService.create_location(
            db=db,
            group_id=group_id,
            name="Начало приключения",
            description=f"Вы начинаете своё приключение в мире: {world_prompt}",
            location_type="starting"
        )

        logger.info(f"Starting location created: id={start_location.id}")

        # Initialize game session
        game_session = GroupService.initialize_game_session(
            db=db,
            group_id=group_id,
            starting_location_id=start_location.id
        )

        logger.info(f"Game session initialized: id={game_session.id}")

        success_msg = f"✅ Кампания <b>{title}</b> создана!\n\n"
        if is_group_chat:
            success_msg += f"📱 Кампания настроена в этом чате\n"
        success_msg += f"ID кампании: <code>{group_id}</code>\n\n"
        success_msg += "Игроки могут присоединиться с помощью /join_group"

        await query.message.edit_text(format_success(success_msg))
        await state.clear()

    except Exception as e:
        logger.error(f"Error creating group: {e}", exc_info=True)
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

            # Count characters
            characters = db.query(Character).filter(
                Character.group_id == membership.group_id
            ).count()

            info_text = f"""
<b>🗺️ {group.title}</b>

<b>ID:</b> <code>{group.id}</code>
<b>Роль:</b> {membership.role}
<b>Участники:</b> {members_count} (игроков: {player_count})
<b>Персонажей:</b> {characters}

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
