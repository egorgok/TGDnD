"""Read the bundled D&D 5e 2024 SRD for character sheets and DM adjudication."""
import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import (
    CharacterAbility, CharacterCurrency, CharacterSpell, DnDRules, Inventory, Item
)
from src.services.open5e_localization import (
    canonical_item_name,
    canonical_spell_name,
    localized_feature_description,
    localized_feature_name,
    localized_item_name,
    localized_spell_name,
)


CLASS_KEYS = {
    "Варвар": "srd_barbarian",
    "Бард": "srd_bard",
    "Жрец": "srd_cleric",
    "Друид": "srd_druid",
    "Боец": "srd_fighter",
    "Монах": "srd_monk",
    "Паладин": "srd_paladin",
    "Рейнджер": "srd_ranger",
    "Плут": "srd_rogue",
    "Чародей": "srd_sorcerer",
    "Волшебник": "srd_wizard",
    "Ведьмак": "srd_warlock",
}

SPELL_STARTING_COUNTS = {
    "Бард": (2, 4),
    "Жрец": (3, 2),
    "Друид": (2, 2),
    "Монах": (0, 0),
    "Паладин": (0, 2),
    "Рейнджер": (0, 2),
    "Чародей": (4, 2),
    "Волшебник": (3, 6),
    "Ведьмак": (2, 2),
}


def _srd2024_records(db: Session, category: str) -> list[dict]:
    return [
        data for (data,) in db.query(DnDRules.rule_data).filter(
            DnDRules.rule_category == f"srd2024_{category}"
        ).all()
        if isinstance(data, dict)
    ]


def _srd2024_record(db: Session, category: str, key: str) -> dict | None:
    return next(
        (
            record for record in _srd2024_records(db, category)
            if str(record.get("key") or record.get("index") or "").casefold()
            == key.casefold()
        ),
        None
    )


def _srd2024_class_record(db: Session, class_key: str) -> dict | None:
    class_index = class_key.removeprefix("srd_")
    class_data = _srd2024_record(db, "classes", class_index)
    if not class_data:
        return None
    levels = [
        record for record in _srd2024_records(db, "levels")
        if isinstance(record.get("class"), dict)
        and record["class"].get("index") == class_index
    ]
    feature_records = {
        str(feature.get("index") or feature.get("key") or ""): feature
        for feature in _srd2024_records(db, "features")
    }
    features = []
    for level_data in sorted(levels, key=lambda item: int(item.get("level") or 0)):
        level = int(level_data.get("level") or 0)
        for reference in level_data.get("features", []):
            if not isinstance(reference, dict):
                continue
            feature = feature_records.get(str(reference.get("index") or ""))
            if not feature:
                continue
            localized = feature.get("localized_ru") or {}
            features.append({
                "name": localized.get("name") or feature.get("name"),
                "canonical_name": feature.get("name"),
                "desc": localized.get("description") or feature.get("description") or "",
                "feature_type": "CLASS_FEATURE",
                "gained_at": [{"level": level}],
            })
    localized_class = class_data.get("localized_ru") or {}
    equipment_options = (
        localized_class.get("starting_equipment_options")
        or class_data.get("starting_equipment_options")
        or []
    )
    features.append({
        "name": "Стартовое снаряжение",
        "desc": "\n".join(
            str(option.get("desc") or "")
            for option in equipment_options if isinstance(option, dict)
        ),
        "feature_type": "STARTING_EQUIPMENT",
    })
    return {
        **class_data,
        "features": features,
        "starting_equipment_options": equipment_options,
        "source_edition": "2024",
    }


def _trait_descriptions(db: Session, references: list[dict]) -> list[tuple[str, str]]:
    result = []
    for reference in references:
        if not isinstance(reference, dict):
            continue
        key = str(reference.get("index") or "")
        record = _srd2024_record(db, "traits", key) if key else None
        localized = (record or {}).get("localized_ru") or {}
        name = str(
            localized.get("name") or reference.get("name") or
            (record or {}).get("name") or ""
        ).strip()
        description = str(
            localized.get("description")
            or (record or {}).get("description")
            or "Особенность вида из SRD 2024."
        ).strip()
        if name:
            result.append((name, description))
    return result


