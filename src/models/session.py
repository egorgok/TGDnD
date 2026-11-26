from sqlalchemy import Column, Integer, BigInteger, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.sql import func
from src.models.base import Base

class GameSession(Base):
    __tablename__ = "game_sessions"

    id = Column(Integer, primary_key=True)
    group_id = Column(BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, unique=True)
    current_location_id = Column(Integer, ForeignKey("locations.id"))
    is_active = Column(Boolean, default=True)
    turn_order = Column(Text)
    combat_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<GameSession for group {self.group_id}>"
