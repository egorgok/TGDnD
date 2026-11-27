from sqlalchemy import Column, Integer, ForeignKey
from src.models.base import Base


class CharacterCurrency(Base):
    """D&D coin purse, stored separately so existing character tables need no migration."""

    __tablename__ = "character_currency"

    character_id = Column(
        Integer,
        ForeignKey("characters.id", ondelete="CASCADE"),
        primary_key=True
    )
    gold = Column(Integer, nullable=False, default=0)
    silver = Column(Integer, nullable=False, default=0)
    copper = Column(Integer, nullable=False, default=0)
