from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, ForceReply
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import (
    Character, CharacterAttribute, CharacterAbility, CharacterSpell, Inventory, Item, ActionHistory, GameSession,
    Location, LocationConnection, CombatEncounter, CombatParticipant, GameEntity,
    CampaignNPC, NPCMemory, DnDRules, CharacterCurrency, CampaignQuest
)
from src.services import (
    AIService, GameEngineService, CharacterService, InventoryService,
    CombatService, GroupService
)
from src.services.open5e_rules_service import (
    build_dm_rules_context, build_creature_catalog, build_item_catalog,
    localize_catalog_item, is_open5e_catalog_item
)
from src.services.open5e_localization import canonical_item_name, localized_item_type
from src.services.character_service import add_experience, XP_THRESHOLDS
from src.utils import format_error
from src.services.campaign_context import resolve_character, resolve_group
import logging
import base64
import httpx
import re
import json
import html
import random
from datetime import datetime
from typing import Optional
from io import BytesIO

logger = logging.getLogger(__name__)
router = Router()


def _quest_reward_text(quest: CampaignQuest) -> str:
    rewards = [f"{int(quest.reward_xp or 0)} XP"]
    coins = []
    for field, label in (
        ("reward_gold", "зм"), ("reward_silver", "см"), ("reward_copper", "мм")
    ):
        amount = int(getattr(quest, field, 0) or 0)
        if amount:
            coins.append(f"{amount} {label}")
    if coins:
        rewards.append(", ".join(coins))
    items = getattr(quest, "reward_items", []) or []
    if items:
        item_parts = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            qty = _safe_int(it.get("quantity"), 1, 1, 99)
            item_parts.append(f"{name} x{qty}" if qty != 1 else name)
        if item_parts:
            rewards.append(", ".join(item_parts[:4]))
    spells = getattr(quest, "reward_spells", []) or []
    if spells:
        spell_parts = [str(s.get("name","")).strip() for s in spells if isinstance(s, dict) and str(s.get("name","")).strip()]
        if spell_parts:
            rewards.append("заклинания: " + ", ".join(spell_parts[:3]))
    return " + ".join(rewards)


def _grant_item_to_character(db: Session, character: Character, item_name: str, quantity: int = 1, description: str | None = None) -> tuple[Item, int]:
    quantity = max(1, min(int(quantity or 1), 99))
    canonical_name = canonical_item_name(item_name)
    source_item = db.query(Item).filter(Item.name.ilike(canonical_name)).first()
    if source_item is None:
        source_item = Item(
            name=canonical_name[:255],
            item_type="gear",
            description=(description or "")[:1000] or None,
            damage_dice=None,
            damage_type=None,
            armor_class=None,
            is_stackable=False,
        )
        db.add(source_item)
        db.flush()
    inv = db.query(Inventory).filter(
        Inventory.character_id == character.id,
        Inventory.item_id == source_item.id
    ).first()
    if inv:
        inv.quantity = max(1, (inv.quantity or 0) + quantity)
    else:
        is_armor = source_item.item_type.startswith("armor:") or source_item.item_type == "shield"
        is_weapon = source_item.item_type.startswith("weapon:")
        inv = Inventory(
            character_id=character.id,
            item_id=source_item.id,
            quantity=quantity,
            is_equipped=is_armor or is_weapon,
            slot="armor" if is_armor else "weapon" if is_weapon else None,
        )
        db.add(inv)
    db.flush()
    return source_item, quantity


def _credit_character_copper(db: Session, character: Character, copper_total: int) -> None:
    purse = db.query(CharacterCurrency).filter(
        CharacterCurrency.character_id == character.id
    ).first()
    if purse is None:
        purse = CharacterCurrency(character_id=character.id)
        db.add(purse)
        db.flush()
    total = (
        (purse.gold or 0) * 100 + (purse.silver or 0) * 10
        + (purse.copper or 0) + max(0, copper_total)
    )
    purse.gold, remainder = divmod(total, 100)
    purse.silver, purse.copper = divmod(remainder, 10)


def _apply_equipment_action(db: Session, character: Character, action: str) -> Optional[str]:
    """Apply explicit put-away/take-in-hand actions to the authoritative inventory."""
    text = action.casefold()
    inventory = InventoryService.get_inventory(db, character.id)
    if not inventory:
        return None
    commands = []
    put_match = re.search(
        r"(?:убрал|убираю|положил|кладу|поместил|помещаю)\s+(.+?)(?:\s+в\s+(?:рюкзак|сумку))?(?:\s+и\s+|[,.;]|$)",
        text
    )
    take_match = re.search(
        r"(?:взял|беру|возьму|взять|держу|экипир\w*)\s+(?:в\s+руки\s+)?(.+?)(?:\s+в\s+руки)?(?:\s+и\s+|[,.;]|$)",
        text
    )
    if put_match:
        commands.append((False, put_match.group(1)))
    if take_match:
        commands.append((True, take_match.group(1)))
    if not commands:
        return None
    applied = []
    for take_item, raw_requested in commands:
        requested = re.sub(r"[\"'«»]", "", raw_requested).strip()
        candidates = [
            row for row in inventory
            if row.get("item") and (
                requested in row["item"].name.casefold()
                or row["item"].name.casefold() in requested
            )
        ]
        if not candidates:
            continue
        selected = candidates[0]["item"]
        rows = db.query(Inventory).filter(
            Inventory.character_id == character.id,
            Inventory.item_id == selected.id
        ).all()
        if not take_item:
            for row in rows:
                row.is_equipped = False
            applied.append(f"• {selected.name} убран")
            continue
        item_type = (selected.item_type or "").casefold()
        slot = "armor" if item_type.startswith("armor:") or item_type == "shield" else (
            "weapon" if item_type.startswith("weapon:") else "hands"
        )
        for row in db.query(Inventory).filter(
            Inventory.character_id == character.id,
            Inventory.slot == slot
        ).all():
            row.is_equipped = False
        for row in rows:
            row.is_equipped = True
            row.slot = slot
        applied.append(f"✓ {selected.name} экипирован")
    return ", ".join(applied) if applied else None


def _is_inventory_position_phrase(name: str) -> bool:
    """Reject AI-created pseudo-items such as «в руки факел»."""
    normalized = re.sub(r"[\"'«»]", "", (name or "").casefold()).strip()
    return bool(re.match(
        r"^(?:в\s+руки|в\s+рюкзак|в\s+сумку|в\s+карман|надел(?:а)?|экипировал(?:а)?)\s+",
        normalized
    ))


XP_BY_CR = {
    "0": 10, "1/8": 25, "1/4": 50, "1/2": 100,
    **{str(cr): xp for cr, xp in enumerate(
        [200, 450, 700, 1100, 1800, 2300, 2900, 3900, 5000, 5900,
         7200, 8400, 10000, 11500, 13000, 15000, 18000, 20000, 22000,
         25000, 33000, 41000, 50000, 62000, 75000, 90000, 105000,
         120000, 135000, 155000],
        start=1
    )}
}


@router.message(Command("kirito228mango67"))
async def kirito_secret_command(message: types.Message):
    """Secretly grant 1 HP or revive a fallen character without changing max HP."""
    db: Session = SessionLocal()
    try:
        character = resolve_character(db, message.from_user.id, message.chat)
        if not character:
            await message.answer("❌ Персонаж не найден.")
            return

        character.max_hp = max(1, int(character.max_hp or 0))
        current_hp = int(character.current_hp or 0)
        character.current_hp = min(
            character.max_hp,
            1 if current_hp <= 0 else current_hp + 1,
        )
        if character.current_hp > 0:
            character.is_dead = False
            character.death_successes = 0
            character.death_failures = 0
        db.commit()
        await message.answer(
            f"✨ Секрет активирован. HP: {character.current_hp}/{character.max_hp}"
        )
    except Exception:
        db.rollback()
        logger.exception("Secret command failed")
        await message.answer("❌ Не удалось применить секретную команду.")
    finally:
        db.close()


def _entity_xp_reward(db: Session, entity: GameEntity, character_level: int) -> int:
    """Use official monster CR XP where available; modest fallback for homebrew foes."""
    normalized_name = _normalize_entity_name(entity.name)
    for (record,) in db.query(DnDRules.rule_data).filter(
        DnDRules.rule_category == "srd2024_monsters"
    ).all():
        if not isinstance(record, dict):
            continue
        official_name = _normalize_entity_name(str(record.get("name") or ""))
        if official_name != normalized_name:
            continue
        cr = record.get("challenge_rating", record.get("cr", ""))
        if isinstance(cr, dict):
            cr = cr.get("value") or cr.get("name") or ""
        xp = record.get("xp") or record.get("experience_points")
        try:
            if xp:
                return max(0, int(xp))
        except (TypeError, ValueError):
            pass
        cr_key = str(cr).strip().removesuffix(".0")
        if cr_key in XP_BY_CR:
            return XP_BY_CR[cr_key]
    return max(25, character_level * 25)


_NUMBER_WORDS_RU = {
    "ноль": 0, "один": 1, "одна": 1, "одного": 1, "одном": 1,
    "два": 2, "две": 2, "двух": 2, "три": 3, "трёх": 3, "трех": 3,
    "четыре": 4, "четырёх": 4, "четырех": 4, "пять": 5, "пяти": 5,
    "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8,
    "восьми": 8, "девять": 9, "девяти": 9, "десять": 10, "десяти": 10,
    "одиннадцать": 11, "двенадцать": 12, "полтора": 1.5, "полутора": 1.5,
    "пара": 2, "паре": 2
}


def _last_narrated_distance_feet(entity_name: str, recent_actions: list) -> Optional[float]:
    """Recover the first established position unless code has since moved the entity."""
    name = entity_name.casefold()
    if any(
        re.search(
            r"(подхожу|приближаюсь|подойти|иду к|направляюсь к|"
            r"бегу.{0,50}(?:на|к)|несусь.{0,50}(?:на|к)|"
            r"бросаюсь.{0,50}(?:на|к)|кидаюсь.{0,50}(?:на|к))",
            (
                entry.get("action_description", "") if isinstance(entry, dict)
                else entry.action_description or ""
            ).casefold()
        )
        for entry in recent_actions
    ):
        return None
    distance_pattern = re.compile(
        r"(?P<value>\d+(?:[.,]\d+)?(?:-[а-яё]+)?|[а-яё]+(?:-[а-яё]+)?)\s*"
        r"(?P<unit>фут\w*|ft\b|метр\w*|м\b)",
        re.IGNORECASE
    )
    for entry in recent_actions:
        text = (
            entry.get("ai_response", "") if isinstance(entry, dict)
            else entry.ai_response or ""
        )
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            if name not in sentence.casefold():
                continue
            match = distance_pattern.search(sentence)
            if not match:
                continue
            raw_value = match.group("value").replace(",", ".")
            try:
                value = float(raw_value)
            except ValueError:
                numeric_prefix = re.match(r"^\d+(?:\.\d+)?", raw_value)
                value = float(numeric_prefix.group(0)) if numeric_prefix else None
                if value is None:
                    value = _NUMBER_WORDS_RU.get(raw_value.casefold())
                if value is None and "-" in raw_value:
                    left, right = raw_value.casefold().split("-", 1)
                    left_value = _NUMBER_WORDS_RU.get(left)
                    right_value = _NUMBER_WORDS_RU.get(right)
                    if left_value is not None and right_value is not None:
                        value = left_value + right_value
            if value is None:
                continue
            unit = match.group("unit").casefold()
            return value * 3.28084 if unit.startswith("метр") or unit == "м" else value
    return None


def _remove_target_distance_claims(narrative: str, target_name: str) -> str:
    """Prevent contradictory model-authored distances from overriding stored position."""
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", narrative):
        if (
            target_name.casefold() in sentence.casefold()
            and re.search(
                r"\d+(?:[.,]\d+)?\s*(?:фут\w*|ft\b|метр\w*|м\b)|"
                r"(?:ноль|один|одна|два|две|три|четыре|пять|шесть|семь|"
                r"восемь|девять|десять|пара|полтора)\s+"
                r"(?:фут\w*|ft\b|метр\w*|м\b)",
                sentence.casefold()
            )
        ):
            continue
        kept.append(sentence)
    return " ".join(part.strip() for part in kept if part.strip())


def _sanitize_combat_narrative(narrative: str) -> str:
    """Keep tactical numbers and implementation details out of the DM prose."""
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", narrative or ""):
        lowered = sentence.casefold()
        if re.search(
            r"\d+(?:[.,]\d+)?\s*(?:фут\w*|ft\b|метр\w*|м\b)|"
            r"\b(?:кд|ac|armor class|hp|hit points|хп|хитов|хиты|"
            r"str|dex|con|int|wis|cha|strength|dexterity|constitution|"
            r"intelligence|wisdom|charisma|cr|challenge rating|"
            r"saving throw\w*|resistance\w*|immunity|vulnerability|"
            r"сопротивлени\w*|"
            r"уязвимост\w*|спасброс\w*|инициатив\w*|бонус атаки|"
            r"сила|ловкость|телосложение|интеллект|мудрость|харизма)\b"
            r"\s*[:=]?\s*[+\-]?\d+|"
            r"\b(?:armor class|класс защиты|hit points|очки здоровья|"
            r"save dc|сл)\b.{0,20}\d+|"
            r"\d+\s*(?:hp|хп|хитов)|\b\d*d\d+(?:[+-]\d+)?\b|"
            r"\bd20\s*\(",
            lowered
        ):
            continue
        if any(term in lowered for term in (
            "боевая система", "игровая система", "код разреш", "механика разреш",
            "система определит", "система проверит", "исход удара ещё не определён",
            "исход удара пока не ясен", "исход атаки определит",
            "класс защиты", "armor class", "damage resistance",
            "resistance", "saving throw", "immune", "immunity", "vulnerab",
            "сопротивлен", "иммунитет", "уязвим"
        )):
            continue
        kept.append(sentence.strip())
    return " ".join(sentence for sentence in kept if sentence)


def _entity_wound_description(entity: GameEntity) -> str:
    """Qualitative condition only; never expose a creature's exact HP."""
    if not entity.is_alive or entity.current_hp <= 0:
        return "повержен"
    fraction = entity.current_hp / max(1, entity.max_hp)
    if fraction >= 0.9:
        return "почти не задет"
    if fraction >= 0.6:
        return "заметно ранен"
    if fraction >= 0.3:
        return "тяжело ранен"
    return "едва держится на ногах"


def _action_check_ability(action: str) -> Optional[tuple[str, str]]:
    """Map common D&D situations to an ability check; luck is only a last resort."""
    text = action.casefold()
    checks = (
        ("сила", "сила", r"карабк|карабка|лез\w*|взбира\w*|поднима\w* по (?:верёвк|веревк)|"
                            r"спуска\w* по (?:верёвк|веревк)|поднять|сломать|вырвать|удержать|толкнуть"),
        ("ловкость", "акробатика", r"прыг\w*|кувырк|балансир|равновес|перепрыг|уклон\w*|"
                                   r"проскольз|пролез\w*|ловк\w* движ"),
        ("ловкость", "ловкость рук", r"карман|украст|спрятать предмет|ловкость рук|"
                                   r"быстро доста|незаметно доста"),
        ("ловкость", "скрытность", r"прокрасть|скрыт|тайно|незаметно|подкрасть"),
        ("ловкость", "взлом или обезвреживание", r"обезвредить|вскрыть|замок|ловушк"),
        ("телосложение", "выносливость", r"выдержать|вынослив|устал|изнур|долгий бег|"
                                      r"долго плыв|терпеть боль|яд|болезн"),
        ("мудрость", "внимательность", r"осмотр|осмотреть|огляд|заметить|внимани|прислуш|"
                                      r"поиск|найти след|увидеть скрыт"),
        ("мудрость", "проницательность", r"прочитать намерени|понять мотив|лож\w*|"
                                      r"чувств\w* обман|проницатель"),
        ("мудрость", "выживание", r"ориентир|след\w*|выжить в|развести костёр|найти еду|"
                                 r"найти воду|охот\w*|погода|природ\w*"),
        ("мудрость", "обращение с животными", r"успокоить животн|оседлать|дрессир|"
                                             r"понять животн"),
        ("интеллект", "понимание магии", r"разобраться в магии|понять маги|заклинани|"
                                       r"магическ|расшифровать|руна|чар\w*"),
        ("интеллект", "расследование", r"исследова|расследова|изучить механизм|"
                                     r"найти улику|пазл|загадк|логическ"),
        ("интеллект", "история или религия", r"вспомнить историю|историческ|древн\w*|"
                                           r"религи|бог\w*|ритуал"),
        ("харизма", "убеждение", r"убедить|уговорить|договор\w*|переговор"),
        ("харизма", "обман", r"обмануть|солгать|ложн\w*|выдать себя за"),
        ("харизма", "запугивание", r"запугать|угроз\w*|пригрозить"),
        ("харизма", "выступление", r"сыграть роль|выступить|спеть|сыграть музыку|"
                                  r"рассмешить|впечатлить публику"),
    )
    for ability, label, pattern in checks:
        if re.search(pattern, text):
            return ability, label
    uncertain_action = re.search(
        r"неизвест\w*|неожидан\w*|случайн\w*|на удачу|риск\w*|"
        r"непонят\w*|неясн\w*|непредсказуем\w*|что произойд\w*",
        text
    )
    return ("удача", "удача") if uncertain_action else None


async def _send_generated_image(send_photo, prompt: str, caption: str) -> bool:
    """Generate an image and send it without failing the game turn on image errors."""
    try:
        image_result = await AIService().generate_image(prompt=prompt)
        if isinstance(image_result, dict):
            image_b64 = image_result.get("b64_json")
            if image_b64:
                image_bytes = base64.b64decode(image_b64)
            elif image_result.get("url"):
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.get(image_result["url"])
                    response.raise_for_status()
                    image_bytes = response.content
            else:
                image_bytes = None
        else:
            image_bytes = base64.b64decode(image_result) if image_result else None
        if not image_bytes:
            logger.warning("Scene image generation returned empty payload")
            return False
        image = BufferedInputFile(image_bytes, filename="scene.png")
        await send_photo(photo=image, caption=caption[:900])
        return True
    except Exception:
        logger.exception("Could not generate or send scene image")
        return False


def _ensure_action_combat(db: Session, character: Character, target: GameEntity):
    """Start or reuse a combat encounter when the GM or player initiates hostilities."""
    session = db.query(GameSession).filter(
        GameSession.group_id == character.group_id
    ).first()
    if session is None:
        session = GameSession(
            group_id=character.group_id,
            is_active=True,
            combat_active=False
        )
        db.add(session)
        db.flush()

    encounter = db.query(CombatEncounter).filter(
        CombatEncounter.session_id == session.id,
        CombatEncounter.enemy_name == target.name,
        CombatEncounter.is_active.is_(True)
    ).first()
    if encounter:
        session.combat_active = True
        return session, encounter, False, None

    target.is_hostile = True
    encounter = CombatEncounter(
        session_id=session.id,
        location_id=session.current_location_id,
        enemy_name=target.name,
        enemy_hp=target.current_hp,
        enemy_ac=target.armor_class,
        round=1,
        is_active=True
    )
    db.add(encounter)
    db.flush()

    attributes = db.query(CharacterAttribute).filter(
        CharacterAttribute.character_id == character.id
    ).first()
    dexterity_modifier = (attributes.dexterity - 10) // 2 if attributes else 0
    player_initiative = CombatService.roll_initiative(dexterity_modifier)
    enemy_initiative = CombatService.roll_initiative(0)
    db.add_all([
        CombatParticipant(
            encounter_id=encounter.id,
            character_id=character.id,
            initiative=player_initiative,
            is_player_character=True
        ),
        CombatParticipant(
            encounter_id=encounter.id,
            character_id=None,
            initiative=enemy_initiative,
            is_player_character=False
        )
    ])
    session.combat_active = True
    session.turn_order = json.dumps({
        "encounter_id": encounter.id,
        "player_initiative": player_initiative,
        "enemy_initiative": enemy_initiative,
        "first": "player" if player_initiative >= enemy_initiative else "enemy"
    })
    return session, encounter, True, (player_initiative, enemy_initiative)


