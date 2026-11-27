import logging
from typing import Optional, List, Dict
from sqlalchemy.orm import Session
from sqlalchemy import and_
from src.models import Character, CharacterAttribute, CharacterAbility, CharacterSpell, Item, Inventory

XP_THRESHOLDS = {
    1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500, 6: 14000,
    7: 23000, 8: 34000, 9: 48000, 10: 64000, 11: 85000,
    12: 100000, 13: 120000, 14: 140000, 15: 165000,
    16: 195000, 17: 225000, 18: 265000, 19: 305000, 20: 355000,
}
CLASS_HIT_DICE = {
    "Варвар": 12, "Бард": 8, "Жрец": 8, "Друид": 8, "Боец": 10,
    "Монах": 8, "Паладин": 10, "Рейнджер": 10, "Плут": 8,
    "Чародей": 6, "Волшебник": 6, "Ведьмак": 8,
}

def xp_for_next_level(level: int) -> int | None:
    return None if level >= 20 else XP_THRESHOLDS.get(level + 1)

def add_experience(db: Session, character: Character, amount: int) -> tuple[int, list[int], list[str]]:
    """Award XP and unlock every reached level, preserving excess XP."""
    if amount <= 0 or character.level >= 20:
        return 0, [], []
    character.experience = (character.experience or 0) + amount
    levels = []
    while character.level < 20 and character.experience >= XP_THRESHOLDS[character.level + 1]:
        character.level += 1
        levels.append(character.level)
        attributes = db.query(CharacterAttribute).filter(
            CharacterAttribute.character_id == character.id
        ).first()
        con_modifier = (attributes.constitution - 10) // 2 if attributes else 0
        hit_die = CLASS_HIT_DICE.get(character.class_, 8)
        hp_gain = max(1, (hit_die // 2 + 1) + con_modifier)
        character.max_hp += hp_gain
        if character.current_hp > 0:
            character.current_hp += hp_gain
    unlocked_features = []
    if levels:
        from src.services.open5e_rules_service import grant_class_features_for_levels
        unlocked_features = grant_class_features_for_levels(db, character, levels)
    db.commit()
    return amount, levels, unlocked_features
from src.models.base import Base

logger = logging.getLogger(__name__)

class CharacterService:
    """Service for character creation, management, and updates"""

    @staticmethod
    def create_character(
        db: Session,
        user_id: int,
        group_id: int,
        name: str,
        race: str,
        class_: str,
        attributes: Dict[str, int],  # {str, dex, con, int, wis, cha}
        hp: int
    ) -> Optional[Character]:
        """Create a new character"""
        try:
            # Check if character with this name already exists for this user
            existing = db.query(Character).filter(
                and_(
                    Character.user_id == user_id,
                    Character.group_id == group_id,
                    Character.name == name
                )
            ).first()

            if existing:
                logger.warning(f"Character {name} already exists for user {user_id}")
                return None

            character = Character(
                user_id=user_id,
                group_id=group_id,
                name=name,
                race=race,
                class_=class_,
                max_hp=hp,
                current_hp=hp,
                level=1
            )

            db.add(character)
            db.flush()

            # Create attributes
            char_attr = CharacterAttribute(
                character_id=character.id,
                strength=attributes.get('str', 10),
                dexterity=attributes.get('dex', 10),
                constitution=attributes.get('con', 10),
                intelligence=attributes.get('int', 10),
                wisdom=attributes.get('wis', 10),
                charisma=attributes.get('cha', 10)
            )
            db.add(char_attr)
            db.commit()

            logger.info(f"Created character {character.id} - {name}")
            return character

        except Exception as e:
            logger.error(f"Error creating character: {e}")
            db.rollback()
            raise

    @staticmethod
    def get_character(db: Session, character_id: int) -> Optional[Character]:
        """Get character by ID with full details"""
        return db.query(Character).filter(Character.id == character_id).first()

    @staticmethod
    def get_character_full(db: Session, character_id: int) -> Optional[Dict]:
        """Get character with all related data"""
        character = db.query(Character).filter(Character.id == character_id).first()
        if not character:
            return None

        attributes = db.query(CharacterAttribute).filter(
            CharacterAttribute.character_id == character_id
        ).first()

        abilities = db.query(CharacterAbility).filter(
            CharacterAbility.character_id == character_id
        ).all()

        spells = db.query(CharacterSpell).filter(
            CharacterSpell.character_id == character_id
        ).all()

        inventory = db.query(Inventory).filter(
            Inventory.character_id == character_id
        ).all()

        return {
            "character": character,
            "attributes": attributes,
            "abilities": [a.ability_name for a in abilities],
            "spells": [s.spell_name for s in spells],
            "inventory": inventory
        }

    @staticmethod
    def get_user_character_in_group(db: Session, user_id: int, group_id: int) -> Optional[Character]:
        """Get character for specific user in specific group"""
        return db.query(Character).filter(
            and_(
                Character.user_id == user_id,
                Character.group_id == group_id
            )
        ).first()

    @staticmethod
    def delete_character(db: Session, character_id: int) -> bool:
        """Delete character and all related data"""
        try:
            character = db.query(Character).filter(Character.id == character_id).first()
            if not character:
                return False

            db.delete(character)
            db.commit()
            logger.info(f"Deleted character {character_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting character: {e}")
            db.rollback()
            raise

    @staticmethod
    def add_ability(db: Session, character_id: int, ability_name: str, ability_desc: str, is_racial: bool = False, is_class: bool = False):
        """Add ability to character"""
        try:
            ability = CharacterAbility(
                character_id=character_id,
                ability_name=ability_name,
                ability_description=ability_desc,
                is_racial=is_racial,
                is_class=is_class
            )
            db.add(ability)
            db.commit()
            return ability
        except Exception as e:
            logger.error(f"Error adding ability: {e}")
            db.rollback()
            raise

    @staticmethod
    def add_spell(db: Session, character_id: int, spell_name: str, spell_level: int) -> Optional[CharacterSpell]:
        """Add spell to character"""
        try:
            spell = CharacterSpell(
                character_id=character_id,
                spell_name=spell_name,
                spell_level=spell_level
            )
            db.add(spell)
            db.commit()
            return spell
        except Exception as e:
            logger.error(f"Error adding spell: {e}")
            db.rollback()
            raise

    @staticmethod
    def level_up(db: Session, character_id: int) -> Optional[Character]:
        """Level up character"""
        try:
            character = db.query(Character).filter(Character.id == character_id).first()
            if not character or character.level >= 20:
                return None

            character.level += 1
            # Fixed-average hit die advancement from D&D 5e.
            attributes = db.query(CharacterAttribute).filter(
                CharacterAttribute.character_id == character_id
            ).first()
            con_modifier = (attributes.constitution - 10) // 2 if attributes else 0
            hit_die = CLASS_HIT_DICE.get(character.class_, 8)
            hp_gain = max(1, hit_die // 2 + 1 + con_modifier)
            character.max_hp += hp_gain
            if character.current_hp > 0:
                character.current_hp += hp_gain
            from src.services.open5e_rules_service import grant_class_features_for_levels
            grant_class_features_for_levels(db, character, [character.level])

            db.commit()
            logger.info(f"Character {character_id} leveled up to {character.level}")
            return character

        except Exception as e:
            logger.error(f"Error leveling up character: {e}")
            db.rollback()
            raise