def grant_starting_species_data(db: Session, character) -> int:
    """Persist the chosen 2024 species traits for a newly created character."""
    from src.services.rules_service import RulesService

    species_key = RulesService.SPECIES_KEYS.get(character.race)
    species = _srd2024_record(db, "species", species_key) if species_key else None
    if not species:
        return 0
    localized_species = species.get("localized_ru") or {}
    references = localized_species.get("traits") or species.get("traits") or []
    added = 0
    for trait_name, description in _trait_descriptions(db, references):
        exists = db.query(CharacterAbility.id).filter(
            CharacterAbility.character_id == character.id,
            CharacterAbility.ability_name == trait_name
        ).first()
        if exists:
            continue
        db.add(CharacterAbility(
            character_id=character.id,
            ability_name=trait_name,
            ability_description=description[:5000],
            is_racial=True,
            is_class=False
        ))
        added += 1
    db.commit()
    return added


def grant_starting_background_data(db: Session, character, background_key: str) -> dict[str, int]:
    """Grant a 2024 background's origin feat, proficiencies, and starting gear."""
    background = _srd2024_record(db, "backgrounds", background_key)
    if not background:
        return {"features": 0, "items": 0}
    localized = background.get("localized_ru") or {}
    features_added = 0
    feat_ref = localized.get("feat") or background.get("feat")
    if isinstance(feat_ref, dict) and feat_ref.get("index"):
        feat = _srd2024_record(db, "feats", str(feat_ref["index"]))
        feat_localized = (feat or {}).get("localized_ru") or {}
        feat_name = str(
            feat_localized.get("name") or feat_ref.get("name") or "Черта предыстории"
        )
        feat_description = str(
            feat_localized.get("description")
            or (feat or {}).get("description")
            or "Черта происхождения по SRD 2024."
        )
        ability_name = f"Черта предыстории: {feat_name}"
        exists = db.query(CharacterAbility.id).filter(
            CharacterAbility.character_id == character.id,
            CharacterAbility.ability_name == ability_name
        ).first()
        if not exists:
            db.add(CharacterAbility(
                character_id=character.id,
                ability_name=ability_name,
                ability_description=feat_description[:5000],
                is_racial=False,
                is_class=False
            ))
            features_added += 1

    proficiencies = localized.get("proficiencies") or background.get("proficiencies") or []
    for proficiency in proficiencies:
        if not isinstance(proficiency, dict):
            continue
        label = str(proficiency.get("name") or "").strip()
        if not label:
            continue
        ability_name = f"Владение: {label}"
        if db.query(CharacterAbility.id).filter(
            CharacterAbility.character_id == character.id,
            CharacterAbility.ability_name == ability_name
        ).first():
            continue
        db.add(CharacterAbility(
            character_id=character.id,
            ability_name=ability_name,
            ability_description="Владение, предоставленное предысторией SRD 2024.",
            is_racial=False,
            is_class=False
        ))
        features_added += 1

    gear_data = {
        "starting_equipment_options": (
            localized.get("equipment_options")
            or background.get("equipment_options")
            or []
        )
    }
    item_count = _apply_srd2024_starting_equipment(db, character, gear_data)
    db.commit()
    return {"features": features_added, "items": item_count}


def _class_record(db: Session, class_name: str) -> dict | None:
    expected_key = CLASS_KEYS.get(class_name)
    if not expected_key:
        return None
    srd2024_class = _srd2024_class_record(db, expected_key)
    if srd2024_class:
        return srd2024_class
    return None


def open5e_class_is_available(db: Session, class_name: str) -> bool:
    record = _class_record(db, class_name)
    return bool(
        record
        and any(
            isinstance(feature, dict)
            and feature.get("feature_type") == "STARTING_EQUIPMENT"
            for feature in record.get("features", [])
        )
    )


def localize_catalog_item(
    db: Session,
    source_item: Item,
    requested_display_name: str | None = None
) -> Item:
    display_name = localized_item_name(source_item.name)
    if display_name == source_item.name and requested_display_name:
        if re.search(r"[а-яё]", requested_display_name.casefold()):
            display_name = requested_display_name[:255]
    if display_name == source_item.name:
        return source_item

    localized = db.query(Item).filter(
        func.lower(Item.name) == display_name.casefold()
    ).first()
    if localized is None:
        description = "Стартовое снаряжение по правилам SRD."
        range_match = re.search(
            r"Range:\s*(\d+)(?:/(\d+))?\s*feet",
            source_item.description or "",
            flags=re.IGNORECASE
        )
        if range_match:
            distance = range_match.group(1)
            if range_match.group(2):
                distance += f"/{range_match.group(2)}"
            description += f" Дальность: {distance} футов."
        localized = Item(
            name=display_name,
            item_type=source_item.item_type,
            rarity=source_item.rarity,
            weight=source_item.weight,
            description=description,
            damage_dice=source_item.damage_dice,
            damage_type=source_item.damage_type,
            armor_class=source_item.armor_class,
            is_stackable=source_item.is_stackable
        )
        db.add(localized)
        db.flush()
    return localized


