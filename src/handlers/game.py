from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from sqlalchemy.orm import Session
from src.models.base import SessionLocal
from src.models import (
    Character, CharacterAttribute, CharacterSpell, Inventory, Item, ActionHistory, GameSession,
    Location, LocationConnection, CombatEncounter, CombatParticipant, GameEntity,
    CampaignNPC, NPCMemory, DnDRules
)
from src.services import (
    AIService, GameEngineService, CharacterService, InventoryService,
    CombatService, GroupService
)
from src.services.open5e_rules_service import (
    build_dm_rules_context, build_creature_catalog, build_item_catalog,
    localize_catalog_item
)
from src.services.open5e_localization import canonical_item_name, localized_item_type
from src.utils import format_error
import logging
import base64
import re
import json
import html
from typing import Optional
from io import BytesIO

logger = logging.getLogger(__name__)
router = Router()


async def _send_generated_image(send_photo, prompt: str, caption: str) -> bool:
    """Generate an image and send it without failing the game turn on image errors."""
    try:
        image_b64 = await AIService().generate_image(prompt=prompt)
        if not image_b64:
            return False
        image = BufferedInputFile(base64.b64decode(image_b64), filename="scene.png")
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
    return [{"name": name.capitalize(), "type": "предмет", "quantity": 1}]


