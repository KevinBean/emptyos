"""Seed a realistic populated PDR via the running daemon.

Run: python scripts/seed_reports_demo.py
Requires: EmptyOS daemon running. Reads [network] from emptyos.toml (or uses
EMPTYOS_BASE env var) so the script works on any machine regardless of
host/port config.

Creates a demo "Offshore Export Cable Route PDR" with populated sections,
requirements/risks/verification tables, and tries both PDF and DOCX exports.
Leaves the report in place so you can inspect it in the UI.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
import urllib.request as ur
from pathlib import Path


def _resolve_base() -> str:
    env = os.environ.get("EMPTYOS_BASE")
    if env:
        return env.rstrip("/")
    try:
        cfg = tomllib.loads(
            (Path(__file__).resolve().parent.parent / "emptyos.toml").read_text(encoding="utf-8")
        )
        net = cfg.get("network", {})
        host = net.get("host", "127.0.0.1")
        if host in ("0.0.0.0", ""):
            host = "127.0.0.1"
        port = net.get("port", 9000)
        return f"http://{host}:{port}"
    except Exception:
        return "http://127.0.0.1:9000"


BASE = _resolve_base()


def req(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = ur.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with ur.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read().decode())


SECTIONS = {
    "executive-summary": """This review covers the preliminary design of the 132 kV AC export cable linking an offshore wind farm substation to the onshore grid connection point. The route crosses approximately 48 km of seabed with mixed sand, clay, and exposed bedrock segments.

The design is at PDR maturity: route corridor is selected, burial strategy is defined per soil class, and the cable conductor is sized against the worst-case export profile. Open items are flagged in {{req:REQ-006}} (marine-growth allowance) and in the risk register.""",
    "scope": """### In scope
- Submarine cable (wet section, 48 km)
- Transition joint at landfall
- Onshore duct bank to the grid substation (700 m)
- Cable protection system for exposed bedrock sections

### Out of scope
- Onshore substation equipment beyond the sealing end
- Cable-lay vessel procurement (separate contract)
- SCADA / fibre integration (covered by {{req:REQ-008}} only as an interface requirement)

### Assumptions
- Water depths along the route: 8 to 42 m
- Return-to-port base: nearest commercial port with suitable cable-lay quay
- Design life: 30 years with in-service inspection every 5 years""",
    "requirements": """Requirements are derived from the farm export profile, the grid code, and the marine route constraints. Each requirement carries a verification method defined in Section 6.

{{table:requirements}}

Priority is set against outage-cost impact: high-priority requirements drive irreversible design decisions; medium-priority requirements can be met with test-based refinement.""",
    "architecture": """### Cable construction
Three-core XLPE-insulated cable with integrated fibre-optic element. Conductor cross-section 630 mm2 stranded copper, sized against continuous current rating with derating for burial depth and soil thermal resistivity.

### Route corridor
The corridor follows the marine spatial plan designated cable window, minimising conflict with fishing grounds and shipping lanes. Deviations are documented in the route engineering report.

### Burial strategy
| Soil class | Burial method | Target depth |
|---|---|---|
| Soft sand / silt | Jet trencher | 1.5 m |
| Dense sand / clay | Mechanical plough | 1.0 m |
| Exposed bedrock | Rock placement + cable protection system | 0.5 m cover |

### Protection
Exposed segments over bedrock receive an articulated half-shell cable protection system, locally rock-placed. Landfall transition uses horizontal directional drilling under the intertidal zone.""",
    "interfaces": """- **Offshore substation sealing end.** Cable terminates at the substation switchgear via a GIS-compatible outdoor sealing end. ICD-SUB-001 defines the mechanical and electrical interface.
- **Landfall joint bay.** Transition joint between submarine and onshore cable. Joint bay sized for future maintenance access; vent provision per {{req:REQ-005}}.
- **Onshore grid substation.** Onshore cable terminates at the grid substation cable sealing end; beyond this is out of scope.
- **Fibre network.** Integrated fibre is terminated at both ends into the SCADA and communications patch panels.""",
    "risks": """Principal risks are captured in the risk register. The highest-ranked item is the exposed bedrock at the landfall approach ({{risk:RISK-002}}), which drives the cable protection system scope.