def _loot_from_explicit_action(action_description: str) -> list[dict]:
    """Fallback for simple explicit actions when the AI returns plain text."""
    match = re.search(
        r"\b(?:подобрать|подбираю|взять|беру|найти|нахожу|получить|получаю)\s+"
        r"(?:себе\s+|его\s+|ее\s+|её\s+)?([а-яёa-z0-9][а-яёa-z0-9 '-]{1,80})",
        action_description.lower()
    )
    if not match:
        return []

    name = re.split(r"\s+(?:и|чтобы|для|в|на|с)\s+|[,.;!?]", match.group(1))[0].strip()
    if len(name) < 2:
        return []
    if _is_inventory_position_phrase(name):
        return []
    return [{"name": name.capitalize(), "type": "предмет", "quantity": 1}]


def _safe_int(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def _normalize_entity_name(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", value.casefold()).strip()


def _official_creature_data(db: Session, key: str, name: str) -> Optional[dict]:
    """Resolve only 2024 SRD records; legacy keys are aliases, not legacy stats."""
    normalized_name = _normalize_entity_name(name)
    key_candidates = [key] if key else []
    if key and key.casefold().startswith("srd_"):
        # Existing entity rows may carry 2014-style keys. Resolve their canonical
        # index against 2024 data, never by reading the old 2014 stat block.
        key_candidates.append(key[4:])
    for category in ("srd2024_monsters",):
        records = db.query(DnDRules.rule_name, DnDRules.rule_data).filter(
            DnDRules.rule_category == category
        ).order_by(DnDRules.id).all()
        for rule_name, record in records:
            if not isinstance(record, dict):
                continue
            record_key = str(record.get("key") or rule_name.rpartition(":")[2]).strip()
            if key_candidates and any(
                record_key.casefold() == candidate.casefold()
                for candidate in key_candidates
            ):
                return record
            if not key and _normalize_entity_name(str(record.get("name") or "")) == normalized_name:
                return record
    return None


def _entity_open5e_key(entity: GameEntity) -> Optional[str]:
    match = re.search(
        r"\[open5e_key:([a-z0-9_-]+)\]",
        entity.description or "",
        re.IGNORECASE
    )
    return match.group(1) if match else None


def _entity_official_profile(db: Session, entity: GameEntity) -> Optional[dict]:
    if "[combat_profile:manual_human]" in (entity.description or "") or (
        "[combat_profile:homebrew]" in (entity.description or "")
    ):
        return {
            "max_hp": entity.max_hp,
            "armor_class": entity.armor_class,
            "attack_bonus": entity.attack_bonus,
            "damage_dice": entity.damage_dice,
        }
    record = _official_creature_data(
        db,
        _entity_open5e_key(entity) or "",
        entity.name
    )
    profile = _official_combat_profile(record) if record else None
    if profile:
        return profile

    archetype = _human_combat_archetype(
        entity.name, entity.entity_type, entity.description or ""
    )
    if (
        not archetype
        and entity.is_hostile
        and not re.search(
            r"гоблин|орк|дракон|монстр|чудовищ|звер|нежит|демон|дьявол|"
            r"тролл|огр|скелет|зомби|волк|паук|goblin|orc|dragon|monster|"
            r"beast|undead|fiend|troll|ogre|skeleton|zombie|wolf|spider",
            f"{entity.name} {entity.entity_type} {entity.description or ''}",
            re.IGNORECASE
        )
    ):
        archetype = "mysterious"
    if archetype:
        manual = _manual_human_profile(archetype)
        entity.max_hp = manual["max_hp"]
        entity.current_hp = min(entity.current_hp, entity.max_hp)
        entity.armor_class = manual["armor_class"]
        entity.attack_bonus = manual["attack_bonus"]
        entity.damage_dice = manual["damage_dice"]
        entity.ability_scores = manual["scores"]
        entity.description = (entity.description or "") + " [combat_profile:manual_human]"
        return {
            "max_hp": entity.max_hp,
            "armor_class": entity.armor_class,
            "attack_bonus": entity.attack_bonus,
            "damage_dice": entity.damage_dice,
        }
    if entity.is_hostile:
        # Older campaigns may contain hostile entities created before we
        # persisted a source marker. Preserve their stored sheet as explicit
        # homebrew instead of bricking combat after switching to SRD 2024.
        entity.max_hp = _safe_int(entity.max_hp, 10, 1, 500)
        entity.current_hp = min(
            _safe_int(entity.current_hp, entity.max_hp, 0, entity.max_hp),
            entity.max_hp
        )
        entity.armor_class = _safe_int(entity.armor_class, 12, 5, 30)
        entity.attack_bonus = _safe_int(entity.attack_bonus, 2, -5, 20)
        dice = str(entity.damage_dice or "1d6")
        if not re.fullmatch(r"\d{1,2}d(?:4|6|8|10|12)(?:[+-]\d{1,2})?", dice):
            dice = "1d6"
        entity.damage_dice = dice
        entity.description = (entity.description or "") + " [combat_profile:homebrew]"
        return {
            "max_hp": entity.max_hp,
            "armor_class": entity.armor_class,
            "attack_bonus": entity.attack_bonus,
            "damage_dice": entity.damage_dice,
        }
    return None


def _official_hidden_traits(record: Optional[dict]) -> dict:
    """Internal API mechanics for DM adjudication; never include in player output."""
    if not isinstance(record, dict):
        return {}
    fields = (
        "saving_throws", "damage_resistances", "damage_immunities",
        "damage_vulnerabilities", "condition_immunities"
    )
    return {field: record.get(field) for field in fields if record.get(field)}


def _official_combat_profile(record: dict) -> Optional[dict]:
    """Extract a complete, API-backed simple weapon-attack profile; no invented defaults."""
    try:
        max_hp = int(record.get("hit_points"))
    except (TypeError, ValueError):
        return None
    raw_ac = record.get("armor_class")
    if isinstance(raw_ac, list):
        raw_ac = next(
            (
                entry.get("value") or entry.get("ac")
                for entry in raw_ac if isinstance(entry, dict)
            ),
            None
        )
    elif isinstance(raw_ac, dict):
        raw_ac = raw_ac.get("value") or raw_ac.get("ac")
    try:
        armor_class = int(raw_ac)
    except (TypeError, ValueError):
        return None
    first_attack = next(
        (
            attack
            for action in record.get("actions", [])
            if isinstance(action, dict)
            for attack in (
                action.get("attacks", [])
                or ([action] if action.get("attack_bonus") is not None else [])
            )
            if isinstance(attack, dict)
        ),
        None
    )
    if not first_attack:
        return None
    try:
        attack_bonus = int(
            first_attack.get("to_hit_mod", first_attack.get("attack_bonus"))
        )
    except (TypeError, ValueError):
        return None
    damage_dice = ""
    attack_action = next(
        (
            action for action in record.get("actions", [])
            if isinstance(action, dict)
            and (
                action is first_attack
                or first_attack in (action.get("attacks") or [])
            )
        ),
        {}
    )
    damage_records = attack_action.get("damage") or []
    for damage_record in damage_records:
        if isinstance(damage_record, dict) and damage_record.get("damage_dice"):
            damage_dice = str(damage_record["damage_dice"])
            break
    if not damage_dice:
        match = re.search(
            r"\((\d+d\d+(?:\s*[+-]\s*\d+)?)\)",
            str(attack_action.get("desc") or ""),
            re.IGNORECASE
        )
        if match:
            damage_dice = re.sub(r"\s+", "", match.group(1))
    if not damage_dice and first_attack.get("damage_die_type"):
        damage_dice = (
            f"{_safe_int(first_attack.get('damage_die_count'), 1, 1, 10)}"
            f"d{str(first_attack['damage_die_type']).lower().lstrip('d')}"
        )
    if (
        max_hp < 1 or max_hp > 5000
        or armor_class < 5 or armor_class > 40
        or not -20 <= attack_bonus <= 40
        or not re.fullmatch(r"\d*d\d+(?:[+-]\d+)?", damage_dice)
    ):
        return None
    return {
        "max_hp": max_hp,
        "armor_class": armor_class,
        "attack_bonus": attack_bonus,
        "damage_dice": damage_dice,
    }


_CREATURE_ARCHETYPE_KEYS = {
    "разбойник": ("bandit",),
    "бандит": ("bandit",),
    "разбойники": ("bandit",),
    "bandit": ("bandit",),
    "страж": ("guard",),
    "стражник": ("guard",),
    "guard": ("guard",),
    "солдат": ("guard", "veteran"),
    "гоблин": ("goblin-warrior", "goblin-minion"),
    "гоблины": ("goblin-warrior", "goblin-minion"),
    "goblin": ("goblin-warrior", "goblin-minion"),
    "орк": ("orc",),
    "волк": ("wolf",),
    "скелет": ("skeleton",),
    "зомби": ("zombie",),
    "паук": ("giant-spider",),
}


_HUMAN_ABILITY_SCORES = {
    "commoner": {"strength": 10, "dexterity": 10, "constitution": 10,
                 "intelligence": 10, "wisdom": 10, "charisma": 10},
    "guard": {"strength": 13, "dexterity": 12, "constitution": 12,
              "intelligence": 10, "wisdom": 11, "charisma": 10},
    "mysterious": {"strength": 10, "dexterity": 10, "constitution": 10,
                   "intelligence": 10, "wisdom": 10, "charisma": 10},
}


def _human_combat_archetype(name: str, entity_type: str, description: str) -> Optional[str]:
    text = _normalize_entity_name(f"{name} {entity_type} {description}")
    if re.search(
        r"силуэт|тень|таинственн|неизвестн\w*\s+(?:личност|фигур)|"
        r"скрыт\w*\s+(?:фигур|человек)|mysterious|silhouette|shadowy figure",
        text
    ):
        return "mysterious"
    if re.search(
        r"страж|охранник|часовой|караул|городск\w*\s+страж|guard|watchman",
        text
    ):
        return "guard"
    if re.search(
        r"трактирщик|хозяин\w*\s+(?:постоял|трактир|таверн)|"
        r"крестьянин|горожанин|жител\w*|лавочник|торговец|безоружн\w*|"
        r"обычн\w*\s+человек|commoner|innkeeper|tavernkeeper|peasant|townsfolk",
        text
    ):
        return "commoner"
    return None


def _manual_human_profile(archetype: str) -> dict:
    if archetype == "guard":
        return {
            "max_hp": 11, "armor_class": 16, "attack_bonus": 3,
            "damage_dice": "1d6+1", "scores": _HUMAN_ABILITY_SCORES["guard"],
            "label": "стражник",
        }
    if archetype == "mysterious":
        return {
            "max_hp": 6, "armor_class": 12, "attack_bonus": 0,
            "damage_dice": "1d4", "scores": _HUMAN_ABILITY_SCORES["mysterious"],
            "label": "таинственная личность",
        }
    return {
        "max_hp": 4, "armor_class": 10, "attack_bonus": 2,
        "damage_dice": "1d4", "scores": _HUMAN_ABILITY_SCORES["commoner"],
        "label": "обычный житель",
    }


def _location_visit_key(scene_state: str) -> str:
    """A stable-ish location key: first line/sentence, normalized for comparison."""
    first_part = re.split(r"[\n.!?。]", (scene_state or "").strip(), maxsplit=1)[0]
    return _normalize_entity_name(first_part)[:180]


def _fail_quests_for_npc(db: Session, group_id: int, npc_name: str) -> list[str]:
    npc = db.query(CampaignNPC).filter(
        CampaignNPC.group_id == group_id,
        CampaignNPC.name.ilike(npc_name)
    ).first()
    if not npc:
        return []
    failed = db.query(CampaignQuest).filter(
        CampaignQuest.group_id == group_id,
        CampaignQuest.npc_id == npc.id,
        CampaignQuest.status == "offered"
    ).all()
    for quest in failed:
        quest.status = "failed"
    return [quest.title for quest in failed]


def _official_archetype_data(db: Session, name: str, entity_type: str) -> tuple[Optional[dict], Optional[str]]:
    """Find a matching creature archetype in the imported 2024 SRD."""
    text = _normalize_entity_name(f"{name} {entity_type}")
    for marker, keys in _CREATURE_ARCHETYPE_KEYS.items():
        normalized_marker = _normalize_entity_name(marker)
        marker_matches = (
            bool(re.search(rf"\b{re.escape(normalized_marker)}\b", text))
            if normalized_marker.isascii()
            else normalized_marker in text
        )
        if not marker_matches:
            continue
        for key in keys:
            record = _official_creature_data(db, key, "")
            if record:
                return record, str(record.get("key") or key)
    # A hostile unnamed humanoid is most safely represented by the imported
    # bandit profile rather than by AI-invented numbers.
    if any(marker in text.split() for marker in ("person", "humanoid", "человек", "гуманоид", "unknown", "неизвестный")):
        for key in ("bandit", "guard"):
            record = _official_creature_data(db, key, "")
            if record:
                return record, str(record.get("key") or key)
    return None, None


DAMAGE_TYPES_RU = {
    "bludgeoning": "дробящий", "дробящий": "дробящий",
    "piercing": "колющий", "колющий": "колющий",
    "slashing": "рубящий", "рубящий": "рубящий",
    "fire": "огонь", "огонь": "огонь",
    "cold": "холод", "холод": "холод",
    "acid": "кислота", "кислота": "кислота",
    "poison": "яд", "яд": "яд",
    "lightning": "электричество", "электричество": "электричество",
    "thunder": "звук", "звук": "звук",
    "force": "силовой", "силовой": "силовой",
    "necrotic": "некротический", "некротический": "некротический",
    "radiant": "излучение", "излучение": "излучение",
    "psychic": "психический", "психический": "психический",
}


def _choose_weapon(weapons: list[Item], action: str) -> Optional[Item]:
    normalized_action = _normalize_entity_name(action)
    if not weapons:
        return None

    hints = (
        (("коротк", "shortsword"), ("shortsword", "короткий меч")),
        (("длинн", "longsword"), ("longsword", "длинный меч")),
        (("меч", "sword", "рапир"), ("longsword", "shortsword", "rapier", "длинный меч", "короткий меч", "рапира")),
        (("арбал", "crossbow"), ("crossbow", "арбалет")),
        (("кинжал", "dagger"), ("dagger", "кинжал")),
        (("топор", "axe"), ("greataxe", "handaxe", "battleaxe", "топор")),
        (("лук", "bow"), ("longbow", "shortbow", "длинный лук", "короткий лук")),
        (("копь", "javelin", "spear"), ("javelin", "spear", "копьё", "копье")),
        (("булав", "mace"), ("mace", "булава")),
        (("молот", "hammer"), ("warhammer", "hammer", "молот")),
        (("посох", "staff"), ("quarterstaff", "staff", "посох")),
        (("дротик", "dart"), ("dart", "дротик")),
    )
    for markers, item_terms in hints:
        if any(marker in normalized_action for marker in markers):
            match = next(
                (
                    weapon for term in item_terms
                    for weapon in weapons
                    if term in _normalize_entity_name(weapon.name)
                ),
                None
            )
            if match:
                return match

    for weapon in weapons:
        if _normalize_entity_name(weapon.name) in normalized_action:
            return weapon
    return weapons[0]


def _is_improvised_weapon_action(action: str, carried_weapons: list[Item]) -> bool:
    """Detect an explicitly named non-catalog object so it doesn't use a carried sword."""
    normalized = _normalize_entity_name(action)
    object_markers = (
        "камн", "стул", "бутыл", "факел", "палк", "кирпич", "доск",
        "труб", "кружк", "чашк", "поднос", "сапог", "кулак", "руками",
        "самодельн", "чем то", "предметом", "обломком",
    )
    if not any(marker in normalized for marker in object_markers):
        return False
    return not any(
        _normalize_entity_name(weapon.name) in normalized
        for weapon in carried_weapons
        if weapon.name
    )


def _roll_entity_damage(dice: str, critical: bool = False) -> int:
    return _roll_entity_damage_detail(dice, critical=critical)[0]


def _roll_entity_damage_detail(dice: str, critical: bool = False) -> tuple[int, str]:
    match = re.fullmatch(r"(\d*)d(\d+)(?:\s*([+-])\s*(\d+))?", dice or "")
    if not match:
        return 1, "1"
    count, sides = int(match.group(1) or 1), int(match.group(2))
    if count < 1 or count > 10 or sides < 2 or sides > 100:
        return 1, "1"
    if critical:
        count = min(count * 2, 20)
    bonus = int(match.group(4) or 0)
    if match.group(3) == "-":
        bonus *= -1
    rolls = [GameEngineService.roll_dice(f"d{sides}") for _ in range(count)]
    total = max(0, sum(rolls) + bonus)
    detail = f"{count}d{sides} ({', '.join(map(str, rolls))})"
    if bonus:
        detail += f" {'+' if bonus > 0 else '-'} {abs(bonus)}"
    return total, detail


def _sanitize_custom_item(raw_item: dict) -> Optional[dict]:
    name = str(raw_item.get("name") or "").strip()[:255]
    if not name or _is_inventory_position_phrase(name):
        return None

    raw_type = str(raw_item.get("item_type") or "gear").casefold()
    if "shield" in raw_type or "щит" in raw_type:
        item_type = "shield"
        armor_class = 2
        damage_dice = None
    elif "armor" in raw_type or "брон" in raw_type or "доспех" in raw_type:
        category = "heavy" if "heavy" in raw_type or "тяж" in raw_type else (
            "medium" if "medium" in raw_type or "сред" in raw_type else "light"
        )
        item_type = f"armor:{category}"
        default_ac = {"light": 11, "medium": 13, "heavy": 16}[category]
        armor_class = _safe_int(raw_item.get("armor_class"), default_ac, 10, 18)
        damage_dice = None
    elif "weapon" in raw_type or "оруж" in raw_type:
        if "ranged" in raw_type or "дальн" in raw_type:
            item_type = "weapon:ranged"
        elif "improvised" in raw_type or "подруч" in raw_type:
            item_type = "weapon:improvised"
        else:
            item_type = "weapon:melee"
        armor_class = None
        # Homebrew weapons get one consistent, modest baseline. Official gear
        # never reaches this branch; its API damage remains authoritative.
        damage_dice = "1d4"
    else:
        item_type = "gear"
        armor_class = None
        damage_dice = None

    raw_damage_type = str(raw_item.get("damage_type") or "").strip().casefold()
    damage_type = DAMAGE_TYPES_RU.get(raw_damage_type, raw_damage_type[:50]) or None
    description = str(raw_item.get("description") or "Самодельный предмет.").strip()[:2000]
    if item_type == "weapon:ranged":
        raw_range = str(raw_item.get("range") or "")
        range_numbers = re.findall(r"\d+", raw_range)
        if not range_numbers:
            existing_range = re.search(
                r"(?:Range|Дальность):\s*(\d+)(?:/(\d+))?",
                description,
                flags=re.IGNORECASE
            )
            range_numbers = [
                value for value in existing_range.groups() if value
            ] if existing_range else []
        normal_range = _safe_int(range_numbers[0], 20, 5, 120) if range_numbers else 20
        long_range = (
            _safe_int(range_numbers[1], normal_range * 3, normal_range, 400)
            if len(range_numbers) > 1
            else min(normal_range * 3, 400)
        )
        description = re.sub(
            r"(?:Range|Дальность):\s*\d+(?:/\d+)?\s*(?:feet|фут(?:а|ов|ы))\.?",
            "",
            description,
            flags=re.IGNORECASE
        ).strip()
        description += f" Дальность: {normal_range}/{long_range} футов."
    return {
        "name": name,
        "item_type": item_type,
        "armor_class": armor_class,
        "damage_dice": damage_dice,
        "damage_type": damage_type,
        "description": description,
        "add_to_inventory": bool(raw_item.get("add_to_inventory", False)),
    }


def _complete_homebrew_spell_spec(action: str, raw_spell: dict, character) -> Optional[dict]:
    spellcasters = {"Бард", "Жрец", "Друид", "Чародей", "Волшебник", "Ведьмак"}
    half_casters = {"Паладин", "Рейнджер"}
    if (
        character.class_ not in spellcasters
        and not (character.class_ in half_casters and character.level >= 2)
    ):
        return None

    required_fields = ("name", "description", "casting_time", "range", "duration", "components")
    if any(not str(raw_spell.get(field) or "").strip() for field in required_fields):
        return None

    text = action.casefold()
    level_specified = bool(
        re.search(r"(?:уров\w*\s*[:№]?\s*[0-9]|level\s*[0-9])", text)
        or re.search(r"\b[0-9]\s*уров\w*", text)
        or re.search(r"\b(?:заговор|cantrip)\b", text)
    )
    range_specified = bool(
        re.search(r"\b\d+\s*(?:фут\w*|ft|метр\w*|м\b)", text)
        or any(marker in text for marker in ("касание", "touch"))
    )
    duration_specified = bool(
        re.search(r"\b\d+\s*(?:раунд\w*|round\w*|секунд\w*|second\w*|минут\w*|minute\w*|час\w*|hour\w*|дн\w*|day\w*)", text)
        or any(marker in text for marker in ("мгновенн", "instant", "постоянн", "концентрация"))
    )
    casting_specified = bool(
        any(marker in text for marker in (
            "время накладывания", "время наложения", "время сотворения", "бонусным действием",
            "реакцией", "одним действием", "за действие", "casting time",
            "cast as an action"
        ))
        or re.search(r"\bдействием\b", text)
    )
    components_specified = bool(
        any(marker in text for marker in (
            "вербаль", "соматич", "материаль", "v/s", "v,s", "v s", "vsm"
        ))
        or re.search(r"\bкомпонент\w*\s*[:=]\s*[vsm,\s]+\b", text)
        or re.search(r"\b(?:v|s|m)(?:\s*[,/]\s*(?:v|s|m))+\b", text)
    )
    effect_specified = bool(
        re.search(r"\d+d\d+", text)
        or re.search(r"(?:урон|леч\w*|damage|heal)\s*(?:[:=]\s*)?\d+", text)
        or re.search(r"(?:эффект|effect)\s*[:=]", text)
    )
    if not all((
        level_specified,
        range_specified,
        duration_specified,
        casting_specified,
        components_specified,
        effect_specified
    )):
        return None
    if not any(marker in text for marker in (
        "урон", "леч", "эффект", "damage", "heal", "спасброс", "состояние", "цель"
    )):
        return None

    try:
        level = int(raw_spell.get("level"))
    except (TypeError, ValueError):
        return None
    if level < 0 or level > 9:
        return None
    if character.class_ in half_casters:
        max_cast_level = min(5, character.level // 2)
    else:
        max_cast_level = min(9, (character.level + 1) // 2)
    if level > max_cast_level:
        return None
    damage_dice = str(raw_spell.get("damage_dice") or "").strip()
    if damage_dice:
        match = re.fullmatch(r"(\d*)d(4|6|8|10|12)", damage_dice)
        if not match:
            return None
        count, sides = int(match.group(1) or 1), int(match.group(2))
        max_count = 1 if level == 0 else min(10, level + 1)
        damage_cap = 10 if level == 0 else min(80, 8 * (level + 1))
        if count > max_count or count * sides > damage_cap:
            return None

    return {
        "name": str(raw_spell["name"]).strip()[:255],
        "level": level,
        "description": str(raw_spell["description"]).strip()[:3000],
        "casting_time": str(raw_spell["casting_time"]).strip()[:100],
        "range": str(raw_spell["range"]).strip()[:100],
        "duration": str(raw_spell["duration"]).strip()[:150],
        "components": str(raw_spell["components"]).strip()[:150],
        "damage_dice": damage_dice or None,
        "damage_type": str(raw_spell.get("damage_type") or "").strip()[:50] or None,
        "saving_throw": str(raw_spell.get("saving_throw") or "").strip()[:50] or None,
        "add_to_spellbook": bool(raw_spell.get("add_to_spellbook", True)),
    }


def _is_waiting_for_opponent(action: str) -> bool:
    return bool(re.search(
        r"(жду|ожидаю|подожд\w*|ждать|жд[её]м).{0,70}(противник|соперник|бой|поединок|враг)|"
        r"(противник|соперник|бой|поединок|враг).{0,70}(выйдет|появится|начн[её]тся)",
        action.lower()
    ))


def _is_party_invitation(action: str) -> bool:
    return bool(re.search(
        r"(приглаш\w*|позвал\w*|зову|пойд[её]м|пойти со мной|"
        r"идти со мной|состав\w*\s+компан|следуй за мной|"
        r"пойд[её]м вместе|пойд[её]м со мной)",
        action.casefold()
    ))


def _is_lay_on_hands_action(action: str) -> bool:
    return bool(re.search(
        r"возложени\w*\s+рук|lay\s+on\s+hands",
        action.casefold()
    ))


def _party_member_named_in_action(action: str, members: list[Character]) -> Optional[Character]:
    normalized = _normalize_entity_name(action)
    tokens = normalized.split()
    for member in members:
        if not member.name:
            continue
        member_tokens = [
            token for token in _normalize_entity_name(member.name).split()
            if len(token) >= 3
        ]
        if member_tokens and any(
            token in action_token or action_token in token
            for token in member_tokens for action_token in tokens
            if min(len(token), len(action_token)) >= 3
        ):
            return member
    return None


def _is_looking_around(action: str) -> bool:
    return bool(re.search(
        r"\b(осмотр\w*|осматрива\w*|огляд\w*|look around|look over the area|survey the area)\b|"
        r"(посмотр\w*|изуч\w*)\s+(?:вокруг|местност\w*|окрестност\w*)",
        action.casefold()
    ))


def _is_aggressive_action(action: str) -> bool:
    return bool(re.search(
        r"(атак\w*|напада\w*|удар\w*|бью|бить|руб\w*|реж\w*|кол\w*|"
        r"пронза\w*|поража\w*|сража\w*|strike\w*|attack\w*|slash\w*|stab\w*|hit\b|"
        r"стреля\w*|выстрел\w*|брос(?!аюсь)\w*|мет\w*)",
        action.casefold()
    ))


def _is_charge_action(action: str) -> bool:
    return bool(re.search(
        r"\b(бегу|несусь|мчусь|бросаюсь|кидаюсь|налетаю|заряжаю)\b"
        r".{0,50}\b(на|к|в сторону)\b",
        action.casefold()
    ))


def _charge_is_aimed_at(action: str, target_name: str) -> bool:
    if not _is_charge_action(action):
        return False
    normalized_action = _normalize_entity_name(action)
    normalized_target = _normalize_entity_name(target_name)
    if not normalized_target:
        return False
    name_tokens = [token for token in normalized_target.split() if len(token) >= 3]
    after_direction = re.search(
        r"\b(?:бегу|несусь|мчусь|бросаюсь|кидаюсь|налетаю|заряжаю)\b"
        r".{0,50}\b(?:на|к|в сторону)\b(.{1,70})",
        normalized_action
    )
    if not after_direction:
        return False
    target_segment = re.split(
        r"\b(?:и|вместе|который|которая|которые|но|затем)\b",
        after_direction.group(1),
        maxsplit=1
    )[0]
    target_tokens = target_segment.split()
    return any(
        target_token.startswith(name_token)
        for target_token in target_tokens
        for name_token in name_tokens
    )


def _is_arena_setting(setting: str) -> bool:
    return bool(re.search(r"арен|гладиатор|колиз|colosseum|gladiator", setting.lower()))


def _player_names_in_group(db: Session, group_id: int) -> set[str]:
    return {
        character.name.casefold().strip()
        for character in db.query(Character).filter(Character.group_id == group_id).all()
        if character.name
    }


@router.message(Command("inspect"))
async def inspect_entity_command(message: types.Message):
    """Inspect known people and creatures, optionally by name."""
    db: Session = SessionLocal()
    try:
        character = resolve_character(db, message.from_user.id, message.chat)
        if not character:
            await message.answer("Сначала создайте персонажа.")
            return

        npcs = db.query(CampaignNPC).filter(
            CampaignNPC.group_id == character.group_id
        ).order_by(CampaignNPC.last_seen_at.desc()).all()
        companions = db.query(Character).filter(
            Character.group_id == character.group_id,
            Character.id != character.id
        ).order_by(Character.name).all()
        query_name = (message.text or "").partition(" ")[2].strip()
        if query_name:
            companion = next(
                (
                    candidate for candidate in companions
                    if candidate.name.casefold() == query_name.casefold()
                ),
                None
            )
            if companion:
                attributes = db.query(CharacterAttribute).filter(
                    CharacterAttribute.character_id == companion.id
                ).first()
                armor_class = GameEngineService.calculate_armor_class(
                    companion, attributes, db
                )
                purse = db.query(CharacterCurrency).filter(
                    CharacterCurrency.character_id == companion.id
                ).first()
                inventory = InventoryService.get_inventory(db, companion.id)
                abilities = db.query(CharacterAbility).filter(
                    CharacterAbility.character_id == companion.id,
                    ~CharacterAbility.ability_name.startswith("__")
                ).all()
                spells = db.query(CharacterSpell).filter(
                    CharacterSpell.character_id == companion.id
                ).order_by(CharacterSpell.spell_level, CharacterSpell.spell_name).all()
                if attributes:
                    stats = (
                        f"СИЛ {attributes.strength} ({(attributes.strength - 10)//2:+d}) · "
                        f"ЛОВ {attributes.dexterity} ({(attributes.dexterity - 10)//2:+d}) · "
                        f"ТЕЛ {attributes.constitution} ({(attributes.constitution - 10)//2:+d})\n"
                        f"ИНТ {attributes.intelligence} ({(attributes.intelligence - 10)//2:+d}) · "
                        f"МДР {attributes.wisdom} ({(attributes.wisdom - 10)//2:+d}) · "
                        f"ХАР {attributes.charisma} ({(attributes.charisma - 10)//2:+d})"
                    )
                else:
                    stats = "Характеристики не указаны."
                inventory_text = "\n".join(
                    f"{'✓' if row['is_equipped'] else '•'} {html.escape(row['item'].name)} x{row['quantity']}"
                    for row in inventory if row.get("item")
                ) or "Пуст"
                ability_text = "\n".join(
                    f"• {html.escape(ability.ability_name)}"
                    for ability in abilities
                ) or "Нет"
                spell_text = "\n".join(
                    f"{'✓' if not (spell.slots_used or 0) else '•'} "
                    f"{html.escape(spell.spell_name)} "
                    f"({'заговор' if spell.spell_level == 0 else f'{spell.spell_level} ур.'})"
                    for spell in spells
                ) or "Нет"
                status = "☠️ Погиб" if companion.is_dead else (
                    "🩸 При смерти" if companion.current_hp <= 0 else "❤️ Здоров"
                )
                await message.answer(
                    f"<b>⚔️ {html.escape(companion.name)}</b>\n\n"
                    f"<b>Вид:</b> {html.escape(companion.race)}\n"
                    f"<b>Класс:</b> {html.escape(companion.class_)}\n"
                    f"<b>Предыстория:</b> {html.escape(companion.background or '—')}\n"
                    f"<b>Уровень:</b> {companion.level} · "
                    f"<b>Опыт:</b> {companion.experience or 0}\n"
                    f"<b>Состояние:</b> {status}\n"
                    f"<b>HP:</b> {companion.current_hp}/{companion.max_hp}\n"
                    f"<b>КД:</b> {armor_class}\n"
                    f"<b>Кошелёк:</b> {purse.gold if purse else 0} зм · "
                    f"{purse.silver if purse else 0} см · {purse.copper if purse else 0} мм\n\n"
                    f"<b>📊 Характеристики:</b>\n{stats}\n\n"
                    f"<b>🎒 Инвентарь:</b>\n{inventory_text}\n\n"
                    f"<b>✨ Особенности и владения:</b>\n{ability_text}\n\n"
                    f"<b>🔮 Заклинания:</b>\n{spell_text}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
                    ])
                )
                return
            npc = next(
                (candidate for candidate in npcs if candidate.name.casefold() == query_name.casefold()),
                None
            )
            if not npc:
                await message.answer("Такого персонажа или существа пока не встречали.")
                return
            memories = db.query(NPCMemory).filter(
                NPCMemory.npc_id == npc.id
            ).order_by(NPCMemory.created_at.asc()).all()
            quests = db.query(CampaignQuest).filter(
                CampaignQuest.npc_id == npc.id
            ).order_by(CampaignQuest.created_at.asc()).all()
            quest_text = "\n".join(
                f"• {html.escape(quest.title)} — "
                f"{'выполнено' if quest.status == 'completed' else 'провалено' if quest.status == 'failed' else 'активно'} "
                f"({_quest_reward_text(quest)})\n  {html.escape(quest.description)}"
                for quest in quests
            )
            status = "жив" if npc.is_alive else "погиб"
            visibility = "враждебен" if npc.is_hostile else "не враждебен"
            memory_text = "\n".join(
                f"• {html.escape(_sanitize_combat_narrative(memory.fact))}"
                for memory in memories
            )
            await message.answer(
                f"<b>{html.escape(npc.name)}</b> — {html.escape(npc.role or 'NPC')}\n"
                f"{html.escape(_sanitize_combat_narrative(npc.description or 'Подробности неизвестны.'))}\n"
                f"<b>Личность:</b> {html.escape(_sanitize_combat_narrative(npc.personality or 'Неизвестна'))}\n"
                f"<b>Последнее известное место:</b> "
                f"{html.escape(npc.current_location or 'Неизвестно')}\n\n"
                f"Состояние: {status}, {visibility}\n\n"
                f"<b>Задания:</b>\n{quest_text or 'Пока нет известных заданий.'}\n\n"
                f"<b>Что вы о нём знаете:</b>\n{memory_text or 'Пока никаких особых фактов.'}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
                ])
            )
        elif npcs or companions:
            lines = []
            if companions:
                lines.append("<b>🤝 Товарищи:</b>")
                for companion in companions:
                    status = "погиб" if companion.is_dead else (
                        "при смерти" if companion.current_hp <= 0 else "жив"
                    )
                    lines.append(
                        f"• {html.escape(companion.name)} — "
                        f"{html.escape(companion.race)} {html.escape(companion.class_)}, {status}"
                    )
                lines.append("")
            if npcs:
                lines.append("<b>Знакомые люди и существа:</b>")
            for npc in npcs:
                status = "жив" if npc.is_alive else "погиб"
                has_quest = db.query(CampaignQuest.id).filter(
                    CampaignQuest.npc_id == npc.id,
                    CampaignQuest.status == "offered"
                ).first()
                lines.append(
                    f"• {html.escape(npc.name)} — {html.escape(npc.role or 'NPC')}, "
                    f"{status}{', есть задание' if has_quest else ''}"
                )
            lines.append("\nПодробнее: <code>/inspect имя</code>")
            await message.answer(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
                ])
            )
        else:
            await message.answer(
                "Вы пока не встречали товарищей, людей или существ, которых можно осмотреть.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
                ])
            )
    except Exception:
        logger.exception("Failed to inspect entity")
        await message.answer("Не удалось осмотреть цель.")
    finally:
        db.close()


