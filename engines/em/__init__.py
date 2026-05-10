"""engines/em — frequency-domain EM physics, EmptyOS-side adapters.

In-tree light-compute pieces only:
    biot_savart/  — 3D Biot-Savart B-field + image-method E-field
                    for overhead lines & buried cables (port of EMF JS repo)
    resap/        — soil resistivity fitting (RESAP), uniform + 2-layer

Heavy-compute em-family solvers (MALT, MALZ, HIFREQ, full SPLITS,
Sommerfeld) live in the sibling repo `emptyos-em`. This package never
imports from there; consumers either depend on emptyos-em separately
or use the in-tree light-compute pieces only.
"""
