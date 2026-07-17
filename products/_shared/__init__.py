"""Shared plumbing for EmptyOS desktop products.

A *product* is a frozen, double-clickable slice of EmptyOS for someone who will
never run the daemon from source (``.claude/rules/product-packaging.md``). Each
product is a directory under ``products/`` holding a ``product.toml`` (name,
tier, brand, start URL) plus a ~40-line ``launcher.py`` that hands off to
:mod:`_shared.launcher_core`. Everything else — first-run config, daemon
supervision, window, tray, updates — lives here.

Extracted at product #2 (``desktop-windows``, after ``writedesk``), which is the
graduation trigger the packaging rule names.

Modules:
- :mod:`product_config` — parse ``product.toml`` (pure)
- :mod:`launcher_core`  — first-run config, daemon supervisor, window
- :mod:`tray`           — system-tray icon owned by the launcher process
"""
