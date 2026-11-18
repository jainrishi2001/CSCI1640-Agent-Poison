"""Ask the configured target model to call a dummy get_weather tool.

Prints whether the response contains a tool call. Requires a filled-in ``.env``
and Google Application Default Credentials. The offline test suite does not
run this script.
"""

from __future__ import annotations

import logging

from agent_sec.config import get_settings
from agent_sec.llm import chat

_GET_WEATHER = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = get_settings()
    response = chat(
        messages=[
            {
                "role": "user",
                "content": "Use the get_weather tool to check the weather in Providence.",
            }
        ],
        tools=[_GET_WEATHER],
        model=settings.target_model,
    )
    message = response.choices[0].message
    tool_calls = message.tool_calls or []
    print(f"model: {settings.target_model}")
    print(f"tool_call: {'yes' if tool_calls else 'no'}")
    for call in tool_calls:
        function = call.function
        print(f"tool_name: {function.name}")
        print(f"tool_arguments: {function.arguments}")


if __name__ == "__main__":
    main()
