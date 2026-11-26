from sqlalchemy import Column, Integer, String, Boolean, Numeric, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from src.models.base import Base

class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    item_type = Column(String(100), nullable=False)
    rarity = Column(String(50))
    weight = Column(Numeric(5, 2))
    description = Column(Text)
    damage_dice = Column(String(50))
    damage_type = Column(String(50))
    armor_class = Column(Integer)
    is_stackable = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

    def __repr__(self):
        return f"<Item {self.name} ({self.item_type})>"


class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    quantity = Column(Integer, default=1)
    is_equipped = Column(Boolean, default=False)
    slot = Column(String(50))
    created_at = Column(DateTime, default=func.now())

    def __repr__(self):
        return f"<Inventory {self.quantity}x {self.item_id} for character {self.character_id}>"
