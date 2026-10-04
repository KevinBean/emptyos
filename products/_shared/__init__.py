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
- :mod:`tray`           — tray icons owned outside the daemon (product supervisor, desktop shell)
- :mod:`updater`, :mod:`stub` — versioned installs and the exe that picks the newest
- :mod:`build_release`, :mod:`smoke`, :mod:`smoke_update` — release zip + boot the real artifact
- :mod:`shell_core`     — desktop shell decisions: paths, health, splash (no GUI)
- :mod:`single_instance` — one shell per port + second-launch hand-off (Win32)
- :mod:`shell`          — the native window (pywebview → WebView2), attach mode

``shell_core``, ``single_instance``, ``shell`` and ``tray`` run from the shell's
own venv, not the daemon's, so they never import ``emptyos.sdk``. They use the
stdlib, each other, the stdlib-only top-level ``emptyos.headless`` and
``emptyos.desktop_presence``, and — lazily — ``webview``, pythonnet's ``System``
(UI-thread calls), ``pystray`` and ``PIL`` (the tray).
"""
