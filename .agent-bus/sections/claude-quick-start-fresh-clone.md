

```bash
cp emptyos.toml.example emptyos.toml   # then set notes.path = "D:/YourVault"
pip install -e .
restart.bat                            # Windows; or: python -m emptyos start
# → http://127.0.0.1:9000  (use 127.0.0.1, not localhost — local mode binds
#                           IPv4 loopback only; some browsers try ::1 first)
```

In a Claude Code session: `/eos-session-resume` to pick up the last session, `/eos-session-wrapup` to close one out. `/preflight` sanity-checks git + daemons before any work. Run `/env-check` at session start if shell behavior seems off — it probes Python 3.13 dep gaps, actively tests the cp1252 stdout trap with a non-ASCII write, and curls :9000 + :9001. See `.claude/rules/environment.md`.
