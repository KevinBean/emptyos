

Three layers, different purposes:

| Layer | Where | When |
|---|---|---|
| **Breadcrumbs** (reactor) | Daily journal note (`50_Journal/`) | Automatic — `git:saved` ripples |
| **Session summaries** (`/devlog`) | `10_Projects/emptyos/log/YYYY-MM-DD.md` | End of session — invoke `/devlog` |
| **Raw history** | `git log` | Every commit |

At session end for meaningful changes, invoke `/eos-session-wrapup`. See `.claude/rules/docs-sync.md` for triggers.
