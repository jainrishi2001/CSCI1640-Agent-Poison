"""The baseline prompt stays a plain office instruction."""

from agent_sec.gateway.prompts import BASE_SYSTEM_PROMPT


def test_base_prompt_asks_for_the_users_task_and_not_a_defense() -> None:
    text = BASE_SYSTEM_PROMPT.casefold()
    assert "office assistant" in text
    assert "tools" in text
    for phrase in ("ignore", "hidden instruction", "do not reveal", "secret"):
        assert phrase not in text