def _normalize_words(value: str) -> str:
    value = value.replace("’", "'").casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _plural(word: str) -> str:
    if word.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    if word.endswith("y") and len(word) > 1:
        return word[:-1] + "ies"
    if word.endswith("fe"):
        return word[:-2] + "ves"
    if word.endswith("f"):
        return word[:-1] + "ves"
    return word + "s"


def _equipment_matches(text: str, catalog: list[Item]) -> list[tuple[Item, int]]:
    normalized = _normalize_words(text)
    candidates: list[tuple[int, int, Item, tuple[int, int]]] = []
    for item in catalog:
        normalized_name = _normalize_words(item.name)
        if not normalized_name:
            continue
        name_variants = [normalized_name]
        without_parenthetical = _normalize_words(re.sub(r"\([^)]*\)", "", item.name))
        if without_parenthetical and without_parenthetical not in name_variants:
            name_variants.append(without_parenthetical)
        if "," in item.name:
            reversed_name = _normalize_words(" ".join(reversed(item.name.split(",", 1))))
            if reversed_name and reversed_name not in name_variants:
                name_variants.append(reversed_name)
        if "crossbow bolt" in normalized_name:
            name_variants.extend(variant for variant in ("bolt", "bolts") if variant not in name_variants)
        tokens = normalized_name.split()
        if tokens:
            plural_name = " ".join(tokens[:-1] + [_plural(tokens[-1])])
            if plural_name not in name_variants:
                name_variants.append(plural_name)
        found_spans = []
        for variant in name_variants:
            match = re.search(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])", normalized)
            if match:
                found_spans.append(match.span())
        if found_spans:
            found_span = max(found_spans, key=lambda span: (span[1] - span[0], -span[0]))
            candidates.append((found_span[0], found_span[1] - found_span[0], item, found_span))

    selected: list[tuple[int, Item, int, tuple[int, int]]] = []
    occupied_spans: list[tuple[int, int]] = []
    for start, _, item, found_span in sorted(
        candidates,
        key=lambda candidate: (-candidate[1], candidate[0], -len(_normalize_words(candidate[2].name)))
    ):
        if any(left < found_span[1] and found_span[0] < right for left, right in occupied_spans):
            continue
        before = normalized[max(0, found_span[0] - 16):found_span[0]].strip()
        number_match = re.search(
            r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
            r"twenty|thirty|forty|fifty|\d+)$",
            before
        )
        quantity_words = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30,
            "forty": 40, "fifty": 50
        }
        quantity = 1
        if number_match:
            token = number_match.group(1)
            quantity = quantity_words.get(token, int(token) if token.isdigit() else 1)
        selected.append((start, item, min(quantity, 100), found_span))
        occupied_spans.append(found_span)
    return [
        (item, quantity)
        for _, item, quantity, _ in sorted(selected, key=lambda entry: entry[0])
    ]


def _default_equipment_option(bullet: str, catalog: list[Item]) -> list[tuple[Item, int]]:
    marked_options = re.findall(
        r"\(\*[a-z]\*\)\s*(.*?)(?=(?:,\s*)?\(\*[a-z]\*\)|$)",
        bullet,
        flags=re.IGNORECASE | re.DOTALL
    )
    if marked_options:
        for option in marked_options:
            matches = _equipment_matches(option, catalog)
            generic_option = re.search(
                r"\b(?:martial|simple)(?:\s+melee)?\s+weapon\b",
                option,
                flags=re.IGNORECASE
            )
            if matches or generic_option:
                chosen_text = option
                break
        else:
            return []
    else:
        chosen_text = bullet
        matches = _equipment_matches(chosen_text, catalog)

    has_weapon = any(item.item_type.startswith("weapon:") for item, _ in matches)
    if not has_weapon:
        generic_weapon = None
        chosen_lower = chosen_text.casefold()
        if "martial weapon" in chosen_lower:
            generic_weapon = next((item for item in catalog if item.name.casefold() == "longsword"), None)
        elif re.search(r"\bsimple(?:\s+melee)?\s+weapon\b", chosen_lower):
            generic_weapon = next((item for item in catalog if item.name.casefold() == "dagger"), None)
        if generic_weapon:
            quantity = 2 if re.search(r"\btwo\s+martial\s+weapons?\b", chosen_lower) else 1
            matches.append((generic_weapon, quantity))
    return matches