{{table:risks}}""",
    "verification": """Verification methods map back to requirements. Every REQ ID is covered; closure happens at CDR (cable type-test data), FAT (factory joint performance), and commissioning (install-as-built records).

{{table:verification}}""",
    "open-issues": """1. Final decision on marine-growth allowance for thermal derating, awaiting site-specific biofouling survey (drives {{req:REQ-006}}).
2. Landfall HDD length. Two candidate entry points remain; choice hinges on the cliff-stability geotechnical report due week 14.
3. Fibre core allocation. Open discussion with the operations team on whether to reserve dark cores for future instrumentation.""",
    "signoff": """Approval below confirms the design has passed the preliminary milestone. The CDR gate date will be set once open issues are closed.""",
}


REQUIREMENTS = [
    {
        "id": "REQ-001",
        "text": "Cable shall carry the farm export profile (peak 180 MVA) continuously for 30 years.",
        "priority": "High",
        "verification": "Analysis",
        "status": "Approved",
    },
    {
        "id": "REQ-002",
        "text": "Cable shall withstand seabed thermal resistivity up to 1.2 K m/W at burial depth.",
        "priority": "High",
        "verification": "Analysis",
        "status": "Approved",
    },
    {
        "id": "REQ-003",
        "text": "Short-circuit rating: 40 kA for 1 second at the offshore substation busbar.",
        "priority": "High",
        "verification": "Test",
        "status": "Proposed",
    },
    {
        "id": "REQ-004",
        "text": "Burial depth shall meet the regulator minimum per soil class along the entire route.",
        "priority": "High",
        "verification": "Inspection",
        "status": "Approved",
    },
    {
        "id": "REQ-005",
        "text": "Joint bay shall provide access for in-service maintenance without a cable cut.",
        "priority": "Medium",
        "verification": "Demonstration",
        "status": "Proposed",
    },
    {
        "id": "REQ-006",
        "text": "Thermal derating allowance for marine growth to be finalised at CDR.",
        "priority": "Medium",
        "verification": "Analysis",
        "status": "Proposed",
    },
    {
        "id": "REQ-007",
        "text": "Cable shall be type-tested to CIGRE TB 490 or equivalent.",
        "priority": "High",
        "verification": "Test",
        "status": "Proposed",
    },
    {
        "id": "REQ-008",
        "text": "Integrated fibre shall support a minimum 24 cores with OTDR baseline at commissioning.",
        "priority": "Medium",
        "verification": "Test",
        "status": "Proposed",
    },
]


RISKS = [
    {
        "id": "RISK-001",
        "description": "Seabed survey reveals a deeper clay layer than assumed; jet trencher struggles with burial.",
        "likelihood": "Medium",
        "impact": "Medium",
        "mitigation": "Contingency budget for plough-based burial alternative; confirm with geotechnical survey before CDR.",
        "owner": "Marine Engineering Lead",
    },
    {
        "id": "RISK-002",
        "description": "Exposed bedrock at the landfall approach requires additional cable protection system, scope and cost increase.",
        "likelihood": "High",
        "impact": "Medium",
        "mitigation": "Half-shell CPS pre-procured; rock placement plan aligned with installation vessel window.",
        "owner": "Project Manager",
    },
    {
        "id": "RISK-003",
        "description": "Factory joint failure during FAT delays shipment past the weather window.",
        "likelihood": "Low",
        "impact": "High",
        "mitigation": "Two factory joints qualified in parallel; spare cable length reserved on the reel.",
        "owner": "QA Lead",
    },
    {
        "id": "RISK-004",
        "description": "Marine-growth allowance under-estimated, reducing long-term ampacity margin.",
        "likelihood": "Medium",
        "impact": "Low",
        "mitigation": "Biofouling survey commissioned; thermal model to be revised at CDR.",
        "owner": "Cable Engineer",
    },
    {
        "id": "RISK-005",
        "description": "Fishing-vessel anchor strike on an unburied bedrock section.",
        "likelihood": "Low",
        "impact": "High",
        "mitigation": "Cable corridor gazetted; CPS rated for anchor impact energy; notice-to-mariners coordinated.",
        "owner": "Marine Consents",
    },
]


VERIFICATION = [
    {
        "req_id": "REQ-001",
        "method": "Analysis",
        "evidence": "Ampacity calc report REP-ELC-0012 rev A",
        "status": "Planned",
    },
    {
        "req_id": "REQ-002",
        "method": "Analysis",
        "evidence": "Thermal model with soil-resistivity sensitivity",
        "status": "Planned",
    },
    {
        "req_id": "REQ-003",
        "method": "Test",
        "evidence": "Type-test short-circuit certificate (CIGRE TB 490)",
        "status": "Planned",
    },
    {
        "req_id": "REQ-004",
        "method": "Inspection",
        "evidence": "Post-lay survey report and as-built burial-depth map",
        "status": "Planned",
    },
    {
        "req_id": "REQ-005",
        "method": "Demonstration",
        "evidence": "Joint bay access mock-up; FAT walkthrough",
        "status": "Planned",
    },
    {
        "req_id": "REQ-006",
        "method": "Analysis",
        "evidence": "Biofouling survey and updated thermal model at CDR",
        "status": "Planned",
    },
    {
        "req_id": "REQ-007",
        "method": "Test",
        "evidence": "Type-test certificate from accredited lab",
        "status": "Planned",
    },
    {
        "req_id": "REQ-008",
        "method": "Test",
        "evidence": "OTDR baseline trace at commissioning",
        "status": "Planned",
    },
]


META = {
    "subtitle": "132 kV AC submarine export cable, 48 km",
    "version": "1.0",
    "authors": ["Demo Author"],
    "organisation": "EmptyOS Demonstration",
    "status": "ready",
    "approvers": ["Lead Engineer", "Project Manager", "Client Representative"],
    "tags": ["report", "report-pdr", "demo"],
}


def main() -> int:
    created = req(
        "POST",
        "/reports/api/reports",
        {
            "template": "pdr",
            "title": "DEMO - Offshore Export Cable Route PDR",
            "project_id": "demo-offshore-cable",
        },
    )
    if not created.get("ok"):
        print(f"Create failed: {created}")
        return 1
    rid = created["id"]
    print(f"Created: {rid}")

    for slug, body in SECTIONS.items():
        put = req(
            "PUT",
            f"/reports/api/reports/{rid}/sections/{slug}",
            {"body": body, "meta": {"status": "ready"}},
        )
        ok = "OK" if put.get("ok") else "FAIL"
        print(f"  section {slug:20s} [{ok}] ({len(body)} chars)")

    for name, rows in (
        ("requirements", REQUIREMENTS),
        ("risks", RISKS),
        ("verification", VERIFICATION),
    ):
        put = req("PUT", f"/reports/api/reports/{rid}/tables/{name}", {"rows": rows})
        ok = "OK" if put.get("ok") else "FAIL"
        print(f"  table   {name:20s} [{ok}] ({put.get('count', len(rows))} rows)")

    patched = req("PATCH", f"/reports/api/reports/{rid}/meta", {"meta": META})
    print(f"  meta patched: {patched.get('ok')}")

    print()
    print(f"  UI:       {BASE}/reports/#{rid}")
    print(f"  Preview:  {BASE}/reports/api/reports/{rid}/preview")

    print("\n--- Trying PDF export ---")
    pdf = req("POST", f"/reports/api/reports/{rid}/export/pdf")
    if pdf.get("ok"):
        print(f"  PDF: {pdf['file']} ({pdf['size']:,} bytes)")
    else:
        print(f"  PDF skipped or failed: {pdf}")

    print("\n--- Trying DOCX export ---")
    docx = req("POST", f"/reports/api/reports/{rid}/export/docx")
    if docx.get("ok"):
        print(f"  DOCX: {docx['file']} ({docx['size']:,} bytes)")
    else:
        print(f"  DOCX skipped or failed: {docx}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
