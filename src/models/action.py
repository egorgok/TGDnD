from sqlalchemy import Column, Integer, BigInteger, DateTime, ForeignKey, Boolean, Text, String
from sqlalchemy.sql import func
from src.models.base import Base

class ActionHistory(Base):
    __tablename__ = "action_history"

    id = Column(Integer, primary_key=True)
    group_id = Column(BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    character_id = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    action_description = Column(Text, nullable=False)
    ai_response = Column(Text)
    dice_roll = Column(Integer)
    success = Column(Boolean)
    generated_image_url = Column(String(500))
    consequences = Column(Text)
    created_at = Column(DateTime, default=func.now())

    def __repr__(self):
        return f"<ActionHistory {self.id} - {self.character_id} in group {self.group_id}>"