def _ensure_missing_starting_gear(db: Session, description: str, catalog: list[Item]) -> list[Item]:
    """Create simple catalog entries for SRD starting items absent from API item lists."""
    patterns = (
        (r"\bexplorer[’']?s\s+pack\b", "Explorer's Pack"),
        (r"\bdungeoneer[’']?s\s+pack\b", "Dungeoneer's Pack"),
        (r"\bpriest[’']?s\s+pack\b", "Priest's Pack"),
        (r"\bdiplomat[’']?s\s+pack\b", "Diplomat's Pack"),
        (r"\bentertainer[’']?s\s+pack\b", "Entertainer's Pack"),
        (r"\bburglar[’']?s\s+pack\b", "Burglar's Pack"),
        (r"\bscholar[’']?s\s+pack\b", "Scholar's Pack"),
        (r"\bdruidic\s+focus\b", "Druidic Focus"),
        (r"\barcane\s+focus\b", "Arcane Focus"),
        (r"\bholy\s+symbol\b", "Holy Symbol"),
        (r"\b(\d+|ten|twenty)\s+arrows?\b", "Arrow (bow)"),
        (r"\b(\d+|twenty)\s+bolts?\b", "Crossbow bolt"),
    )
    existing_names = {item.name.casefold() for item in catalog}
    for pattern, name in patterns:
        if not re.search(pattern, description, flags=re.IGNORECASE):
            continue
        if name.casefold() in existing_names:
            continue
        item = db.query(Item).filter(func.lower(Item.name) == name.casefold()).first()
        if item is None:
            item = Item(
                name=name,
                item_type="gear",
                description="Class starting equipment from the 2024 SRD.",
                is_stackable=name in {"Arrow (bow)", "Crossbow bolt"}
            )
            db.add(item)
            db.flush()
        catalog.append(item)
        existing_names.add(name.casefold())
    return catalog


