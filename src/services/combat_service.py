import logging
from typing import Optional, List, Dict, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_
from src.models import CombatEncounter, CombatParticipant, Character, GameSession
import random

logger = logging.getLogger(__name__)

class CombatService:
    """Service for combat resolution and management"""

    @staticmethod
    def start_combat(
        db: Session,
        session_id: int,
        location_id: int,
        enemy_name: str,
        enemy_hp: int,
        enemy_ac: int
    ) -> Optional[CombatEncounter]:
        """Start a new combat encounter"""
        try:
            encounter = CombatEncounter(
                session_id=session_id,
                location_id=location_id,
                enemy_name=enemy_name,
                enemy_hp=enemy_hp,
                enemy_ac=enemy_ac,
                round=1,
                is_active=True
            )
            db.add(encounter)
            db.flush()
            logger.info(f"Combat started: {encounter.id} vs {enemy_name}")
            return encounter
        except Exception as e:
            logger.error(f"Error starting combat: {e}")
            db.rollback()
            raise

    @staticmethod
    def add_participant(
        db: Session,
        encounter_id: int,
        character_id: int,
        initiative: int,
        is_player: bool = True
    ) -> Optional[CombatParticipant]:
        """Add participant to combat"""
        try:
            participant = CombatParticipant(
                encounter_id=encounter_id,
                character_id=character_id,
                initiative=initiative,
                is_player_character=is_player
            )
            db.add(participant)
            db.commit()
            return participant
        except Exception as e:
            logger.error(f"Error adding combat participant: {e}")
            db.rollback()
            raise

    @staticmethod
    def roll_initiative(dexterity_modifier: int) -> int:
        """Roll initiative (d20 + DEX modifier)"""
        d20 = random.randint(1, 20)
        return d20 + dexterity_modifier

    @staticmethod
    def resolve_attack(
        attacker_bonus: int,
        target_ac: int,
        weapon_damage_dice: str = "d8"
    ) -> Dict:
        """Resolve attack roll and damage"""
        attack_roll = random.randint(1, 20)
        attack_total = attack_roll + attacker_bonus

        is_hit = attack_total >= target_ac
        is_critical = attack_roll == 20
        is_miss = attack_roll == 1

        damage = 0
        damage_text = ""

        if is_critical:
            # Double damage dice on critical hit
            damage = CombatService._roll_damage(weapon_damage_dice, 2) + attacker_bonus
            damage_text = f"🎉 КРИТИЧЕСКИЙ ХИТ! {damage} урона"
        elif is_hit:
            damage = CombatService._roll_damage(weapon_damage_dice) + attacker_bonus
            damage_text = f"✅ Попадание! {damage} урона"
        elif is_miss:
            damage_text = "💀 ПРОМАХ! Урона не нанесено"
        else:
            damage_text = "❌ Промах"

        return {
            "attack_roll": attack_roll,
            "attack_total": attack_total,
            "target_ac": target_ac,
            "is_hit": is_hit or is_critical,
            "is_critical": is_critical,
            "is_miss": is_miss,
            "damage": damage,
            "description": damage_text
        }

    @staticmethod
    def _roll_damage(damage_dice: str, multiplier: int = 1) -> int:
        """Roll damage dice"""
        if not damage_dice or 'd' not in damage_dice:
            return 1

        try:
            parts = damage_dice.split('d')
            num_dice = int(parts[0]) * multiplier
            sides = int(parts[1])
            return sum(random.randint(1, sides) for _ in range(num_dice))
        except:
            return 1

    @staticmethod
    def get_turn_order(db: Session, encounter_id: int) -> List[CombatParticipant]:
        """Get participants sorted by initiative"""
        participants = db.query(CombatParticipant).filter(
            CombatParticipant.encounter_id == encounter_id
        ).order_by(CombatParticipant.initiative.desc()).all()
        return participants

    @staticmethod
    def end_combat(db: Session, encounter_id: int) -> bool:
        """End combat encounter"""
        try:
            encounter = db.query(CombatEncounter).filter(
                CombatEncounter.id == encounter_id
            ).first()
            if encounter:
                encounter.is_active = False
                db.commit()
                logger.info(f"Combat {encounter_id} ended")
                return True
            return False
        except Exception as e:
            logger.error(f"Error ending combat: {e}")
            db.rollback()
            raise

    @staticmethod
    def next_round(db: Session, encounter_id: int) -> int:
        """Move to next combat round"""
        try:
            encounter = db.query(CombatEncounter).filter(
                CombatEncounter.id == encounter_id
            ).first()
            if encounter:
                encounter.round += 1
                db.commit()
                return encounter.round
            return 1
        except Exception as e:
            logger.error(f"Error advancing round: {e}")
            db.rollback()
            raise

    @staticmethod
    def apply_damage_to_enemy(
        db: Session,
        encounter_id: int,
        damage: int
    ) -> Tuple[int, bool]:
        """Apply damage to enemy in combat"""
        try:
            encounter = db.query(CombatEncounter).filter(
                CombatEncounter.id == encounter_id
            ).first()
            if not encounter:
                return encounter.enemy_hp, False

            encounter.enemy_hp = max(0, encounter.enemy_hp - damage)
            is_dead = encounter.enemy_hp <= 0

            if is_dead:
                encounter.is_active = False

            db.commit()
            return encounter.enemy_hp, is_dead
        except Exception as e:
            logger.error(f"Error applying damage: {e}")
            db.rollback()
            raise
