"""One-shot audit of EOS_UI.confirm() call sites.

Buckets every caller by form (bare string vs options object) and notes whether
the options form sets `danger` explicitly. Used 2026-05-24 to scope the
default-flip sweep.
"""
import re
import pathlib

ROOTS = ["apps", "emptyos/web/static"]
EXTS = {".html", ".js"}
SKIP = ("_retired", "__pycache__")

BACKSLASH = chr(92)

bare = []
opts_no_danger = []
opts_explicit = []

for root in ROOTS:
    for path in pathlib.Path(root).rglob("*"):
        if not path.is_file() or path.suffix not in EXTS:
            continue
        if any(s in str(path) for s in SKIP):
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"EOS_UI\.confirm\(", src):
            start = match.end()
            depth = 1
            i = start
            in_str = None
            esc = False
            while i < len(src) and depth > 0:
                ch = src[i]
                if esc:
                    esc = False
                elif in_str:
                    if ch == BACKSLASH:
                        esc = True
                    elif ch == in_str:
                        in_str = None
                elif ch in ('"', "'", "`"):
                    in_str = ch
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                i += 1
            call = src[start:i - 1].strip()
            line_no = src[:match.start()].count("\n") + 1
            entry = (str(path).replace(BACKSLASH, "/"), line_no, call[:140].replace("\n", " "))
            if call.startswith("{"):
                if re.search(r"\bdanger\s*:", call):
                    opts_explicit.append(entry)
                else:
                    opts_no_danger.append(entry)
            elif call.startswith(('"', "'", "`")):
                bare.append(entry)

print(f"BARE-STRING ({len(bare)}):")
for e in bare:
    print(f"  {e[0]}:{e[1]}  {e[2]}")
print(f"\nOPTIONS without danger (silent-flip risk) ({len(opts_no_danger)}):")
for e in opts_no_danger:
    print(f"  {e[0]}:{e[1]}  {e[2]}")
print(f"\nOPTIONS with explicit danger ({len(opts_explicit)}):")
for e in opts_explicit:
    print(f"  {e[0]}:{e[1]}  {e[2]}")