def _safe_int(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def _normalize_entity_name(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", value.casefold()).strip()


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


def _roll_entity_damage(dice: str, critical: bool = False) -> int:
    match = re.fullmatch(r"(\d*)d(\d+)(?:\s*([+-])\s*(\d+))?", dice or "")
    if not match:
        return 1
    count, sides = int(match.group(1) or 1), int(match.group(2))
    if count < 1 or count > 10 or sides < 2 or sides > 100:
        return 1
    if critical:
        count = min(count * 2, 20)
    bonus = int(match.group(4) or 0)
    if match.group(3) == "-":
        bonus *= -1
    return max(0, sum(GameEngineService.roll_dice(f"d{sides}") for _ in range(count)) + bonus)


def _sanitize_custom_item(raw_item: dict) -> Optional[dict]:
    name = str(raw_item.get("name") or "").strip()[:255]
    if not name:
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
        damage_dice = str(raw_item.get("damage_dice") or ("1" if item_type.endswith("improvised") else "1d4")).strip()
        if not re.fullmatch(r"(?:[1-9]|1\d|20|[12]d(?:4|6|8|10|12))", damage_dice):
            return None
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


def _is_looking_around(action: str) -> bool:
    return bool(re.search(
        r"\b(осмотр\w*|осматрива\w*|огляд\w*|look around|look over the area|survey the area)\b|"
        r"(посмотр\w*|изуч\w*)\s+(?:вокруг|местност\w*|окрестност\w*)",
        action.casefold()
    ))


def _is_aggressive_action(action: str) -> bool:
    return bool(re.search(
        r"(атак\w*|напада\w*|удар\w*|бью|бить|стреля\w*|выстрел\w*|"
        r"брос\w*|мет\w*|бег\w*.{0,50}(?:на|к|в сторону)\b|"
        r"нес\w*.{0,50}(?:на|к|в сторону)\b|"
        r"бросаюсь.{0,50}(?:на|к|в сторону)\b|"
        r"налетаю.{0,50}(?:на|к|в сторону)\b|"
        r"кидаюсь.{0,50}(?:на|к|в сторону)\b|"
        r"заряд\w*.{0,50}(?:на|к|в сторону)\b)",
        action.casefold()
    ))


def _is_arena_setting(setting: str) -> bool:
    return bool(re.search(r"арен|гладиатор|колиз|colosseum|gladiator", setting.lower()))


@router.message(Command("inspect"))
async def inspect_entity_command(message: types.Message):
    """Inspect known people and creatures, optionally by name."""
    db: Session = SessionLocal()
    try:
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id
        ).first()
        if not character:
            await message.answer("Сначала создайте персонажа.")
            return

        npcs = db.query(CampaignNPC).filter(
            CampaignNPC.group_id == character.group_id
        ).order_by(CampaignNPC.last_seen_at.desc()).all()
        query_name = (message.text or "").partition(" ")[2].strip()
        if query_name:
            npc = next(
                (candidate for candidate in npcs if candidate.name.casefold() == query_name.casefold()),
                None
            )
            if not npc:
                await message.answer("Такого персонажа или существа пока не встречали.")
                return
            entity = db.query(GameEntity).filter(
                GameEntity.character_id == character.id,
                GameEntity.name.ilike(npc.name)
            ).first()
            memories = db.query(NPCMemory).filter(
                NPCMemory.npc_id == npc.id
            ).order_by(NPCMemory.created_at.asc()).all()
            status = "жив" if npc.is_alive else "погиб"
            visibility = "враждебен" if npc.is_hostile else "не враждебен"
            combat_stats = (
                f"\nHP: {entity.current_hp}/{entity.max_hp} | КД: {entity.armor_class}"
                f"\nАтака: {entity.attack_bonus:+d}, урон: {html.escape(entity.damage_dice)}"
                f"\nДистанция: {entity.distance_m} м"
                if entity else ""
            )
            memory_text = "\n".join(f"• {html.escape(memory.fact)}" for memory in memories)
            await message.answer(
                f"<b>{html.escape(npc.name)}</b> — {html.escape(npc.role or 'NPC')}\n"
                f"{html.escape(npc.description or 'Подробности неизвестны.')}\n"
                f"<b>Личность:</b> {html.escape(npc.personality or 'Неизвестна')}\n"
                f"<b>Последнее известное место:</b> "
                f"{html.escape(npc.current_location or 'Неизвестно')}\n\n"
                f"Состояние: {status}, {visibility}{combat_stats}\n\n"
                f"<b>Что вы о нём знаете:</b>\n{memory_text or 'Пока никаких особых фактов.'}"
            )
        elif npcs:
            lines = ["<b>Знакомые люди и существа:</b>"]
            for npc in npcs:
                entity = db.query(GameEntity).filter(
                    GameEntity.character_id == character.id,
                    GameEntity.name.ilike(npc.name)
                ).first()
                status = (
                    f"{entity.current_hp}/{entity.max_hp} HP"
                    if entity and entity.is_alive
                    else "погиб" if not npc.is_alive else "статус неизвестен"
                )
                lines.append(
                    f"• {html.escape(npc.name)} — {html.escape(npc.role or 'NPC')}, {status}"
                )
            lines.append("\nПодробнее: <code>/inspect имя</code>")
            await message.answer("\n".join(lines))
        else:
            await message.answer("Вы пока не встречали людей или существ, которых можно осмотреть.")
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
        # Get user's default group
        from src.models import Group, GroupMember
        default_group = db.query(Group).join(
            GroupMember
        ).filter(
            GroupMember.user_id == message.from_user.id,
            GroupMember.role == "gm"
        ).first()

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

        await message.answer(
            f"⚔️ <b>Действие персонажа {character.name}</b>\n\n"
            "Опишите, что вы хотите сделать:"
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
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id
        ).first()

        if not character:
            await message.answer(format_error("Персонаж не найден"))
            await state.clear()
            return

        # Get group and world description
        from src.models import Group
        group = db.query(Group).filter(Group.id == character.group_id).first()
        world_desc = group.world_description if group else "Неизвестный мир"
        recent_actions = db.query(ActionHistory).filter(
            ActionHistory.character_id == character.id
        ).order_by(ActionHistory.created_at.desc()).limit(8).all()
        recent_actions.reverse()
        scene_state = ""
        if recent_actions:
            try:
                latest_state = json.loads(recent_actions[-1].consequences or "{}")
                scene_state = latest_state.get("scene_state", "")
            except (json.JSONDecodeError, AttributeError):
                # Older action history stored free-form consequences.
                scene_state = ""

        arena_event_text = ""
        arena_opponent = None
        if _is_arena_setting(world_desc) and _is_waiting_for_opponent(action_description):
            active_opponent = db.query(GameEntity).filter(
                GameEntity.character_id == character.id,
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
                "action": entry.action_description,
                "outcome": (entry.ai_response or "")[:500],
                "roll": entry.dice_roll
            }
            for entry in recent_actions
        ]
        entities = db.query(GameEntity).filter(
            GameEntity.character_id == character.id
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
                "description": entity.description,
                "personality": entity.personality,
                "hp": f"{entity.current_hp}/{entity.max_hp}",
                "ac": entity.armor_class,
                "attack_bonus": entity.attack_bonus,
                "damage_dice": entity.damage_dice,
                "hostile": entity.is_hostile,
                "distance_m": entity.distance_m,
                "alive": entity.is_alive
            }
            for entity in entities
        ]
        campaign_npcs = db.query(CampaignNPC).filter(
            CampaignNPC.group_id == character.group_id
        ).order_by(CampaignNPC.last_seen_at.desc()).limit(30).all()
        npc_context = []
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
                "memories": [memory.fact for memory in memories]
            })

        # Show "thinking" message
        thinking_msg = await message.answer("🤔 ДМ думает...")

        try:
            # Roll before the DM adjudicates the action so the result can affect
            # the narrative and consequences.
            d20_roll = GameEngineService.roll_d20()

            # Call AI to get action result
            ai_result = await AIService.process_action(
                character_name=character.name,
                action_description=action_description,
                world_setting=world_desc,
                d20_roll=d20_roll,
                scene_state=scene_state,
                recent_actions=recent_context,
                known_entities=entity_context,
                known_npcs=npc_context,
                rules_context=build_dm_rules_context(db, character, action_description),
                creature_catalog=build_creature_catalog(db),
                item_catalog=build_item_catalog(db),
                inventory=[
                    row["item"].name
                    for row in InventoryService.get_inventory(db, character.id)
                    if row.get("item")
                ]
            )
            if arena_opponent and isinstance(ai_result, dict):
                ai_result["target_name"] = arena_opponent.name
                ai_result["enemy_attacks"] = arena_opponent.distance_m <= 1

            # Save AI-awarded loot silently; its narrative is shown as usual.
            narrative = ai_result.get("narrative", "") if isinstance(ai_result, dict) else str(ai_result)
            new_hostile_arrived = bool(
                isinstance(ai_result, dict)
                and any(
                    entity.get("is_hostile")
                    for entity in ai_result.get("entities", [])
                    if isinstance(entity, dict)
                )
            )
            scene_image_kind = (
                "combat" if arena_event_text or new_hostile_arrived
                else "location" if isinstance(ai_result, dict) and ai_result.get("location_changed")
                else "look" if _is_looking_around(action_description)
                else None
            )
            if arena_event_text:
                narrative = f"{arena_event_text}\n\n{narrative}"
            for found_entity in ai_result.get("entities", []) if isinstance(ai_result, dict) else []:
                name = str(found_entity.get("name", "")).strip()[:255]
                if not name:
                    continue
                existing_entity = db.query(GameEntity).filter(
                    GameEntity.character_id == character.id,
                    GameEntity.name.ilike(name)
                ).first()
                if existing_entity:
                    continue
                max_hp = _safe_int(found_entity.get("max_hp"), 10, 1, 500)
                dice = str(found_entity.get("damage_dice") or "d4")[:20]
                armor_class = _safe_int(found_entity.get("armor_class"), 10, 5, 30)
                attack_bonus = _safe_int(found_entity.get("attack_bonus"), 0, -5, 20)
                entity_type = str(found_entity.get("entity_type") or "person")[:100]
                open5e_key = str(found_entity.get("open5e_key") or "").strip()
                if open5e_key:
                    official_row = db.query(DnDRules.rule_data).filter(
                        DnDRules.rule_category == "open5e_creatures",
                        DnDRules.rule_name.like(f"%:{open5e_key}")
                    ).first()
                    official = official_row[0] if official_row else None
                    if isinstance(official, dict):
                        max_hp = _safe_int(official.get("hit_points"), max_hp, 1, 5000)
                        armor_class = _safe_int(official.get("armor_class"), armor_class, 5, 40)
                        entity_type = str(
                            (official.get("type") or {}).get("name")
                            if isinstance(official.get("type"), dict)
                            else official.get("type") or entity_type
                        )[:100]
                        first_attack = next(
                            (
                                attack
                                for action in official.get("actions", [])
                                if isinstance(action, dict)
                                for attack in action.get("attacks", [])
                                if isinstance(attack, dict)
                            ),
                            None
                        )
                        if first_attack:
                            attack_bonus = _safe_int(
                                first_attack.get("to_hit_mod"), attack_bonus, -20, 40
                            )
                            attack_text = next(
                                (
                                    action.get("desc", "")
                                    for action in official.get("actions", [])
                                    if isinstance(action, dict) and action.get("attacks")
                                ),
                                ""
                            )
                            damage_match = re.search(
                                r"\((\d+d\d+(?:\s*[+-]\s*\d+)?)\)",
                                attack_text,
                                flags=re.IGNORECASE
                            )
                            if damage_match:
                                dice = re.sub(r"\s+", "", damage_match.group(1))[:20]
                            elif first_attack.get("damage_die_type"):
                                dice = (
                                    f"{_safe_int(first_attack.get('damage_die_count'), 1, 1, 10)}"
                                    f"d{str(first_attack['damage_die_type']).lower().lstrip('d')}"
                                )
                if not re.fullmatch(r"\d*d\d+(?:[+-]\d+)?", dice):
                    dice = "d4"
                db.add(GameEntity(
                    group_id=character.group_id,
                    character_id=character.id,
                    name=name,
                    entity_type=entity_type,
                    description=str(found_entity.get("description") or "")[:2000],
                    personality=str(found_entity.get("personality") or "")[:1000],
                    current_hp=max_hp,
                    max_hp=max_hp,
                    armor_class=armor_class,
                    attack_bonus=attack_bonus,
                    damage_dice=dice,
                    distance_m=_safe_int(found_entity.get("distance_m"), 5, 0, 1000),
                    is_hostile=bool(found_entity.get("is_hostile", False)),
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
            db.flush()

            # Persist identities and update whereabouts for campaign-wide NPCs.
            if isinstance(ai_result, dict):
                for npc_data in ai_result.get("npcs", []):
                    npc_name = str(npc_data.get("name", "")).strip()[:255]
                    if not npc_name:
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

            homebrew_spell_warning = ""
            newly_added_improvised_weapons = []
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
                        if custom_item.item_type == "weapon:improvised":
                            newly_added_improvised_weapons.append(custom_item)
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
            target = None
            if target_name:
                target = db.query(GameEntity).filter(
                    GameEntity.character_id == character.id,
                    GameEntity.name.ilike(target_name),
                    GameEntity.is_alive.is_(True)
                ).first()

            lower_action = action_description.lower()
            is_attack_action = _is_aggressive_action(action_description)
            enemy_attacks = bool(
                isinstance(ai_result, dict) and ai_result.get("enemy_attacks")
            )
            if target is None and (is_attack_action or enemy_attacks):
                living_entities = db.query(GameEntity).filter(
                    GameEntity.character_id == character.id,
                    GameEntity.is_alive.is_(True)
                ).all()
                if len(living_entities) == 1:
                    target = living_entities[0]

            if target and re.search(r"\b(подхожу|приближаюсь|подойти|иду к|направляюсь к)\b", lower_action):
                target.distance_m = max(0, target.distance_m - 3)
                narrative += (
                    f"\n\n📏 До {target.name} осталось примерно {target.distance_m} м."
                    if target.distance_m > 1
                    else f"\n\n📏 Вы подошли вплотную к {target.name}."
                )

            active_encounter = None
            active_game_session = None
            combat_started = False
            initiative_result = None
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
                scene_image_kind = "combat"
                if combat_started:
                    player_initiative, enemy_initiative = initiative_result
                    narrative += (
                        f"\n\n⚔️ <b>Начинается бой!</b> Инициатива: "
                        f"{character.name} — {player_initiative}, "
                        f"{target.name} — {enemy_initiative}."
                    )

            if is_attack_action and target:
                carried_weapons = db.query(Item).join(
                    Inventory, Inventory.item_id == Item.id
                ).filter(
                    Inventory.character_id == character.id,
                    Item.item_type.like("weapon:%")
                ).order_by(Item.name).all()
                requested_weapon = ai_result.get("weapon_name") if isinstance(ai_result, dict) else None
                weapon = next(
                    (
                        candidate for candidate in carried_weapons
                        if requested_weapon and candidate.name.casefold() == requested_weapon.casefold()
                    ),
                    None
                ) or (newly_added_improvised_weapons[-1] if newly_added_improvised_weapons else None)
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
                target_distance_feet = target.distance_m * 3.28084
                if is_ranged_weapon and weapon_range:
                    normal_range_feet = int(weapon_range.group(1))
                    long_range_feet = int(weapon_range.group(2) or normal_range_feet)
                    if target_distance_feet > long_range_feet:
                        narrative += (
                            f"\n\n❌ {target.name} вне дальности {weapon.name} "
                            f"({long_range_feet} фт.)."
                        )
                        weapon_out_of_range = True
                    else:
                        weapon_out_of_range = False
                else:
                    weapon_out_of_range = False

                if weapon_out_of_range:
                    pass
                elif target.distance_m > 1 and not is_ranged_weapon:
                    narrative += f"\n\n❌ {target.name} слишком далеко для удара в ближнем бою."
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
                    attack_bonus = ability_modifier + GameEngineService.calculate_proficiency_bonus(character.level)
                    attack_roll = d20_roll
                    long_range_disadvantage = bool(
                        is_ranged_weapon and weapon_range
                        and target_distance_feet > int(weapon_range.group(1))
                    )
                    if long_range_disadvantage:
                        second_roll = GameEngineService.roll_d20()
                        attack_roll = min(d20_roll, second_roll)
                    attack_total = attack_roll + attack_bonus
                    if attack_roll == 20 or (attack_roll != 1 and attack_total >= target.armor_class):
                        damage_dice = weapon.damage_dice if weapon and weapon.damage_dice else "d4"
                        fixed_damage = damage_dice.isdigit()
                        damage = (
                            int(damage_dice)
                            if fixed_damage
                            else max(1, _roll_entity_damage(damage_dice) + ability_modifier)
                        )
                        if attack_roll == 20 and not fixed_damage:
                            damage += _roll_entity_damage(damage_dice)
                        target.current_hp = max(0, target.current_hp - damage)
                        if target.current_hp == 0:
                            target.is_alive = False
                            npc_record = db.query(CampaignNPC).filter(
                                CampaignNPC.group_id == character.group_id,
                                CampaignNPC.name.ilike(target.name)
                            ).first()
                            if npc_record:
                                npc_record.is_alive = False
                        damage_type = (
                            DAMAGE_TYPES_RU.get(
                                (weapon.damage_type or "").casefold(),
                                weapon.damage_type or ""
                            )
                            if weapon else ""
                        )
                        damage_label = f" урона ({damage_type})" if damage_type else " урона"
                        narrative += (
                            f"\n\n⚔️ {weapon.name if weapon else 'Безоружная атака'}: бросок "
                            f"{f'{d20_roll} и {second_roll}' if long_range_disadvantage else str(d20_roll)}"
                            f"{' (помеха)' if long_range_disadvantage else ''}+{attack_bonus} против КД "
                            f"{target.armor_class}. Попадание! {target.name} получает "
                            f"{damage}{damage_label}: "
                            f"{target.current_hp}/{target.max_hp} HP."
                        )
                        if not target.is_alive:
                            narrative += f"\n☠️ {target.name} повержен."
                    else:
                        narrative += (
                            f"\n\n⚔️ {weapon.name if weapon else 'Безоружная атака'}: бросок "
                            f"{f'{d20_roll} и {second_roll}' if long_range_disadvantage else str(d20_roll)}"
                            f"{' (помеха)' if long_range_disadvantage else ''}+{attack_bonus} против КД "
                            f"{target.armor_class}. Промах — урона нет."
                        )

            loot = ai_result.get("loot", []) if isinstance(ai_result, dict) else []
            if not loot:
                loot = _loot_from_explicit_action(action_description)
            for found in loot:
                if not isinstance(found, dict):
                    continue
                name = str(found.get("name", "")).strip()[:255]
                if not name:
                    continue
                try:
                    quantity = max(1, min(int(found.get("quantity", 1)), 1000))
                except (TypeError, ValueError):
                    quantity = 1
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
                    InventoryService.add_item(db, character.id, item.id, quantity=quantity)

            if homebrew_spell_warning:
                narrative += homebrew_spell_warning

            damage_text = ""
            damage = ai_result.get("damage", 0) if isinstance(ai_result, dict) else 0
            if isinstance(ai_result, dict) and ai_result.get("damage_dice"):
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
                    damage_text += "\n😵 Персонаж без сознания."

            enemy_attack_text = ""
            if isinstance(ai_result, dict) and ai_result.get("enemy_attacks") and target and target.is_hostile and target.is_alive:
                attributes = db.query(CharacterAttribute).filter(
                    CharacterAttribute.character_id == character.id
                ).first()
                player_ac = GameEngineService.calculate_armor_class(character, attributes, db)
                enemy_roll = GameEngineService.roll_d20()
                if enemy_roll == 20 or (enemy_roll != 1 and enemy_roll + target.attack_bonus >= player_ac):
                    enemy_damage = _roll_entity_damage(
                        target.damage_dice,
                        critical=enemy_roll == 20
                    )
                    old_hp = character.current_hp
                    character.current_hp = max(0, character.current_hp - enemy_damage)
                    db.commit()
                    enemy_attack_text = (
                        f"\n\n🩸 {target.name} атакует: бросок {enemy_roll}+"
                        f"{target.attack_bonus} против вашей КД {player_ac} — попадание, "
                        f"{old_hp - character.current_hp} урона. "
                        f"Ваше HP: {character.current_hp}/{character.max_hp}."
                    )
                else:
                    enemy_attack_text = (
                        f"\n\n🛡️ {target.name} атакует, но промахивается "
                        f"(бросок {enemy_roll}+{target.attack_bonus} против КД {player_ac})."
                    )

            if active_encounter and target:
                active_encounter.enemy_hp = target.current_hp
                if not target.is_alive:
                    active_encounter.is_active = False
                elif not combat_started:
                    active_encounter.round = (active_encounter.round or 1) + 1
                if active_game_session:
                    active_game_session.combat_active = bool(
                        db.query(CombatEncounter.id).filter(
                            CombatEncounter.session_id == active_game_session.id,
                            CombatEncounter.is_active.is_(True)
                        ).first()
                    )

            new_scene_state = ai_result.get("scene_state", "") if isinstance(ai_result, dict) else ""

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
                            GameEntity.character_id == character.id
                        ).all()
                    ]
                }, ensure_ascii=False)
            )
            db.add(history_entry)
            db.commit()

            roll_text = f"🎲 <b>Результат броска:</b> {d20_roll}\n\n"

            result_text = f"""
⚔️ <b>{character.name}</b> выполняет действие

<b>Действие:</b> {action_description}

{roll_text}
<b>Результат:</b>
{narrative}{damage_text}{enemy_attack_text}
"""

            await thinking_msg.delete()
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➡️ Дальше", callback_data="continue_action")]
            ])
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
            if active_encounter and active_encounter.is_active:
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
        character = db.query(Character).filter(
            Character.user_id == query.from_user.id
        ).first()
        if not character:
            await query.answer("Персонаж не найден", show_alert=True)
            return

        await query.message.answer(
            f"⚔️ <b>Действие персонажа {character.name}</b>\n\n"
            "Опишите следующее действие:"
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
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id
        ).first()

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
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id
        ).first()

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        # Get group and world description
        from src.models import Group
        group = db.query(Group).filter(Group.id == character.group_id).first()
        world_desc = group.world_description if group else "Неизвестный мир"

        location_text = f"""
📍 <b>Текущая локация</b>

Ваш персонаж: <b>{character.name}</b>

<b>Мир:</b>
{world_desc}

Используй /action для выполнения действия
"""

        await message.answer(location_text)

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
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id
        ).first()

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
        from src.models import Group, GroupMember
        default_group = db.query(Group).join(
            GroupMember
        ).filter(
            GroupMember.user_id == message.from_user.id,
            GroupMember.role == "gm"
        ).first()

        if not default_group:
            await message.answer(format_error("Создайте персонажа командой /create_character"))
            return

        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id,
            Character.group_id == default_group.id
        ).first()

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
        from src.models import Group, GroupMember
        default_group = db.query(Group).join(
            GroupMember
        ).filter(
            GroupMember.user_id == message.from_user.id,
            GroupMember.role == "gm"
        ).first()

        if not default_group:
            await message.answer(format_error("Создайте персонажа командой /create_character"))
            return

        # Get character
        character = db.query(Character).filter(
            Character.user_id == message.from_user.id,
            Character.group_id == default_group.id
        ).first()

        if not character:
            await message.answer("❌ У вас нет персонажа")
            return

        # Get inventory
        inventory_items = db.query(Inventory).filter(
            Inventory.character_id == character.id
        ).all()

        inventory_text = f"<b>🎒 Инвентарь {character.name}</b>\n\n"

        if inventory_items:
            total_weight = 0
            for idx, inv in enumerate(inventory_items, 1):
                item = db.query(Item).filter(Item.id == inv.item_id).first()
                equipped = "✓ " if inv.is_equipped else "  "
                weight = float(item.weight or 0) * inv.quantity
                total_weight += weight
                inventory_text += (
                    f"{idx}. {equipped}<b>{item.name}</b> x{inv.quantity}\n"
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


@router.message(Command("help"))
async def help_command(message: types.Message):
    """Show help with available commands"""
    help_text = """
🎲 <b>D&D RPG Bot - Справка</b>

<b>📋 Персонаж:</b>
/create_character - Создать нового персонажа
/profile - Показать профиль персонажа
/inventory - Управлять инвентарём
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