def _apply_srd2024_starting_equipment(db: Session, character, class_record: dict) -> int:
    """Grant the first listed 2024 class equipment option once at creation."""
    option_groups = class_record.get("starting_equipment_options") or []
    if not option_groups:
        return 0
    equipment_by_index = {
        str(record.get("key") or record.get("index") or ""): record
        for record in _srd2024_records(db, "equipment")
    }
    chosen: dict[str, int] = {}
    gold = silver = copper = 0

    def visit(node):
        nonlocal gold, silver, copper
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if not isinstance(node, dict):
            return
        option_type = str(node.get("option_type") or "").casefold()
        if option_type == "money":
            unit = str(node.get("unit") or "").casefold()
            amount = max(0, int(node.get("count") or node.get("quantity") or 0))
            if unit in {"gp", "зм"}:
                gold += amount
            elif unit in {"sp", "см"}:
                silver += amount
            elif unit in {"cp", "мм"}:
                copper += amount
            return
        if option_type == "counted_reference":
            reference = node.get("of") or node.get("item") or {}
            if isinstance(reference, dict):
                key = str(reference.get("index") or "").strip()
                if key:
                    chosen[key] = chosen.get(key, 0) + max(
                        1, int(node.get("count") or 1)
                    )
            return
        if option_type == "reference":
            reference = node.get("item") or node.get("of") or {}
            if isinstance(reference, dict) and reference.get("index"):
                key = str(reference["index"])
                chosen[key] = chosen.get(key, 0) + 1
            return
        if option_type == "multiple":
            visit(node.get("items") or [])
            return
        from_data = node.get("from")
        if isinstance(from_data, dict):
            available = from_data.get("options") or []
            if available:
                # Deterministic default: take the first official listed option.
                visit(available[0])
            return
        options = node.get("options")
        if isinstance(options, list) and options:
            visit(options[0])
        for child_key in ("items", "equipment", "starting_equipment"):
            if child_key in node:
                visit(node[child_key])

    # Each group is one independent choice. Select its first official option.
    for group in option_groups:
        if isinstance(group, dict):
            source = group.get("from") or {}
            options = source.get("options") if isinstance(source, dict) else None
            if isinstance(options, list) and options:
                visit(options[0])
            else:
                visit(group)

    added = 0
    for index, quantity in chosen.items():
        source_item = equipment_by_index.get(index)
        localized = (source_item or {}).get("localized_ru") or {}
        display_name = (
            localized.get("name")
            or (source_item or {}).get("name")
            or index.replace("-", " ").title()
        )
        item = db.query(Item).filter(
            func.lower(Item.name) == str(display_name).casefold()
        ).first()
        if item is None:
            item = Item(
                name=str(display_name)[:255],
                item_type="gear",
                description="Стартовое снаряжение по SRD D&D 2024.",
                is_stackable=quantity > 1
            )
            db.add(item)
            db.flush()
        inventory_entry = db.query(Inventory).filter(
            Inventory.character_id == character.id,
            Inventory.item_id == item.id
        ).first()
        if inventory_entry:
            continue
        is_armor = item.item_type.startswith("armor:") or item.item_type == "shield"
        is_weapon = item.item_type.startswith("weapon:")
        db.add(Inventory(
            character_id=character.id,
            item_id=item.id,
            quantity=quantity,
            is_equipped=is_armor or is_weapon,
            slot="armor" if is_armor else "weapon" if is_weapon else None
        ))
        added += 1

    if gold or silver or copper:
        purse = db.query(CharacterCurrency).filter(
            CharacterCurrency.character_id == character.id
        ).first()
        if purse is None:
            purse = CharacterCurrency(
                character_id=character.id,
                gold=0,
                silver=0,
                copper=0,
            )
            db.add(purse)
        total_copper = (
            (purse.gold or 0) * 100 + (purse.silver or 0) * 10 + (purse.copper or 0)
            + gold * 100 + silver * 10 + copper
        )
        purse.gold, remainder = divmod(total_copper, 100)
        purse.silver, purse.copper = divmod(remainder, 10)
    return added


