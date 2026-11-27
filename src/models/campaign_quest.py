from sqlalchemy import Column, Integer, BigInteger, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from src.models.base import Base


class CampaignQuest(Base):
    """An NPC quest offer and its one-time completion/reward state."""

    __tablename__ = "campaign_quests"

    id = Column(Integer, primary_key=True)
    group_id = Column(BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    character_id = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True)
    npc_id = Column(Integer, ForeignKey("campaign_npcs.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    reward_xp = Column(Integer, nullable=False, default=100)
    status = Column(String(30), nullable=False, default="offered")
    created_at = Column(DateTime, nullable=False, default=func.now())
    completed_at = Column(DateTime)
