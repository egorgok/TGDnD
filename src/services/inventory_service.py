import logging
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_
from src.models import Item, Inventory, Character

logger = logging.getLogger(__name__)

class InventoryService:
    """Service for inventory management"""

    @staticmethod
    def add_item(
        db: Session,
        character_id: int,
        item_id: int,
        quantity: int = 1,
        is_equipped: bool = False,
        slot: Optional[str] = None
    ) -> Optional[Inventory]:
        """Add item to character inventory"""
        try:
            # Check if item already in inventory
            existing = db.query(Inventory).filter(
                and_(
                    Inventory.character_id == character_id,
                    Inventory.item_id == item_id
                )
            ).first()

            if existing:
                existing.quantity += quantity
                db.commit()
                logger.info(f"Updated inventory item {item_id} for character {character_id}")
                return existing
            else:
                inventory = Inventory(
                    character_id=character_id,
                    item_id=item_id,
                    quantity=quantity,
                    is_equipped=is_equipped,
                    slot=slot
                )
                db.add(inventory)
                db.commit()
                logger.info(f"Added item {item_id} to character {character_id}")
                return inventory

        except Exception as e:
            logger.error(f"Error adding item: {e}")
            db.rollback()
            raise

    @staticmethod
    def remove_item(db: Session, character_id: int, item_id: int, quantity: int = 1) -> bool:
        """Remove item from inventory"""
        try:
            inventory = db.query(Inventory).filter(
                and_(
                    Inventory.character_id == character_id,
                    Inventory.item_id == item_id
                )
            ).first()

            if not inventory:
                logger.warning(f"Item {item_id} not found in character {character_id} inventory")
                return False

            inventory.quantity -= quantity
            if inventory.quantity <= 0:
                db.delete(inventory)
            db.commit()
            logger.info(f"Removed {quantity}x item {item_id} from character {character_id}")
            return True

        except Exception as e:
            logger.error(f"Error removing item: {e}")
            db.rollback()
            raise

    @staticmethod
    def get_inventory(db: Session, character_id: int) -> List[dict]:
        """Get all items in character inventory"""
        try:
            items = db.query(Inventory).filter(
                Inventory.character_id == character_id
            ).all()

            result = []
            for inv in items:
                item = db.query(Item).filter(Item.id == inv.item_id).first()
                result.append({
                    "item": item,
                    "quantity": inv.quantity,
                    "is_equipped": inv.is_equipped,
                    "slot": inv.slot
                })
            return result

        except Exception as e:
            logger.error(f"Error getting inventory: {e}")
            raise

    @staticmethod
    def equip_item(db: Session, character_id: int, item_id: int, slot: str) -> bool:
        """Equip item to slot"""
        try:
            inventory = db.query(Inventory).filter(
                and_(
                    Inventory.character_id == character_id,
                    Inventory.item_id == item_id
                )
            ).first()

            if not inventory:
                return False

            inventory.is_equipped = True
            inventory.slot = slot
            db.commit()
            logger.info(f"Equipped item {item_id} to slot {slot} for character {character_id}")
            return True

        except Exception as e:
            logger.error(f"Error equipping item: {e}")
            db.rollback()
            raise

    @staticmethod
    def unequip_item(db: Session, character_id: int, item_id: int) -> bool:
        """Unequip item"""
        try:
            inventory = db.query(Inventory).filter(
                and_(
                    Inventory.character_id == character_id,
                    Inventory.item_id == item_id
                )
            ).first()

            if not inventory:
                return False

            inventory.is_equipped = False
            inventory.slot = None
            db.commit()
            logger.info(f"Unequipped item {item_id} for character {character_id}")
            return True

        except Exception as e:
            logger.error(f"Error unequipping item: {e}")
            db.rollback()
            raise

    @staticmethod
    def get_equipped_items(db: Session, character_id: int) -> List[dict]:
        """Get all equipped items for character"""
        try:
            items = db.query(Inventory).filter(
                and_(
                    Inventory.character_id == character_id,
                    Inventory.is_equipped == True
                )
            ).all()

            result = []
            for inv in items:
                item = db.query(Item).filter(Item.id == inv.item_id).first()
                result.append({
                    "item": item,
                    "slot": inv.slot
                })
            return result

        except Exception as e:
            logger.error(f"Error getting equipped items: {e}")
            raise

    @staticmethod
    def create_item(
        db: Session,
        name: str,
        item_type: str,
        rarity: str = "common",
        weight: float = 0,
        description: str = "",
        damage_dice: Optional[str] = None,
        damage_type: Optional[str] = None,
        armor_class: Optional[int] = None,
        is_stackable: bool = False
    ) -> Optional[Item]:
        """Create new item in catalog"""
        try:
            existing = db.query(Item).filter(Item.name == name).first()
            if existing:
                logger.warning(f"Item {name} already exists")
                return existing

            item = Item(
                name=name,
                item_type=item_type,
                rarity=rarity,
                weight=weight,
                description=description,
                damage_dice=damage_dice,
                damage_type=damage_type,
                armor_class=armor_class,
                is_stackable=is_stackable
            )
            db.add(item)
            db.commit()
            logger.info(f"Created item {name}")
            return item

        except Exception as e:
            logger.error(f"Error creating item: {e}")
            db.rollback()
            raise
