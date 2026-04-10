"""load_skill tool - on-demand full skill body loader"""
from src.tools.registry import Tool
from src.skills import get_skill_catalog


class LoadSkillTool(Tool):
    """Tool that loads the full body of a named skill into the current context.

    Two-layer skill model: system prompt only has the cheap catalog.
    This tool is called when the LLM needs the full skill content.
    """

    name = "load_skill"
    description = "Load the full body of a named skill into the current context."
    is_mutating = False

    async def execute(self, name: str, **kwargs) -> str:
        return get_skill_catalog().load_full_text(name)

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the skill to load",
                    }
                },
                "required": ["name"],
            },
        }
