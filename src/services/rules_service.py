import logging
import json
from typing import Optional, Dict, List
from sqlalchemy.orm import Session
from sqlalchemy import and_
from src.models import DnDRules

logger = logging.getLogger(__name__)

class RulesService:
    """Service for D&D 5e rules validation and reference"""

    # Base D&D 5e races with ability modifiers
    RACES = {
        "Human": {
            "ability_mods": {
                "str": 1, "dex": 1, "con": 1, "int": 1, "wis": 1, "cha": 1
            },
            "hp_bonus": 0,
            "starting_abilities": ["Versatility"]
        },
        "Elf": {
            "ability_mods": {"str": 0, "dex": 2, "con": 0, "int": 0, "wis": 0, "cha": 0},
            "hp_bonus": 0,
            "starting_abilities": ["Keen Senses", "Fey Ancestry", "Trance"]
        },
        "Dwarf": {
            "ability_mods": {"str": 0, "dex": 0, "con": 2, "int": 0, "wis": 1, "cha": 0},
            "hp_bonus": 1,
            "starting_abilities": ["Dwarven Resilience", "Dwarven Combat Training"]
        },
        "Halfling": {
            "ability_mods": {"str": 0, "dex": 2, "con": 0, "int": 0, "wis": 0, "cha": 1},
            "hp_bonus": 0,
            "starting_abilities": ["Lucky", "Brave", "Halfling Nimbleness"]
        },
        "Dragonborn": {
            "ability_mods": {"str": 2, "dex": 0, "con": 1, "int": 0, "wis": 0, "cha": 1},
            "hp_bonus": 0,
            "starting_abilities": ["Draconic Ancestry", "Breath Weapon"]
        },
        "Gnome": {
            "ability_mods": {"str": 0, "dex": 0, "con": 1, "int": 2, "wis": 0, "cha": 0},
            "hp_bonus": 0,
            "starting_abilities": ["Gnome Cunning"]
        },
        "Half-Elf": {
            "ability_mods": {"str": 0, "dex": 0, "con": 0, "int": 0, "wis": 0, "cha": 2},
            "hp_bonus": 0,
            "starting_abilities": ["Fey Ancestry", "Skill Versatility"]
        },
        "Half-Orc": {
            "ability_mods": {"str": 2, "dex": 0, "con": 1, "int": -2, "wis": 0, "cha": 0},
            "hp_bonus": 1,
            "starting_abilities": ["Menacing", "Relentless Endurance"]
        },
        "Tiefling": {
            "ability_mods": {"str": 0, "dex": 0, "con": 0, "int": 1, "wis": 0, "cha": 2},
            "hp_bonus": 0,
            "starting_abilities": ["Infernal Resistance", "Innate Spellcasting"]
        }
    }

    # Base D&D 5e classes
    CLASSES = {
        "Barbarian": {
            "hit_dice": "d12",
            "starting_hp": 12,
            "armor": ["light", "medium", "shields"],
            "weapons": "simple, martial",
            "starting_abilities": ["Rage", "Unarmored Defense"]
        },
        "Bard": {
            "hit_dice": "d8",
            "starting_hp": 8,
            "armor": ["light"],
            "weapons": "simple, hand crossbows, longswords, rapiers, shortswords",
            "starting_abilities": ["Spellcasting", "Bardic Inspiration"]
        },
        "Cleric": {
            "hit_dice": "d8",
            "starting_hp": 8,
            "armor": ["light", "medium", "heavy", "shields"],
            "weapons": "simple",
            "starting_abilities": ["Spellcasting", "Channel Divinity"]
        },
        "Druid": {
            "hit_dice": "d8",
            "starting_hp": 8,
            "armor": ["light", "medium"],
            "weapons": "clubs, daggers, darts, javelins, maces, quarterstaffs, scimitars, sickles, slings, spears",
            "starting_abilities": ["Spellcasting", "Wild Shape"]
        },
        "Fighter": {
            "hit_dice": "d10",
            "starting_hp": 10,
            "armor": ["all", "shields"],
            "weapons": "simple, martial",
            "starting_abilities": ["Fighting Style", "Second Wind"]
        },
        "Monk": {
            "hit_dice": "d8",
            "starting_hp": 8,
            "armor": ["none"],
            "weapons": "simple, shortswords",
            "starting_abilities": ["Unarmored Defense", "Martial Arts"]
        },
        "Paladin": {
            "hit_dice": "d10",
            "starting_hp": 10,
            "armor": ["all", "shields"],
            "weapons": "simple, martial",
            "starting_abilities": ["Divine Sense", "Lay on Hands"]
        },
        "Ranger": {
            "hit_dice": "d10",
            "starting_hp": 10,
            "armor": ["light", "medium"],
            "weapons": "simple, martial",
            "starting_abilities": ["Favored Enemy", "Natural Explorer"]
        },
        "Rogue": {
            "hit_dice": "d8",
            "starting_hp": 8,
            "armor": ["light"],
            "weapons": "simple hand crossbows, longswords, rapiers, shortswords",
            "starting_abilities": ["Expertise", "Sneak Attack"]
        },
        "Sorcerer": {
            "hit_dice": "d6",
            "starting_hp": 6,
            "armor": ["none"],
            "weapons": "simple",
            "starting_abilities": ["Spellcasting", "Sorcerous Origin"]
        },
        "Warlock": {
            "hit_dice": "d8",
            "starting_hp": 8,
            "armor": ["light"],
            "weapons": "simple",
            "starting_abilities": ["Spellcasting", "Otherworldly Patron"]
        },
        "Wizard": {
            "hit_dice": "d6",
            "starting_hp": 6,
            "armor": ["none"],
            "weapons": "simple",
            "starting_abilities": ["Spellcasting", "Arcane Recovery"]
        }
    }

    @staticmethod
    def validate_race(race: str) -> bool:
        """Check if race is valid"""
        return race in RulesService.RACES

    @staticmethod
    def validate_class(class_name: str) -> bool:
        """Check if class is valid"""
        return class_name in RulesService.CLASSES

    @staticmethod
    def get_race_info(race: str) -> Optional[Dict]:
        """Get race information and ability modifiers"""
        return RulesService.RACES.get(race)

    @staticmethod
    def get_class_info(class_name: str) -> Optional[Dict]:
        """Get class information"""
        return RulesService.CLASSES.get(class_name)

    @staticmethod
    def get_starting_hp(class_name: str) -> int:
        """Get starting HP for class"""
        class_info = RulesService.CLASSES.get(class_name)
        return class_info["starting_hp"] if class_info else 8

    @staticmethod
    def apply_ability_modifiers(base_attributes: Dict[str, int], race: str) -> Dict[str, int]:
        """Apply racial ability modifiers to base attributes"""
        race_info = RulesService.get_race_info(race)
        if not race_info:
            return base_attributes

        mods = race_info["ability_mods"]
        return {
            "str": base_attributes.get("str", 10) + mods.get("str", 0),
            "dex": base_attributes.get("dex", 10) + mods.get("dex", 0),
            "con": base_attributes.get("con", 10) + mods.get("con", 0),
            "int": base_attributes.get("int", 10) + mods.get("int", 0),
            "wis": base_attributes.get("wis", 10) + mods.get("wis", 0),
            "cha": base_attributes.get("cha", 10) + mods.get("cha", 0),
        }

    @staticmethod
    def get_all_races() -> List[str]:
        """Get list of all available races"""
        return list(RulesService.RACES.keys())

    @staticmethod
    def get_all_classes() -> List[str]:
        """Get list of all available classes"""
        return list(RulesService.CLASSES.keys())

    @staticmethod
    def cache_rules(db: Session, category: str, name: str, data: Dict, source: str = "dnd.su"):
        """Cache D&D rules in database"""
        try:
            existing = db.query(DnDRules).filter(
                and_(
                    DnDRules.rule_category == category,
                    DnDRules.rule_name == name
                )
            ).first()

            if existing:
                existing.rule_data = data
                existing.source = source
            else:
                rule = DnDRules(
                    rule_category=category,
                    rule_name=name,
                    rule_data=data,
                    source=source,
                    version="5e"
                )
                db.add(rule)
            db.commit()
        except Exception as e:
            logger.error(f"Error caching rules: {e}")
            db.rollback()
