"""CIGRE Technical Brochure 880 — IEC 60287 benchmark cases.

Seven curated test scenarios from CIGRE WG B1.56 covering single-core
and three-core cables in various installations. Target accuracy ±0.5 A
(matching the JS implementation in KevinBean/Cable-reticulation-tool).

Cases are stored as JSON next to this file. Loaded by `cases.py`.
The pytest harness in `tests/test_cigre_tb880.py` iterates them.

Status: skeleton — case fixtures to be ported from the JS tool's
fixtures directory. Tests skip when the fixture is missing.
"""

from .cases import load_cases, Case

__all__ = ["load_cases", "Case"]
