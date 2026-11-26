import logging
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_
from src.models import Group, GroupMember, User, GameSession, Location, LocationConnection
import random

logger = logging.getLogger(__name__)

class GroupService:
    """Service for group/campaign management"""

    @staticmethod
    def create_group(
        db: Session,
        group_id: int,
        title: str,
        gm_prompt: str,
        created_by_user_id: int
    ) -> Optional[Group]:
        """Create a new group/campaign"""
        try:
            group = Group(
                id=group_id,
                title=title,
                group_type="campaign",
                gm_prompt=gm_prompt
            )
            db.add(group)
            db.flush()

            # Add creator as GM
            member = GroupMember(
                group_id=group_id,
                user_id=created_by_user_id,
                role="gm"
            )
            db.add(member)
            db.commit()
            logger.info(f"Group created: {group_id} - {title}")
            return group
        except Exception as e:
            logger.error(f"Error creating group: {e}")
            db.rollback()
            raise

    @staticmethod
    def add_member(
        db: Session,
        group_id: int,
        user_id: int,
        role: str = "player"
    ) -> Optional[GroupMember]:
        """Add member to group"""
        try:
            # Check if already member
            existing = db.query(GroupMember).filter(
                and_(
                    GroupMember.group_id == group_id,
                    GroupMember.user_id == user_id
                )
            ).first()

            if existing:
                logger.warning(f"User {user_id} already in group {group_id}")
                return existing

            member = GroupMember(
                group_id=group_id,
                user_id=user_id,
                role=role
            )
            db.add(member)
            db.commit()
            logger.info(f"User {user_id} added to group {group_id}")
            return member
        except Exception as e:
            logger.error(f"Error adding group member: {e}")
            db.rollback()
            raise

    @staticmethod
    def remove_member(db: Session, group_id: int, user_id: int) -> bool:
        """Remove member from group"""
        try:
            member = db.query(GroupMember).filter(
                and_(
                    GroupMember.group_id == group_id,
                    GroupMember.user_id == user_id
                )
            ).first()

            if member:
                db.delete(member)
                db.commit()
                logger.info(f"User {user_id} removed from group {group_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error removing group member: {e}")
            db.rollback()
            raise

    @staticmethod
    def get_group_members(db: Session, group_id: int) -> List[GroupMember]:
        """Get all members of a group"""
        try:
            members = db.query(GroupMember).filter(
                GroupMember.group_id == group_id
            ).all()
            return members
        except Exception as e:
            logger.error(f"Error getting group members: {e}")
            raise

    @staticmethod
    def create_location(
        db: Session,
        group_id: int,
        name: str,
        description: str,
        location_type: str = "generic"
    ) -> Optional[Location]:
        """Create a location in the game world"""
        try:
            location = Location(
                group_id=group_id,
                name=name,
                description=description,
                location_type=location_type
            )
            db.add(location)
            db.commit()
            logger.info(f"Location created: {location.id} - {name}")
            return location
        except Exception as e:
            logger.error(f"Error creating location: {e}")
            db.rollback()
            raise

    @staticmethod
    def connect_locations(
        db: Session,
        from_location_id: int,
        to_location_id: int,
        description: str = "",
        is_bidirectional: bool = True
    ) -> bool:
        """Connect two locations"""
        try:
            connection = LocationConnection(
                from_location_id=from_location_id,
                to_location_id=to_location_id,
                description=description,
                is_bidirectional=is_bidirectional
            )
            db.add(connection)

            if is_bidirectional:
                reverse_connection = LocationConnection(
                    from_location_id=to_location_id,
                    to_location_id=from_location_id,
                    description=description or "Back",
                    is_bidirectional=False
                )
                db.add(reverse_connection)

            db.commit()
            logger.info(f"Connected locations {from_location_id} <-> {to_location_id}")
            return True
        except Exception as e:
            logger.error(f"Error connecting locations: {e}")
            db.rollback()
            raise

    @staticmethod
    def get_connected_locations(db: Session, location_id: int) -> List[tuple]:
        """Get all connected locations"""
        try:
            from src.models import Location as LocationModel
            connections = db.query(LocationConnection, LocationModel).join(
                LocationModel,
                LocationConnection.to_location_id == LocationModel.id
            ).filter(
                LocationConnection.from_location_id == location_id
            ).all()
            return connections
        except Exception as e:
            logger.error(f"Error getting connected locations: {e}")
            raise

    @staticmethod
    def initialize_game_session(
        db: Session,
        group_id: int,
        starting_location_id: int
    ) -> Optional[GameSession]:
        """Initialize a game session for the group"""
        try:
            # Check if session already exists
            existing = db.query(GameSession).filter(
                GameSession.group_id == group_id
            ).first()

            if existing:
                return existing

            session = GameSession(
                group_id=group_id,
                current_location_id=starting_location_id,
                is_active=True,
                combat_active=False
            )
            db.add(session)
            db.commit()
            logger.info(f"Game session created for group {group_id}")
            return session
        except Exception as e:
            logger.error(f"Error initializing game session: {e}")
            db.rollback()
            raise

    @staticmethod
    def move_to_location(
        db: Session,
        session_id: int,
        new_location_id: int
    ) -> bool:
        """Move game session to a new location"""
        try:
            session = db.query(GameSession).filter(
                GameSession.id == session_id
            ).first()

            if session:
                session.current_location_id = new_location_id
                db.commit()
                logger.info(f"Session {session_id} moved to location {new_location_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error moving to location: {e}")
            db.rollback()
            raise
