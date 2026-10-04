

```bash
cp emptyos.example.toml emptyos.toml   # then set notes.path = "/path/to/your/vault"
pip install -e .
restart.bat                            # Windows; or: python -m emptyos start
# → http://127.0.0.1:9000  (use 127.0.0.1, not localhost — local mode binds
#                           IPv4 loopback only; some browsers try ::1 first)
```

In a Claude Code session: `/eos-session-resume` to pick up the last session, `/eos-session-wrapup` to close one out. `/preflight` sanity-checks git + daemons before any work. Run `/env-check` if shell behavior seems off. See `.claude/rules/environment.md`.
