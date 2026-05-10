"""Domain adapters — convert app-domain dataclasses into time-domain netlists.

One module per source app. Each adapter is a pure function:

    domain_object → Netlist

It must NOT touch the kernel, daemon, or vault — it's just data transformation.
This keeps adapters testable in isolation and lets unrelated apps depend on
the engine without pulling in adapter code they don't need.
"""
