"""Re-export shim — clustering moved to emptyos/sdk/clustering.py (2026-06-11).

Promoted when app-analytics became the second consumer (rule 9). Import from
``emptyos.sdk.clustering`` in new code; this shim keeps old import paths alive.
"""

from emptyos.sdk.clustering import (  # noqa: F401 — re-exported for the old import path
    build_graph,
    get_clusters,
    label_propagation,
    name_cluster,
)
