"""EmptyOS chatbot service — importable as the ``chatbot`` package.

Standalone (Docker) it runs as ``uvicorn chatbot.main:app`` with the repo's
``services/`` dir on ``sys.path``. Mounted, the External Lab Host imports
``from chatbot.main import app, chatbot_init, chatbot_shutdown`` and drives the
lifespan itself (Starlette does not propagate lifespan to mounted sub-apps).
"""
