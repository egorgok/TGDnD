"""Import the 2014 Open5e SRD catalog into the local database.

Run with: python -m src.services.open5e_importer
Open5e source/document metadata is retained with every cached record.
"""
import logging
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import DnDRules, Item
from src.models.base import Base, SessionLocal, engine

logger = logging.getLogger(__name__)

API_BASE = "https://api.open5e.com/v2/"
SRD_DOCUMENT_KEY = "srd-2014"
RESOURCES = ("classes", "spells", "weapons", "armor", "items", "creatures", "rules")


def _document_key(record: dict) -> str | None:
    document = record.get("document")
    if isinstance(document, dict):
        return document.get("key")
    return str(document) if document else None


def _record_name(record: dict) -> str:
    return str(record.get("name") or record.get("key") or "unnamed")[:255]


def _upsert_rule(db: Session, resource: str, record: dict) -> None:
    document = record.get("document") or {}
    doc_key = document.get("key") if isinstance(document, dict) else str(document)
    source_url = document.get("permalink") if isinstance(document, dict) else None
    key = str(record.get("key") or _record_name(record))
    rule_name = f"{doc_key}:{key}"[:255]
    category = f"open5e_{resource}"[:100]
    cached = db.query(DnDRules).filter(
        DnDRules.rule_category == category,
        DnDRules.rule_name == rule_name
    ).first()
    if cached:
        cached.rule_data = record
        cached.source = str(source_url or "https://open5e.com")[:255]
        cached.version = "Open5e v2 / 5e 2014 SRD"
    else:
        db.add(DnDRules(
            rule_category=category,
            rule_name=rule_name,
            rule_data=record,
            source=str(source_url or "https://open5e.com")[:255],
            version="Open5e v2 / 5e 2014 SRD"
        ))


def _upsert_catalog_item(db: Session, resource: str, record: dict) -> None:
    name = _record_name(record)
    if not name or name == "unnamed":
        return

    item_type = "gear"
    armor_class = None
    damage_dice = None
    damage_type = None
    raw_weight = record.get("weight")
    try:
        weight = Decimal(str(raw_weight)) if raw_weight not in (None, "") else None
        if weight is not None and (weight < 0 or weight > Decimal("999.99")):
            weight = None
    except (InvalidOperation, TypeError, ValueError):
        weight = None
    description = record.get("desc") or record.get("description") or ""

    armor = record.get("armor")
    weapon = record.get("weapon")
    if resource == "weapons" or record.get("damage_dice") or isinstance(weapon, dict):
        weapon = weapon if isinstance(weapon, dict) else record
        ranged = bool(weapon.get("range"))
        properties = weapon.get("properties") or []
        property_names = {
            str(prop.get("name") or prop.get("property", {}).get("name", "")).casefold()
            for prop in properties if isinstance(prop, dict)
        }
        item_type = "weapon:ranged" if ranged else "weapon:melee"
        if any("finesse" in prop for prop in property_names):
            item_type += ":finesse"
        damage_dice = weapon.get("damage_dice")
        damage_type_data = weapon.get("damage_type")
        damage_type = (
            damage_type_data.get("name")
            if isinstance(damage_type_data, dict)
            else damage_type_data
        )
        if ranged:
            normal_range = weapon.get("range")
            long_range = weapon.get("long_range")
            description = (
                f"{description}\nRange: {normal_range}/{long_range} feet."
                if long_range else f"{description}\nRange: {normal_range} feet."
            )
        if isinstance(armor, dict):
            item_type = f"armor:{armor.get('category') or 'medium'}"
            armor_class = armor.get("ac_base")
    elif resource == "armor" or isinstance(armor, dict):
        armor = armor if isinstance(armor, dict) else record
        category = str(armor.get("category") or "medium").lower()
        item_type = "shield" if category == "shield" or "shield" in name.lower() else f"armor:{category}"
        armor_class = armor.get("ac_base") or armor.get("armor_class")

    item = db.query(Item).filter(func.lower(Item.name) == name.lower()).first()
    if (
        resource == "items"
        and item is not None
        and item.item_type.startswith(("weapon:", "armor:", "shield"))
    ):
        # The generic item feed can contain a duplicate with less mechanical detail.
        return
    if name.casefold() == "shield":
        item_type = "shield"
        armor_class = 2
    if item is None:
        item = Item(name=name)
        db.add(item)
    item.item_type = item_type[:100]
    item.description = str(description)[:10000] or None
    item.weight = weight
    item.damage_dice = str(damage_dice)[:50] if damage_dice else None
    item.damage_type = str(damage_type)[:50] if damage_type else None
    try:
        item.armor_class = int(armor_class) if armor_class is not None else None
    except (TypeError, ValueError):
        item.armor_class = None
    item.is_stackable = item_type == "gear"


def sync_open5e(db: Session, resources: tuple[str, ...] = RESOURCES) -> dict[str, int]:
    """Fetch and upsert SRD 5.1 records; non-SRD/2024 content is excluded."""
    totals: dict[str, int] = {}
    timeout = httpx.Timeout(30.0, connect=10.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for resource in resources:
            if resource not in RESOURCES:
                raise ValueError(f"Unsupported Open5e resource: {resource}")
            url = f"{API_BASE}{resource}/"
            count = 0
            while url:
                parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.netloc != "api.open5e.com":
                    raise ValueError(f"Unexpected pagination URL from Open5e: {url}")
                if "?" not in url:
                    params = {"limit": 100}
                    if resource == "rules":
                        params["document"] = SRD_DOCUMENT_KEY
                    else:
                        params["document__key"] = SRD_DOCUMENT_KEY
                else:
                    params = None
                response = client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                records = payload.get("results", [])
                for record in records:
                    if _document_key(record) != SRD_DOCUMENT_KEY:
                        continue
                    _upsert_rule(db, resource, record)
                    if resource in {"weapons", "armor", "items"}:
                        _upsert_catalog_item(db, resource, record)
                    count += 1
                db.commit()
                url = payload.get("next")
            totals[resource] = count
            logger.info("Imported %s Open5e SRD records from %s", count, resource)
    return totals


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        counts = sync_open5e(db)
        print("Open5e import complete:")
        for resource, count in counts.items():
            print(f"  {resource}: {count}")
    except Exception:
        db.rollback()
        logger.exception("Open5e import failed")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