def grant_starting_class_data(db: Session, character) -> dict[str, int]:
    """Grant SRD class features, first-option starting gear, and level-1 spells."""
    marker_name = "__open5e_starting_data_applied__"
    if db.query(CharacterAbility.id).filter(
        CharacterAbility.character_id == character.id,
        CharacterAbility.ability_name == marker_name
    ).first():
        return {"features": 0, "items": 0, "spells": 0}

    class_record = _class_record(db, character.class_)
    if not class_record:
        return {"features": 0, "items": 0, "spells": 0}

    feature_count = item_count = spell_count = 0
    if class_record.get("source_edition") == "2024":
        item_count += _apply_srd2024_starting_equipment(
            db, character, class_record
        )
    features = class_record.get("features", [])
    starting_equipment_text = ""
    for feature in features:
        if not isinstance(feature, dict):
            continue
        if feature.get("feature_type") == "STARTING_EQUIPMENT":
            starting_equipment_text = str(feature.get("desc") or "")
            continue
        gained_at = feature.get("gained_at") or []
        if not any(
            isinstance(level, dict) and level.get("level") == 1
            for level in gained_at
        ):
            continue
        name = str(feature.get("name") or "").strip()
        if not name:
            continue
        display_name = localized_feature_name(name)
        exists = db.query(CharacterAbility).filter(
            CharacterAbility.character_id == character.id,
            CharacterAbility.ability_name == display_name
        ).first()
        if not exists:
            db.add(CharacterAbility(
                character_id=character.id,
                ability_name=display_name,
                ability_description=localized_feature_description(
                    name,
                    "Описание особенности взято из SRD D&D 5e 2024."
                ),
                is_class=True
            ))
            feature_count += 1

    if starting_equipment_text and class_record.get("source_edition") != "2024":
        imported_names = set()
        for category in ("open5e_weapons", "open5e_armor", "open5e_items"):
            for (record,) in db.query(DnDRules.rule_data).filter(
                DnDRules.rule_category == category
            ).all():
                if isinstance(record, dict) and record.get("name"):
                    imported_names.add(str(record["name"]).casefold())
        catalog = db.query(Item).all()
        catalog = [item for item in catalog if item.name.casefold() in imported_names]
        catalog = _ensure_missing_starting_gear(db, starting_equipment_text, catalog)
        selected: dict[int, tuple[Item, int]] = {}
        for bullet in re.split(r"(?:^|\n)\s*\*\s*", starting_equipment_text):
            if not bullet.strip():
                continue
            chosen_matches = _default_equipment_option(bullet, catalog)
            for item, quantity in chosen_matches:
                previous = selected.get(item.id)
                selected[item.id] = (item, quantity + (previous[1] if previous else 0))

        for source_item, quantity in selected.values():
            item = localize_catalog_item(db, source_item)
            inventory_entry = db.query(Inventory).filter(
                Inventory.character_id == character.id,
                Inventory.item_id == item.id
            ).first()
            if inventory_entry:
                continue
            item_is_armor = item.item_type.startswith("armor:") or item.item_type == "shield"
            is_weapon = item.item_type.startswith("weapon:")
            db.add(Inventory(
                character_id=character.id,
                item_id=item.id,
                quantity=quantity,
                is_equipped=item_is_armor or is_weapon,
                slot="armor" if item_is_armor else "weapon" if is_weapon else None
            ))
            item_count += 1

    spell_counts = SPELL_STARTING_COUNTS.get(character.class_)
    if spell_counts:
        cantrip_count, level_one_count = spell_counts
        # Character has no ORM relationship; read the stored ability scores directly.
        from src.models import CharacterAttribute
        attributes = db.query(CharacterAttribute).filter(
            CharacterAttribute.character_id == character.id
        ).first()
        if character.class_ in {"Жрец", "Друид"}:
            wisdom_modifier = (attributes.wisdom - 10) // 2 if attributes else 0
            level_one_count = max(1, 1 + wisdom_modifier)
        if character.class_ == "Паладин":
            charisma_modifier = (attributes.charisma - 10) // 2 if attributes else 0
            level_one_count = max(1, 1 + charisma_modifier)
        wizard_prepared_count = 0
        if character.class_ == "Волшебник":
            intelligence_modifier = (attributes.intelligence - 10) // 2 if attributes else 0
            wizard_prepared_count = max(1, 1 + intelligence_modifier)
        class_key = CLASS_KEYS[character.class_]
        spell_rows = [(spell,) for spell in _srd2024_records(db, "spells")]
        eligible = []
        for (spell,) in spell_rows:
            if not isinstance(spell, dict):
                continue
            spell_classes = spell.get("classes") or []
            class_index = class_key.removeprefix("srd_")
            if not any(
                isinstance(spell_class, dict)
                and (spell_class.get("key") or spell_class.get("index")) == class_index
                for spell_class in spell_classes
            ):
                continue
            raw_level = spell.get("level", 0)
            try:
                level = int(raw_level)
            except (TypeError, ValueError):
                level = 0 if str(raw_level).casefold() in {"cantrip", "0"} else 1
            eligible.append((level, str(spell.get("name") or ""), spell))
        eligible.sort(key=lambda entry: (entry[0], entry[1].casefold()))
        cantrips = [entry for entry in eligible if entry[0] == 0][:cantrip_count]
        leveled = [entry for entry in eligible if entry[0] == 1][:level_one_count]
        for index, (level, english_name, spell_data) in enumerate(cantrips + leveled):
            if not english_name:
                continue
            display_name = str(
                (spell_data.get("localized_ru") or {}).get("name")
                or localized_spell_name(english_name)
            )
            exists = db.query(CharacterSpell).filter(
                CharacterSpell.character_id == character.id,
                func.lower(CharacterSpell.spell_name) == display_name.casefold()
            ).first()
            if exists:
                continue
            db.add(CharacterSpell(
                character_id=character.id,
                spell_name=display_name,
                spell_level=level,
                is_prepared=(
                    character.class_ != "Волшебник"
                    or (level > 0 and index - len(cantrips) < wizard_prepared_count)
                )
            ))
            spell_count += 1

    db.add(CharacterAbility(
        character_id=character.id,
        ability_name=marker_name,
        ability_description="D&D 5e 2024 SRD starting data applied.",
        is_class=False
    ))
    db.commit()
    return {"features": feature_count, "items": item_count, "spells": spell_count}


