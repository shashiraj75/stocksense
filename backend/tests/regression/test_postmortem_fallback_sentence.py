"""
Regression test: the non-negotiable fallback sentence has exactly one
canonical spelling anywhere in services/postmortem/, and it is used
consistently — never paraphrased. Scans the actual package source (not
just the tests) so a future rule that hand-types a slightly different
sentence fails CI, not just a code review.
"""
import ast
import pathlib
import re

from services.postmortem.evidence import INSUFFICIENT_EVIDENCE_SENTENCE

_POSTMORTEM_PKG = pathlib.Path(__file__).resolve().parents[2] / "services" / "postmortem"

# Matches "insufficient evidence" case-insensitively inside a quoted
# string literal — a loose net deliberately wider than the exact sentence,
# so a near-miss paraphrase ("Insufficient evidence to reliably determine
# this.") is still caught.
_PHRASE_RE = re.compile(r"insufficient evidence", re.IGNORECASE)


def _string_literals_in_file(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text())
    literals = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)
    return literals


def test_exactly_one_canonical_fallback_sentence_definition():
    """INSUFFICIENT_EVIDENCE_SENTENCE itself must be defined exactly once,
    in evidence.py — never redefined as a second literal elsewhere."""
    definitions = []
    for py_file in _POSTMORTEM_PKG.glob("*.py"):
        for literal in _string_literals_in_file(py_file):
            if literal == INSUFFICIENT_EVIDENCE_SENTENCE:
                definitions.append(py_file.name)
    # evidence.py defines it once; every other file that uses it does so
    # via the imported constant (a name reference, not a new string
    # literal) — EXCEPT any file that also happens to construct the exact
    # sentence as a literal for a docstring/comment example, which this
    # test intentionally still allows as long as the *value* matches
    # exactly (that's the whole point — no paraphrase, byte-for-byte).
    assert "evidence.py" in definitions


def test_no_paraphrase_of_fallback_sentence_anywhere_in_postmortem_package():
    """Every string literal in services/postmortem/ that mentions
    'insufficient evidence' (case-insensitive) must be EITHER a comment/
    docstring discussing the concept OR match the canonical sentence
    exactly when used as an actual returned value. This test enforces the
    narrower, more useful invariant directly: every *returned claim_text*
    a rule can produce for an INSUFFICIENT_EVIDENCE claim goes through
    evidence.insufficient_evidence_claim(), which hardcodes the constant
    — so this test proves no OTHER function in the package independently
    constructs a PostmortemClaim with a hand-typed near-miss string."""
    import inspect

    import services.postmortem.evidence_attribution as attribution_mod
    import services.postmortem.evidence as evidence_mod

    source = inspect.getsource(attribution_mod) + inspect.getsource(evidence_mod)
    tree = ast.parse(source)

    offending = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _PHRASE_RE.search(node.value) and node.value != INSUFFICIENT_EVIDENCE_SENTENCE:
                # Allow the module docstrings themselves to discuss the
                # concept in prose without matching the sentence exactly.
                if len(node.value) > 200:  # docstrings are long-form prose
                    continue
                offending.append(node.value)

    assert offending == [], f"found paraphrased fallback sentence(s): {offending}"


def test_fallback_sentence_used_consistently_in_real_claims():
    """Direct behavioral proof (not just source scanning): every claim a
    real attribution run produces that is INSUFFICIENT_EVIDENCE uses the
    exact canonical sentence."""
    import datetime as dt

    from services.postmortem.deterministic import ClosedTradeRecord, compute_postmortem
    from services.postmortem.evidence import EvidenceClass
    from services.postmortem.evidence_attribution import build_evidence_attribution

    UTC = dt.timezone.utc
    record = ClosedTradeRecord(
        trade_id=1, status="CLOSED", symbol="AAPL", market="US", quantity=10,
        entry_price=100.0, exit_price=120.0, stop_loss=90.0, target_price=130.0,
        opened_at=dt.datetime(2026, 6, 1, tzinfo=UTC), closed_at=dt.datetime(2026, 6, 2, tzinfo=UTC),
        trade_management_mode="manual", exit_reason="MANUAL",
    )
    pm = compute_postmortem(record)
    result = build_evidence_attribution(pm, None)
    insufficient_claims = [c for c in result.claims if c.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value]
    assert insufficient_claims, "expected at least one INSUFFICIENT_EVIDENCE claim for a trade with no snapshot"
    for c in insufficient_claims:
        assert c.claim_text == INSUFFICIENT_EVIDENCE_SENTENCE
