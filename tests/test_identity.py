"""actors.yaml is a human-reviewed artifact, not a classifier."""

from __future__ import annotations

from top_engineers.identity import REVIEW_HINTS, Actors, build_candidates


def test_reviewed_file_wins_over_structural_signal():
    a = Actors(bots={"realbot"}, humans={"looks-like-a-bot"})
    assert a.is_bot("realbot", "User") is True
    assert a.is_bot("looks-like-a-bot", "Bot") is False, "human list must override account type"


def test_structural_signals_only_no_substring_guessing():
    """Substring nets produced 10 flags and 10 false positives -- all real employees."""
    a = Actors()
    assert a.is_bot("some-app[bot]", "User") is True
    assert a.is_bot("dependabot-lookalike", "User") is False, "must NOT classify on substring"
    assert a.is_bot("chog-ci", "User") is False
    assert a.is_bot("stamphog", "User") is False, "USER automation needs human review, not a guess"


def test_bot_account_type_is_structural():
    a = Actors()
    assert a.is_bot("anything", "Bot") is True


def test_candidates_are_a_review_queue_not_a_verdict():
    a = Actors()
    cands = build_candidates(
        [("stamphog", "User", 400), ("newbot", "Bot", 12), ("ordinary", "User", 50)], a
    )
    logins = {c["login"] for c in cands}
    assert "ordinary" not in logins
    assert "stamphog" in logins, "name hints should still PROMPT review"
    assert "newbot" in logins
    # Structural signals sort ahead of unreliable name hints.
    assert cands[0]["login"] == "newbot"
    stamp = next(c for c in cands if c["login"] == "stamphog")
    assert any("UNRELIABLE" in r for r in stamp["reasons"])


def test_already_reviewed_logins_are_not_re_proposed():
    a = Actors(bots={"knownbot"}, humans={"knownhuman"})
    cands = build_candidates([("knownbot", "Bot", 5), ("knownhuman", "User", 5)], a)
    assert cands == []


def test_aliases_canonicalise():
    a = Actors(aliases={"old-login": "new-login"})
    assert a.canonical("old-login") == "new-login"
    assert a.canonical("Old-Login") == "new-login"
    assert a.canonical("untouched") == "untouched"
