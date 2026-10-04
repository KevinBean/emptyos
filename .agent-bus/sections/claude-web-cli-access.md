

```bash
# Outbound — prints the plan (recipient, each attachment + size) then asks y/N.
# --dry-run previews without sending; --json refuses without --yes.
eos send you@example.com -s "Subject" --body-file note.txt -a dist/thing.zip
```

CLI detects running daemon → proxies via HTTP (shared kernel). No daemon → falls back to local kernel.
