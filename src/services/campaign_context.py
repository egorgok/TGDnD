"""Resolve the campaign/character belonging to a Telegram update."""

from sqlalchemy.orm import Session

from src.models import Character, Group, GroupMember, User


def resolve_group(db: Session, user_id: int, chat) -> Group | None:
    """Use the Telegram group for group updates, otherwise the active DM setting."""
    if chat and chat.type in {"group", "supergroup"}:
        group = db.query(Group).filter(Group.id == chat.id).first()
        if not group:
            # Backfill chats where the bot was added before automatic campaign
            # registration was introduced.
            group = Group(
                id=chat.id,
                title=getattr(chat, "title", None) or "Групповой сеттинг",
                group_type="campaign",
                gm_prompt="Групповая кампания",
                world_description=None,
            )
            db.add(group)
            db.flush()
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            user = User(id=user_id)
            db.add(user)
            db.flush()
        membership = db.query(GroupMember.id).filter(
            GroupMember.group_id == group.id,
            GroupMember.user_id == user_id
        ).first()
        # A user may have joined the Telegram chat before the bot was added,
        # so there is no new_chat_members update to register them. Register
        # them lazily on the first game command.
        if membership is None:
            db.add(GroupMember(
                group_id=group.id,
                user_id=user_id,
                role="player"
            ))
            db.commit()
        return group

    user = db.query(User).filter(User.id == user_id).first()
    if user and user.active_group_id:
        group = db.query(Group).filter(Group.id == user.active_group_id).first()
        if group and db.query(GroupMember.id).filter(
            GroupMember.group_id == group.id,
            GroupMember.user_id == user_id
        ).first():
            return group

    # Backward compatibility for existing solo users that have not selected a
    # setting since active_group_id was introduced.
    return db.query(Group).join(GroupMember).filter(
        GroupMember.user_id == user_id,
        Group.group_type == "personal"
    ).first()


def resolve_character(db: Session, user_id: int, chat) -> Character | None:
    group = resolve_group(db, user_id, chat)
    if not group:
        return None
    return db.query(Character).filter(
        Character.user_id == user_id,
        Character.group_id == group.id
    ).first()
