from sqlalchemy import Column, Integer, DateTime, ForeignKey, Boolean, String
from sqlalchemy.sql import func
from src.models.base import Base

class CombatEncounter(Base):
    __tablename__ = "combat_encounters"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"))
    enemy_name = Column(String(255))
    enemy_hp = Column(Integer)
    enemy_ac = Column(Integer)
    round = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<CombatEncounter {self.id} - {self.enemy_name}>"


class CombatParticipant(Base):
    __tablename__ = "combat_participants"

    id = Column(Integer, primary_key=True)
    encounter_id = Column(Integer, ForeignKey("combat_encounters.id", ondelete="CASCADE"), nullable=False)
    character_id = Column(Integer, ForeignKey("characters.id", ondelete="SET NULL"))
    initiative = Column(Integer)
    is_player_character = Column(Boolean, default=True)
    turn_order = Column(Integer)
    created_at = Column(DateTime, default=func.now())

    def __repr__(self):
        return f"<CombatParticipant {self.character_id} in encounter {self.encounter_id}>"
