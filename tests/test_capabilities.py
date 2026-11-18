"""Capability tokens allow a named tool, and caveats only narrow that grant."""

from dataclasses import replace

from agent_sec.gateway.capabilities import add_caveat, mint, verify


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
