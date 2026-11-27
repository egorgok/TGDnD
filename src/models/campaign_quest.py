import json
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
    reward_gold = Column(Integer, nullable=False, default=0)
    reward_silver = Column(Integer, nullable=False, default=0)
    reward_copper = Column(Integer, nullable=False, default=0)
    reward_items_json = Column(Text, nullable=True)
    reward_spells_json = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="offered")
    created_at = Column(DateTime, nullable=False, default=func.now())
    completed_at = Column(DateTime)

    @property
    def reward_items(self):
        if not self.reward_items_json:
            return []
        try:
            data = json.loads(self.reward_items_json)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    @reward_items.setter
    def reward_items(self, value):
        self.reward_items_json = json.dumps(value or [], ensure_ascii=False)

    @property
    def reward_spells(self):
        if not self.reward_spells_json:
            return []
        try:
            data = json.loads(self.reward_spells_json)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    @reward_spells.setter
    def reward_spells(self, value):
        self.reward_spells_json = json.dumps(value or [], ensure_ascii=False)
