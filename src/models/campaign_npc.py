from sqlalchemy import Column, Integer, BigInteger, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func

from src.models.base import Base


class CampaignNPC(Base):
    """Campaign-wide NPC identity and current whereabouts."""

    __tablename__ = "campaign_npcs"

    id = Column(Integer, primary_key=True)
    group_id = Column(BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    aliases = Column(Text)
    role = Column(String(255))
    description = Column(Text)
    personality = Column(Text)
    current_location = Column(String(500))
    is_hostile = Column(Boolean, nullable=False, default=False)
    is_alive = Column(Boolean, nullable=False, default=True)
    first_seen_at = Column(DateTime, nullable=False, default=func.now())
    last_seen_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<CampaignNPC {self.name} in group {self.group_id}>"


class NPCMemory(Base):
    """Persistent factual memories learned about an NPC during play."""

    __tablename__ = "npc_memories"

    id = Column(Integer, primary_key=True)
    npc_id = Column(Integer, ForeignKey("campaign_npcs.id", ondelete="CASCADE"), nullable=False, index=True)
    fact = Column(Text, nullable=False)
    source = Column(String(255))
    created_at = Column(DateTime, nullable=False, default=func.now())

    def __repr__(self):
        return f"<NPCMemory {self.npc_id}: {self.fact[:40]}>"
