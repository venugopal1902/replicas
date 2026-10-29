"""Unit test suite for mail-engine backend modules: render, auth, models.
No server needed. Runs standalone.
"""
import sys
from pathlib import Path

# Ensure service root is in sys.path
SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from mail_engine.render import (
    render,
    used_tags,
    unknown_tags,
    text_to_html,
    build_html,
    unsubscribe_token,
    verify_unsubscribe_token,
    subject_score,
)
from mail_engine.auth import hash_password, verify_password

CHECKS = []


def check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((name, passed, detail))
    status = "PASS" if passed else "FAIL"
    suffix = f" :: {detail}" if detail else ""
    print(f"[{status}] {name}{suffix}")


def run_unit_tests() -> int:
    # 1. Merge tag rendering: first_name
    rendered_fn = render("Hello {{first_name}}!", {"first_name": "Alice"})
    check("merge_first_name", rendered_fn == "Hello Alice!", f"got: {rendered_fn!r}")

    # 2. Merge tag rendering: company
    rendered_co = render("Welcome to {{company}}.", {"company": "Acme Corp"})
    check("merge_company", rendered_co == "Welcome to Acme Corp.", f"got: {rendered_co!r}")

    # 3. Merge tag rendering: unknown tags -> empty string
    rendered_unk = render("Hi {{random_unknown_tag}} here", {})
    check("merge_unknown_tags_empty", rendered_unk == "Hi  here", f"got: {rendered_unk!r}")

    # 4. Merge tag rendering: defaults when field missing (first_name -> 'there')
    rendered_def_fn = render("Hi {{first_name}}", {})
    check("merge_default_first_name", rendered_def_fn == "Hi there", f"got: {rendered_def_fn!r}")

    # 5. Merge tag rendering: defaults when field missing (company -> 'your team')
    rendered_def_co = render("At {{company}}", {})
    check("merge_default_company", rendered_def_co == "At your team", f"got: {rendered_def_co!r}")

    # 6. Merge tag rendering: defaults when field missing (sender_name -> 'Venu')
    rendered_def_sn = render("Best, {{sender_name}}", {})
    check("merge_default_sender_name", rendered_def_sn == "Best, Venu", f"got: {rendered_def_sn!r}")

    # 7. used_tags extracts ordered deduplicated tags
    tags = used_tags("Hi {{first_name}}, {{company}} says hello to {{first_name}}")
    check("used_tags_extraction", tags == ["first_name", "company"], f"got: {tags!r}")

    # 8. unknown_tags identifies fields outside KNOWN_FIELDS
    unk = unknown_tags("{{first_name}} and {{custom_var}} at {{company}} with {{another_var}}")
    check("unknown_tags_identification", unk == ["custom_var", "another_var"], f"got: {unk!r}")

    # 9. text_to_html escapes <script> and dangerous tags
    escaped = text_to_html("<script>alert('xss')</script>")
    script_escaped = "<script>" not in escaped and "&lt;script&gt;" in escaped
    check("text_to_html_escaping", script_escaped, f"got: {escaped!r}")

    # 10. text_to_html paragraph splitting on double newline
    paras = text_to_html("Paragraph one.\n\nParagraph two.")
    check("text_to_html_paragraphs", paras == "<p>Paragraph one.</p><p>Paragraph two.</p>", f"got: {paras!r}")

    # 11. text_to_html converts single newline to <br>
    brs = text_to_html("Line one\nLine two")
    check("text_to_html_newlines_to_br", brs == "<p>Line one<br>Line two</p>", f"got: {brs!r}")

    # 12. build_html contains tracking pixel when tracking_id given
    html_pixel = build_html("Hello there", tracking_id="track_123456")
    pixel_ok = "/api/tracking/open/track_123456.gif" in html_pixel and "<img " in html_pixel
    check("build_html_tracking_pixel", pixel_ok, "pixel <img> tag with tracking id present")

    # 13. build_html contains unsubscribe link when unsubscribe_email given
    html_unsub = build_html("Hello there", unsubscribe_email="lead@example.com")
    unsub_ok = "Unsubscribe" in html_unsub and "/api/unsubscribe/" in html_unsub and "email=lead@example.com" in html_unsub
    check("build_html_unsubscribe_link", unsub_ok, "unsubscribe anchor tag and url present")

    # 14. unsubscribe_token determinism (consistent and case-insensitive)
    t1 = unsubscribe_token("user@example.com")
    t2 = unsubscribe_token("user@example.com")
    t3 = unsubscribe_token("USER@EXAMPLE.COM")
    t4 = unsubscribe_token("other@example.com")
    token_det = (t1 == t2 == t3) and (t1 != t4) and (len(t1) == 32)
    check("unsubscribe_token_determinism", token_det, f"t1={t1!r}")

    # 15. verify_unsubscribe_token good token returns True
    good_tok = unsubscribe_token("test@example.com")
    verify_good = verify_unsubscribe_token("test@example.com", good_tok)
    verify_good_ci = verify_unsubscribe_token("TEST@EXAMPLE.COM", good_tok)
    check("verify_unsubscribe_token_good", verify_good and verify_good_ci, "verified good token True")

    # 16. verify_unsubscribe_token bad token or mismatched email returns False
    verify_bad_tok = verify_unsubscribe_token("test@example.com", "invalid_token_xyz")
    verify_bad_email = verify_unsubscribe_token("other@example.com", good_tok)
    verify_empty = verify_unsubscribe_token("test@example.com", "")
    bad_ok = (not verify_bad_tok) and (not verify_bad_email) and (not verify_empty)
    check("verify_unsubscribe_token_bad", bad_ok, "rejected bad token, mismatched email, empty token")

    # 17. subject_score flags empty subject
    score_empty = subject_score("")
    score_spaces = subject_score("    ")
    empty_ok = ("empty" in score_empty["issues"]) and ("empty" in score_spaces["issues"]) and score_empty["score"] < 100
    check("subject_score_empty", empty_ok, f"issues={score_empty['issues']}")

    # 18. subject_score flags all-caps subject
    score_caps = subject_score("URGENT ANNOUNCEMENT PLEASE READ")
    caps_ok = "all caps" in score_caps["issues"]
    check("subject_score_all_caps", caps_ok, f"issues={score_caps['issues']}")

    # 19. subject_score flags too long subject (> 78 characters)
    score_long = subject_score("This is an exceedingly long subject line designed specifically to exceed seventy-eight chars")
    long_ok = "too long" in score_long["issues"] and score_long["length"] > 78
    check("subject_score_too_long", long_ok, f"length={score_long['length']}, issues={score_long['issues']}")

    # 20. subject_score flags spam words (free, guarantee, risk-free)
    score_spam = subject_score("Claim your free risk-free guarantee today")
    spam_issues = [i for i in score_spam["issues"] if i.startswith("spam word:")]
    check("subject_score_spam_words", len(spam_issues) >= 2, f"spam_issues={spam_issues}")

    # 21. subject_score returns 100 with empty issues for clean subject
    score_clean = subject_score("Quick question about your data pipeline")
    clean_ok = score_clean["score"] == 100 and score_clean["issues"] == []
    check("subject_score_clean", clean_ok, f"score={score_clean['score']}, issues={score_clean['issues']}")

    # 22. hash_password / verify_password round trip
    hashed = hash_password("secretPassword123!")
    pw_roundtrip = verify_password("secretPassword123!", hashed)
    check("password_hash_verify_roundtrip", pw_roundtrip and hashed.startswith("pbkdf2$"), "roundtrip True")

    # 23. verify_password wrong password returns False
    pw_wrong = verify_password("incorrectPassword", hashed)
    check("password_verify_wrong_password", pw_wrong is False, "wrong password returned False")

    # 24. verify_password malformed hash returns False
    malformed_tests = [
        verify_password("secretPassword123!", "not_a_hash"),
        verify_password("secretPassword123!", ""),
        verify_password("secretPassword123!", None),
        verify_password("secretPassword123!", "md5$salt$hash"),
        verify_password("secretPassword123!", "pbkdf2$onlyoneseparator"),
    ]
    check("password_verify_malformed_hash", all(res is False for res in malformed_tests), "malformed hashes return False")

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"\n{passed}/{total} unit checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run_unit_tests())
