"""Capability tokens allow a named tool, and caveats only narrow that grant."""

from dataclasses import replace

from agent_sec.gateway.capabilities import add_caveat, mint, mint_for_task, verify


def test_token_allows_permitted_tool_and_denies_others() -> None:
    root_key = b"test-root-key"
    token = add_caveat(mint(root_key, "cap-1"), "tool = read_file")

    assert verify(token, root_key, {"tool": "read_file"})
    assert not verify(token, root_key, {"tool": "send_email"})
    assert not verify(token, root_key, {"tool": "get_access_code"})


def test_added_caveat_only_narrows_access() -> None:
    root_key = b"test-root-key"
    broad = mint(root_key, "cap-1")
    files_only = add_caveat(broad, "tool = read_file")
    disclosed = add_caveat(files_only, "action = disclose_secret")

    assert verify(broad, root_key, {"tool": "send_email"})
    assert verify(files_only, root_key, {"tool": "read_file"})
    assert not verify(files_only, root_key, {"tool": "send_email"})

    assert verify(disclosed, root_key, {"tool": "read_file", "action": "disclose_secret"})
    assert not verify(disclosed, root_key, {"tool": "read_file"})
    assert not verify(disclosed, root_key, {"tool": "send_email", "action": "disclose_secret"})

    # Two tool equalities are a conjunction, so the token cannot be widened into a union.
    both_tools = add_caveat(files_only, "tool = send_email")
    assert not verify(both_tools, root_key, {"tool": "read_file"})
    assert not verify(both_tools, root_key, {"tool": "send_email"})


def test_forged_or_tampered_token_fails() -> None:
    root_key = b"test-root-key"
    token = add_caveat(mint(root_key, "cap-1"), "tool = read_file")
    request = {"tool": "read_file"}

    forged = add_caveat(mint(b"other-root-key", "cap-1"), "tool = read_file")
    assert not verify(forged, root_key, request)

    bad_signature = replace(token, signature="ab" * 32)
    assert not verify(bad_signature, root_key, request)

    rewritten_caveat = replace(token, caveats=("tool = send_email",))
    assert not verify(rewritten_caveat, root_key, {"tool": "send_email"})

    dropped_caveat = replace(token, caveats=())
    assert not verify(dropped_caveat, root_key, request)

    swapped_identifier = replace(token, identifier="cap-2")
    assert not verify(swapped_identifier, root_key, request)

    assert not verify(replace(token, signature="not-hex"), root_key, request)


def test_in_caveat_allows_listed_tools_and_narrows() -> None:
    root_key = b"test-root-key"
    token = add_caveat(mint(root_key, "cap-in"), "tool in read_file,send_email")

    assert verify(token, root_key, {"tool": "read_file"})
    assert verify(token, root_key, {"tool": "send_email"})
    assert not verify(token, root_key, {"tool": "fetch_url"})

    narrowed = add_caveat(token, "tool in read_file")
    assert verify(narrowed, root_key, {"tool": "read_file"})
    assert not verify(narrowed, root_key, {"tool": "send_email"})
    assert verify(token, root_key, {"tool": "send_email"})

    tampered = replace(token, caveats=("tool in fetch_url,send_email",))
    assert not verify(tampered, root_key, {"tool": "fetch_url"})
    assert not verify(tampered, root_key, {"tool": "read_file"})


def test_mint_for_task_empty_tool_list_allows_nothing() -> None:
    root_key = b"test-root-key"
    token = mint_for_task(root_key, "task-empty", [], allow_disclose_secret=False)

    assert token.caveats[0].startswith("tool in")
    assert not verify(token, root_key, {"tool": "read_file"})
    assert not verify(token, root_key, {"tool": ""})
    assert not verify(token, root_key, {})
    assert not verify(token, root_key, {"action": "disclose_secret"})


def test_mint_for_task_scopes_tools_and_optional_disclosure() -> None:
    root_key = b"test-root-key"
    tools_only = mint_for_task(
        root_key,
        "task-1",
        ["read_file", "send_email"],
        allow_disclose_secret=False,
    )
    assert verify(tools_only, root_key, {"tool": "read_file"})
    assert not verify(tools_only, root_key, {"tool": "get_access_code"})

    disclosed = mint_for_task(
        root_key,
        "task-1",
        ["read_file"],
        allow_disclose_secret=True,
    )
    assert verify(disclosed, root_key, {"tool": "read_file", "action": "disclose_secret"})
    assert not verify(disclosed, root_key, {"tool": "read_file"})
    assert not verify(disclosed, root_key, {"tool": "send_email", "action": "disclose_secret"})
