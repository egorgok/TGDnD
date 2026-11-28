from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean, DateTime, ForeignKey
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from src.models.base import Base


class GameEntity(Base):
    """Persistent people and creatures encountered by a character."""

    __tablename__ = "game_entities"

    id = Column(Integer, primary_key=True)
    group_id = Column(BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    character_id = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    entity_type = Column(String(100), nullable=False, default="person")
    description = Column(Text)
    personality = Column(Text)
    current_hp = Column(Integer, nullable=False, default=10)
    max_hp = Column(Integer, nullable=False, default=10)
    armor_class = Column(Integer, nullable=False, default=10)
    attack_bonus = Column(Integer, nullable=False, default=0)
    damage_dice = Column(String(20), nullable=False, default="d4")
    ability_scores = Column(JSONB)
    distance_m = Column(Integer, nullable=False, default=5)
    is_hostile = Column(Boolean, nullable=False, default=False)
    is_alive = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<GameEntity {self.name} HP={self.current_hp}/{self.max_hp}>"