@router.callback_query(F.data == "menu_acquaintances")
async def inspect_menu_callback(query: types.CallbackQuery):
    """Open the same acquaintances list as /inspect from the main menu."""
    try:
        command_message = query.message.model_copy(update={
            "from_user": query.from_user,
            "text": "/inspect"
        })
        await inspect_entity_command(command_message)
        await query.answer()
    except Exception:
        logger.exception("Failed to open acquaintances from menu")
        await query.answer("Не удалось открыть список знакомых.", show_alert=True)


class ActionStates(StatesGroup):
    """FSM states for action system"""
    describing_action = State()
    waiting_for_resolution = State()


class CombatStates(StatesGroup):
    """FSM states for combat"""
    in_combat = State()
    choosing_attack = State()


class TravelStates(StatesGroup):
    """FSM states for travel"""
    choosing_destination = State()


@router.message(Command("action"))
async def action_command(message: types.Message, state: FSMContext):
    """Start action sequence"""
    db: Session = SessionLocal()
    try:
        default_group = resolve_group(db, message.from_user.id, message.chat)

        if not default_group:
            await message.answer("❌ Создайте персонажа командой /create_character")
            return

        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id,
            Character.group_id == default_group.id
        ).first()

        if not character:
            await message.answer(
                "❌ У вас нет персонажа\n\n"
                "Создайте персонажа командой /create_character"
            )
            return
        if character.current_hp <= 0:
            await message.answer(f"☠️ {character.name} погиб и больше не может действовать.")
            await state.clear()
            return

        await message.answer(
            f"⚔️ <b>Действие персонажа {character.name}</b>\n\n"
            "Опишите, что вы хотите сделать:",
            reply_markup=ForceReply(
                selective=False,
                input_field_placeholder="Опишите действие"
            )
        )
        await state.set_state(ActionStates.describing_action)

    except Exception as e:
        logger.error(f"Error in action_command: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(ActionStates.describing_action)
async def handle_action_description(message: types.Message, state: FSMContext):
    """Handle action description from player"""
    db: Session = SessionLocal()
    try:
        action_description = message.text.strip()

        if not action_description or len(action_description) < 3:
            await message.answer("❌ Описание действия слишком короткое. Попробуйте снова.")
            return

        # Get character
        character = resolve_character(db, message.from_user.id, message.chat)

        if not character:
            await message.answer(format_error("Персонаж не найден"))
            await state.clear()
            return
        if character.current_hp <= 0:
            await message.answer(
                f"☠️ <b>{character.name} погиб.</b>\n"
                "При 0 HP персонаж больше не может совершать действия."
            )
            await state.clear()
            return

        # Get group and world description
        from src.models import Group
        group = db.query(Group).filter(Group.id == character.group_id).first()
        world_desc = group.world_description if group else "Неизвестный мир"
        # Keep each PC's position independent. Other members' actions remain
        # shared context, but cannot relocate this character without their own
        # explicit participation.
        recent_actions = db.query(ActionHistory).filter(
            ActionHistory.group_id == character.group_id
        ).order_by(ActionHistory.created_at.desc()).limit(8).all()
        recent_actions.reverse()
        scene_state = ""
        personal_scene_action = db.query(ActionHistory).filter(
            ActionHistory.character_id == character.id,
            ActionHistory.group_id == character.group_id
        ).order_by(ActionHistory.created_at.desc()).first()
        if personal_scene_action:
            try:
                latest_state = json.loads(personal_scene_action.consequences or "{}")
                scene_state = latest_state.get("scene_state", "")
            except (json.JSONDecodeError, AttributeError):
                # Older action history stored free-form consequences.
                scene_state = ""

        arena_event_text = ""
        arena_opponent = None
        if _is_arena_setting(world_desc) and _is_waiting_for_opponent(action_description):
            active_opponent = db.query(GameEntity).filter(
                GameEntity.group_id == character.group_id,
                GameEntity.is_alive.is_(True),
                GameEntity.is_hostile.is_(True)
            ).first()
            if active_opponent:
                arena_opponent = active_opponent
                arena_opponent.distance_m = max(0, arena_opponent.distance_m - 3)
                arena_event_text = (
                    f"{arena_opponent.name} не заставляет себя ждать и сближается с вами. "
                    f"Между вами около {arena_opponent.distance_m} м."
                )
            else:
                used_names = {
                    name.casefold()
                    for (name,) in db.query(CampaignNPC.name).filter(
                        CampaignNPC.group_id == character.group_id
                    ).all()
                }
                candidates = ["Варек Каменный Кулак", "Сера Красный Шрам", "Драган Волк"]
                opponent_name = next(
                    (candidate for candidate in candidates if candidate.casefold() not in used_names),
                    f"Аренный боец {len(used_names) + 1}"
                )
                opponent_npc = CampaignNPC(
                    group_id=character.group_id,
                    name=opponent_name,
                    role="гладиатор",
                    description="Опытный боец, вышедший на арену сражаться.",
                    personality="Собранный и решительный; уважает достойного противника.",
                    current_location=scene_state or "Гладиаторская арена",
                    is_hostile=True,
                    is_alive=True
                )
                db.add(opponent_npc)
                arena_opponent = GameEntity(
                    group_id=character.group_id,
                    character_id=character.id,
                    name=opponent_name,
                    entity_type="гладиатор",
                    description=opponent_npc.description,
                    personality=opponent_npc.personality,
                    current_hp=22,
                    max_hp=22,
                    armor_class=14,
                    attack_bonus=4,
                    damage_dice="d8",
                    distance_m=8,
                    is_hostile=True,
                    is_alive=True
                )
                db.add(arena_opponent)
                scene_state = f"Гладиаторская арена. {opponent_name} только что вышел на арену; дистанция около 8 метров."
                arena_event_text = (
                    f"Ворота арены с грохотом открываются. На песок выходит "
                    f"{opponent_name}, готовый к бою."
                )
                db.commit()

        recent_context = [
            {
                "character": (
                    db.query(Character.name).filter(
                        Character.id == entry.character_id
                    ).scalar() or "Персонаж"
                ),
                "action": entry.action_description,
                "outcome": (entry.ai_response or "")[:500],
                "roll": entry.dice_roll
            }
            for entry in recent_actions
        ]
        entities = db.query(GameEntity).filter(
            GameEntity.group_id == character.group_id
        ).all()
        for entity in entities:
            npc = db.query(CampaignNPC).filter(
                CampaignNPC.group_id == character.group_id,
                CampaignNPC.name.ilike(entity.name)
            ).first()
            if npc is None:
                db.add(CampaignNPC(
                    group_id=character.group_id,
                    name=entity.name,
                    role=entity.entity_type,
                    description=entity.description,
                    personality=entity.personality,
                    current_location=scene_state[:500] or None,
                    is_hostile=entity.is_hostile,
                    is_alive=entity.is_alive
                ))
        db.flush()
        entity_context = [
            {
                "name": entity.name,
                "type": entity.entity_type,
                "description": re.sub(
                    r"\s*\[open5e_key:[a-z0-9_-]+\]\s*",
                    "",
                    entity.description or "",
                    flags=re.IGNORECASE
                ),
                "open5e_key": _entity_open5e_key(entity),
                "api_traits": _official_hidden_traits(
                    _official_creature_data(
                        db,
                        _entity_open5e_key(entity) or "",
                        entity.name
                    )
                ),
                "personality": entity.personality,
                "ability_scores": entity.ability_scores,
                "hp": f"{entity.current_hp}/{entity.max_hp}",
                "ac": entity.armor_class,
                "attack_bonus": entity.attack_bonus,
                "damage_dice": entity.damage_dice,
                "hostile": entity.is_hostile,
                # Stored position is authoritative; narrative history can contain
                # outdated or contradictory distances.
                "distance_ft": round(entity.distance_m * 3.28084, 1),
                "alive": entity.is_alive
            }
            for entity in entities
        ]
        campaign_npcs = db.query(CampaignNPC).filter(
            CampaignNPC.group_id == character.group_id
        ).order_by(CampaignNPC.last_seen_at.desc()).limit(30).all()
        existing_npc_names = {npc.name.casefold() for npc in campaign_npcs}
        npc_context = []
        party_characters = db.query(Character).filter(
            Character.group_id == character.group_id
        ).order_by(Character.name).all()
        player_names = _player_names_in_group(db, character.group_id)
        party_name_to_character = {
            member.name.casefold().strip(): member
            for member in party_characters
            if member.name and member.id != character.id
        }
        party_members_context = [
            {
                "name": member.name,
                "race": member.race,
                "class": member.class_,
                "hp": f"{member.current_hp}/{member.max_hp}",
                "alive": not member.is_dead and member.current_hp > 0,
                "is_self": member.id == character.id,
            }
            for member in party_characters
        ]
        lay_on_hands_target = None
        if _is_lay_on_hands_action(action_description):
            named_target = _party_member_named_in_action(
                action_description, party_characters
            )
            if named_target:
                lay_on_hands_target = named_target
            elif re.search(r"\b(себя|себе|я сам|я сама|self)\b", action_description.casefold()):
                lay_on_hands_target = character
            else:
                wounded_allies = [
                    member for member in party_characters
                    if member.id != character.id
                    and not member.is_dead
                    and member.current_hp < member.max_hp
                ]
                if len(wounded_allies) == 1:
                    lay_on_hands_target = wounded_allies[0]
                elif character.current_hp < character.max_hp:
                    lay_on_hands_target = character
        other_party_members = [m for m in party_characters if m.id != character.id]
        party_invitation = (
            _is_party_invitation(action_description) and bool(other_party_members)
        )
        invitation_target = None
        if party_invitation:
            normalized_action = _normalize_entity_name(action_description)
            for member in party_characters:
                if member.id == character.id or not member.name:
                    continue
                name_tokens = [
                    token for token in _normalize_entity_name(member.name).split()
                    if len(token) >= 3
                ]
                if name_tokens and any(
                    token in normalized_action.split() for token in name_tokens
                ):
                    invitation_target = member
                    break
            if invitation_target is None:
                if len(other_party_members) == 1:
                    invitation_target = other_party_members[0]
        for npc in campaign_npcs:
            memories = db.query(NPCMemory).filter(
                NPCMemory.npc_id == npc.id
            ).order_by(NPCMemory.created_at.asc()).limit(8).all()
            npc_context.append({
                "name": npc.name,
                "aliases": npc.aliases,
                "role": npc.role,
                "description": npc.description,
                "personality": npc.personality,
                "current_location": npc.current_location,
                "hostile": npc.is_hostile,
                "alive": npc.is_alive,
                "memories": [memory.fact for memory in memories],
                "quests": [
                    {
                        "title": quest.title,
                        "description": quest.description,
                        "reward_xp": quest.reward_xp,
                        "reward_gold": quest.reward_gold,
                        "reward_silver": quest.reward_silver,
                        "reward_copper": quest.reward_copper,
                        "reward_items": quest.reward_items,
                        "status": quest.status
                    }
                    for quest in db.query(CampaignQuest).filter(
                        CampaignQuest.npc_id == npc.id
                    ).order_by(CampaignQuest.created_at.asc()).all()
                ]
            })
        active_quest_ids_at_start = [
            quest_id for (quest_id,) in db.query(CampaignQuest.id).filter(
                CampaignQuest.group_id == character.group_id,
                CampaignQuest.status == "offered"
            ).all()
        ]

        # Show "thinking" message
        thinking_msg = await message.answer("🤔 ДМ думает...")

        try:
            purse = db.query(CharacterCurrency).filter(
                CharacterCurrency.character_id == character.id
            ).first()
            # Roll before the DM adjudicates the action so the result can affect
            # the narrative and consequences.
            is_declared_attack = _is_aggressive_action(action_description)
            if not is_declared_attack and _is_charge_action(action_description):
                is_declared_attack = any(
                    _charge_is_aimed_at(action_description, known_name)
                    for known_name in player_names
                    | {
                        str(entity.get("name") or "").casefold()
                        for entity in entity_context
                        if entity.get("name")
                    }
                )
            lay_on_hands_action = _is_lay_on_hands_action(action_description)
            ability_check = (
                None if lay_on_hands_action
                else _action_check_ability(action_description)
            )
            is_luck_check = ability_check is not None and ability_check[0] == "удача"
            check_total = None
            check_result = None
            attributes = db.query(CharacterAttribute).filter(
                CharacterAttribute.character_id == character.id
            ).first()
            ability_modifiers = {}
            if attributes:
                for key, field in (
                    ("сила", "strength"), ("ловкость", "dexterity"),
                    ("телосложение", "constitution"), ("интеллект", "intelligence"),
                    ("мудрость", "wisdom"), ("харизма", "charisma")
                ):
                    ability_modifiers[key] = (int(getattr(attributes, field) or 10) - 10) // 2
            proficiency_bonus = GameEngineService.calculate_proficiency_bonus(character.level)
            if ability_check and not is_declared_attack:
                ability_key, check_label = ability_check
                ability_map = {
                    "сила": "strength", "ловкость": "dexterity",
                    "телосложение": "constitution", "интеллект": "intelligence",
                    "мудрость": "wisdom", "харизма": "charisma",
                }
                modifier = 0
                if attributes:
                    if ability_key != "удача":
                        score = getattr(attributes, ability_map.get(ability_key, ""), 10)
                        modifier = (int(score or 10) - 10) // 2
                        if ability_key in {
                            "сила", "ловкость", "телосложение",
                            "интеллект", "мудрость", "харизма"
                        }:
                            modifier += proficiency_bonus
                d20_roll = GameEngineService.roll_d20()
                check_total = d20_roll + modifier
                if ability_key == "удача":
                    check_result = (
                        f"Проверка удачи (d20): выпало {d20_roll}. "
                        "Оцени неизвестную ситуацию по результату: 1 — крайне плохо, "
                        "20 — исключительно благоприятно."
                    )
                else:
                    check_result = f"{check_label} ({ability_key}), итог {check_total}"
            # The combat resolver rolls its own attack die after target/range are known.
            # Do not feed the DM a misleading generic check roll for an attack.
            if is_declared_attack:
                d20_roll = None
            elif lay_on_hands_action:
                d20_roll = None
            elif not ability_check:
                d20_roll = GameEngineService.roll_d20()

            # Call AI to get action result
            ai_result = await AIService.process_action(
                character_name=character.name,
                action_description=action_description,
                world_setting=world_desc,
                d20_roll=d20_roll,
                check_result=check_result,
                ability_modifiers=ability_modifiers,
                proficiency_bonus=proficiency_bonus,
                scene_state=scene_state,
                recent_actions=recent_context,
                known_entities=entity_context,
                known_npcs=npc_context,
                party_members=party_members_context,
                rules_context=build_dm_rules_context(db, character, action_description),
                creature_catalog=build_creature_catalog(db),
                item_catalog=build_item_catalog(db),
                inventory=[
                    {
                        "name": row["item"].name,
                        "quantity": row["quantity"]
                    }
                    for row in InventoryService.get_inventory(db, character.id)
                    if row.get("item")
                ],
                currency={
                    "gold": purse.gold,
                    "silver": purse.silver,
                    "copper": purse.copper
                } if purse else {"gold": 0, "silver": 0, "copper": 0}
            )
            # Persist and de-duplicate any existing rows before applying new loot.
            for item_id, in db.query(Inventory.item_id).filter(
                Inventory.character_id == character.id
            ).distinct().all():
                rows = db.query(Inventory).filter(
                    Inventory.character_id == character.id,
                    Inventory.item_id == item_id
                ).order_by(Inventory.id).all()
                if not rows:
                    continue
                item_obj = db.query(Item).filter(Item.id == item_id).first()
                if item_obj and _is_inventory_position_phrase(item_obj.name):
                    for row in rows:
                        db.delete(row)
                    continue
                if len(rows) > 1:
                    canonical_row = rows[0]
                    canonical_row.quantity = sum(max(0, row.quantity or 0) for row in rows)
                    canonical_row.is_equipped = any(row.is_equipped for row in rows)
                    canonical_row.slot = canonical_row.slot or next(
                        (row.slot for row in rows[1:] if row.slot), None
                    )
                    for duplicate_row in rows[1:]:
                        db.delete(duplicate_row)
            db.commit()
            if arena_opponent and isinstance(ai_result, dict):
                ai_result["target_name"] = arena_opponent.name
                ai_result["enemy_attacks"] = arena_opponent.distance_m <= 1

            if isinstance(ai_result, dict) and ai_result.get("provider_error"):
                await thinking_msg.delete()
                await message.answer(
                    ai_result["narrative"],
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")]
                    ])
                )
                await state.clear()
                return

            if party_invitation and isinstance(ai_result, dict):
                # An invitation is not consent. Do not let the DM move, speak
                # for, or create consequences for the invited player's PC.
                invitation_text = (
                    f"Вы приглашаете {invitation_target.name} отправиться с вами."
                    if invitation_target
                    else "Вы обращаетесь к товарищам с приглашением отправиться с вами."
                )
                ai_result.update({
                    "narrative": (
                        f"{invitation_text} "
                        "Решение остаётся за игроком приглашённого персонажа; он "
                        "может ответить собственным действием. Пока вы остаётесь на месте."
                    ),
                    "scene_state": scene_state,
                    "location_changed": False,
                    "entities": [],
                    "npcs": [],
                    "npc_memories": [],
                    "quest_offers": [],
                    "quest_completions": [],
                    "loot": [],
                    "custom_items": [],
                    "custom_spells": [],
                    "consumed_items": [],
                    "currency": {"gold": 0, "silver": 0, "copper": 0},
                    "damage": 0,
                    "damage_dice": None,
                    "target_name": None,
                    "enemy_attacks": False,
                })

            if lay_on_hands_action and isinstance(ai_result, dict):
                healing_result = ""
                if character.class_ != "Паладин":
                    healing_result = (
                        "«Возложение рук» доступно паладину; способность не применена."
                    )
                elif lay_on_hands_target is None:
                    healing_result = (
                        "Не удалось определить цель исцеления. Укажите имя союзника."
                    )
                elif lay_on_hands_target.is_dead:
                    healing_result = (
                        f"{lay_on_hands_target.name} уже погиб; «Возложение рук» "
                        "не может вернуть его к жизни."
                    )
                else:
                    pool = character.lay_on_hands_remaining
                    if pool is None:
                        pool = 5 * character.level
                    missing_hp = max(
                        0, lay_on_hands_target.max_hp - lay_on_hands_target.current_hp
                    )
                    amount_match = re.search(
                        r"\b(\d+)\s*(?:hp|хп|хитов|хита|хит)\b",
                        action_description.casefold()
                    )
                    requested_amount = int(amount_match.group(1)) if amount_match else None
                    amount = min(
                        max(0, pool),
                        missing_hp,
                        requested_amount if requested_amount is not None else missing_hp
                    )
                    if amount <= 0:
                        healing_result = (
                            f"«Возложение рук» не применено: у "
                            f"{lay_on_hands_target.name} нет недостающих HP "
                            "или у паладина закончился запас исцеления."
                        )
                    else:
                        old_hp = lay_on_hands_target.current_hp
                        lay_on_hands_target.current_hp += amount
                        character.lay_on_hands_remaining = pool - amount
                        lay_on_hands_target.death_successes = 0
                        lay_on_hands_target.death_failures = 0
                        healing_result = (
                            f"«Возложение рук» восстанавливает "
                            f"{lay_on_hands_target.name} {lay_on_hands_target.current_hp - old_hp} HP. "
                            f"Теперь у него {lay_on_hands_target.current_hp}/"
                            f"{lay_on_hands_target.max_hp} HP. В запасе способности "
                            f"осталось {character.lay_on_hands_remaining} HP."
                        )
                ai_result.update({
                    "narrative": healing_result,
                    "scene_state": scene_state,
                    "location_changed": False,
                    "entities": [],
                    "npcs": [],
                    "npc_memories": [],
                    "quest_offers": [],
                    "quest_completions": [],
                    "loot": [],
                    "custom_items": [],
                    "custom_spells": [],
                    "consumed_items": [],
                    "currency": {"gold": 0, "silver": 0, "copper": 0},
                    "damage": 0,
                    "damage_dice": None,
                    "target_name": None,
                    "enemy_attacks": False,
                })

            equipment_note = _apply_equipment_action(db, character, action_description)
            if equipment_note:
                db.commit()
                narrative_preview = ai_result.get("narrative", "") if isinstance(ai_result, dict) else ""
                if isinstance(ai_result, dict):
                    ai_result["narrative"] = f"{narrative_preview}\n\n{equipment_note}"

            active_game_session = db.query(GameSession).filter(
                GameSession.group_id == character.group_id
            ).first()
            active_combat_exists = bool(
                active_game_session
                and db.query(CombatEncounter.id).filter(
                    CombatEncounter.session_id == active_game_session.id,
                    CombatEncounter.is_active.is_(True)
                ).first()
            )
            # Save AI-awarded loot silently; its narrative is shown as usual.
            narrative = ai_result.get("narrative", "") if isinstance(ai_result, dict) else str(ai_result)
            xp_earned = 0
            group_xp_earned = 0
            quest_messages = []
            new_hostile_arrived = bool(
                isinstance(ai_result, dict)
                and any(
                    entity.get("is_hostile")
                    for entity in ai_result.get("entities", [])
                    if isinstance(entity, dict)
                )
            )
            scene_image_kind = (
                "look" if _is_looking_around(action_description)
                else "location" if isinstance(ai_result, dict) and ai_result.get("location_changed")
                else None
            )
            if (
                scene_image_kind == "location"
                and isinstance(ai_result, dict)
            ):
                destination_key = _location_visit_key(
                    str(ai_result.get("scene_state") or "")
                )
                if destination_key:
                    previously_visited = False
                    for (consequences,) in db.query(
                        ActionHistory.consequences
                    ).filter(
                        ActionHistory.group_id == character.group_id
                    ).all():
                        try:
                            prior_state = json.loads(consequences or "{}").get(
                                "scene_state", ""
                            )
                        except (json.JSONDecodeError, AttributeError, TypeError):
                            continue
                        if _location_visit_key(str(prior_state)) == destination_key:
                            previously_visited = True
                            break
                    if previously_visited:
                        scene_image_kind = None
            new_npc_for_image = None
            if isinstance(ai_result, dict):
                for npc_data in ai_result.get("npcs", []):
                    if not isinstance(npc_data, dict):
                        continue
                    npc_name = str(npc_data.get("name") or "").strip()
                    if (
                        npc_name
                        and npc_name.casefold() not in existing_npc_names
                        and npc_name.casefold() not in player_names
                    ):
                        new_npc_for_image = npc_data
                        break
                if new_npc_for_image is None:
                    for found_entity in ai_result.get("entities", []):
                        if not isinstance(found_entity, dict) or found_entity.get("is_hostile"):
                            continue
                        npc_name = str(found_entity.get("name") or "").strip()
                        if (
                            npc_name
                            and npc_name.casefold() not in existing_npc_names
                            and npc_name.casefold() not in player_names
                        ):
                            new_npc_for_image = found_entity
                            break
            if arena_event_text:
                narrative = f"{arena_event_text}\n\n{narrative}"
            for found_entity in ai_result.get("entities", []) if isinstance(ai_result, dict) else []:
                name = str(found_entity.get("name", "")).strip()[:255]
                if not name:
                    continue
                # A player character can be mentioned by the DM, but can never
                # be persisted as a second NPC/entity.
                if name.casefold() in player_names:
                    continue
                existing_entity = db.query(GameEntity).filter(
                    GameEntity.group_id == character.group_id,
                    GameEntity.name.ilike(name)
                ).first()
                if existing_entity:
                    continue
                entity_type = str(found_entity.get("entity_type") or "person")[:100]
                open5e_key = str(found_entity.get("open5e_key") or "").strip()
                is_hostile_entity = bool(found_entity.get("is_hostile", False))
                is_monster_entity = bool(re.search(
                    r"гоблин|орк|дракон|монстр|чудовищ|звер|нежит|демон|дьявол|"
                    r"тролл|огр|скелет|зомби|волк|паук|goblin|orc|dragon|monster|"
                    r"beast|undead|fiend|troll|ogre|skeleton|zombie|wolf|spider",
                    f"{name} {entity_type}",
                    re.IGNORECASE
                ))
                requires_api_profile = is_hostile_entity or is_monster_entity
                human_archetype = _human_combat_archetype(
                    name,
                    entity_type,
                    str(found_entity.get("description") or "")
                )
                if not human_archetype and is_hostile_entity and not is_monster_entity:
                    human_archetype = "mysterious"
                official = _official_creature_data(db, open5e_key, name)
                if official and not open5e_key:
                    open5e_key = str(official.get("key") or "").strip()
                combat_profile = _official_combat_profile(official) if official else None
                manual_profile = None
                if (
                    not combat_profile
                    and human_archetype in {"commoner", "guard"}
                    and not is_monster_entity
                ):
                    archetype_key = human_archetype
                    official = _official_creature_data(db, archetype_key, "")
                    combat_profile = (
                        _official_combat_profile(official) if official else None
                    )
                    if combat_profile:
                        open5e_key = str(
                            official.get("key") or archetype_key
                        )
                if (
                    not combat_profile
                    and human_archetype
                    and not is_monster_entity
                ):
                    manual_profile = _manual_human_profile(human_archetype)
                    combat_profile = manual_profile
                homebrew_profile = None
                if requires_api_profile and not combat_profile:
                    # Try a confirmed 2024/Open5e archetype first; use a DM-created
                    # homebrew profile only when the API sources have no match.
                    official, archetype_key = _official_archetype_data(
                        db, name, entity_type
                    )
                    combat_profile = (
                        _official_combat_profile(official) if official else None
                    )
                    if combat_profile:
                        if manual_profile:
                            narrative += (
                                f"\n\nДля существа используется профиль: "
                                f"{manual_profile['label']}."
                            )
                        else:
                            open5e_key = archetype_key
                            narrative += (
                                f"\n\nСущество распознано как типичный архетип "
                                f"«{entity_type}»."
                            )
                    else:
                        open5e_key = ""
                        raw_dice = str(found_entity.get("damage_dice") or "1d6").strip()
                        dice_match = re.fullmatch(
                            r"(\d{1,2})d(4|6|8|10|12)(?:\s*([+-])\s*(\d{1,2}))?",
                            raw_dice
                        )
                        if dice_match:
                            count = _safe_int(dice_match.group(1), 1, 1, 4)
                            sides = dice_match.group(2)
                            modifier = _safe_int(
                                dice_match.group(4), 0, 0, 10
                            )
                            sign = dice_match.group(3)
                            raw_dice = f"{count}d{sides}"
                            if sign and modifier:
                                raw_dice += f"{sign}{modifier}"
                        else:
                            raw_dice = "1d4"
                        raw_scores = found_entity.get("ability_scores")
                        default_scores = {
                            "strength": 10, "dexterity": 10, "constitution": 10,
                            "intelligence": 10, "wisdom": 10, "charisma": 10
                        }
                        safe_scores = {
                            stat: _safe_int(
                                raw_scores.get(stat), default_scores[stat], 1, 30
                            )
                            for stat in default_scores
                        } if isinstance(raw_scores, dict) else default_scores
                        homebrew_profile = {
                            "max_hp": _safe_int(found_entity.get("max_hp"), 10, 1, 500),
                            "armor_class": _safe_int(found_entity.get("armor_class"), 12, 5, 30),
                            "attack_bonus": _safe_int(found_entity.get("attack_bonus"), 2, -5, 20),
                            "damage_dice": raw_dice,
                            "scores": safe_scores,
                        }
                        combat_profile = homebrew_profile
                        narrative += (
                            f"\n\nДля {name} создан внутренний боевой профиль ДМа: "
                            "это авторская характеристика, а не запись SRD/API."
                        )
                if manual_profile:
                    max_hp = manual_profile["max_hp"]
                    armor_class = manual_profile["armor_class"]
                    attack_bonus = manual_profile["attack_bonus"]
                    dice = manual_profile["damage_dice"]
                elif combat_profile:
                    max_hp = combat_profile["max_hp"]
                    armor_class = combat_profile["armor_class"]
                    attack_bonus = combat_profile["attack_bonus"]
                    dice = combat_profile["damage_dice"]
                    if homebrew_profile:
                        pass
                    elif isinstance(official, dict):
                        entity_type = str(
                            (official.get("type") or {}).get("name")
                            if isinstance(official.get("type"), dict)
                            else official.get("type") or entity_type
                        )[:100]
                else:
                    max_hp = _safe_int(found_entity.get("max_hp"), 10, 1, 500)
                    dice = str(found_entity.get("damage_dice") or "d4")[:20]
                    armor_class = _safe_int(found_entity.get("armor_class"), 10, 5, 30)
                    attack_bonus = _safe_int(found_entity.get("attack_bonus"), 0, -5, 20)
                if not re.fullmatch(r"\d*d\d+(?:[+-]\d+)?", dice):
                    dice = "d4"
                entity_distance_m = _safe_int(found_entity.get("distance_m"), 5, 0, 1000)
                narrated_distance_ft = _last_narrated_distance_feet(
                    name,
                    [{"ai_response": narrative, "action_description": ""}]
                )
                if narrated_distance_ft is not None:
                    entity_distance_m = max(0, int(narrated_distance_ft / 3.28084))
                db.add(GameEntity(
                    group_id=character.group_id,
                    character_id=character.id,
                    name=name,
                    entity_type=entity_type,
                    description=(
                        str(found_entity.get("description") or "")[:1800]
                        + (
                            " [combat_profile:manual_human]"
                            if manual_profile else ""
                        )
                        + (
                            " [combat_profile:homebrew]"
                            if homebrew_profile else ""
                        )
                        + (f" [open5e_key:{open5e_key}]" if combat_profile and open5e_key else "")
                    ),
                    ability_scores=(
                        manual_profile["scores"] if manual_profile
                        else homebrew_profile.get("scores") if homebrew_profile
                        else {
                            stat: official.get(stat, 10)
                            for stat in (
                                "strength", "dexterity", "constitution",
                                "intelligence", "wisdom", "charisma"
                            )
                        } if isinstance(official, dict) and any(
                            stat in official for stat in (
                                "strength", "dexterity", "constitution",
                                "intelligence", "wisdom", "charisma"
                            )
                        )
                        else None
                    ),
                    personality=str(found_entity.get("personality") or "")[:1000],
                    current_hp=max_hp,
                    max_hp=max_hp,
                    armor_class=armor_class,
                    attack_bonus=attack_bonus,
                    damage_dice=dice,
                    distance_m=entity_distance_m,
                    is_hostile=is_hostile_entity,
                    is_alive=True
                ))
                npc = db.query(CampaignNPC).filter(
                    CampaignNPC.group_id == character.group_id,
                    CampaignNPC.name.ilike(name)
                ).first()
                if not npc:
                    npc = CampaignNPC(
                        group_id=character.group_id,
                        name=name,
                        role=entity_type[:255],
                        description=str(found_entity.get("description") or "")[:4000],
                        personality=str(found_entity.get("personality") or "")[:2000],
                        current_location=scene_state[:500] or None,
                        is_hostile=bool(found_entity.get("is_hostile", False)),
                        is_alive=True
                    )
                    db.add(npc)
            if new_hostile_arrived:
                narrative = _sanitize_combat_narrative(narrative)
            db.flush()

            # Persist identities and update whereabouts for campaign-wide NPCs.
            if isinstance(ai_result, dict):
                for npc_data in ai_result.get("npcs", []):
                    npc_name = str(npc_data.get("name", "")).strip()[:255]
                    if not npc_name:
                        continue
                    if npc_name.casefold() in player_names:
                        continue
                    npc = db.query(CampaignNPC).filter(
                        CampaignNPC.group_id == character.group_id,
                        CampaignNPC.name.ilike(npc_name)
                    ).first()
                    if npc is None:
                        npc = CampaignNPC(group_id=character.group_id, name=npc_name)
                        db.add(npc)
                    for field, limit in (
                        ("aliases", 1000), ("role", 255), ("description", 4000),
                        ("personality", 2000), ("current_location", 500)
                    ):
                        value = npc_data.get(field)
                        if value:
                            setattr(npc, field, str(value)[:limit])
                    if "is_hostile" in npc_data:
                        npc.is_hostile = bool(npc_data["is_hostile"])
                    if "is_alive" in npc_data:
                        npc.is_alive = bool(npc_data["is_alive"])

                db.flush()
                for memory_data in ai_result.get("npc_memories", []):
                    npc_name = str(memory_data.get("npc_name", "")).strip()
                    fact = str(memory_data.get("fact", "")).strip()[:2000]
                    if not npc_name or not fact:
                        continue
                    if npc_name.casefold() in player_names:
                        continue
                    npc = db.query(CampaignNPC).filter(
                        CampaignNPC.group_id == character.group_id,
                        CampaignNPC.name.ilike(npc_name)
                    ).first()
                    if npc is None:
                        continue
                    existing_memory = db.query(NPCMemory).filter(
                        NPCMemory.npc_id == npc.id,
                        NPCMemory.fact == fact
                    ).first()
                    if not existing_memory:
                        db.add(NPCMemory(
                            npc_id=npc.id,
                            fact=fact,
                            source=str(memory_data.get("source") or "игровая сцена")[:255]
                        ))
                db.flush()

                for offer in ai_result.get("quest_offers", []):
                    npc_name = str(offer.get("npc_name") or "").strip()[:255]
                    title = str(offer.get("title") or "").strip()[:255]
                    description = str(offer.get("description") or "").strip()[:2000]
                    if not npc_name or not title or not description:
                        continue
                    if npc_name.casefold() in player_names:
                        continue
                    npc = db.query(CampaignNPC).filter(
                        CampaignNPC.group_id == character.group_id,
                        CampaignNPC.name.ilike(npc_name),
                        CampaignNPC.is_hostile.is_(False),
                        CampaignNPC.is_alive.is_(True)
                    ).first()
                    if not npc:
                        continue
                    prior_quest = db.query(CampaignQuest.id).filter(
                        CampaignQuest.npc_id == npc.id
                    ).first()
                    if prior_quest:
                        continue
                    reward_xp = _safe_int(offer.get("reward_xp"), 100, 25, 5000)
                    reward_gold = _safe_int(offer.get("reward_gold"), 0, 0, 100000)
                    reward_silver = _safe_int(offer.get("reward_silver"), 0, 0, 100000)
                    reward_copper = _safe_int(offer.get("reward_copper"), 0, 0, 100000)
                    reward_items = [
                        {
                            "name": str(item.get("name")).strip()[:255],
                            "quantity": _safe_int(item.get("quantity"), 1, 1, 99),
                            "description": str(item.get("description") or "")[:1000],
                        }
                        for item in offer.get("reward_items", [])
                        if isinstance(item, dict) and str(item.get("name") or "").strip()
                    ][:10]
                    reward_spells = [
                        {"name": str(sp.get("name")).strip()[:255], "level": _safe_int(sp.get("level"), 1, 0, 9)}
                        for sp in offer.get("reward_spells", [])
                        if isinstance(sp, dict) and str(sp.get("name") or "").strip()
                    ][:5]
                    db.add(CampaignQuest(
                        group_id=character.group_id,
                        character_id=None,
                        npc_id=npc.id,
                        title=title,
                        description=description,
                        reward_xp=reward_xp,
                        reward_gold=reward_gold,
                        reward_silver=reward_silver,
                        reward_copper=reward_copper,
                        reward_items=reward_items,
                        reward_spells=reward_spells,
                        status="offered"
                    ))
                    quest_messages.append(
                        f"📜 <b>Задание от {html.escape(npc.name)}: "
                        f"{html.escape(title)}</b>\n"
                        f"{html.escape(description)}\nНаграда: "
                        f"{reward_xp} XP"
                        + (f", {reward_gold} зм" if reward_gold else "")
                        + (f", {reward_silver} см" if reward_silver else "")
                        + (f", {reward_copper} мм" if reward_copper else "")
                        + (f", предметы: {', '.join(str(i['name']) for i in reward_items)}" if reward_items else "")
                        + (f", заклинания: {', '.join(str(i['name']) for i in reward_spells)}" if reward_spells else "")
                        + "."
                    )

                db.flush()
                for completion in ai_result.get("quest_completions", []):
                    if not active_quest_ids_at_start:
                        continue
                    npc_name = str(completion.get("npc_name") or "").strip()[:255]
                    title = str(completion.get("title") or "").strip()[:255]
                    if not npc_name or not title:
                        continue
                    if _is_aggressive_action(action_description):
                        declared_target = str(
                            ai_result.get("target_name") or ""
                        ).strip()
                        action_mentions_npc = (
                            _normalize_entity_name(npc_name)
                            and _normalize_entity_name(npc_name) in
                            _normalize_entity_name(action_description)
                        )
                        if (
                            declared_target.casefold() == npc_name.casefold()
                            or action_mentions_npc
                        ):
                            # Killing/attacking the quest giver is not a quest
                            # completion; death resolution will fail the quest.
                            continue
                    quest = db.query(CampaignQuest).join(
                        CampaignNPC, CampaignNPC.id == CampaignQuest.npc_id
                    ).filter(
                        CampaignQuest.group_id == character.group_id,
                        CampaignQuest.id.in_(active_quest_ids_at_start),
                        CampaignQuest.status == "offered",
                        CampaignNPC.name.ilike(npc_name),
                        CampaignQuest.title.ilike(title)
                    ).first()
                    if not quest:
                        continue
                    quest.status = "completed"
                    quest.completed_at = datetime.utcnow()
                    group_xp_earned += quest.reward_xp
                    group_characters = db.query(Character).filter(
                        Character.group_id == character.group_id
                    ).all()
                    reward_copper_total = (
                        (quest.reward_gold or 0) * 100
                        + (quest.reward_silver or 0) * 10
                        + (quest.reward_copper or 0)
                    )
                    if reward_copper_total:
                        recipients = group_characters or [character]
                        share, remainder = divmod(reward_copper_total, len(recipients))
                        remainder_recipients = set(
                            random.sample(range(len(recipients)), remainder)
                        ) if remainder else set()
                        for index, recipient in enumerate(recipients):
                            _credit_character_copper(
                                db,
                                recipient,
                                share + (1 if index in remainder_recipients else 0)
                            )
                    reward_recipient = random.choice(group_characters or [character])
                    personal_rewards = []
                    for reward_item in quest.reward_items:
                        _grant_item_to_character(
                            db,
                            reward_recipient,
                            reward_item.get("name"),
                            reward_item.get("quantity", 1),
                            reward_item.get("description"),
                        )
                        personal_rewards.append(str(reward_item.get("name") or "предмет"))
                    for reward_spell in getattr(quest, "reward_spells", []) or []:
                        spell_name = str(reward_spell.get("name") or "").strip()[:255]
                        if not spell_name:
                            continue
                        exists = db.query(CharacterSpell.id).filter(
                            CharacterSpell.character_id == reward_recipient.id,
                            CharacterSpell.spell_name.ilike(spell_name)
                        ).first()
                        if not exists:
                            db.add(CharacterSpell(
                                character_id=reward_recipient.id,
                                spell_name=spell_name,
                                spell_level=_safe_int(reward_spell.get("level"), 1, 0, 9),
                                slots_used=0,
                            ))
                            personal_rewards.append(spell_name)
                    distribution_note = f"{quest.reward_xp} XP каждому участнику"
                    if reward_copper_total:
                        distribution_note += "; монеты разделены между группой"
                    quest_messages.append(
                        f"✅ Задание «{html.escape(quest.title)}» выполнено. "
                        f"{distribution_note}. "
                        + (
                            f"Награды {', '.join(html.escape(name) for name in personal_rewards)} "
                            f"достались {html.escape(reward_recipient.name)}."
                            if personal_rewards else ""
                        )
                    )
                if quest_messages:
                    narrative += "\n\n" + "\n\n".join(quest_messages)

            homebrew_spell_warning = ""
            newly_added_improvised_weapons = []
            acquired_via_custom_items: dict[int, int] = {}
            if isinstance(ai_result, dict):
                for raw_item in ai_result.get("custom_items", []):
                    definition = _sanitize_custom_item(raw_item)
                    if not definition:
                        continue
                    custom_item = db.query(Item).filter(
                        Item.name.ilike(definition["name"])
                    ).first()
                    if custom_item is None:
                        catalog_name = str(raw_item.get("catalog_name") or "").strip()
                        canonical_name = canonical_item_name(catalog_name or definition["name"])
                        source_item = db.query(Item).filter(
                            Item.name.ilike(canonical_name)
                        ).first()
                        if source_item:
                            already_owned = db.query(Inventory.id).filter(
                                Inventory.character_id == character.id,
                                Inventory.item_id == source_item.id
                            ).first()
                            custom_item = source_item if already_owned else localize_catalog_item(
                                db, source_item, requested_display_name=definition["name"]
                            )
                        else:
                            custom_item = Item(
                                name=definition["name"],
                                # The DM may create bounded homebrew equipment.
                                # API-backed records use the separate source_item branch.
                                item_type=definition["item_type"],
                                description=definition["description"],
                                damage_dice=definition["damage_dice"],
                                damage_type=definition["damage_type"],
                                armor_class=definition["armor_class"],
                                is_stackable=False
                            )
                            db.add(custom_item)
                            db.flush()
                    action_uses_item = bool(re.search(
                        r"(использ\w*|дерусь|драться|атак\w*|удар\w*|бью|бить|брос\w*|мет\w*|"
                        r"подоб\w*|взять|беру|надеть|экип\w*|хватаю)",
                        action_description.casefold()
                    ))
                    add_to_inventory = definition["add_to_inventory"] or action_uses_item
                    if add_to_inventory:
                        owned = db.query(Inventory).filter(
                            Inventory.character_id == character.id,
                            Inventory.item_id == custom_item.id
                        ).first()
                        if not owned:
                            is_armor = (
                                custom_item.item_type.startswith("armor:")
                                or custom_item.item_type == "shield"
                            )
                            is_weapon = custom_item.item_type.startswith("weapon:")
                            db.add(Inventory(
                                character_id=character.id,
                                item_id=custom_item.id,
                                quantity=1,
                                is_equipped=is_armor or is_weapon,
                                slot="armor" if is_armor else "weapon" if is_weapon else None
                            ))
                            db.flush()
                            # custom_items and loot often describe the same pickup;
                            # remember this award so the loot pass doesn't grant it twice.
                            acquired_via_custom_items[custom_item.id] = 1
                db.flush()

                raw_custom_spells = ai_result.get("custom_spells", [])
                if raw_custom_spells:
                    accepted_spell = False
                    for raw_spell in raw_custom_spells:
                        spell_spec = _complete_homebrew_spell_spec(
                            action_description,
                            raw_spell,
                            character
                        )
                        if not spell_spec:
                            continue

                        slug = re.sub(
                            r"[^\w-]+",
                            "-",
                            spell_spec["name"].casefold(),
                            flags=re.UNICODE
                        ).strip("-")[:150]
                        rule_name = f"character-{character.id}-{slug}"[:255]
                        existing_rule = db.query(DnDRules).filter(
                            DnDRules.rule_category == "homebrew_spells",
                            DnDRules.rule_name == rule_name
                        ).first()
                        if not existing_rule:
                            db.add(DnDRules(
                                rule_category="homebrew_spells",
                                rule_name=rule_name,
                                rule_data={
                                    **spell_spec,
                                    "character_id": character.id,
                                    "source": "player-designed, AI balance-checked"
                                },
                                source="homebrew",
                                version="1"
                            ))
                        if spell_spec["add_to_spellbook"]:
                            existing_spell = db.query(CharacterSpell).filter(
                                CharacterSpell.character_id == character.id,
                                CharacterSpell.spell_name.ilike(spell_spec["name"])
                            ).first()
                            if not existing_spell:
                                db.add(CharacterSpell(
                                    character_id=character.id,
                                    spell_name=spell_spec["name"],
                                    spell_level=spell_spec["level"],
                                    is_prepared=True
                                ))
                        accepted_spell = True
                    if not accepted_spell:
                        homebrew_spell_warning = (
                            "\n\n⚠️ Самодельное заклинание не добавлено. Для проверки нужны "
                            "уровень, время накладывания, дальность, длительность, компоненты "
                            "и полный эффект с уроном/спасброском."
                        )
                db.flush()

            target_name = ai_result.get("target_name") if isinstance(ai_result, dict) else None
            lower_action = action_description.casefold()
            directly_named_party_attack = _is_aggressive_action(action_description) or any(
                _charge_is_aimed_at(action_description, party_name)
                for party_name in party_name_to_character
            )
            if not target_name and directly_named_party_attack:
                # The model may omit target_name for declined/ambiguous PvP.
                # Recover a directly named party target from the player's text.
                for party_name in party_name_to_character:
                    name_tokens = [
                        token for token in party_name.split() if len(token) >= 3
                    ]
                    if name_tokens and any(
                        token in lower_action for token in name_tokens
                    ):
                        target_name = party_name
                        break
            target = None
            named_party_target = bool(
                target_name and target_name.casefold().strip() in player_names
            )
            party_target = None
            if target_name:
                if named_party_target:
                    party_target = db.query(Character).filter(
                        Character.group_id == character.group_id,
                        Character.name.ilike(target_name)
                    ).first()
                else:
                    target = db.query(GameEntity).filter(
                        GameEntity.group_id == character.group_id,
                        GameEntity.name.ilike(target_name),
                        GameEntity.is_alive.is_(True)
                    ).first()
                    if target is None:
                        npc_target = db.query(CampaignNPC).filter(
                            CampaignNPC.group_id == character.group_id,
                            CampaignNPC.name.ilike(target_name),
                            CampaignNPC.is_alive.is_(True)
                        ).first()
                        if npc_target:
                            npc_archetype = _human_combat_archetype(
                                npc_target.name,
                                npc_target.role or "person",
                                npc_target.description or ""
                            )
                            npc_monsterish = bool(re.search(
                                r"гоблин|орк|дракон|монстр|чудовищ|звер|нежит|демон|дьявол|"
                                r"тролл|огр|скелет|зомби|волк|паук|goblin|orc|dragon|monster|"
                                r"beast|undead|fiend|troll|ogre|skeleton|zombie|wolf|spider",
                                f"{npc_target.name} {npc_target.role or ''}",
                                re.IGNORECASE
                            ))
                            if not npc_archetype and not npc_target.is_hostile:
                                npc_archetype = "commoner"
                            elif not npc_archetype and npc_target.is_hostile and not npc_monsterish:
                                npc_archetype = "mysterious"
                            official_npc_profile = None
                            official_npc_record = None
                            npc_homebrew = False
                            if npc_archetype in {"commoner", "guard"}:
                                official_npc_record = _official_creature_data(
                                    db, npc_archetype, ""
                                )
                            else:
                                official_npc_record = _official_creature_data(
                                    db, "", npc_target.name
                                )
                                if not official_npc_record:
                                    official_npc_record, _ = _official_archetype_data(
                                        db,
                                        npc_target.name,
                                        npc_target.role or ""
                                    )
                            if official_npc_record:
                                official_npc_profile = _official_combat_profile(
                                    official_npc_record
                                )
                            if official_npc_profile:
                                npc_profile = official_npc_profile
                            elif npc_archetype:
                                npc_profile = _manual_human_profile(npc_archetype)
                            else:
                                # There is no SRD match for this specific existing
                                # NPC; persist a modest homebrew profile for combat.
                                npc_archetype = "mysterious"
                                npc_homebrew = True
                                npc_profile = {
                                    "max_hp": 10,
                                    "armor_class": 12,
                                    "attack_bonus": 2,
                                    "damage_dice": "1d6",
                                    "scores": {
                                        "strength": 10, "dexterity": 10,
                                        "constitution": 10, "intelligence": 10,
                                        "wisdom": 10, "charisma": 10
                                    },
                                }
                            npc_description = (npc_target.description or "")
                            if official_npc_profile and official_npc_record:
                                official_key = str(
                                    official_npc_record.get("key") or npc_archetype
                                )
                                npc_description += f" [open5e_key:{official_key}]"
                                npc_scores = {
                                    stat: official_npc_record.get(stat, 10)
                                    for stat in (
                                        "strength", "dexterity", "constitution",
                                        "intelligence", "wisdom", "charisma"
                                    )
                                }
                            elif npc_homebrew:
                                npc_description += " [combat_profile:homebrew]"
                                npc_scores = npc_profile["scores"]
                            else:
                                npc_description += " [combat_profile:manual_human]"
                                npc_scores = npc_profile.get(
                                    "scores",
                                    {
                                        "strength": 10, "dexterity": 10,
                                        "constitution": 10, "intelligence": 10,
                                        "wisdom": 10, "charisma": 10
                                    }
                                )
                            target = GameEntity(
                                group_id=character.group_id,
                                character_id=character.id,
                                name=npc_target.name,
                                entity_type=npc_target.role or "person",
                                description=npc_description,
                                personality=npc_target.personality,
                                current_hp=npc_profile["max_hp"],
                                max_hp=npc_profile["max_hp"],
                                armor_class=npc_profile["armor_class"],
                                attack_bonus=npc_profile["attack_bonus"],
                                damage_dice=npc_profile["damage_dice"],
                                ability_scores=npc_scores,
                                distance_m=0,
                                is_hostile=True,
                                is_alive=True,
                            )
                            npc_target.is_hostile = True
                            db.add(target)
                            db.flush()

            is_attack_action = _is_aggressive_action(action_description) or bool(
                (party_target or target)
                and _charge_is_aimed_at(
                    action_description,
                    party_target.name if party_target else target.name
                )
            )
            enemy_attacks = bool(
                isinstance(ai_result, dict) and ai_result.get("enemy_attacks")
            )
            if target is None and party_target is None and (is_attack_action or enemy_attacks):
                living_entities = db.query(GameEntity).filter(
                    GameEntity.group_id == character.group_id,
                    GameEntity.is_alive.is_(True)
                ).all()
                if len(living_entities) == 1:
                    target = living_entities[0]
            if target and (is_attack_action or enemy_attacks):
                if is_attack_action or enemy_attacks:
                    target.is_hostile = True
                if _entity_official_profile(db, target) is None:
                    narrative += (
                        f"\n\n⚠️ Для {target.name} не удалось подтвердить боевой блок "
                        "в 2024 SRD/API. Его характеристики не выдумываются, поэтому "
                        "числовой бой с этой целью пока не разрешён."
                    )
                    target = None
                    enemy_attacks = False
                    is_attack_action = False

            target_distance_feet = None
            if target:
                target_distance_feet = target.distance_m * 3.28084
                approach_action = bool(re.search(
                    r"\b(подхожу|приближаюсь|подойти|иду к|направляюсь к)\b",
                    lower_action
                ))
                charge_action = bool(re.search(
                    r"\b(бегу|несусь|мчусь|бросаюсь|кидаюсь)\b.{0,50}"
                    r"(?:на|к|в сторону)\b",
                    lower_action
                ))
                close_action = bool(re.search(
                    r"(?:в упор|вплотную|досягаем\w*|расстояни[ея]\s+удара|"
                    r"максимально близко|подойти ещё ближе|подойди ещё ближе|"
                    r"подойди ближе|в предел\w*\s+удара)",
                    lower_action
                ))
                # Narrative is never allowed to update or contradict the stored
                # tactical distance, even when the player simply waits or talks.
                narrative = _remove_target_distance_claims(narrative, target.name)
                narrative = _sanitize_combat_narrative(narrative)
                if close_action:
                    target_distance_feet = 0
                    target.distance_m = 0
                elif approach_action or charge_action:
                    target_distance_feet = max(
                        0,
                        target_distance_feet - (29.5276 if charge_action else 9.84252)
                    )
                    target.distance_m = int(target_distance_feet / 3.28084)
                if is_attack_action:
                    narrative = re.sub(r"\s+", " ", narrative).strip()

            active_encounter = None
            active_game_session = None
            combat_started = False
            initiative_result = None
            had_active_combat = bool(
                db.query(CombatEncounter.id).join(
                    GameSession, GameSession.id == CombatEncounter.session_id
                ).filter(
                    GameSession.group_id == character.group_id,
                    CombatEncounter.is_active.is_(True)
                ).first()
            )
            if target and (
                is_attack_action
                or enemy_attacks
                or bool(arena_event_text)
            ):
                # The DM's explicit hostile action is itself the authoritative
                # signal; don't require the model to also set is_hostile perfectly.
                if is_attack_action or enemy_attacks or arena_event_text:
                    target.is_hostile = True
                    npc_record = db.query(CampaignNPC).filter(
                        CampaignNPC.group_id == character.group_id,
                        CampaignNPC.name.ilike(target.name)
                    ).first()
                    if npc_record:
                        npc_record.is_hostile = True
                active_game_session, active_encounter, combat_started, initiative_result = (
                    _ensure_action_combat(db, character, target)
                )
                if combat_started:
                    if not active_combat_exists:
                        scene_image_kind = "combat"
                    narrative += (
                        f"\n\n⚔️ <b>Начинается бой!</b> "
                        f"{character.name} и {target.name} вступают в схватку."
                    )

            player_combat_text = ""
            if is_attack_action and party_target and party_target.id != character.id:
                # Player characters use their real sheet (including calculated
                # AC/HP), not a fabricated creature stat block.
                carried_weapons = db.query(Item).join(
                    Inventory, Inventory.item_id == Item.id
                ).filter(
                    Inventory.character_id == character.id,
                    Item.item_type.like("weapon:%")
                ).order_by(Item.name).all()
                requested_weapon = (
                    ai_result.get("weapon_name") if isinstance(ai_result, dict) else None
                )
                weapon = None if _is_improvised_weapon_action(
                    lower_action, carried_weapons
                ) else next(
                    (
                        candidate for candidate in carried_weapons
                        if requested_weapon
                        and candidate.name.casefold() == requested_weapon.casefold()
                    ),
                    None
                ) or _choose_weapon(carried_weapons, lower_action)
                weapon_type = weapon.item_type if weapon else "weapon:unarmed"
                attacker_attributes = db.query(CharacterAttribute).filter(
                    CharacterAttribute.character_id == character.id
                ).first()
                defender_attributes = db.query(CharacterAttribute).filter(
                    CharacterAttribute.character_id == party_target.id
                ).first()
                strength_modifier = (
                    (attacker_attributes.strength - 10) // 2 if attacker_attributes else 0
                )
                dexterity_modifier = (
                    (attacker_attributes.dexterity - 10) // 2 if attacker_attributes else 0
                )
                is_ranged_weapon = ":ranged" in weapon_type
                ability_modifier = (
                    max(strength_modifier, dexterity_modifier)
                    if ":finesse" in weapon_type
                    else dexterity_modifier if is_ranged_weapon
                    else strength_modifier
                )
                attack_bonus = (
                    ability_modifier
                    + GameEngineService.calculate_proficiency_bonus(character.level)
                )
                attack_roll = GameEngineService.roll_d20()
                attack_total = attack_roll + attack_bonus
                target_ac = GameEngineService.calculate_armor_class(
                    party_target, defender_attributes, db
                )
                if attack_roll == 20 or (attack_roll != 1 and attack_total >= target_ac):
                    damage_dice = (
                        weapon.damage_dice
                        if weapon and weapon.damage_dice and is_open5e_catalog_item(db, weapon)
                        else "1d4"
                    )
                    fixed_damage = damage_dice.isdigit()
                    rolled_damage = (
                        int(damage_dice)
                        if fixed_damage
                        else _roll_entity_damage(damage_dice)
                    )
                    if attack_roll == 20 and not fixed_damage:
                        rolled_damage += _roll_entity_damage(damage_dice)
                    dealt_damage = max(
                        1,
                        rolled_damage + (
                            ability_modifier if not fixed_damage or weapon is None else 0
                        )
                    )
                    if party_target.current_hp <= 0:
                        party_target.death_failures = min(
                            3,
                            (party_target.death_failures or 0)
                            + (2 if attack_roll == 20 else 1)
                        )
                        if party_target.death_failures >= 3:
                            party_target.is_dead = True
                        player_combat_text = (
                            f"\n\n⚔️ PvP: удар достигает лежащего "
                            f"{html.escape(party_target.name)}. "
                            f"Неудачи спасброска: {party_target.death_failures}/3."
                        )
                        if party_target.is_dead:
                            player_combat_text += " Персонаж погиб."
                    else:
                        old_target_hp = party_target.current_hp
                        party_target.current_hp = max(0, old_target_hp - dealt_damage)
                        if party_target.current_hp <= 0:
                            party_target.death_successes = 0
                            party_target.death_failures = 0
                        player_combat_text = (
                            f"\n\n⚔️ PvP: атака по {html.escape(party_target.name)} — "
                            f"d20 ({attack_roll}) + {attack_bonus} = {attack_total} "
                            f"против КД {target_ac}: попадание. "
                            f"Урон {dealt_damage}; HP цели "
                            f"{party_target.current_hp}/{party_target.max_hp}."
                        )
                else:
                    player_combat_text = (
                        f"\n\n⚔️ PvP: атака по {html.escape(party_target.name)} — "
                        f"d20 ({attack_roll}) + {attack_bonus} = {attack_total} "
                        f"против КД {target_ac}: промах."
                    )

            if is_attack_action and target:
                carried_weapons = db.query(Item).join(
                    Inventory, Inventory.item_id == Item.id
                ).filter(
                    Inventory.character_id == character.id,
                    Item.item_type.like("weapon:%")
                ).order_by(Item.name).all()
                requested_weapon = ai_result.get("weapon_name") if isinstance(ai_result, dict) else None
                weapon = None if _is_improvised_weapon_action(
                    lower_action, carried_weapons
                ) else next(
                    (
                        candidate for candidate in carried_weapons
                        if requested_weapon and candidate.name.casefold() == requested_weapon.casefold()
                    ),
                    None
                ) or None
                weapon = weapon or _choose_weapon(carried_weapons, lower_action)
                weapon_type = weapon.item_type if weapon else "weapon:unarmed"
                is_ranged_weapon = ":ranged" in weapon_type
                weapon_range = (
                    re.search(
                        r"(?:Range|Дальность):\s*(\d+)(?:/(\d+))?\s*(?:feet|фут(?:а|ов|ы)?)",
                        weapon.description or "",
                        re.I
                    )
                    if weapon else None
                )
                if target_distance_feet is None:
                    target_distance_feet = target.distance_m * 3.28084
                if is_ranged_weapon and weapon_range:
                    normal_range_feet = int(weapon_range.group(1))
                    long_range_feet = int(weapon_range.group(2) or normal_range_feet)
                    if target_distance_feet > long_range_feet:
                        narrative += (
                            f"\n\n❌ {weapon.name} не достаёт до {target.name}."
                        )
                        weapon_out_of_range = True
                    else:
                        weapon_out_of_range = False
                else:
                    weapon_out_of_range = False

                if weapon_out_of_range:
                    pass
                elif target_distance_feet > 5 and not is_ranged_weapon:
                    narrative += (
                        f"\n\n❌ {target.name} слишком далеко для удара этим оружием."
                    )
                else:
                    attributes = db.query(CharacterAttribute).filter(
                        CharacterAttribute.character_id == character.id
                    ).first()
                    strength_modifier = (attributes.strength - 10) // 2 if attributes else 0
                    dexterity_modifier = (attributes.dexterity - 10) // 2 if attributes else 0
                    ability_modifier = (
                        max(strength_modifier, dexterity_modifier)
                        if ":finesse" in weapon_type
                        else dexterity_modifier if is_ranged_weapon
                        else strength_modifier
                    )
                    ability_label = (
                        "Ловкость" if is_ranged_weapon
                        else "Сила" if strength_modifier >= dexterity_modifier
                        else "Ловкость"
                    )
                    attack_bonus = ability_modifier + GameEngineService.calculate_proficiency_bonus(character.level)
                    attack_roll = GameEngineService.roll_d20()
                    first_attack_roll = attack_roll
                    long_range_disadvantage = bool(
                        is_ranged_weapon and weapon_range
                        and target_distance_feet > int(weapon_range.group(1))
                    )
                    if long_range_disadvantage:
                        second_roll = GameEngineService.roll_d20()
                        attack_roll = min(attack_roll, second_roll)
                    attack_total = attack_roll + attack_bonus
                    hit = attack_roll == 20 or (attack_roll != 1 and attack_total >= target.armor_class)
                    if hit:
                        damage_dice = (
                            weapon.damage_dice
                            if weapon and weapon.damage_dice
                            and is_open5e_catalog_item(db, weapon)
                            else "1d4"
                        )
                        fixed_damage = damage_dice.isdigit()
                        base_damage = int(damage_dice) if fixed_damage else _roll_entity_damage(damage_dice)
                        crit_damage = (
                            0 if fixed_damage or attack_roll != 20
                            else _roll_entity_damage(damage_dice)
                        )
                        damage = max(
                            1,
                            base_damage + crit_damage
                            + (ability_modifier if not fixed_damage or weapon is None else 0)
                        )
                        target.current_hp = max(0, target.current_hp - damage)
                        if target.current_hp == 0:
                            target.is_alive = False
                            xp_earned += _entity_xp_reward(db, target, character.level)
                            npc_record = db.query(CampaignNPC).filter(
                                CampaignNPC.group_id == character.group_id,
                                CampaignNPC.name.ilike(target.name)
                            ).first()
                            if npc_record:
                                npc_record.is_alive = False
                        if not target.is_alive:
                            failed_quests = _fail_quests_for_npc(
                                db, character.group_id, target.name
                            )
                            if failed_quests:
                                player_combat_text += (
                                    "\n\n❌ Задания этого NPC провалены: "
                                    + ", ".join(html.escape(title) for title in failed_quests)
                                    + "."
                                )
                            player_combat_text += (
                                f"\n\n🎯 Ваш удар попадает: "
                                f"{target.name} падает и больше не поднимается."
                            )
                        else:
                            player_combat_text += (
                                f"\n\n🎯 Ваш удар попадает по {target.name}; "
                                f"противник {_entity_wound_description(target)}."
                            )
                    else:
                        player_combat_text += (
                            f"\n\n⚔️ Вы не задеваете {target.name}; "
                            "удар проходит мимо или противник успевает защититься."
                        )

            loot = ai_result.get("loot", []) if isinstance(ai_result, dict) else []
            if not loot:
                loot = _loot_from_explicit_action(action_description)
            loot_quantities_by_item: dict[int, tuple[Item, int]] = {}
            for found in loot:
                if not isinstance(found, dict):
                    continue
                name = str(found.get("name", "")).strip()[:255]
                if not name or _is_inventory_position_phrase(name):
                    continue
                try:
                    quantity = max(0, min(int(found.get("quantity", 1)), 1000))
                except (TypeError, ValueError):
                    quantity = 1
                if quantity == 0:
                    continue
                item = db.query(Item).filter(Item.name.ilike(name)).first()
                if item is None:
                    item = InventoryService.create_item(
                        db=db,
                        name=name,
                        item_type=str(found.get("type") or "сокровище")[:100],
                        rarity=str(found.get("rarity") or "common")[:50],
                        description=str(found.get("description") or "")[:2000],
                        is_stackable=True
                    )
                if item is not None:
                    previous = loot_quantities_by_item.get(item.id)
                    loot_quantities_by_item[item.id] = (
                        item,
                        max(quantity, previous[1] if previous else 0)
                    )
            for item_id, (item, reported_quantity) in loot_quantities_by_item.items():
                quantity_to_add = max(
                    0,
                    reported_quantity - acquired_via_custom_items.get(item_id, 0)
                )
                if quantity_to_add <= 0:
                    continue
                rows = db.query(Inventory).filter(
                    Inventory.character_id == character.id,
                    Inventory.item_id == item_id
                ).order_by(Inventory.id).all()
                if rows:
                    rows[0].quantity = sum(max(0, row.quantity or 0) for row in rows) + quantity_to_add
                    for duplicate in rows[1:]:
                        db.delete(duplicate)
                else:
                    db.add(Inventory(
                        character_id=character.id,
                        item_id=item_id,
                        quantity=quantity_to_add,
                        is_equipped=False,
                        slot=None
                    ))

            consumed_report: list[str] = []
            if isinstance(ai_result, dict):
                for consumed in ai_result.get("consumed_items", []):
                    if not isinstance(consumed, dict):
                        continue
                    cname = str(consumed.get("name", "")).strip()[:255]
                    if not cname:
                        continue
                    try:
                        cqty = max(1, min(int(consumed.get("quantity", 1)), 1000))
                    except (TypeError, ValueError):
                        cqty = 1
                    item = db.query(Item).filter(Item.name.ilike(cname)).first()
                    if item is None:
                        # Try matching against inventory item names loosely.
                        inv_rows = db.query(Inventory).join(Item).filter(
                            Inventory.character_id == character.id,
                            Item.name.ilike(f"%{cname}%")
                        ).all()
                        if inv_rows:
                            item = inv_rows[0].item
                    if item is None:
                        continue
                    rows = db.query(Inventory).filter(
                        Inventory.character_id == character.id,
                        Inventory.item_id == item.id
                    ).order_by(Inventory.id).all()
                    remaining = cqty
                    for row in rows:
                        if remaining <= 0:
                            break
                        take = min(max(0, row.quantity or 0), remaining)
                        row.quantity = max(0, (row.quantity or 0) - take)
                        remaining -= take
                        if row.quantity <= 0 and not row.is_equipped:
                            db.delete(row)
                    consumed_qty = cqty - remaining
                    if consumed_qty > 0:
                        consumed_report.append(f"{item.name} x{consumed_qty}")

            db.commit()

            if consumed_report:
                narrative += "\n\n📦 <i>Потрачено: " + ", ".join(consumed_report) + "</i>"

            if homebrew_spell_warning:
                narrative += homebrew_spell_warning

            damage_text = ""
            damage = ai_result.get("damage", 0) if isinstance(ai_result, dict) else 0
            if is_attack_action or enemy_attacks:
                # Combat damage is resolved by the explicit attacker/target code below.
                # Never let the DM's generic player-damage field misapply a goblin hit
                # or a player's attack as damage to the character.
                damage = 0
            elif isinstance(ai_result, dict) and ai_result.get("damage_dice"):
                damage = max(
                    0,
                    _roll_entity_damage(ai_result["damage_dice"])
                    + _safe_int(ai_result.get("damage_bonus"), 0, -20, 100)
                )
            if isinstance(damage, int) and damage > 0:
                old_hp = character.current_hp
                alive, _ = GameEngineService.apply_damage(character, damage, db)
                actual_damage = old_hp - character.current_hp
                if actual_damage:
                    damage_text = (
                        f"\n\n💥 Получено урона: {actual_damage}. "
                        f"Здоровье: {character.current_hp}/{character.max_hp}."
                    )
                if not alive:
                    damage_text += "\n🩸 Персонаж повержен и нуждается в помощи союзника."

            enemy_attack_text = ""
            # A player action is one combat round: after it resolves, every living
            # hostile combatant in reach gets exactly one response, in DEX order.
            # Do not rely on the model's enemy_attacks flag for turn advancement.
            hostiles = db.query(GameEntity).filter(
                GameEntity.group_id == character.group_id,
                GameEntity.is_alive.is_(True),
                GameEntity.is_hostile.is_(True)
            ).order_by(GameEntity.id).all()
            unverified_hostiles = [
                enemy for enemy in hostiles
                if _entity_official_profile(db, enemy) is None
            ]
            if unverified_hostiles and had_active_combat:
                narrative += (
                    "\n\n⚠️ Некоторые противники исключены из механического боя: "
                    "для них не найден сохранённый боевой профиль."
                )
            hostiles = [
                enemy for enemy in hostiles
                if _entity_official_profile(db, enemy) is not None
            ]
            player_attributes = db.query(CharacterAttribute).filter(
                CharacterAttribute.character_id == character.id
            ).first()
            player_ac = GameEngineService.calculate_armor_class(
                character, player_attributes, db
            )
            # Only take a full enemy turn if combat was already underway or was
            # initiated by this player's declared attack / the DM's hostile event.
            enemy_turn_due = bool(
                had_active_combat
                or combat_started
                or enemy_attacks
                or (is_attack_action and target is not None)
            )
            enemy_lines = []
            active_hostiles = hostiles
            if enemy_turn_due:
                if active_game_session is None:
                    active_game_session = db.query(GameSession).filter(
                        GameSession.group_id == character.group_id
                    ).first()
                turn_state = {}
                if active_game_session and active_game_session.turn_order:
                    try:
                        turn_state = json.loads(active_game_session.turn_order)
                        if not isinstance(turn_state, dict):
                            turn_state = {}
                    except (TypeError, json.JSONDecodeError):
                        turn_state = {}
                combat_enemy_ids = {
                    int(enemy_id) for enemy_id in turn_state.get("combat_enemy_ids", [])
                    if str(enemy_id).isdigit()
                }
                # Seed/refresh the encounter roster. On a new combat, include nearby
                # hostiles; on existing combats with an empty/legacy roster, include
                # all living hostiles so no combatant is skipped.
                if combat_started:
                    combat_enemy_ids.update(
                        enemy.id for enemy in hostiles
                        if enemy.distance_m <= 10 or (target and enemy.id == target.id)
                    )
                elif not combat_enemy_ids:
                    combat_enemy_ids.update(enemy.id for enemy in hostiles)
                if target and (is_attack_action or enemy_attacks):
                    combat_enemy_ids.add(target.id)
                active_hostiles = [
                    enemy for enemy in hostiles if enemy.id in combat_enemy_ids
                ]
                # Keep roster non-empty during active combat.
                if not active_hostiles and hostiles:
                    active_hostiles = hostiles
                turn_state["combat_enemy_ids"] = [
                    enemy.id for enemy in active_hostiles
                ]
                enemy_initiatives = turn_state.setdefault("enemy_initiatives", {})
                for enemy in active_hostiles:
                    if str(enemy.id) not in enemy_initiatives:
                        enemy_initiatives[str(enemy.id)] = CombatService.roll_initiative(
                            (enemy.attack_bonus or 0) // 2
                        )
                active_hostiles.sort(
                    key=lambda entity: enemy_initiatives.get(str(entity.id), 0),
                    reverse=True
                )
                if active_game_session:
                    active_game_session.turn_order = json.dumps(
                        turn_state, ensure_ascii=False
                    )
            for enemy in active_hostiles if enemy_turn_due else []:
                if character.current_hp <= 0:
                    break
                # The model cannot move a known combatant. Explicit charge/approach
                # actions update persisted distance above; otherwise use stored range.
                distance_ft = (
                    target_distance_feet if target and enemy.id == target.id
                    and target_distance_feet is not None
                    else enemy.distance_m * 3.28084
                )
                if distance_ft > 5:
                    enemy.distance_m = max(0, enemy.distance_m - 2)
                    distance_ft = enemy.distance_m * 3.28084
                if distance_ft > 5:
                    enemy_lines.append(f"{enemy.name} не успевает сблизиться.")
                    continue
                enemy_roll = GameEngineService.roll_d20()
                enemy_total = enemy_roll + enemy.attack_bonus
                if enemy_roll == 20 or (enemy_roll != 1 and enemy_total >= player_ac):
                    enemy_damage = _roll_entity_damage(
                        enemy.damage_dice,
                        critical=enemy_roll == 20
                    )
                    old_hp = character.current_hp
                    character.current_hp = max(0, character.current_hp - enemy_damage)
                    enemy_lines.append(
                        f"{enemy.name} находит брешь в защите и попадает. "
                        f"Вы теряете {old_hp - character.current_hp} HP."
                    )
                else:
                    enemy_lines.append(
                        f"{enemy.name} атакует, но вы успеваете уклониться или принять удар на броню."
                    )
            if enemy_lines:
                db.commit()
                enemy_attack_text = (
                    "\n\n🩸 <b>Ход противников:</b>\n"
                    + "\n".join(f"• {line}" for line in enemy_lines)
                    + f"\nВаше HP: {character.current_hp}/{character.max_hp}."
                )
            if character.current_hp <= 0 and not character.is_dead:
                narrative += (
                    "\n\n🩸 Персонаж при смерти. Союзник может бросить спасбросок: "
                    f"<code>/deathsave {character.name}</code>. "
                    "Три успеха поднимут его с 1 HP, три неудачи приведут к смерти."
                )
            if enemy_turn_due and active_game_session:
                active_game_session.combat_active = bool(
                    db.query(CombatEncounter.id).join(
                        GameSession, GameSession.id == CombatEncounter.session_id
                    ).filter(
                        GameSession.group_id == character.group_id,
                        CombatEncounter.is_active.is_(True)
                    ).first()
                )

            if enemy_turn_due and active_game_session is None:
                active_game_session = db.query(GameSession).filter(
                    GameSession.group_id == character.group_id
                ).first()
            if active_encounter and target:
                active_encounter.enemy_hp = target.current_hp
            if active_encounter:
                active_encounter.is_active = bool(active_hostiles)
                if not combat_started:
                    active_encounter.round = (active_encounter.round or 1) + 1
            if active_game_session and enemy_turn_due:
                active_game_session.combat_active = bool(active_hostiles)

            progression_note = ""
            if xp_earned > 0:
                granted_xp, reached_levels, unlocked_features = add_experience(
                    db, character, xp_earned
                )
                progression_note = (
                    f"\n\n✨ Получено опыта: {granted_xp} XP "
                    f"(всего {character.experience}; "
                    f"до следующего уровня: "
                    f"{max(0, XP_THRESHOLDS.get(character.level + 1, character.experience) - character.experience)} XP)."
                )
                if reached_levels:
                    progression_note += (
                        f"\n🎉 Новый уровень: {character.level}! "
                        f"Максимум HP: {character.max_hp}."
                    )
                    if unlocked_features:
                        progression_note += "\nОткрыты особенности: " + ", ".join(unlocked_features) + "."
                narrative += progression_note
            if group_xp_earned > 0:
                party_lines = []
                party = db.query(Character).filter(
                    Character.group_id == character.group_id
                ).all()
                for party_character in party:
                    granted_xp, reached_levels, unlocked_features = add_experience(
                        db, party_character, group_xp_earned
                    )
                    line = f"• {party_character.name}: +{granted_xp} XP"
                    if reached_levels:
                        line += f", новый уровень {party_character.level}"
                        if unlocked_features:
                            line += "; открыто: " + ", ".join(unlocked_features)
                    party_lines.append(line)
                narrative += (
                    "\n\n🎉 <b>Опыт за групповое задание получили все участники:</b>\n"
                    + "\n".join(party_lines)
                )

            new_scene_state = ai_result.get("scene_state", "") if isinstance(ai_result, dict) else ""
            if scene_state and not (
                isinstance(ai_result, dict) and ai_result.get("location_changed")
            ):
                # A response that stays in place cannot silently relocate this
                # character's scene based on another member's recent action.
                new_scene_state = scene_state
            elif not new_scene_state:
                new_scene_state = scene_state

            history_entry = ActionHistory(
                group_id=character.group_id,
                character_id=character.id,
                action_description=action_description,
                ai_response=narrative,
                dice_roll=d20_roll,
                consequences=json.dumps({
                    "damage": damage,
                    "scene_state": new_scene_state[:1200],
                    "entities": [
                        {"name": entity.name, "hp": entity.current_hp, "alive": entity.is_alive,
                         "distance_m": entity.distance_m}
                        for entity in db.query(GameEntity).filter(
                            GameEntity.group_id == character.group_id
                        ).all()
                    ]
                }, ensure_ascii=False)
            )
            db.add(history_entry)
            db.commit()

            # Dice and target statistics stay internal; the DM narrative and
            # combat resolver communicate the outcome without exposing raw rolls.
            roll_text = ""
            if ability_check and check_total is not None and d20_roll is not None:
                ability_key = ability_check[0]
                modifier = check_total - d20_roll
                sign = "+" if modifier >= 0 else "−"
                roll_text = (
                    f"🎲 <b>Проверка: {ability_check[1]}</b> — "
                    f"d20 ({d20_roll}) {sign} {abs(modifier)} = {check_total}\n\n"
                )

            currency_note = ""
            currency_delta = ai_result.get("currency", {}) if isinstance(ai_result, dict) else {}
            if isinstance(currency_delta, dict):
                deltas = {
                    coin: _safe_int(currency_delta.get(coin), 0, -100000, 100000)
                    for coin in ("gold", "silver", "copper")
                }
                if any(deltas.values()):
                    purse = db.query(CharacterCurrency).filter(
                        CharacterCurrency.character_id == character.id
                    ).first()
                    if purse is None:
                        purse = CharacterCurrency(character_id=character.id)
                        db.add(purse)
                        db.flush()
                    # D&D exchange rates: 10 copper = 1 silver; 10 silver = 1 gold.
                    total_copper = max(
                        0,
                        (purse.gold or 0) * 100 + (purse.silver or 0) * 10 + (purse.copper or 0)
                        + deltas["gold"] * 100 + deltas["silver"] * 10 + deltas["copper"]
                    )
                    purse.gold, remainder = divmod(total_copper, 100)
                    purse.silver, purse.copper = divmod(remainder, 10)
                    currency_note = (
                        f"\n\n💰 Кошелёк: {purse.gold} зм, "
                        f"{purse.silver} см, {purse.copper} мм."
                    )
                    db.commit()

            result_text = f"""
⚔️ <b>{character.name}</b> выполняет действие

<b>Действие:</b> {action_description}

{roll_text}
<b>Результат:</b>
{narrative}{player_combat_text}{damage_text}{enemy_attack_text}{currency_note}
"""

            await thinking_msg.delete()
            action_buttons = []
            if character.current_hp > 0:
                action_buttons.append([
                    InlineKeyboardButton(text="➡️ Дальше", callback_data="continue_action")
                ])
            action_buttons.append([
                InlineKeyboardButton(text="🎮 В меню", callback_data="main_menu")
            ])
            keyboard = InlineKeyboardMarkup(inline_keyboard=action_buttons)
            await message.answer(result_text, reply_markup=keyboard)
            if scene_image_kind:
                image_prompt = (
                    f"Create a cinematic tabletop RPG scene illustration. "
                    f"World and genre: {world_desc}. Current place and position: "
                    f"{new_scene_state or scene_state or 'the location described in the action result'}. "
                    f"Visible scene details: {narrative[:1200]}. "
                    "Stay faithful to the established setting and characters. "
                    "Show the environment or battlefield clearly, no text, no lettering, no UI."
                )
                caption = {
                    "combat": "⚔️ Поле боя",
                    "location": "🗺️ Новая локация",
                    "look": "🔎 Вид вокруг",
                }[scene_image_kind]
                await _send_generated_image(message.answer_photo, image_prompt, caption)
            if new_npc_for_image:
                npc_name = str(new_npc_for_image.get("name") or "Новый персонаж")
                npc_description = str(
                    new_npc_for_image.get("description")
                    or new_npc_for_image.get("personality")
                    or "Внешность персонажа описана в сцене."
                )
                npc_prompt = (
                    f"Portrait of an NPC for a tabletop RPG. World and genre: {world_desc}. "
                    f"Name: {npc_name}. Appearance and role: {npc_description[:1200]}. "
                    "Show the character from the waist up, expressive face, clothing and "
                    "distinctive physical features. No text, labels, lettering, or UI."
                )
                await _send_generated_image(
                    message.answer_photo,
                    npc_prompt,
                    f"👤 {npc_name}"
                )
            if character.current_hp <= 0:
                living_party_members = db.query(Character.id).filter(
                    Character.group_id == character.group_id,
                    Character.current_hp > 0,
                    Character.is_dead.is_(False)
                ).count()
                if not living_party_members:
                    if active_encounter:
                        active_encounter.is_active = False
                    if active_game_session:
                        active_game_session.combat_active = False
                        active_game_session.turn_order = None
                db.commit()
                await state.clear()
            elif active_encounter and active_encounter.is_active:
                await state.set_state(CombatStates.in_combat)
                await state.update_data(
                    encounter_id=active_encounter.id,
                    character_id=character.id,
                    target_entity_id=target.id
                )
            else:
                await state.clear()

        except Exception as ai_error:
            logger.error(f"AI error: {ai_error}")
            await thinking_msg.delete()
            await message.answer(format_error(f"Ошибка ИИ: {str(ai_error)}"))
            await state.clear()

    except Exception as e:
        logger.error(f"Error handling action: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
        await state.clear()
    finally:
        db.close()


@router.callback_query(F.data == "continue_action")
async def continue_action_callback(query: types.CallbackQuery, state: FSMContext):
    """Prompt for another action without requiring the /action command."""
    db: Session = SessionLocal()
    try:
        character = resolve_character(db, query.from_user.id, query.message.chat)
        if not character:
            await query.answer("Персонаж не найден", show_alert=True)
            return
        if character.current_hp <= 0:
            await query.message.answer(f"☠️ {character.name} погиб и больше не может действовать.")
            await state.clear()
            await query.answer()
            return

        await query.message.answer(
            f"⚔️ <b>Действие персонажа {character.name}</b>\n\n"
            "Опишите следующее действие:",
            reply_markup=ForceReply(
                selective=False,
                input_field_placeholder="Опишите действие"
            )
        )
        await state.set_state(ActionStates.describing_action)
        await query.answer()
    except Exception:
        logger.exception("Failed to continue action")
        await query.answer("Не удалось продолжить", show_alert=True)
    finally:
        db.close()


@router.message(Command("travel"))
async def travel_command(message: types.Message, state: FSMContext):
    """Travel to a connected location"""
    db: Session = SessionLocal()
    try:
        # Get character
        character = resolve_character(db, message.from_user.id, message.chat)

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        await message.answer(
            "🗺️ <b>Путешествие</b>\n\n"
            "Выберите направление или используйте /location для просмотра текущей локации"
        )

    except Exception as e:
        logger.error(f"Error in travel_command: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("location"))
async def location_command(message: types.Message):
    """Show current location"""
    db: Session = SessionLocal()
    try:
        # Get character
        character = resolve_character(db, message.from_user.id, message.chat)

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        session = db.query(GameSession).filter(
            GameSession.group_id == character.group_id
        ).first()
        if not session or not session.current_location_id:
            await message.answer("❌ Сессия ещё не получила начальную локацию.")
            return
        location = db.query(Location).filter(
            Location.id == session.current_location_id
        ).first()
        if not location:
            await message.answer("❌ Текущая локация не найдена.")
            return
        connections = GroupService.get_connected_locations(db, location.id)
        text = f"📍 <b>{location.name}</b>\n\n{location.description or ''}"
        if connections:
            text += "\n\n<b>Возможные направления:</b>"
            text += "".join(f"\n• {connection[1].name}" for connection in connections)
        await message.answer(text)

    except Exception as e:
        logger.error(f"Error in location_command: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("travel"))
async def travel_command(message: types.Message):
    """Travel command"""
    db: Session = SessionLocal()
    try:
        # Get character
        character = resolve_character(db, message.from_user.id, message.chat)

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        await message.answer(
            "🗺️ <b>Путешествие</b>\n\n"
            "Используйте /location для просмотра текущей локации\n"
            "или /action для выполнения действия"
        )

    except Exception as e:
        logger.error(f"Error in travel_command: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.callback_query(TravelStates.choosing_destination, F.data.startswith("travel_to_"))
async def travel_to_location_callback(query: types.CallbackQuery, state: FSMContext):
    """Travel to selected location"""
    db: Session = SessionLocal()
    try:
        new_location_id = int(query.data.replace("travel_to_", ""))

        # Get character and session
        character = db.query(Character).filter(
            Character.user_id == query.from_user.id,
            Character.group_id == 0
        ).first()

        session = db.query(GameSession).filter(
            GameSession.group_id == character.group_id
        ).first()

        # Move to new location
        GroupService.move_to_location(db, session.id, new_location_id)

        # Get new location info
        location = db.query(Location).filter(Location.id == new_location_id).first()

        # Generate location image description
        ai_service = AIService()
        try:
            from src.models import Group
            group = db.query(Group).filter(Group.id == character.group_id).first()
            world_style = group.world_description if group and group.world_description else "adventure setting"
            image_prompt = (
                f"Cinematic tabletop role-playing location illustration. World style: {world_style}. "
                f"Place: {location.name}. Environment: {location.description}. "
                "Show a wide establishing view with clear environmental details, "
                "coherent atmosphere and lighting. No text, lettering, labels, or interface."
            )
            image_b64 = await ai_service.generate_image(prompt=image_prompt)

            travel_text = f"""
🗺️ <b>Вы прибыли в: {location.name}</b>

📍 <b>Описание:</b>
{location.description}

[🖼️ Изображение локации загружается...]
"""
            msg = await query.message.edit_text(travel_text)

            # Send image if generated
            if image_b64:
                try:
                    image_data = base64.b64decode(image_b64)
                    await query.message.answer_photo(
                        photo=image_data,
                        caption=f"🎨 {location.name}"
                    )
                except Exception as e:
                    logger.warning(f"Could not send image: {e}")

        except Exception as e:
            logger.warning(f"Could not generate image: {e}")
            travel_text = f"""
🗺️ <b>Вы прибыли в: {location.name}</b>

📍 <b>Описание:</b>
{location.description}
"""
            await query.message.edit_text(travel_text)

        await state.clear()

    except Exception as e:
        logger.error(f"Error traveling: {e}")
        await query.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("fight"))
async def fight_command(message: types.Message, state: FSMContext):
    """Combat is started by the DM during /action, never by a generic /fight."""
    await message.answer(
        "Бой начинается по ситуации — когда вы атакуете или противник нападает. "
        "Опишите намерение через /action."
    )


@router.callback_query(CombatStates.in_combat, F.data.startswith("attack_in_"))
async def attack_in_combat_callback(query: types.CallbackQuery, state: FSMContext):
    """Player attacks in combat"""
    db: Session = SessionLocal()
    try:
        data = await state.get_data()
        encounter_id = data.get("encounter_id")
        character_id = data.get("character_id")

        # Get encounter and character
        encounter = db.query(CombatEncounter).filter(
            CombatEncounter.id == encounter_id
        ).first()

        character = db.query(Character).filter(
            Character.id == character_id
        ).first()
        if not encounter or not character:
            await query.answer("Бой или персонаж больше не доступны.", show_alert=True)
            await state.clear()
            return
        if character.current_hp <= 0:
            CombatService.end_combat(db, encounter_id)
            await state.clear()
            await query.answer("Персонаж погиб и больше не может действовать.", show_alert=True)
            return

        attributes = db.query(CharacterAttribute).filter(
            CharacterAttribute.character_id == character_id
        ).first()

        # Resolve attack
        attack_bonus = (attributes.strength - 10) // 2 + 2  # +2 for proficiency
        attack_result = CombatService.resolve_attack(
            attacker_bonus=attack_bonus,
            target_ac=encounter.enemy_ac,
            weapon_damage_dice="d8"
        )

        # Apply damage to enemy
        new_enemy_hp, is_dead = CombatService.apply_damage_to_enemy(
            db, encounter_id, attack_result['damage']
        )

        # Enemy counterattack
        enemy_damage = GameEngineService.roll_dice("d6")
        GameEngineService.apply_damage(character, enemy_damage, db)

        # Generate combat image
        ai_service = AIService()
        image_b64 = None
        try:
            character_name = character.name
            from src.models import Group
            group = db.query(Group).filter(Group.id == character.group_id).first()
            location = (
                db.query(Location).filter(Location.id == encounter.location_id).first()
                if encounter.location_id else None
            )
            combat_image_prompt = (
                f"Cinematic tabletop role-playing battle scene. World style: "
                f"{group.world_description if group and group.world_description else 'fantasy adventure'}. "
                f"Location: {location.name if location else 'the current encounter'}. "
                f"Environment: {location.description if location else 'the encounter field'}. "
                f"Show {character_name} ({character.race} {character.class_}) facing "
                f"{encounter.enemy_name or 'an enemy'} in the current battle. "
                f"Enemy remaining health: {new_enemy_hp}/{encounter.enemy_hp}. "
                "Keep visual continuity with the location and character equipment; "
                "no text, labels, lettering, or UI."
            )
            image_b64 = await ai_service.generate_image(prompt=combat_image_prompt)
        except Exception as e:
            logger.warning(f"Could not generate combat image: {e}")

        # Format response
        combat_text = f"""
⚔️ <b>Раунд боя</b>

<b>Ваша атака:</b>
{attack_result['description']}

<b>Контратака врага:</b>
Враг наносит {enemy_damage} урона!

<b>Статус:</b>
Враг HP: {new_enemy_hp}/20
Ваш HP: {character.current_hp}/{character.max_hp}
"""

        if is_dead:
            combat_text += "\n\n🎉 <b>ПОБЕДА!</b> Враг побежден!"
            await query.message.edit_text(combat_text)
            if image_b64:
                try:
                    image_data = base64.b64decode(image_b64)
                    await query.message.answer_photo(photo=image_data, caption="🎨 Финальный удар!")
                except:
                    pass
            CombatService.end_combat(db, encounter_id)
            await state.clear()
        elif character.current_hp <= 0:
            combat_text += f"\n\n💀 <b>Поражение!</b> {character.name} повержен!"
            await query.message.edit_text(combat_text)
            if image_b64:
                try:
                    image_data = base64.b64decode(image_b64)
                    await query.message.answer_photo(photo=image_data, caption="💀 Последний момент...")
                except:
                    pass
            CombatService.end_combat(db, encounter_id)
            await state.clear()
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚔️ Атаковать", callback_data=f"attack_in_{encounter_id}")]
            ])
            await query.message.edit_text(combat_text, reply_markup=keyboard)
            if image_b64:
                try:
                    image_data = base64.b64decode(image_b64)
                    await query.message.answer_photo(photo=image_data, caption="⚔️ Боевая сцена")
                except:
                    pass

    except Exception as e:
        logger.error(f"Error attacking: {e}")
        await query.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("location"))
