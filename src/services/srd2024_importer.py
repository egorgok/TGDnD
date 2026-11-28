"""Import the bundled 2024 SRD data from 5e-bits/5e-srd-api into PostgreSQL.

The upstream repository's 2024 English and Russian JSON data are vendored under
database/srd_2024. Open5e rows are never overwritten; each source has its own
DnDRules category and provenance.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import DnDRules, Item
from src.models.base import SessionLocal

logger = logging.getLogger(__name__)
DATA_ROOT = Path(__file__).resolve().parents[2] / "database" / "srd_2024"
SOURCE_URL = "https://github.com/5e-bits/5e-srd-api"
SOURCE_VERSION = "5e-bits SRD 2024 @ 3fa06d725667"


def _category_from_filename(filename: str) -> str:
    stem = re.sub(r"^5e-SRD-", "", Path(filename).stem, flags=re.IGNORECASE)
    return "srd2024_" + re.sub(r"[^a-z0-9]+", "_", stem.casefold()).strip("_")


def _merged_source_records() -> dict[str, dict[str, dict]]:
    merged: dict[str, dict[str, dict]] = {}
    for filename in (DATA_ROOT / "en").glob("5e-SRD-*.json"):
        category = _category_from_filename(filename.name)
        en_records = _localized_records_for_file(filename)
        ru_file = DATA_ROOT / "ru" / filename.name
        ru_records = _localized_records_for_file(ru_file) if ru_file.exists() else {}
        category_records = {}
        for index in set(en_records) | set(ru_records):
            en_data = en_records.get(index)
            if not en_data:
                # English is the authoritative rules source. Keep Russian-only
                # upstream records without pretending their stat keys are English.
                en_data = ru_records[index]
            category_records[index] = {
                **en_data,
                "key": index,
                "source_version": SOURCE_VERSION,
                "localized_ru": ru_records.get(index),
            }
        merged[category] = category_records
    return merged


def _localized_records_for_file(filename: Path) -> dict[str, dict]:
    try:
        payload = json.loads(filename.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read bundled SRD file %s", filename)
        raise
    if not isinstance(payload, list):
        return {}
    return {
        str(row.get("index") or row.get("name")): row
        for row in payload
        if isinstance(row, dict) and (row.get("index") or row.get("name"))
    }


def _upsert_rule(db: Session, category: str, key: str, data: dict) -> None:
    rule_name = f"2024:{key}"[:255]
    row = db.query(DnDRules).filter(
        DnDRules.rule_category == category,
        DnDRules.rule_name == rule_name
    ).first()
    if row is None:
        db.add(DnDRules(
            rule_category=category,
            rule_name=rule_name,
            rule_data=data,
            source=SOURCE_URL,
            version=SOURCE_VERSION
        ))
    else:
        row.rule_data = data
        row.source = SOURCE_URL
        row.version = SOURCE_VERSION


def _upsert_item(db: Session, record: dict) -> None:
    """Create/update the runtime equipment row while retaining upstream source rows."""
    english_name = str(record.get("name") or "").strip()
    if not english_name:
        return
    localized = record.get("localized_ru") or {}
    display_name = str(localized.get("name") or english_name).strip()
    categories = {
        str(category.get("index") or "").casefold()
        for category in record.get("equipment_categories", [])
        if isinstance(category, dict)
    }
    is_weapon = "weapons" in categories
    armor_data = record.get("armor_class") or {}
    is_shield = "shields" in categories or english_name.casefold() == "shield"
    is_armor = is_shield or bool(armor_data)
    properties = {
        str(prop.get("index") or "").casefold()
        for prop in record.get("properties", [])
        if isinstance(prop, dict)
    }
    ranged = (
        "ranged-weapons" in categories
        or "simple-ranged-weapons" in categories
        or "martial-ranged-weapons" in categories
        or (
            is_weapon
            and int((record.get("range") or {}).get("normal") or 5) > 5
        )
    )
    if is_shield:
        item_type = "shield"
    elif is_armor:
        armor_weight = next(
            (
                category.removesuffix("-armor")
                for category in ("heavy-armor", "medium-armor", "light-armor")
                if category in categories
            ),
            "medium"
        )
        item_type = f"armor:{armor_weight}"
    elif is_weapon:
        item_type = "weapon:ranged" if ranged else "weapon:melee"
    else:
        item_type = "gear"
    if is_weapon and "finesse" in properties:
        item_type += ":finesse"
    damage = record.get("damage") or {}
    damage_type = damage.get("damage_type") or {}
    ru_damage = (localized.get("damage") or {}).get("damage_type") or {}
    raw_ac = armor_data.get("base") if isinstance(armor_data, dict) else None
    item = db.query(Item).filter(
        func.lower(Item.name) == display_name.casefold()
    ).first()
    if item is None:
        item = Item(name=display_name)
        db.add(item)
    item.item_type = item_type[:100]
    description = str(
        localized.get("description") or record.get("description") or
        "Снаряжение по SRD D&D 2024."
    )
    range_data = record.get("throw_range") or record.get("range") or {}
    if isinstance(range_data, dict) and range_data.get("normal"):
        range_text = str(range_data["normal"])
        long_range = range_data.get("long")
        if long_range:
            range_text += f"/{long_range}"
        description += f"\nДальность: {range_text} футов."
    item.description = description[:10000]
    item.weight = record.get("weight")
    item.damage_dice = str(damage.get("damage_dice") or "")[:50] or None
    item.damage_type = str(ru_damage.get("name") or damage_type.get("name") or "")[:50] or None
    item.armor_class = int(raw_ac) if raw_ac is not None else None
    item.is_stackable = item_type == "gear"


def sync_srd2024(db: Session) -> dict[str, int]:
    """Upsert all bundled 2024 English/Russian SRD collections."""
    if not (DATA_ROOT / "en").is_dir():
        raise FileNotFoundError(f"Bundled 2024 SRD dataset not found: {DATA_ROOT}")
    collections = _merged_source_records()
    totals: dict[str, int] = {}
    for category, records in collections.items():
        existing_count = db.query(DnDRules.id).filter(
            DnDRules.rule_category == category
        ).count()
        # Same count means this version is already cached. Upstream updates are
        # applied when the vendored snapshot changes its collection cardinality;
        # manual CLI refresh can force an upsert by deleting the category first.
        existing_version = db.query(DnDRules.version).filter(
            DnDRules.rule_category == category
        ).first()
        if existing_count != len(records) or not existing_version or existing_version[0] != SOURCE_VERSION:
            for key, record in records.items():
                _upsert_rule(db, category, key, record)
                if category in {"srd2024_equipment", "srd2024_magic_items"}:
                    _upsert_item(db, record)
            db.commit()
        totals[category] = len(records)
    logger.info("Loaded bundled 2024 SRD data: %s", totals)
    return totals


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db: Session = SessionLocal()
    try:
        print(json.dumps(sync_srd2024(db), indent=2, ensure_ascii=False))
    except Exception:
        db.rollback()
        logger.exception("2024 SRD import failed")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
