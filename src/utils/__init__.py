"""
Utility functions for the bot
"""
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

def format_character_sheet(character_data: Dict) -> str:
    """Format character data for display"""
    char = character_data.get("character")
    attrs = character_data.get("attributes")
    abilities = character_data.get("abilities", [])

    sheet = f"""
<b>⚔️ {char.name}</b>
<b>Раса:</b> {char.race}
<b>Класс:</b> {char.class_}
<b>Уровень:</b> {char.level}
<b>HP:</b> {char.current_hp}/{char.max_hp}
<b>Опыт:</b> {char.experience}

<b>📊 Характеристики:</b>
STR: {attrs.strength} ({get_modifier(attrs.strength):+d})
DEX: {attrs.dexterity} ({get_modifier(attrs.dexterity):+d})
CON: {attrs.constitution} ({get_modifier(attrs.constitution):+d})
INT: {attrs.intelligence} ({get_modifier(attrs.intelligence):+d})
WIS: {attrs.wisdom} ({get_modifier(attrs.wisdom):+d})
CHA: {attrs.charisma} ({get_modifier(attrs.charisma):+d})

<b>🎯 Способности:</b>
{', '.join(abilities) if abilities else 'Нет'}
"""
    return sheet

def get_modifier(ability_score: int) -> int:
    """Calculate D&D ability modifier"""
    return (ability_score - 10) // 2

def format_inventory(inventory_items: List) -> str:
    """Format inventory for display"""
    if not inventory_items:
        return "Инвентарь пуст"

    items_text = ""
    for inv in inventory_items:
        item = inv["item"]
        qty = inv["quantity"]
        equipped = "✓" if inv["is_equipped"] else " "
        items_text += f"\n[{equipped}] {item.name} x{qty}"

    return f"<b>🎒 Инвентарь:</b>{items_text}"

def format_roll_result(result: Dict) -> str:
    """Format dice roll result"""
    emoji = "✅" if result["success"] else "❌"
    critical = ""
    if result["critical"] == "success":
        critical = " 🎉 КРИТИЧЕСКИЙ УСПЕХ!"
    elif result["critical"] == "failure":
        critical = " 💀 КРИТИЧЕСКИЙ ПРОВАЛ!"

    return f"""{emoji} {result["description"]}
Бросок: {result["roll"]}
Модификатор: {result["modifier"]:+d}
Итого: {result["total"]}{critical}"""

def format_action_response(action_data: Dict) -> str:
    """Format action validation response"""
    is_possible = action_data.get("is_possible", False)
    emoji = "✅" if is_possible else "❌"

    text = f"{emoji} <b>{action_data.get('reason', 'Действие оценено')}</b>\n"
    text += f"\n{action_data.get('narrative', '')}"

    if not is_possible:
        requires = action_data.get("requires", [])
        if requires:
            text += f"\n\n<b>Необходимо:</b>\n"
            for req in requires:
                text += f"• {req}\n"

    return text

def format_error(error_msg: str) -> str:
    """Format error message"""
    return f"❌ <b>Ошибка:</b> {error_msg}"

def format_success(success_msg: str) -> str:
    """Format success message"""
    return f"✅ {success_msg}"
