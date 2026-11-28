from sqlalchemy import Column, Integer, String, BigInteger, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.sql import func
from src.models.base import Base

class Character(Base):
    __tablename__ = "characters"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    group_id = Column(BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    race = Column(String(100), nullable=False)
    class_ = Column("class", String(100), nullable=False)
    background = Column(String(100))
    level = Column(Integer, default=1)
    max_hp = Column(Integer, nullable=False)
    current_hp = Column(Integer, nullable=False)
    experience = Column(Integer, default=0)
    death_successes = Column(Integer, nullable=False, default=0)
    death_failures = Column(Integer, nullable=False, default=0)
    lay_on_hands_remaining = Column(Integer)
    is_dead = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Character {self.name} ({self.race} {self.class_}) - Level {self.level}>"


class CharacterAttribute(Base):
    __tablename__ = "character_attributes"

    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, unique=True)
    strength = Column(Integer, nullable=False)
    dexterity = Column(Integer, nullable=False)
    constitution = Column(Integer, nullable=False)
    intelligence = Column(Integer, nullable=False)
    wisdom = Column(Integer, nullable=False)
    charisma = Column(Integer, nullable=False)

    def get_modifier(self, attribute_value):
        """Calculate D&D ability modifier from ability score"""
        return (attribute_value - 10) // 2

    def __repr__(self):
        return f"<CharacterAttribute for character {self.character_id}>"


class CharacterAbility(Base):
    __tablename__ = "character_abilities"

    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    ability_name = Column(String(255), nullable=False)
    ability_description = Column(Text)
    is_racial = Column(Boolean, default=False)
    is_class = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

    def __repr__(self):
        return f"<CharacterAbility {self.ability_name}>"


class CharacterSpell(Base):
    __tablename__ = "character_spells"

    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    spell_name = Column(String(255), nullable=False)
    spell_level = Column(Integer, nullable=False)
    is_prepared = Column(Boolean, default=False)
    slots_used = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())

    def __repr__(self):
        return f"<CharacterSpell {self.spell_name} (Level {self.spell_level})>"
