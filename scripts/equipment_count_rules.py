"""Exact owner-approved E01-E12 authority; no external data or model calls."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    'owner_decision_sha256': ('tests/fixtures/equipment-takeoff/2026-09-16-owner-decision.json',
                            '8bd1bf975100bbff4ff232ac3d6c48fd3aa14c82603c673326e7f99b386b4251'),
    'rules_sha256': ('docs/superpowers/specs/2026-09-16-equipment-counting-rules.md',
                     'dfeea1adabd2478dca7622e96051d6ae3a2ea8eeac42179542b32c4ba03ae134'),
    'examples_sha256': ('tests/fixtures/equipment-takeoff/2026-09-16-rule-examples.json',
                        '0c233e93253b0c545c4a59ed2b4c6205e021f20c1300dfb63dbff5cf2e1ec150'),
}


def binding():
    value = {'version': 'equipment-count-rules-1', 'rules': ['E%02d' % n for n in range(1, 13)]}
    for name, (relative, expected) in PINS.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError('Approved equipment rule bytes changed: ' + relative)
        value[name] = actual
    return value
