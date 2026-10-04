"""EmptyOS Commons — multi-tenant shared knowledge service.

The shared layer of a collaborative EmptyOS: real accounts, a public commons,
private content, and selective sharing. Each personal EmptyOS daemon stays
single-user (the private layer); this service holds only content a user has
chosen to publish or share. See docs/AUTH.md and the build plan.

Structure (lifted from services/englishos-control-plane, then re-pointed):
- models.py        record types (+ commons content records)
- auth.py          IdentityVerifier Protocol + opaque session hashing
- settings.py      env-backed config
- visibility.py    the owner+visibility+ACL primitive (pure, the heart)
- repositories.py  repo Protocol + Postgres + in-memory impls
- app.py           FastAPI factory + auth + content endpoints
"""