async def location_command(message: types.Message):
    """Show current location"""
    db: Session = SessionLocal()
    try:
        # Get user's default group
        default_group = resolve_group(db, message.from_user.id, message.chat)

        if not default_group:
            await message.answer(format_error("Создайте персонажа командой /create_character"))
            return

        # Get character
        character = resolve_character(db, message.from_user.id, message.chat)

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        # Get current session and location
        session = db.query(GameSession).filter(
            GameSession.group_id == default_group.id
        ).first()

        if not session or not session.current_location_id:
            await message.answer("❌ Сессия не активна")
            return

        location = db.query(Location).filter(
            Location.id == session.current_location_id
        ).first()

        # Get connected locations
        connections = GroupService.get_connected_locations(db, session.current_location_id)

        location_text = f"""
📍 <b>{location.name}</b>

{location.description}

<b>Возможные направления:</b>
"""
        for connection in connections:
            location_text += f"\n• {connection[1].name}"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚶 Путешествовать", callback_data="travel_command")]
        ])

        await message.answer(location_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error getting location: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("inventory"))
async def inventory_command(message: types.Message):
    """Show and manage inventory"""
    db: Session = SessionLocal()
    try:
        # Get user's default group
        default_group = resolve_group(db, message.from_user.id, message.chat)

        if not default_group:
            await message.answer(format_error("Создайте персонажа командой /create_character"))
            return

        # Get character
        character = resolve_character(db, message.from_user.id, message.chat)

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        # Get inventory
        inventory_items = InventoryService.get_inventory(db, character.id)

        inventory_text = f"<b>🎒 Инвентарь {character.name}</b>\n\n"

        if inventory_items:
            total_weight = 0
            for idx, row in enumerate(inventory_items, 1):
                item = row["item"]
                if not item:
                    continue
                quantity = row["quantity"]
                equipped = "✓ " if row["is_equipped"] else "  "
                weight = float(item.weight or 0) * quantity
                total_weight += weight
                inventory_text += (
                    f"{idx}. {equipped}<b>{item.name}</b> x{quantity}\n"
                    f"   Вес: {weight:.1f} фн | Тип: {localized_item_type(item.item_type)}\n"
                )
            inventory_text += f"\n<b>Общий вес:</b> {total_weight:.1f} фунтов"
        else:
            inventory_text += "Инвентарь пуст"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 Снять предмет", callback_data="unequip_item")],
            [InlineKeyboardButton(text="🎯 Экипировать", callback_data="equip_item")]
        ])

        await message.answer(inventory_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error showing inventory: {e}")
        await message.answer(format_error(f"Ошибка: {str(e)}"))
    finally:
        db.close()


@router.message(Command("send"))
async def send_currency_command(message: types.Message):
    """Transfer coins to another character in the same campaign."""
    db: Session = SessionLocal()
    try:
        group = resolve_group(db, message.from_user.id, message.chat)
        sender = resolve_character(db, message.from_user.id, message.chat)
        if not group or not sender:
            await message.answer("Сначала создайте персонажа в этом сеттинге.")
            return
        parts = (message.text or "").split(maxsplit=3)
        if len(parts) < 4:
            await message.answer("Формат: /send [количество] [зм/см/мм] [имя персонажа]")
            return
        try:
            amount = int(parts[1])
        except ValueError:
            await message.answer("Количество монет должно быть целым числом.")
            return
        if amount <= 0:
            await message.answer("Можно передать только положительное количество монет.")
            return

        denomination = parts[2].casefold()
        coin_values = {
            "зм": ("gold", 100), "золотых": ("gold", 100), "золотые": ("gold", 100),
            "см": ("silver", 10), "серебряных": ("silver", 10), "серебряные": ("silver", 10),
            "мм": ("copper", 1), "медных": ("copper", 1), "медные": ("copper", 1),
        }
        if denomination not in coin_values:
            await message.answer("Укажите номинал: зм, см или мм.")
            return
        coin_field, copper_value = coin_values[denomination]
        recipient_name = parts[3].strip().strip("\"'«»")
        matches = db.query(Character).filter(
            Character.group_id == group.id,
            Character.name.ilike(recipient_name)
        ).all()
        if len(matches) != 1:
            await message.answer(
                "Персонаж с таким именем не найден в этом сеттинге "
                "или имя неоднозначно."
            )
            return
        recipient = matches[0]
        if recipient.id == sender.id:
            await message.answer("Нельзя передать монеты самому себе.")
            return

        sender_purse = db.query(CharacterCurrency).filter(
            CharacterCurrency.character_id == sender.id
        ).first()
        if not sender_purse:
            await message.answer("В вашем кошельке нет монет.")
            return
        balance = (
            (sender_purse.gold or 0) * 100
            + (sender_purse.silver or 0) * 10
            + (sender_purse.copper or 0)
        )
        transfer_total = amount * copper_value
        if balance < transfer_total:
            await message.answer("Недостаточно монет для перевода.")
            return
        balance -= transfer_total
        sender_purse.gold, remainder = divmod(balance, 100)
        sender_purse.silver, sender_purse.copper = divmod(remainder, 10)
        _credit_character_copper(db, recipient, transfer_total)
        db.commit()
        await message.answer(
            f"💸 {html.escape(sender.name)} передал {amount} {denomination} "
            f"персонажу {html.escape(recipient.name)}."
        )
    except Exception:
        db.rollback()
        logger.exception("Currency transfer failed")
        await message.answer("Не удалось перевести монеты.")
    finally:
        db.close()


@router.message(Command("deathsave"))
async def death_save_command(message: types.Message):
    """An ally rolls a downed character's death save; three failures mean death."""
    db: Session = SessionLocal()
    try:
        group = resolve_group(db, message.from_user.id, message.chat)
        actor = resolve_character(db, message.from_user.id, message.chat)
        if not group or not actor:
            await message.answer("Сначала создайте персонажа в этом сеттинге.")
            return

        requested_name = (message.text or "").partition(" ")[2].strip().strip("\"'«»")
        if not requested_name:
            await message.answer(
                "Бросок делает союзник за павшего персонажа: "
                "/deathsave [имя персонажа]"
            )
            return
        candidates = db.query(Character).filter(
            Character.group_id == group.id,
            Character.name.ilike(requested_name)
        ).all()
        if not candidates:
            candidates = db.query(Character).filter(
                Character.group_id == group.id,
                Character.name.ilike(f"%{requested_name}%")
            ).all()

        if len(candidates) != 1:
            await message.answer(
                "Укажите имя персонажа с 0 HP: /deathsave [имя персонажа]"
            )
            return
        target = candidates[0]
        if target.id == actor.id:
            await message.answer(
                "Спасбросок должен бросить другой участник группы за вас."
            )
            return
        if target.is_dead:
            await message.answer(f"☠️ {html.escape(target.name)} уже погиб.")
            return
        if target.current_hp > 0:
            await message.answer(f"У {html.escape(target.name)} больше нет нужды в спасброске.")
            return
        if (target.death_successes or 0) >= 3:
            await message.answer(
                f"🩹 {html.escape(target.name)} уже стабилизирован. "
                "Для возвращения HP союзник может применить лечение."
            )
            return

        roll = GameEngineService.roll_d20()
        if roll == 20:
            target.current_hp = 1
            target.death_successes = 0
            target.death_failures = 0
            result = "Критический успех! Персонаж приходит в себя с 1 HP."
        else:
            if roll == 1:
                target.death_failures = min(3, (target.death_failures or 0) + 2)
            elif roll >= 10:
                target.death_successes = min(3, (target.death_successes or 0) + 1)
            else:
                target.death_failures = min(3, (target.death_failures or 0) + 1)

            if target.death_failures >= 3:
                target.is_dead = True
                result = "Третья неудача. Персонаж погиб."
            elif target.death_successes >= 3:
                target.current_hp = 0
                result = (
                    "Три успеха! Персонаж стабилизирован при 0 HP. "
                    "Для восстановления сознания нужно лечение."
                )
            else:
                result = (
                    f"Успехи: {target.death_successes}/3; "
                    f"неудачи: {target.death_failures}/3."
                )
        db.commit()
        await message.answer(
            f"🎲 {html.escape(actor.name)} делает спасбросок за "
            f"{html.escape(target.name)}: d20 = {roll}.\n{result}"
        )
    except Exception:
        db.rollback()
        logger.exception("Death save failed")
        await message.answer("Не удалось выполнить спасбросок.")
    finally:
        db.close()


@router.message(Command("help"))
async def help_command(message: types.Message):
    """Show help with available commands"""
    help_text = """
🎲 <b>D&D RPG Bot - Справка</b>

<b>📋 Персонаж:</b>
/create_character - Создать нового персонажа
/profile - Показать профиль персонажа
/inventory - Показать инвентарь
/send [количество] [зм/см/мм] [имя] - Передать монеты союзнику
/deathsave [имя] - Сделать спасбросок за персонажа с 0 HP
/equip <номер> - Экипировать предмет (по номеру из /inventory)
/unequip <номер> - Снять предмет (по номеру из /inventory)
/inspect [имя] - Осмотреть встреченного человека или существо

<b>🎮 Игра:</b>
/action [описание] - Выполнить действие (с d20 проверкой)
/location - Показать текущую локацию
/travel - Путешествовать в соседние локации
/fight - Начать боевое сражение

<b>🗺️ Группа:</b>
/create_group - Создать новую группу/кампанию
/join_group - Присоединиться к группе
/group_info - Информация о группе

<b>⚙️ Система:</b>
/help - Эта справка
/start - Главное меню

<b>Система D&D:</b>
• Характеристики: STR, DEX, CON, INT, WIS, CHA
• Броски: d20 для проверок, другие кубики для урона
• Крит-успех (20) и крит-провал (1)
• Сложность (DC) зависит от действия

Используйте /start для меню!
"""
    await message.answer(help_text)