def grant_class_features_for_levels(db: Session, character, levels: list[int]) -> list[str]:
    """Persist official SRD class features unlocked at the specified levels."""
    class_record = _class_record(db, character.class_)
    if not class_record:
        return []

    granted = []
    for feature in class_record.get("features", []):
        if not isinstance(feature, dict) or feature.get("feature_type") == "STARTING_EQUIPMENT":
            continue
        if not any(
            isinstance(gained, dict) and gained.get("level") in levels
            for gained in feature.get("gained_at", [])
        ):
            continue
        english_name = str(feature.get("name") or "").strip()
        if not english_name:
            continue
        display_name = localized_feature_name(english_name)
        if db.query(CharacterAbility.id).filter(
            CharacterAbility.character_id == character.id,
            CharacterAbility.ability_name == display_name
        ).first():
            continue
        db.add(CharacterAbility(
            character_id=character.id,
            ability_name=display_name,
            ability_description=localized_feature_description(
                english_name,
                str(feature.get("desc") or "Особенность класса по SRD D&D 5e.")
            ),
            is_class=True
        ))
        granted.append(display_name)
    return granted


def build_dm_rules_context(db: Session, character, action: str) -> str:
    """Build a compact, sourced rules excerpt for this character's current action."""
    lines: list[str] = []

    for feature in db.query(CharacterAbility).filter(
        CharacterAbility.character_id == character.id,
        CharacterAbility.is_class.is_(True)
    ).all():
        lines.append(
            f"Class feature {feature.ability_name}: "
            f"{str(feature.ability_description or '')[:1000]}"
        )
    class_record = _class_record(db, character.class_)
    if class_record:
        for feature in class_record.get("features", []):
            if not isinstance(feature, dict) or feature.get("feature_type") == "STARTING_EQUIPMENT":
                continue
            if any(
                isinstance(gained, dict) and gained.get("level") == 1
                for gained in feature.get("gained_at", [])
            ):
                lines.append(
                    f"Official class feature {feature.get('name')}: "
                    f"{str(feature.get('desc') or '')[:1200]}"
                )

    # Actual weapon/armor values from the character sheet.
    inventory = db.query(Inventory).filter(
        Inventory.character_id == character.id
    ).all()
    item_ids = [entry.item_id for entry in inventory]
    for item in db.query(Item).filter(Item.id.in_(item_ids)).all() if item_ids else []:
        if item.damage_dice or item.armor_class:
            lines.append(
                f"Equipment {item.name}: type={item.item_type}, "
                f"damage={item.damage_dice or 'n/a'} {item.damage_type or ''}, "
                f"armor_base={item.armor_class or 'n/a'}, "
                f"details={str(item.description or '')[:400]}."
            )

    character_spells = db.query(CharacterSpell).filter(
        CharacterSpell.character_id == character.id
    ).all()
    known_spell_names = {
        canonical_spell_name(spell.spell_name).casefold()
        for spell in character_spells
    }
    if known_spell_names:
        srd2024_spells = _srd2024_records(db, "spells")
        if srd2024_spells:
            for data in srd2024_spells:
                english_name = str(data.get("name") or "")
                russian_data = data.get("localized_ru") or {}
                russian_name = str(russian_data.get("name") or localized_spell_name(english_name))
                aliases = {
                    english_name.casefold(),
                    russian_name.casefold(),
                    canonical_spell_name(russian_name).casefold(),
                }
                if aliases & known_spell_names:
                    damage = data.get("damage") or {}
                    damage_by_level = damage.get("damage_at_slot_level") or {}
                    lines.append(
                        f"D&D 5e 2024 SRD spell {russian_name} "
                        f"(English: {english_name}, level {data.get('level')}, "
                        f"range {data.get('range')}, casting time {data.get('casting_time')}, "
                        f"duration {data.get('duration')}, damage {damage_by_level}): "
                        f"{str(russian_data.get('description') or data.get('description') or '')[:1600]}"
                    )

    homebrew_spells = db.query(DnDRules.rule_data).filter(
        DnDRules.rule_category == "homebrew_spells"
    ).all()
    for (spell_data,) in homebrew_spells:
        if (
            isinstance(spell_data, dict)
            and spell_data.get("character_id") == character.id
        ):
            lines.append(
                f"Approved homebrew spell {spell_data.get('name')} "
                f"(level {spell_data.get('level')}, casting time {spell_data.get('casting_time')}, "
                f"range {spell_data.get('range')}, duration {spell_data.get('duration')}, "
                f"components {spell_data.get('components')}, damage "
                f"{spell_data.get('damage_dice') or 'none'} {spell_data.get('damage_type') or ''}, "
                f"save {spell_data.get('saving_throw') or 'none'}): "
                f"{str(spell_data.get('description') or '')[:1600]}"
            )

    # Add relevant SRD rules by mapping common Russian action words to rule headings.
    action_lower = action.casefold()
    topic_words = {
        "fall": ("пад", "упал", "упала", "упасть", "с дерева", "высот", "соскоч", "прыг"),
        "damage": ("урон", "ранен", "повреж"),
        "attack": ("атак", "удар", "стрел", "оруж"),
        "spell": ("заклин", "маг", "колдов"),
        "movement": ("двиг", "подх", "бег", "прыж", "идти", "путь"),
        "grapple": ("захват", "схват"),
        "rest": ("отдых", "леч", "восстанов"),
        "saving throw": ("спасброс", "ловк", "яд", "огонь")
    }
    selected_topics = [
        topic for topic, markers in topic_words.items()
        if any(marker in action_lower for marker in markers)
    ]
    if not selected_topics:
        selected_topics = ["attack", "damage", "movement"]

    rules = []
    selected_rules = []
    for (rule,) in rules:
        if not isinstance(rule, dict):
            continue
        title = str(rule.get("name") or "").casefold()
        desc = str(rule.get("desc") or "")
        if any(topic in title for topic in selected_topics):
            selected_rules.append((title, desc))
    for title, desc in selected_rules[:5]:
        lines.append(f"SRD rule — {title}: {desc[:1200]}")

    return "\n\n".join(lines)[:9000] or "No matching imported SRD rules found."


