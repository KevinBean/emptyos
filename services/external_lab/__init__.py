"""EmptyOS External Lab Host — one local ASGI host on 127.0.0.1:9100.

Mounts external-style modules under path prefixes (chatbot at /chatbot) and
serves local test websites under /demos/<id>/. It is developer/local
infrastructure — it does NOT replace the independently deployed production
chatbot (which serves at its own public domain + root paths).

Run: ``uvicorn external_lab.main:app`` with ``services/`` on sys.path (so both
``external_lab`` and ``chatbot`` import as packages). The external-lab-host
plugin spawns exactly this.
"""
