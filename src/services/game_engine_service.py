import random
import logging
from typing import Optional, Dict, List, Tuple
from sqlalchemy.orm import Session
from src.models import Character, CharacterAttribute, Item, Inventory

logger = logging.getLogger(__name__)

class GameEngineService:
    """Core game mechanics - action resolution, dice rolls, consequences"""

    @staticmethod
    def roll_d20() -> int:
        """Roll a d20 (1-20) for action checks"""
        return random.randint(1, 20)

    @staticmethod
    def roll_dice(dice_type: str) -> int:
        """Roll custom dice (d4, d6, d8, d10, d12, d20)"""
        dice_map = {
            "d4": 4,
            "d6": 6,
            "d8": 8,
            "d10": 10,
            "d12": 12,
            "d20": 20
        }
        num_dice = 1
        if 'd' in dice_type and dice_type[0].isdigit():
            parts = dice_type.split('d')
            num_dice = int(parts[0])
            sides = int(parts[1])
        else:
            sides = dice_map.get(dice_type, 20)

        total = sum(random.randint(1, sides) for _ in range(num_dice))
        return total

    @staticmethod
    def calculate_proficiency_bonus(level: int) -> int:
        """Calculate D&D proficiency bonus by level"""
        if level < 5:
            return 2
        elif level < 9:
            return 3
        elif level < 13:
            return 4
        elif level < 17:
            return 5
        else:
            return 6

    @staticmethod
    def calculate_armor_class(character: Character, attributes, db: Session) -> int:
        """Calculate AC from SRD armor rules, with class unarmored defense."""
        dexterity_modifier = (attributes.dexterity - 10) // 2 if attributes else 0
        wisdom_modifier = (attributes.wisdom - 10) // 2 if attributes else 0
        constitution_modifier = (attributes.constitution - 10) // 2 if attributes else 0
        equipped_items = (
            db.query(Item.item_type, Item.armor_class)
            .join(Inventory, Inventory.item_id == Item.id)
            .filter(
                Inventory.character_id == character.id,
                Inventory.is_equipped.is_(True),
                Item.armor_class.isnot(None)
            )
            .all()
        )
        if equipped_items:
            body_armor = next(
                ((item_type, armor_base) for item_type, armor_base in equipped_items
                 if (item_type or "").startswith("armor:")),
                None
            )
            has_shield = any(item_type == "shield" for item_type, _ in equipped_items)
            if body_armor:
                item_type, armor_base = body_armor
                category = item_type.partition(":")[2]
                if category == "light":
                    armor = int(armor_base) + dexterity_modifier
                elif category == "medium":
                    armor = int(armor_base) + min(dexterity_modifier, 2)
                else:
                    armor = int(armor_base)
                return armor + (2 if has_shield else 0)

        if character.class_ == "Варвар":
            return 10 + dexterity_modifier + constitution_modifier + (
                2 if equipped_items and any(item_type == "shield" for item_type, _ in equipped_items) else 0
            )
        if character.class_ == "Монах":
            return 10 + dexterity_modifier + wisdom_modifier
        if equipped_items and any(item_type == "shield" for item_type, _ in equipped_items):
            return 10 + dexterity_modifier + 2
        return 10 + dexterity_modifier

    @staticmethod
    def apply_damage(character: Character, damage_amount: int, db: Session) -> Tuple[bool, str]:
        """
        Apply damage to character
        Returns: (is_alive, message)
        """
        old_hp = character.current_hp
        character.current_hp = max(0, character.current_hp - damage_amount)
        db.commit()

        if character.current_hp <= 0:
            return False, f"{character.name} has died! HP: {old_hp} → 0"
        else:
            return True, f"{character.name} takes {damage_amount} damage! HP: {old_hp} → {character.current_hp}"

    @staticmethod
    def apply_healing(character: Character, healing_amount: int, db: Session) -> str:
        """Apply healing to character (capped at max_hp)"""
        old_hp = character.current_hp
        character.current_hp = min(character.max_hp, character.current_hp + healing_amount)
        db.commit()

        return f"{character.name} heals for {healing_amount} HP! HP: {old_hp} → {character.current_hp}/{character.max_hp}"

    @staticmethod
    def resolve_action(
        character: Character,
        difficulty_class: int,
        ability_modifier: int = 0,
        advantage: bool = False,
        disadvantage: bool = False
    ) -> Dict:
        """
        Resolve an action with d20 check
        Returns: {
            "roll": int,
            "modifier": int,
            "total": int,
            "success": bool,
            "critical": "success" | "failure" | None,
            "description": str
        }
        """
        if advantage:
            roll = max(self.roll_d20(), self.roll_d20())
            roll_type = "with advantage"
        elif disadvantage:
            roll = min(self.roll_d20(), self.roll_d20())
            roll_type = "with disadvantage"
        else:
            roll = self.roll_d20()
            roll_type = ""

        total = roll + ability_modifier
        success = total >= difficulty_class

        critical = None
        if roll == 20:
            critical = "success"
            description = f"🎲 CRITICAL SUCCESS! Rolled 20! {roll_type}"
        elif roll == 1:
            critical = "failure"
            description = f"💀 CRITICAL FAILURE! Rolled 1! {roll_type}"
        else:
            description = f"🎲 Rolled {roll} (DC {difficulty_class}) {roll_type}"

        return {
            "roll": roll,
            "modifier": ability_modifier,
            "total": total,
            "success": success,
            "critical": critical,
            "description": description
        }

    @staticmethod
    def calculate_xp_to_level(current_level: int) -> int:
        """Calculate XP needed to reach next level"""
        xp_thresholds = {
            1: 300,
            2: 900,
            3: 2700,
            4: 6500,
            5: 14000,
            6: 23000,
            7: 34000,
            8: 48000,
            9: 64000,
            10: 85000,
            11: 100000,
            12: 120000,
            13: 140000,
            14: 165000,
            15: 195000,
            16: 225000,
            17: 265000,
            18: 305000,
            19: 355000,
            20: 999999
        }
        return xp_thresholds.get(current_level, 999999)