def build_creature_catalog(db: Session) -> str:
    """Return only the configured D&D 5e 2024 SRD/API creature index."""
    entries = []
    seen_names: set[str] = set()
    for category in ("srd2024_monsters",):
        records = db.query(DnDRules.rule_data).filter(
            DnDRules.rule_category == category
        ).order_by(DnDRules.id).all()
        for (creature,) in records:
            if not isinstance(creature, dict):
                continue
            key = str(creature.get("key") or "").strip()
            name = str(
                (creature.get("localized_ru") or {}).get("name")
                or creature.get("name") or ""
            ).strip()
            if not key or not name or name.casefold() in seen_names:
                continue
            seen_names.add(name.casefold())
            entries.append(
                f"{name} [key={key}, CR={creature.get('challenge_rating', '?')}, 2024 SRD]"
            )
    return "; ".join(sorted(entries, key=str.casefold))[:9000]


def build_item_catalog(db: Session) -> str:
    """Compact bilingual name index so the DM can distinguish catalog gear from homebrew."""
    records = []
    seen = set()
    for category in ("srd2024_equipment", "srd2024_magic_items"):
        for (item_data,) in db.query(DnDRules.rule_data).filter(
            DnDRules.rule_category == category
        ).all():
            if not isinstance(item_data, dict):
                continue
            english_name = str(item_data.get("name") or "").strip()
            display_name = str(
                (item_data.get("localized_ru") or {}).get("name")
                or localized_item_name(english_name)
            ).strip()
            if not english_name or not display_name:
                continue
            key = display_name.casefold()
            if key in seen:
                continue
            seen.add(key)
            records.append(f"{display_name} [{english_name}]")
    return "; ".join(sorted(records, key=str.casefold))[:8000]


def is_open5e_catalog_item(db: Session, item: Item) -> bool:
    """True only for equipment whose mechanics originate in imported API records."""
    if not item:
        return False
    item_names = {
        canonical_item_name(item.name).casefold(),
        str(item.name or "").casefold()
    }
    for category in ("srd2024_equipment", "srd2024_magic_items"):
        for (record,) in db.query(DnDRules.rule_data).filter(
            DnDRules.rule_category == category
        ).all():
            if not isinstance(record, dict):
                continue
            official_name = str(record.get("name") or "").strip()
            russian_name = str(
                (record.get("localized_ru") or {}).get("name") or ""
            ).strip()
            if not official_name:
                continue
            if (
                official_name.casefold() in item_names
                or russian_name.casefold() in item_names
            ):
                return True
            if localized_item_name(official_name).casefold() in item_names:
                return True
    return False
