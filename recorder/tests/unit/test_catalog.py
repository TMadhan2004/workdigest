"""Meta-test: every test is documented in the test-case catalog."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load_catalog_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "gen_test_catalog", ROOT / "scripts" / "gen_test_catalog.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_test_catalog"] = module
    spec.loader.exec_module(module)
    return module


def test_every_test_has_a_unique_documented_case() -> None:
    """TC-META-001: Every test has a unique TC ID with Given/When/Then, and the catalog is up to date.

    Given: all test files under recorder/tests.
    When: the catalog generator collects docstrings.
    Then: there are no missing or duplicate IDs, and docs/test-cases.md matches the generated text.
    """
    catalog = _load_catalog_module()
    cases, problems = catalog.collect()
    assert problems == []
    assert cases
    assert catalog.OUTPUT.read_text(encoding="utf-8") == catalog.render(cases), (
        "docs/test-cases.md is stale: run `python3 scripts/gen_test_catalog.py`"
    )
