"""Load the immutable owner-approved air-device policy, including after relocation."""
import hashlib
import json
from pathlib import Path

VERSION = "air-device-rules-1"
MANIFEST = "tests/fixtures/air-device-takeoff/2026-09-16-rule-binding.json"
MANIFEST_SHA256 = "918c7f5697f7df0be9166c76ccc162960e7654ff60193228a4312a71cb68b4f1"
_MAX_BYTES = 256 * 1024


class AirDeviceRuleError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _read(root, relative, expected):
    path = root / relative
    try:
        # Policy travels with the package; no external policy through symlinks.
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        if path.is_symlink() or not path.is_file():
            raise ValueError("not a regular policy file")
        with path.open("rb") as stream:
            raw = stream.read(_MAX_BYTES + 1)
    except (OSError, ValueError, RuntimeError) as exc:
        raise AirDeviceRuleError("rule_unavailable", "Approved air-device policy is unavailable.") from exc
    if len(raw) > _MAX_BYTES or hashlib.sha256(raw).hexdigest() != expected:
        raise AirDeviceRuleError("rule_changed", "Approved air-device policy bytes do not match.")
    return raw


def load_binding(root=None):
    """Verify exact approved bytes. Historical pending labels are not new decisions.

    The pinned manifest links the unmodified draft/examples to the subsequent
    owner receipt. A later presentation-heading edit cannot change this binding.
    No drawing geometry, scale, inference, filesystem location or timestamp is
    part of the policy's identity.
    """
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    manifest = json.loads(_read(root, MANIFEST, MANIFEST_SHA256))
    data = {name: _read(root, name, digest) for name, digest in manifest["files"].items()}
    decision = json.loads(data[manifest["decision"]])
    examples = json.loads(data[manifest["examples"]])
    if (decision["decision"] != "approved" or
            decision["approved_rules"] != ["A%02d" % i for i in range(1, 13)] or
            decision["approved_examples"] != ["AC%02d" % i for i in range(1, 18)] or
            [row["id"] for row in examples["examples"]] != decision["approved_examples"] or
            manifest["files"][manifest["approved_packet"]] != decision["approved_packet_sha256"] or
            manifest["files"][manifest["examples"]] != decision["examples_sha256"]):
        raise AirDeviceRuleError("rule_unapproved", "The bound policy lacks the approved air-device basis.")
    return {
        "schema": "air-device-approved-rules-1",
        "version": VERSION,
        "manifest_sha256": MANIFEST_SHA256,
        "decision_sha256": manifest["files"][manifest["decision"]],
        "packet_sha256": decision["approved_packet_sha256"],
        "examples_sha256": decision["examples_sha256"],
        "rules": decision["approved_rules"],
        "examples": decision["approved_examples"],
        "unit": "each",
        "attribute_output_units": "imperial",
    }
