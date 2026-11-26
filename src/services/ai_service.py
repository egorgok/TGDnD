import httpx
import base64
import json
import re
from typing import Optional
from config.settings import get_settings
import logging

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self):
        self.settings = get_settings()

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 500
    ) -> str:
        """
        Generate text response using GPT-6-Luna via custom gateway
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.settings.ai_text_gateway_token}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": self.settings.ai_text_model,
                "messages": [],
                "max_tokens": max_tokens,
                "temperature": 0.7
            }

            if system_prompt:
                payload["messages"].append({
                    "role": "system",
                    "content": system_prompt
                })

            payload["messages"].append({
                "role": "user",
                "content": prompt
            })

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.settings.ai_text_gateway_url}/chat/completions",
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]

        except Exception as e:
            logger.error(f"Error generating text: {e}")
            raise

    async def generate_image(
        self,
        prompt: str,
        size: str = "auto"
    ) -> Optional[str]:
        """
        Generate image using GPT-Image-2 via custom gateway
        Returns base64 encoded image
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.settings.ai_image_gateway_token}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": self.settings.ai_image_model,
                "prompt": prompt,
                "size": size,
                "quality": self.settings.ai_image_quality,
                "n": 1,
                "response_format": "b64_json"
            }

            logger.info(
                "Requesting scene image from %s using model %s",
                self.settings.ai_image_gateway_url,
                self.settings.ai_image_model
            )
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    self.settings.ai_image_gateway_url,
                    json=payload,
                    headers=headers
                )
                logger.info("Image gateway responded with HTTP %s", response.status_code)
                response.raise_for_status()
                data = response.json()
                image_data = data.get("data") or []
                if not image_data:
                    raise ValueError("Image gateway response has no data entries")
                first_image = image_data[0]
                if first_image.get("b64_json"):
                    return first_image["b64_json"]
                if first_image.get("url"):
                    image_response = await client.get(first_image["url"])
                    image_response.raise_for_status()
                    return base64.b64encode(image_response.content).decode("ascii")
                raise ValueError("Image gateway response contains neither b64_json nor url")

        except Exception as e:
            logger.exception("Error generating image")
            raise

    async def validate_action(
        self,
        character_data: dict,
        action_description: str,
        world_context: str,
        gm_prompt: str
    ) -> dict:
        """
        Validate if action is possible and generate consequences
        Returns: {
            "is_possible": bool,
            "reason": str,
            "requires": list,
            "ai_response": str
        }
        """
        system_prompt = f"""You are a D&D Game Master.
{gm_prompt}

Evaluate the following player action based on D&D 5e rules and world context.
Respond with whether it's possible and what might be needed.
Format your response as JSON with keys: 'is_possible' (bool), 'reason' (str), 'requires' (list of items/skills/spells needed), 'narrative' (the DM's response)"""

        prompt = f"""Character: {character_data['name']} ({character_data['race']} {character_data['class']})
Level: {character_data['level']}
HP: {character_data['current_hp']}/{character_data['max_hp']}
Inventory: {character_data['inventory']}
Abilities: {character_data['abilities']}

World Context: {world_context}

Action: {action_description}

Is this action possible? What are the consequences?"""

        try:
            response = await self.generate_text(prompt, system_prompt)
            # Try to parse as JSON, fallback to plain text
            try:
                return json.loads(response)
            except json.JSONDecodeError:
                return {
                    "is_possible": True,
                    "reason": "Action evaluated",
                    "requires": [],
                    "narrative": response
                }
        except Exception as e:
            logger.error(f"Error validating action: {e}")
            return {
                "is_possible": False,
                "reason": "System error evaluating action",
                "requires": [],
                "narrative": "The GM struggles to process this..."
            }

    @staticmethod
    async def process_action(
        character_name: str,
        action_description: str,
        world_setting: str,
        inventory: Optional[list] = None,
        d20_roll: Optional[int] = None,
        scene_state: Optional[str] = None,
        recent_actions: Optional[list] = None,
        known_entities: Optional[list] = None,
        known_npcs: Optional[list] = None,
        rules_context: Optional[str] = None,
        creature_catalog: Optional[str] = None,
        item_catalog: Optional[str] = None
    ) -> dict:
        """
        Process player action and return DM narrative
        Uses static method for simple async calls
        """
        settings = get_settings()

        system_prompt = f"""You are an experienced D&D 5e Game Master narrating an epic adventure.
Always respond in Russian.
All player-facing item, spell, and feature names should be in Russian. Keep canonical
Open5e names only in internal keys; use Russian display names in the narrative.

World Setting: {world_setting}
Treat this setting as binding canon. Keep the genre, atmosphere, technology level,
and world rules consistent in every response; do not introduce elements from another
genre unless the player explicitly changes the setting.

Your role is to describe the consequences of player actions in an engaging, immersive way.
Keep responses concise but vivid. Include sensory details.
When describing combat outcomes, use D&D mechanics (advantage, disadvantage, etc.).
Use the supplied Open5e SRD rules, spell descriptions, character features, and equipment
as the source of truth for mechanics. Do not invent weapon/spell damage, armor values,
spell effects, or fall damage. If the exact rule is not supplied, say what check is
needed and avoid fabricating a numeric result.
When the player explicitly uses or acquires an item absent from their inventory, include
it in custom_items. If it exists in the supplied catalog, use its exact name and the bot
will load stored stats; only propose stats if it is not in the catalog. Everyday
improvised objects stay weak: for
example, a pacifier used as a club is type "weapon:improvised", deals fixed 1 bludgeoning
damage, and has no special effect. If the player explicitly says they are using the item,
add it to their inventory unless the narrative makes possession impossible. Use only these
item_type values: gear, weapon:melee, weapon:ranged, weapon:improvised, armor:light,
armor:medium, armor:heavy, shield. Homebrew armor must be plausible and never exceed AC 18.
Never invent custom spell rules for a vague request. Create a custom spell only when the player supplies its level,
casting time, range, duration, components, and complete effect/damage mechanics, and
the design is balanced for that level.
Return ONLY valid JSON with keys "narrative" (Russian text shown to the player)
and "loot" (array of objects with name, type, rarity, description, quantity)
and "damage" (integer HP damage to apply, 0 if there is no plausible injury)
and "damage_dice" (dice only, e.g. "6d6"; otherwise null)
and "damage_bonus" (separate integer modifier; do not include it in damage_dice)
and "scene_state" (short Russian summary of the character's exact current location,
position, and immediate situation after this action)
and "location_changed" (true only if the character actually moved to a different named place)
and "entities" (array of newly encountered named people/creatures, each with name,
entity_type, description, personality, max_hp, armor_class, attack_bonus, damage_dice,
is_hostile, distance_m, open5e_key if the creature matches the supplied SRD catalog)
and "custom_items" (explicitly used/acquired items absent from the character's inventory;
each with Russian display name, catalog_name as exact canonical DB name if it exists or
null if homebrew, item_type, damage_dice, damage_type, armor_class, range, description,
add_to_inventory)
and "weapon_name" (the exact catalog/carried weapon being used, otherwise null)
and "custom_spells" (fully specified homebrew spells with name, level, description,
casting_time, range, duration, components, damage_dice, damage_type, saving_throw,
add_to_spellbook)
and "target_name" (known entity this action targets, otherwise null)
and "enemy_attacks" (true only when a present hostile creature actually attacks now).
and "npcs" (new or revisited NPC records with name, aliases, role, description,
personality, current_location, is_hostile, is_alive) and "npc_memories" (new factual
memories, each with npc_name, fact, source).
Put items in loot ONLY when the character actually finds/receives them in this action.
If your narrative says the character picks an item up, takes it, keeps it, or puts it
in a bag/pocket, that item MUST be included in loot using its exact name.
The d20 result is authoritative: 1 is a disastrous failure, 20 is an exceptional success.
For 2-19, judge whether the action succeeds based on difficulty and circumstances.
Make the narration and consequences match the roll. On a failed risky physical action,
describe a fitting setback; apply damage only if an injury is plausible, not automatically.
For damage defined by supplied SRD rules as dice (including falling, spells, or other
effects), return only the dice formula in damage_dice (for example "6d6") and its
modifier separately in damage_bonus; the bot will roll the dice. Use damage only for
fixed damage without a dice formula.
Continuity is mandatory. Treat the supplied current scene and recent actions as facts.
Do not teleport the character, change location, or assume an action in a different place
has happened. If the requested action requires being somewhere else, explain in the
narrative that the character is still in the current scene and ask them to move there first.
Update scene_state only for changes that actually happen in this action.
Set location_changed=true only when the action successfully moves the character into a
different distinct location, not for looking around or moving within the same room.
Every action involving waiting, traveling, searching, approaching, or pursuing must
advance the situation. Never return another empty "you wait" result: an arrival,
discovery, interruption, concrete time change, or clear obstacle must occur. Repeated
waiting must advance or escalate the event, not repeat the previous response.
In an arena/gladiator setting, when the player waits for an opponent, bring one into
the arena in this response, create it as a persistent NPC and combat entity with stable
stats, and begin the confrontation. Never make the player repeat the wait action.
Keep known people and creatures consistent; do not recreate them. When introducing a
named or noteworthy person/creature that can be interacted with, create plausible
stable stats once and include them in entities in this response. For unclear references
like "him", choose a target only if exactly one known entity is present in the scene.
Do not narrate an attack as a hit; the game system resolves attacks against stored AC/HP.
Whenever an encountered creature exactly matches the supplied Open5e SRD creature catalog,
include its exact key as open5e_key; do not make up a key. The bot will use that official
creature's HP, AC, attack bonus, and damage dice instead of invented stats.
Treat known_npcs as campaign canon across locations. Reuse the same NPC and retain
established facts exactly if encountered elsewhere. Record durable facts learned in
conversation in npc_memories. Update their current_location only when established.
When the player approaches a known entity, do not consume a turn without progress:
reduce its distance by at least 3 meters, and if within 1 meter mark it as reachable.
Never let a melee attack hit from beyond 1 meter.
If the action names a weapon, return its stored name in weapon_name.
Treat a clear charge toward a hostile creature while holding/using an object as the
player initiating an attack, even if they did not use the verb "attack". If a hostile
creature suddenly attacks, set enemy_attacks=true, identify it in target_name, and make
the encounter begin now rather than waiting for a /fight command.
Only set add_to_inventory when the item is already owned or is acquired in this scene.
For a custom spell, return it only if the player provided all required mechanics; otherwise
explain what details are missing and leave custom_spells empty.
Do not invent loot for ordinary actions. Never list items already in the character inventory."""

        prompt = f"""Player: {character_name}
Current inventory: {inventory or []}
Open5e item catalog (Russian display name [canonical DB name]):
{item_catalog or "No imported item catalog."}
Action: {action_description}
Player's d20 result: {d20_roll}
Current scene state: {scene_state or "Not established yet; establish a plausible starting scene from the world setting."}
Recent actions and outcomes: {recent_actions or []}
Known entities and their current stats: {known_entities or []}
Campaign NPC registry and factual memories: {known_npcs or []}
Open5e SRD 5.1 creature index (use exact key for a matching monster):
{creature_catalog or "No creature index imported."}
Imported Open5e 5e-2014 SRD rules and this character's actual abilities/equipment:
{rules_context or "No imported rule excerpts available. Do not claim a numeric rule that is not known."}

Resolve the action and describe what happens. Whenever the character acquires an item,
include it in loot even if it is an ordinary object such as a stone, branch, or coin.
If no item is acquired, loot must be an empty array. Preserve the scene unless this
action explicitly and successfully changes it."""

        try:
            headers = {
                "Authorization": f"Bearer {settings.ai_text_gateway_token}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": settings.ai_text_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 1400,
                "temperature": 0.8
            }

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    (settings.ai_text_gateway_url.rstrip("/") if settings.ai_text_gateway_url.rstrip("/").endswith("/chat/completions") else f"{settings.ai_text_gateway_url.rstrip('/')}/chat/completions"),
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                try:
                    # Some compatible gateways wrap JSON in a Markdown code fence.
                    json_content = content.strip()
                    if json_content.startswith("```"):
                        json_content = re.sub(
                            r"^```(?:json)?\s*|\s*```$",
                            "",
                            json_content,
                            flags=re.IGNORECASE
                        ).strip()
                    result = json.loads(json_content)
                    if not isinstance(result, dict) or not isinstance(result.get("narrative"), str):
                        raise ValueError("Invalid action response shape")
                    if not isinstance(result.get("loot", []), list):
                        result["loot"] = []
                    valid_loot = [
                        item for item in result.get("loot", [])
                        if isinstance(item, dict) and str(item.get("name", "")).strip()
                    ]
                    try:
                        damage = max(0, min(int(result.get("damage", 0)), 100))
                    except (TypeError, ValueError):
                        damage = 0
                    damage_dice = str(result.get("damage_dice") or "").strip()
                    if not re.fullmatch(r"\d*d\d+", damage_dice):
                        damage_dice = ""
                    try:
                        damage_bonus = max(-20, min(int(result.get("damage_bonus", 0)), 100))
                    except (TypeError, ValueError):
                        damage_bonus = 0
                    return {
                        "narrative": result["narrative"],
                        "loot": valid_loot,
                        "damage": damage,
                        "damage_dice": damage_dice or None,
                        "damage_bonus": damage_bonus,
                        "scene_state": str(result.get("scene_state") or scene_state or "")[:2000],
                        "location_changed": bool(result.get("location_changed", False)),
                        "entities": [
                            {
                                **entity,
                                "open5e_key": str(entity.get("open5e_key", "")).strip()[:100]
                                if entity.get("open5e_key") else None
                            }
                            for entity in result.get("entities", [])
                            if isinstance(entity, dict) and str(entity.get("name", "")).strip()
                        ] if isinstance(result.get("entities", []), list) else [],
                        "custom_items": [
                            item for item in result.get("custom_items", [])
                            if isinstance(item, dict) and str(item.get("name", "")).strip()
                        ] if isinstance(result.get("custom_items", []), list) else [],
                        "weapon_name": str(result["weapon_name"]).strip()[:255]
                        if result.get("weapon_name") else None,
                        "custom_spells": [
                            spell for spell in result.get("custom_spells", [])
                            if isinstance(spell, dict) and str(spell.get("name", "")).strip()
                        ] if isinstance(result.get("custom_spells", []), list) else [],
                        "target_name": str(result["target_name"]).strip()[:255]
                        if result.get("target_name") else None,
                        "enemy_attacks": bool(result.get("enemy_attacks", False)),
                        "npcs": [
                            npc for npc in result.get("npcs", [])
                            if isinstance(npc, dict) and str(npc.get("name", "")).strip()
                        ] if isinstance(result.get("npcs", []), list) else [],
                        "npc_memories": [
                            memory for memory in result.get("npc_memories", [])
                            if isinstance(memory, dict)
                            and str(memory.get("npc_name", "")).strip()
                            and str(memory.get("fact", "")).strip()
                        ] if isinstance(result.get("npc_memories", []), list) else []
                    }
                except (json.JSONDecodeError, ValueError):
                    # Do not award anything when the model fails to follow JSON format.
                    return {
                        "narrative": content,
                        "loot": [],
                        "damage": 0,
                        "damage_dice": None,
                        "damage_bonus": 0,
                        "scene_state": scene_state or "",
                        "location_changed": False,
                        "entities": [],
                        "custom_items": [],
                        "weapon_name": None,
                        "custom_spells": [],
                        "target_name": None,
                        "enemy_attacks": False,
                        "npcs": [],
                        "npc_memories": []
                    }

        except Exception as e:
            logger.error(f"Error processing action: {e}")
            return {
                "narrative": "ДМ на мгновение задумался и не смог разрешить действие.",
                "loot": [],
                "damage": 0,
                "damage_dice": None,
                "damage_bonus": 0,
                "scene_state": scene_state or "",
                "location_changed": False,
                "entities": [],
                "custom_items": [],
                "weapon_name": None,
                "custom_spells": [],
                "target_name": None,
                "enemy_attacks": False,
                "npcs": [],
                "npc_memories": []
            }
