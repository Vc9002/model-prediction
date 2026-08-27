"""Pin the artifact-hash verification convention and the documented non-verifiers.

The two config/models artifacts that do not self-verify against their
embedded ``artifact_hash`` are DOCUMENTED evidence artifacts, not defects:

- ``archive/wnba-spread-baseline-v1.json`` — ``_retired*`` fields were
  appended at archive time; archived rollback evidence is never re-signed
  (docs/FEATURE_MODEL_AUDIT.md "Archive integrity").
- ``research/mlb-v9-candidate-1.json`` — quarantine fields (``status`` /
  ``invalidation_reason`` / ``replacement``) were appended by the
  quarantine commit; preserved for audit, never promoted (docs/ROADMAP.md).

Everything else under config/models/ with an ``artifact_hash`` must
self-verify under the registry's canonical convention
(``production_registry.compute_artifact_hash``: sort_keys, compact
separators, exclude the hash field).
"""

import hashlib
import json
from pathlib import Path

from model_prediction.production_registry import compute_artifact_hash

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "config" / "models"

# The ONLY artifacts that may fail self-verification. Any addition needs a
# documented reason (evidence artifact that is never re-signed).
KNOWN_NON_VERIFIERS = frozenset(
    {
        "config/models/archive/wnba-spread-baseline-v1.json",
        "config/models/research/mlb-v9-candidate-1.json",
    }
)


def _hash(payload: dict, *, sort_keys: bool = True, separators=(",", ":")) -> str:
    """Manual reconstruction of the canonical hash (compact, ASCII payloads)."""
    canonical = {k: v for k, v in payload.items() if k != "artifact_hash"}
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=sort_keys, separators=separators).encode()
    ).hexdigest()


def _artifacts():
    for path in sorted(MODELS.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("artifact_hash"):
            yield str(path.relative_to(ROOT)), payload


def test_canonical_hash_convention_is_pinned_on_a_live_artifact() -> None:
    """compute_artifact_hash must stay sort_keys + compact + excludes the hash field."""
    rel = "config/models/mlb-elo-trend-lr-v8.json"
    payload = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    assert payload["artifact_hash"] == compute_artifact_hash(payload)
    # The manual reconstruction must agree with the registry's function.
    assert payload["artifact_hash"] == _hash(payload)


def test_only_documented_evidence_artifacts_fail_self_verification() -> None:
    """No artifact may mismatch its embedded hash except the two documented ones."""
    non_verifiers = {
        rel for rel, payload in _artifacts() if compute_artifact_hash(payload) != payload["artifact_hash"]
    }
    assert non_verifiers == KNOWN_NON_VERIFIERS


def test_wnba_archive_hash_verifies_without_retired_annotation() -> None:
    """The _retired* annotation is the only post-signing edit (never re-signed)."""
    rel = "config/models/archive/wnba-spread-baseline-v1.json"
    payload = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    stored = payload["artifact_hash"]
    annotation_fields = {"_retired", "_retired_date", "_retirement_reason"}
    pre_annotation = {k: v for k, v in payload.items() if k not in annotation_fields}
    assert stored == _hash(pre_annotation)


def test_candidate_hash_verifies_without_quarantine_annotation() -> None:
    """The quarantine fields are the only post-signing edit; the writer used default separators."""
    rel = "config/models/research/mlb-v9-candidate-1.json"
    payload = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    stored = payload["artifact_hash"]
    quarantine = {"status", "invalidation_reason", "replacement"}
    pre_quarantine = {k: v for k, v in payload.items() if k not in quarantine}
    assert stored == _hash(pre_quarantine, separators=(", ", ": "))
