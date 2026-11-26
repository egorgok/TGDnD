import httpx
import base64
import json
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

            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    self.settings.ai_image_gateway_url,
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()
                return data["data"][0]["b64_json"]

        except Exception as e:
            logger.error(f"Error generating image: {e}")
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
    @staticmethod
    async def process_action(
        character_name: str,
        action_description: str,
        world_setting: str
    ) -> str:
        """
        Process player action and return DM narrative
        Uses static method for simple async calls
        """
        settings = get_settings()

        system_prompt = f"""You are an experienced D&D 5e Game Master narrating an epic adventure.
Always respond in Russian.

World Setting: {world_setting}

Your role is to describe the consequences of player actions in an engaging, immersive way.
Keep responses concise but vivid. Include sensory details.
When describing combat outcomes, use D&D mechanics (advantage, disadvantage, etc.)."""

        prompt = f"""Player: {character_name}
Action: {action_description}

Describe what happens as a result of this action. Be creative and engaging."""

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
                "max_tokens": 500,
                "temperature": 0.8
            }

            base_url = settings.ai_text_gateway_url.rstrip("/")
            endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    endpoint,
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]

        except Exception as e:
            logger.error(f"Error processing action: {e}")
            return f"The GM pauses, uncertain... (Error: {str(e)})"
