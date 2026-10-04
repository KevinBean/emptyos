"""Unit tests for scripts/guard_destructive_delete.py — the delete-asks guard.

Pins both directions (`.claude/rules/audits.md`): it must ask on the commands
that actually deleted data without permission on 2026-09-23, and stay silent on
everyday commands that merely mention a delete word. No daemon, no kernel.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location(
    "guard_destructive_delete", _SCRIPTS / "guard_destructive_delete.py"
)
guard = importlib.util.module_from_spec(_SPEC)
sys.modules["guard_destructive_delete"] = guard
_SPEC.loader.exec_module(guard)

classify = guard.classify
SCRATCH = "C:/Users/someone/AppData/Local/Temp/claude/D--repo/abc/scratchpad"


ASKS = [
    # The command that deleted the folder, and the installers beside it.
    ('Remove-Item -LiteralPath "D:\\Archive\\2023-2024work" -Recurse -Force -Confirm:$false', "PowerShell"),
    ('$targets = @("D:\\temp\\old")\nforeach ($t in $targets) { Remove-Item -LiteralPath $t -Recurse -Force }', "PowerShell"),
    ('cd "D:/Notes" && rm -- "30_Resources/some note.md"', "Bash"),
    ("rm -rf build/", "Bash"),
    ("del /s /q D:\\data\\*.db", "PowerShell"),
    ("ri notes.md", "PowerShell"),
    ("find . -name '*.log' -delete", "Bash"),
    ("git clean -fdx", "Bash"),
    ('robocopy "D:/a" "D:/b" /MIR', "PowerShell"),
    ("gcloud projects delete my-project --quiet", "Bash"),
    ("gh repo delete someone/x --yes", "Bash"),
    # Through a runtime, inside a quoted string or a heredoc body.
    ("python -c \"import shutil; shutil.rmtree('D:/stuff')\"", "Bash"),
    ("python - <<'PY'\nimport pathlib\npathlib.Path('D:/Notes/x.md').unlink()\nPY", "Bash"),
    ("[IO.Directory]::Delete('D:\\x', $true)", "PowerShell"),
    # A scratch path does not excuse a second, non-scratch target.
    (f'rm "{SCRATCH}/a.txt" "D:/Notes/b.md"', "Bash"),
    # A relative target cannot be proven to be scratch.
    ("rm -rf crt.git crt_paths.txt", "Bash"),
]


@pytest.mark.parametrize("cmd,tool", ASKS)
def test_asks_before_deleting(cmd, tool):
    assert classify(tool, cmd), cmd


SILENT = [
    ("git status --short", "Bash"),
    ("git rm --cached old.txt", "Bash"),            # git history keeps it
    ('git commit -m "remove the rm -rf from the docs"', "Bash"),
    ("echo 'Remove-Item is dangerous'", "PowerShell"),
    ("grep -rn 'shutil' scripts", "Bash"),
    ("Get-ChildItem -LiteralPath D:\\Archive -Force", "PowerShell"),
    ("python -m pytest tests/test_x.py -q", "Bash"),
    ("npm run format", "Bash"),                      # "rm" inside a word
    ("git checkout -- confirm.txt", "Bash"),
    # Claude's own temp scratch area is exempt.
    (f'rm -rf "{SCRATCH}/crt.git" "{SCRATCH}/crt_paths.txt"', "Bash"),
    (f"Remove-Item -LiteralPath '{SCRATCH}/old.py'", "PowerShell"),
]


@pytest.mark.parametrize("cmd,tool", SILENT)
def test_silent_on_non_deletes(cmd, tool):
    assert classify(tool, cmd) is None, cmd


# ── Targets, not verbs — measured on the prompts of 2026-09-24..10-03 ──
# Each exemption is pinned beside the real delete of the same shape it must keep,
# and every bypass a hostile review constructed on 2026-10-03 is an ASKS row.

TEMP = "C:/Users/someone/AppData/Local/Temp"
VAULT = "D:/My Notes/70_Media"

SILENT_TARGETS = [
    # Regenerable caches.
    ("rm -rf scripts/__pycache__ tests/__pycache__ && python -m pytest tests/t.py -q", "Bash"),
    ("cd D:/repo && rm -f apps/x/__pycache__/app.cpython-313.pyc; git status --short", "Bash"),
    ("rm -rf .pytest_cache", "Bash"),
    # Scratch reached through a variable assigned once in the same command.
    (f'S="{SCRATCH}"; rm -rf "$S/pubclone"; mkdir -p "$S/pubclone/tests"', "Bash"),
    (f'cd /d/repo; SP="{SCRATCH}"; rm -rf "$SP/cmp"; mkdir -p "$SP/cmp"', "Bash"),
    (f'$wt = "{SCRATCH.replace("/", chr(92))}\\t2wt"; if (Test-Path "$wt\\x") {{ "abort" }} else {{ Remove-Item -LiteralPath $wt -Recurse -Force }}', "PowerShell"),
    ('S="$TEMP/claude/D--repo/abc/scratchpad"; rm -rf "$S/x"', "Bash"),          # env var
    ('rm -f "$LOCALAPPDATA/Temp/claude/D--repo/abc/scratchpad/old.txt"', "Bash"),
    (f'rm -f "{SCRATCH}"/final-*', "Bash"),                                      # glob under scratch
    ("rm -rf /c/Users/someone/AppData/Local/Temp/claude/D--repo/abc/scratchpad/x", "Bash"),
    # The Windows extended-length prefix, and a parameter value that is not a path.
    (f'$wt = "{SCRATCH}"; Remove-Item -LiteralPath "\\\\?\\$wt" -Recurse -Force', "PowerShell"),
    (f'Remove-Item -LiteralPath "{SCRATCH}/t2wt" -Recurse -Force -ErrorAction Stop', "PowerShell"),
    # Delete calls inside code that is WRITTEN to a file nothing then runs.
    ("cd /d/repo; cat >> tests/test_x.py <<'PY'\n\ndef test_a(tmp_path):\n    Path(first).unlink()\nPY", "Bash"),
    ("cat > upscale/prepare.py <<'PY'\nimport shutil\nshutil.rmtree(tmp)\nPY", "Bash"),
    # ...or inside a string literal of a patch script that cannot run it.
    ("python - <<'PY'\np=Path(\"t.py\"); s=p.read_text()\nold='''    x.unlink()\n'''\ns=s.replace(old, \"\")\np.write_text(s)\nPY", "Bash"),
    ("python - <<'PY'\nrows = [(\"bad header deleted\", \"os.replace(a, b)\", \"self.store_path.unlink()\")]\nPY", "Bash"),
    # Bash rmdir refuses a non-empty directory.
    ("git mv apps/a apps/b && rmdir apps/a 2>/dev/null", "Bash"),
    # The `${S}` form of a plain read.
    (f'S="{SCRATCH}"; rm -rf "${{S}}/x"', "Bash"),
    # A continued line still names only scratch.
    (f'rm -rf "{SCRATCH}/a" \\\n  "{SCRATCH}/b"', "Bash"),
    # A `<<-` heredoc with a tab-indented delimiter, written to a file.
    ("cat > gen.py <<-PY\n\timport shutil\n\tshutil.rmtree(tmp)\n\tPY", "Bash"),
]


@pytest.mark.parametrize("cmd,tool", SILENT_TARGETS)
def test_silent_on_harmless_targets(cmd, tool, monkeypatch):
    monkeypatch.setattr(guard, "ENV", {"TEMP": TEMP, "LOCALAPPDATA": "C:/Users/someone/AppData/Local"})
    assert classify(tool, cmd) is None, cmd


ASKS_TARGETS = [
    # The same verbs on real data.
    ('cd "D:/Vault/mv/stills" && cp a.jpg b.jpg && rm S041-alt.jpg S042-alt.jpg', "Bash"),
    ('rm -rf "$S/x"', "Bash"),                                                    # unresolved var
    ("rm -rf scripts/__pycache__ apps/x/app.py", "Bash"),                        # one bad target
    ("cd /d/repo && rm -f build.log && cat >> README.md <<'MD'\nnote\nMD", "Bash"),
    ("rmdir D:\\x -Recurse", "PowerShell"),                                       # Remove-Item alias
    ("rmdir D:\\x -Recurse", "exec_command"),                                     # only Bash is exempt
    # Variables: a reassigned, case-folded, prefix-only or later assignment is unresolved.
    (f'S="{SCRATCH}"; S="{VAULT}"; rm -rf "$S"', "Bash"),
    (f'$p = "{SCRATCH}"; $p = "{VAULT}"; Remove-Item -LiteralPath $p -Recurse -Force', "PowerShell"),
    (f'echo "x; S={SCRATCH}"; S="{VAULT}"; rm -rf "$S"', "Bash"),
    (f'echo "x; S={SCRATCH}"; rm -rf "$S"', "Bash"),
    (f'$S = "{SCRATCH}"; $s = "{VAULT}"; Remove-Item $S -Recurse', "PowerShell"),
    (f'S="{SCRATCH}" rm -rf "$S"/*', "Bash"),
    (f'S="{SCRATCH}" true; rm -rf "$S"/*', "Bash"),   # S is empty here: rm -rf /*
    (f'rm -rf "$S/x"; S="{SCRATCH}"', "Bash"),
    ('$env:TEMP = "D:\\"; Remove-Item -Recurse -Force "$env:TEMP\\claude\\..\\My Notes"', "PowerShell"),
    (f'cd "{SCRATCH}" && for X in ../../Documents; do rm -rf "x/$X"; done', "Bash"),
    # Any other way to rebind the name leaves it unresolved (second review, 2026-10-03).
    (f'S="{SCRATCH}"; declare S="{VAULT}"; rm -rf "$S"', "Bash"),
    (f'S="{SCRATCH}"; local S="{VAULT}"; rm -rf "$S"', "Bash"),
    (f'S="{SCRATCH}"; read S <<< "{VAULT}"; rm -rf "$S"', "Bash"),
    (f'S="{SCRATCH}"; for S in "{VAULT}"; do rm -rf "$S"; done', "Bash"),
    (f'S="{SCRATCH}"; printf -v S "%s" "{VAULT}"; rm -rf "$S"', "Bash"),
    (f'S="{SCRATCH}"; eval S="{VAULT}"; rm -rf "$S"', "Bash"),
    (f'S="{SCRATCH}"; S=$(echo "{VAULT}"); rm -rf "$S"', "Bash"),
    (f'S="{SCRATCH}"; : ${{S:={VAULT}}}; rm -rf "$S"', "Bash"),
    (f'S="{SCRATCH}"; S={VAULT}; rm -rf "$S"', "Bash"),          # unquoted, has a space
    (f'S="{SCRATCH}"; S+="/../../../../../../../../D/x"; rm -rf "$S"', "Bash"),
    (f'$S = "{SCRATCH}"; Set-Variable -Name S -Value "{VAULT}"; Remove-Item $S -Recurse', "PowerShell"),
    (f'$S = "{SCRATCH}"; New-Variable S "{VAULT}" -Force; Remove-Item $S -Recurse', "PowerShell"),
    (f'$S = "{SCRATCH}"; foreach ($S in @("{VAULT}")) {{ Remove-Item $S -Recurse }}', "PowerShell"),
    (f'$S = "{SCRATCH}"; ${{S}} = "{VAULT}"; Remove-Item $S -Recurse', "PowerShell"),
    (f'$S = "{SCRATCH}"; $global:S = "{VAULT}"; Remove-Item $S -Recurse', "PowerShell"),
    (f'$S = "{SCRATCH}"; [Environment]::SetEnvironmentVariable("S","{VAULT}"); Remove-Item $env:S -Recurse', "PowerShell"),
    (f'$S = "{SCRATCH}"; $S, $T = "{VAULT}", "x"; Remove-Item $S -Recurse', "PowerShell"),
    (f'$S = "{SCRATCH}"; $S += "/../../../../../../../../D/x"; Remove-Item $S -Recurse', "PowerShell"),
    ('TEMP=/d; rm -rf "$TEMP/claude/a/b/../../../My Notes"', "Bash"),
    ('read TEMP <<< "D:/"; rm -rf "$TEMP/claude/a/b/x"', "Bash"),   # env name rebound
    (f'rm -rf "{SCRATCH}/$(rm -rf {VAULT})"', "Bash"),          # a substitution runs
    (f'rm -rf "{SCRATCH}/`echo x`"', "Bash"),
    # Cache names do not excuse `..`.
    ('rm -rf "D:/My Notes/__pycache__/../70_Media"', "Bash"),
    ("rm -rf __pycache__/../..", "Bash"),
    ('Remove-Item -Recurse -Force "D:/My Notes/.hypothesis/../70_Media"', "PowerShell"),
    # No "created earlier" or cwd exemption: mkdir -p and cd may do nothing.
    (f'mkdir -p "{VAULT}" && rm -rf "{VAULT}"', "Bash"),
    ("[ -d out ] || mkdir out; rm -rf out", "Bash"),
    ("mkdir -p out && rm -rf out/old", "Bash"),
    (f'echo \'see >"{VAULT}"\'; rm -rf "{VAULT}"', "Bash"),
    (f'cd "{SCRATCH}" && rm -rf notes', "Bash"),
    (f'cd "{SCRATCH}" 2>/dev/null; rm -rf *', "Bash"),
    (f'cd "{SCRATCH}/snap" && rm -rf ../../../D--other', "Bash"),
    # Scratch is anchored, normalised, and never claude/ itself.
    ('rm -rf "/tmp/claude/../../d/My Notes/70_Media"', "Bash"),
    ('rm -rf "D:/My Notes/AppData/Local/Temp/claude/../../../../70_Media"', "Bash"),
    ('rm -rf "D:/My Notes/70_Media/tmp/claude-renders/a/b"', "Bash"),
    ('rm -rf "D:/My Notes/Users/x/AppData/Local/Temp/claude/a/b"', "Bash"),
    ("rm -rf /tmp/claude", "Bash"),
    (f'rm -rf "{SCRATCH}/../../../../../../Documents"', "Bash"),
    ('rm -rf "C:/Users/someone/AppData/Local/Temp/claude/"', "Bash"),
    ('rm -rf "C:/Users/someone/AppData/Local/Temp/claude/D--repo/"*', "Bash"),
    # A PowerShell array, a continued line, a move into scratch, an xargs feed.
    (f'Remove-Item "{SCRATCH}/a","{VAULT}" -Recurse -Force', "PowerShell"),
    (f'rm -rf "{SCRATCH}/a" \\\n  "{VAULT}"', "Bash"),
    (f'cd "{SCRATCH}"; Remove-Item a `\n  "{VAULT}" -Recurse -Force', "PowerShell"),
    (f'mv "{VAULT}" "{SCRATCH}/trash" && rm -rf "{SCRATCH}/trash"', "Bash"),
    (f'Move-Item "{VAULT}" "{SCRATCH}/trash"; Remove-Item "{SCRATCH}/trash" -Recurse', "PowerShell"),
    (f'mv "{VAULT}" __pycache__ && rm -rf __pycache__', "Bash"),
    (f'find "{VAULT}" -type f | xargs rm -f "{SCRATCH}/marker"', "Bash"),
    (f'rsync -a --remove-source-files "{VAULT}/" "{SCRATCH}/t/" && rm -rf "{SCRATCH}/t"', "Bash"),
    # Delete calls in code that IS run, however it reaches the interpreter.
    ("python - <<'PY'\nfrom pathlib import Path\nold='''x'''\nPath(\"D:/Notes/x.md\").unlink()\nPY", "Bash"),
    ("\"D:/tools/python.exe\" - <<'PY'\nimport os\nos.remove(\"D:/n.md\")\nPY", "Bash"),
    ("cd D:/repo && python - <<'EOF'\nimport os\nos.remove(os.path.join(D, 'old.py'))\nEOF", "Bash"),
    ("pwsh -Command \"[IO.File]::Delete('D:/x.md')\"", "Bash"),
    (f"cat <<'EOF' | python -\nimport shutil\nshutil.rmtree('{VAULT}')\nEOF", "Bash"),
    (f"tee s.py <<'EOF' | python -\nimport shutil\nshutil.rmtree('{VAULT}')\nEOF", "Bash"),
    (f"cat > s.py <<'EOF'\nimport shutil\nshutil.rmtree('{VAULT}')\nEOF\npython s.py", "Bash"),
    (f"cat > s.py <<'EOF'\nimport shutil\nshutil.rmtree('{VAULT}')\nEOF\npython s*.py", "Bash"),
    (f"F=s.py; cat > $F <<'EOF'\nimport shutil\nshutil.rmtree('{VAULT}')\nEOF\npython $F", "Bash"),
    (f"cat > a <<'EOF'\nimport shutil\nshutil.rmtree('{VAULT}')\nEOF\nmv a b; ./b", "Bash"),
    # Run by a launcher the runner list does not know: the name, or the variable, gives it away.
    (f"cat > s.ps1 <<'EOF'\n[IO.Directory]::Delete('{VAULT}', $true)\nEOF\ncmd /c s.ps1", "Bash"),
    (f"F=s.ps1; cat > $F <<'EOF'\n[IO.Directory]::Delete('{VAULT}', $true)\nEOF\ncmd /c $F", "Bash"),
    (f"cat > s.ps1 <<'EOF'\n[IO.Directory]::Delete('{VAULT}', $true)\nEOF\nstart s.ps1", "Bash"),
    (f"cat > s.ps1 <<'EOF'\n[IO.Directory]::Delete('{VAULT}', $true)\nEOF\npwsh -File s.ps1", "Bash"),
    (f"@'\nimport shutil\nshutil.rmtree('{VAULT}')\n'@ | python -", "PowerShell"),
    (f"python - <<< \"import shutil; shutil.rmtree('{VAULT}')\"", "Bash"),
    (f"echo \"import shutil; shutil.rmtree('{VAULT}')\" | python", "Bash"),
    (f"python - \\\n<<'EOF'\nimport shutil\nshutil.rmtree('{VAULT}')\nEOF", "Bash"),
    (f"CODE=\"import shutil; shutil.rmtree('{VAULT}')\"; python -c \"$CODE\"", "Bash"),
    (f"python3 - <<-EOF\n\timport shutil\n\tshutil.rmtree('{VAULT}')\n\tEOF", "Bash"),
    (f"python -X utf8 -W ignore::DeprecationWarning -B -u -c \"import shutil; shutil.rmtree('{VAULT}')\"", "Bash"),
    (f"python - <<'EOF'\nexec(\"import shutil; shutil.rmtree('{VAULT}')\")\nEOF", "Bash"),
    ("python - <<'EOF'\nopen('s.py','w').write(\"import shutil; shutil.rmtree('x')\")\nEOF\npython s.py", "Bash"),
    # No "self-created" exemption: a temp file elsewhere excuses nothing.
    (f"python -c \"from pathlib import Path; Path('t.txt').write_text('x'); import shutil; shutil.rmtree('{VAULT}')\"", "Bash"),
    ("python - <<'EOF'\nopen('a.txt','w').close()\nimport os; os.remove('a.txt'); os.remove('D:/My Notes/n.md')\nEOF", "Bash"),
    ("python - <<'EOF'\nopen('notes.md','a').close()\nimport os; os.remove('notes.md')\nEOF", "Bash"),
    ("python - <<'EOF'\nimport os; os.remove('notes.md')  # open('notes.md','w')\nEOF", "Bash"),
    # A cd operand is a path like any other.
    (f"cd \"D:/My Notes\" && python -c \"import shutil; shutil.rmtree('70_Media')\" > \"{SCRATCH}/log.txt\"", "Bash"),
]


@pytest.mark.parametrize("cmd,tool", ASKS_TARGETS)
def test_asks_on_real_targets(cmd, tool, monkeypatch):
    monkeypatch.setattr(guard, "ENV", {"TEMP": TEMP})
    assert classify(tool, cmd), cmd


# ── Gaps the original guard also had, closed 2026-10-03 ──
# Code handed to another interpreter, indirect runtime deletes, and verbs in
# positions the command-start pattern did not read.

NL = "\n"


def _py(*lines):
    return "python - <<'EOF'" + NL + NL.join(lines) + NL + "EOF"


GAP_ASKS = [
    # 1. nested shell code
    ("bash <<'EOF'" + NL + f"rm -rf '{VAULT}'" + NL + "EOF", "Bash"),
    ("cat <<'EOF' | sh" + NL + f"rm -rf '{VAULT}'" + NL + "EOF", "Bash"),
    ("tee s.sh <<'EOF' | bash" + NL + f"rm -rf '{VAULT}'" + NL + "EOF", "Bash"),
    (f"bash -c 'rm -rf \"{VAULT}\"'", "Bash"),
    (f"sh -c \"rm -rf '{VAULT}'\"", "Bash"),
    (f"powershell -NoProfile -Command \"Remove-Item -Recurse -Force '{VAULT}'\"", "Bash"),
    (f"pwsh -c \"Remove-Item '{VAULT}' -Recurse\"", "Bash"),
    ("powershell -EncodedCommand SQBFAFgA", "Bash"),
    (f"cmd //c rd /s /q \"{VAULT}\"", "Bash"),
    (f"cmd /c rd /s /q \"{VAULT}\"", "PowerShell"),
    (f"eval \"rm -rf '{VAULT}'\"", "Bash"),
    (f"Invoke-Expression \"Remove-Item '{VAULT}' -Recurse\"", "PowerShell"),
    (f"echo \"$(rm -rf '{VAULT}')\"", "Bash"),
    (f"x=\"`rm -rf '{VAULT}'`\"", "Bash"),
    ("cat > s.sh <<'EOF'" + NL + f"rm -rf '{VAULT}'" + NL + "EOF" + NL + "source s.sh", "Bash"),
    ("cat > s.sh <<'EOF'" + NL + f"rm -rf '{VAULT}'" + NL + "EOF" + NL + "bash -s < s.sh", "Bash"),
    ("cat > s.sh <<'EOF'" + NL + f"rm -rf '{VAULT}'" + NL + "EOF" + NL + "./s.sh", "Bash"),
    (f"powershell -c \"$j = Get-Item '{VAULT}'; $j.Delete($true)\"", "Bash"),
    # 2. indirect runtime deletes
    (_py("import os", f"getattr(os, 'remove')('{VAULT}/n.md')"), "Bash"),
    (_py(f"__import__('shutil').rmtree('{VAULT}')"), "Bash"),
    (_py("from shutil import rmtree as r", f"r('{VAULT}')"), "Bash"),
    (_py("from os import remove", f"remove('{VAULT}/n.md')"), "Bash"),
    (f"python -c \"from os import remove; remove('{VAULT}/n.md')\"", "Bash"),
    (_py("import shutil", "x = shutil.rmtree", f"x('{VAULT}')"), "Bash"),
    (_py("import os", f"os.system(\"rm -rf '{VAULT}'\")"), "Bash"),
    (_py("import subprocess", f"subprocess.run(['rm', '-rf', '{VAULT}'])"), "Bash"),
    (f"node -e \"require('fs').rmSync('{VAULT}', {{recursive: true}})\"", "Bash"),
    ("node - <<'EOF'" + NL + f"require('fs').unlinkSync('{VAULT}/n.md');" + NL + "EOF", "Bash"),
    (f"perl -e 'unlink \"{VAULT}/n.md\"'", "Bash"),
    (f"ruby -e 'FileUtils.rm_rf(\"{VAULT}\")'", "Bash"),
    # 3. verbs in positions the guard did not read
    (f"if false; then :; else rm -rf '{VAULT}'; fi", "Bash"),
    (f"command rm -rf '{VAULT}'", "Bash"),
    (f"\\rm -rf '{VAULT}'", "Bash"),
    (f"/bin/rm -rf '{VAULT}'", "Bash"),
    (f"/usr/bin/rm -rf '{VAULT}'", "Bash"),
    (f"env rm -rf '{VAULT}'", "Bash"),
    (f"nohup rm -rf '{VAULT}' &", "Bash"),
    (f"find '{VAULT}' -name '*.md' -exec rm -f {{}} +", "Bash"),
    (f"find '{VAULT}' -type f -execdir /bin/rm {{}} \\;", "Bash"),
    (f"find '{VAULT}' -name __pycache__ -o -name '*.md' -exec rm -rf {{}} +", "Bash"),
    (f"find '{VAULT}' -name __pycache__ -exec rm -rf {{}}/.. \\;", "Bash"),
    (f"find '{VAULT}' -delete", "Bash"),
    (f"find '{VAULT}' -name __pycache__ -o -type f -exec rm -rf {{}} +", "Bash"),
    (f"find '{VAULT}' -not -name __pycache__ -delete", "Bash"),
    (f"node -e \"require('fs').rm('{VAULT}', {{recursive: true}}, () => {{}})\"", "Bash"),
    (f"node -e \"const fs = require('fs'); fs.rm('{VAULT}', {{recursive: true}}, () => {{}})\"", "Bash"),
    ("cat > s.ps1 <<'EOF'" + NL + f"[IO.Directory]::Delete('{VAULT}', $true)" + NL + "EOF" + NL + "& .\\s.ps1", "PowerShell"),
    (f"(Get-Item '{VAULT}').Delete($true)", "PowerShell"),
    (f"$f = Get-Item '{VAULT}/n.md'; $f.Delete()", "PowerShell"),
]


@pytest.mark.parametrize("cmd,tool", GAP_ASKS)
def test_asks_on_nested_and_indirect_deletes(cmd, tool, monkeypatch):
    monkeypatch.setattr(guard, "ENV", {"TEMP": TEMP})
    assert classify(tool, cmd), cmd


GAP_SILENT = [
    ("bash scripts/restart-check.sh", "Bash"),
    (f"bash -c 'rm -rf \"{SCRATCH}/x\"'", "Bash"),
    (f"powershell -NoProfile -Command \"Remove-Item -Recurse '{SCRATCH}/x'\"", "Bash"),
    ("echo \"$(git rev-parse HEAD)\"", "Bash"),
    ('a=$(grep -c "f(" x); b=$(grep -c "g(" x); c=$(grep -c "h(" x); d=$(grep -c "k(" x)', "Bash"),
    ("N=$(git diff --cached --name-only | wc -l)", "Bash"),
    ('x=$(grep -cE "\\balert\\(|\\bconfirm\\(" "$f"); y=$(grep -c "fetch(" "$f"); z=$(ls $(dirname $f) | wc -l)', "Bash"),
    (_py("import subprocess", "subprocess.run(['git', 'status'])"), "Bash"),
    (_py("import subprocess", "subprocess.run(args)"), "Bash"),
    (_py("xs = [1, 2]", "xs.remove(1)"), "Bash"),
    ("find . -name '*.py' -exec grep -l foo {} +", "Bash"),
    ("find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null; python -m pytest t.py", "Bash"),
    ('find engines apps -name __pycache__ -path "*prelim*" -prune -exec rm -rf {} +', "Bash"),
    ("find . -name '*.pyc' -delete", "Bash"),
    ("git commit -F- <<'MSG'" + NL + "remove with `(Get-Item x).Delete()` and `unlink`" + NL + "MSG", "Bash"),
    ("git commit -m 'drop the `rm -rf` from the docs'", "Bash"),
    ("git commit -m \"note: bash -c 'rm -rf x' is never run here\"", "Bash"),
    ("git commit -m \"note: powershell -c 'Remove-Item x' is never run\"", "Bash"),
    (f'S="{SCRATCH}"; x=$(grep -c "f(" a.txt); rm -rf "$S/x"', "Bash"),
    ('grep -n -i -E "rm|remove|delete|unlink" .claude/settings.json', "Bash"),
    ('python scripts/review_receipt.py write --waive "x.py - indirect deletes (from shutil import rmtree, getattr) missed"', "Bash"),
    ("cat > notes.md <<'EOF'" + NL + "Remove-Item -Recurse x" + NL + "EOF" + NL + "bash scripts/other.sh", "Bash"),
    ("if [ -f x ]; then echo yes; else echo no; fi", "Bash"),
    ("command -v python", "Bash"),
    ("Get-ChildItem | Select-Object Name", "PowerShell"),
    (f'T="{SCRATCH}/up"; rm -rf "$T"; mkdir -p "$T/in"; ffmpeg -ss 4.5 -t 1 -i "D:/x.mp4" "$T/in/%04d.png"', "Bash"),
    (f'S="{SCRATCH}"; D=$S/fb5 && rm -rf $D && mkdir -p $D', "Bash"),
]


@pytest.mark.parametrize("cmd,tool", GAP_SILENT)
def test_silent_on_everyday_nested_commands(cmd, tool, monkeypatch):
    monkeypatch.setattr(guard, "ENV", {"TEMP": TEMP})
    assert classify(tool, cmd) is None, cmd


# ── Third review, 2026-10-03: holes and false prompts in the gap closure ──
# Kept verbatim from the review's case file so each row maps to a finding.
class _Review3:
    NL = "\n"
    BS = "\\"
    SC = "C:/Users/u/AppData/Local/Temp/claude/D--r/x/scratchpad"
    V = "D:/My Notes/70_Media"
    VW = "D:" + BS + "My Notes" + BS + "70_Media"

    ASK = [
        # 1 CRITICAL — provider-path rebinding
        ("PowerShell", f'$target = "{SC}"; Set-Item Variable:/target "{V}"; Remove-Item $target -Recurse -Force'),
        ("PowerShell", f'$target = "{SC}"; Set-Item -Path Variable:{BS}target -Value "{V}"; Remove-Item $target -Recurse -Force'),
        ("PowerShell", f'$target = "{SC}"; New-Item -Path Variable:{BS}target -Value "{V}" -Force; Remove-Item $target -Recurse'),
        ("PowerShell", f'$target = "{SC}"; Copy-Item Variable:/other Variable:/target; Remove-Item $target -Recurse'),
        ("PowerShell", f'Set-Item Env:/TEMP "{V}"; Remove-Item "$env:TEMP/claude/D--r/x/scratchpad" -Recurse -Force'),
        ("PowerShell", f'Set-Item Env:{BS}TEMP "{V}"; Remove-Item "$env:TEMP/claude/D--r/x/scratchpad" -Recurse -Force'),
        # 2 CRITICAL — find cache relief
        ("Bash", f'find "{V}" {BS}! -name __pycache__ -delete'),
        ("Bash", f"find \"{V}\" '!' -name __pycache__ -delete"),
        ("Bash", f'find "{V}" -name __pycache__ , -delete'),
        ("Bash", f'find "{V}" -delete -name __pycache__ -delete'),
        ("Bash", f'find "{V}" -exec rm -rf {{}} + -name __pycache__ -exec rm -rf {{}} +'),
        # 3 HIGH — primaries after \;
        ("Bash", f'find "{V}" -type f -exec echo {{}} {BS}; -exec rm -f {{}} {BS};'),
        ("Bash", f'find "{V}" -type f -exec echo {{}} {BS}; -delete'),
        ("Bash", f'find "{V}" -name __pycache__ -exec rm -rf {{}} {BS}; -o -exec rm -rf {{}} {BS};'),
        # 4 HIGH — quoted cmd payload
        ("Bash", f'cmd //c "rd /s /q {VW}"'),
        ("PowerShell", f'cmd /c "rmdir /s /q {VW}"'),
        ("Bash", f'cmd.exe /C "del /s /q {VW}{BS}n.md"'),
        # 5 HIGH — combined shell flags
        ("Bash", f"bash -lc 'rm -rf \"{V}\"'"),
        ("Bash", f"sh -xc 'rm -rf \"{V}\"'"),
        ("Bash", f"bash -c -- 'rm -rf \"{V}\"'"),
        # 6 MEDIUM — substitutions in unquoted heredocs
        ("Bash", "cat > notes.md <<EOF" + NL + f'$(rm -rf "{V}")' + NL + "EOF"),
        ("Bash", "cat > notes.md <<EOF" + NL + f'`rm -rf "{V}"`' + NL + "EOF"),
        ("Bash", "git commit -F- <<MSG" + NL + f'x $(rm -rf "{V}")' + NL + "MSG"),
        # 7 MEDIUM — wrappers with option arguments, if/while
        ("Bash", f'sudo -u root rm -rf "{V}"'),
        ("Bash", f'nice -n 10 rm -rf "{V}"'),
        ("Bash", f'env -u FOO rm -rf "{V}"'),
        ("Bash", f'timeout 60 rm -rf "{V}"'),
        ("Bash", f'if rm -rf "{V}"; then echo ok; fi'),
        ("Bash", f'while rm -rf "{V}"; do sleep 1; done'),
        # 8 MEDIUM — xargs with flags
        ("Bash", f'find "{V}" -type f -print0 | xargs -0 rm -f'),
        ("Bash", f'ls "{V}" | xargs -I{{}} rm {{}}'),
        # 9 MEDIUM — eval / iex forms
        ("Bash", f"eval rm -rf \"'{V}'\""),
        ("PowerShell", f"Invoke-Expression -Command \"Remove-Item '{V}' -Recurse\""),
        # 10 MEDIUM — PowerShell object deletes
        ("PowerShell", f"Get-ChildItem '{V}' | ForEach-Object Delete"),
        ("PowerShell", f"$items = Get-ChildItem '{V}'; $items[0].Delete()"),
        ("PowerShell", f"$o = Get-Item '{V}/n.md'; $o.Directory.Delete($true)"),
        # 11 MEDIUM — Python aliases
        ("Bash", f"python -c \"import os as o; o.remove('{V}/n.md')\""),
        ("Bash", f"python -c \"__import__('os').remove('{V}/n.md')\""),
        ("Bash", "python - <<'EOF'" + NL + "from os import *" + NL + f"remove('{V}/n.md')" + NL + "EOF"),
        ("Bash", "python - <<'EOF'" + NL + f"if True: from os import remove; remove('{V}/n.md')" + NL + "EOF"),
        ("Bash", "python - <<'EOF'" + NL + "from os import (" + NL + "    remove," + NL + ")" + NL + f"remove('{V}/n.md')" + NL + "EOF"),
        # 12 MEDIUM — echo-written files run later, echo piped to a shell
        ("Bash", f"echo 'rm -rf \"{V}\"' > s.sh && bash s.sh"),
        ("Bash", f"printf 'rm -rf \"{V}\"' > s.sh; sh s.sh"),
        ("Bash", f"echo 'rm -rf \"{V}\"' | bash"),
        # 19 scope notes taken in
        ("Bash", f'rsync -a --delete empty/ "{V}/"'),
        ("Bash", f'git -C "{V}" clean -fdx'),
        ("Bash", "git clean -d --force"),
        ("Bash", f'shred -u "{V}/n.md"'),
        ("Bash", f'/usr/local/bin/rm -rf "{V}"'),
        ("Bash", "node - <<'EOF'" + NL + "const {rm} = require('fs/promises');" + NL + f"rm('{V}', {{recursive: true}});" + NL + "EOF"),
        ("Bash", f"perl -MFile::Path=remove_tree -e 'remove_tree(\"{V}\")'"),
        ("Bash", f"ruby -e 'File.delete(\"{V}/n.md\")'"),
    ]

    SILENT = [
        # 13 — bash -ec is not an encoded command
        ("Bash", "bash -ec 'echo hi'"),
        ("Bash", "sh -ec 'make test'"),
        # 14 — getattr of a non-delete attribute
        ("Bash", "python -c \"import os; print(getattr(os, 'sep'))\""),
        ("Bash", "python - <<'EOF'" + NL + "import os" + NL + "flag = getattr(os, 'O_BINARY', 0)" + NL + "EOF"),
        # 15 — ./file as an argument is not running it
        ("Bash", "cat > test_x.py <<'EOF'" + NL + "def test_a():" + NL + "    d = {1: 2}" + NL + "    del d[1]" + NL + "EOF" + NL + "python -m pytest ./test_x.py -q"),
        ("Bash", "cat > msg.txt <<'EOF'" + NL + "rm the old flag; del x" + NL + "EOF" + NL + "git commit -F ./msg.txt"),
        # 16 — spawn text that is only written or searched for
        ("Bash", "cat > fix.py <<'EOF'" + NL + "import subprocess" + NL + "subprocess.run(['rm', '-rf', 'build'])" + NL + "EOF" + NL + "git add fix.py"),
        ("Bash", "grep -rn 'subprocess.run([\"rm\"' scripts/"),
        ("Bash", "grep -rn \"os.system(\\\"rm\" scripts/"),
        # 17 — Claude Code's own commit shape with list items and a delete word
        ("Bash", "git commit -m \"$(cat <<'EOF'" + NL + "fix(guard): wrappers" + NL + NL + "rm behind a wrapper now asks; so do" + NL + "1) find -exec rm" + NL + "2) del in cmd" + NL + "EOF" + NL + ")\""),
        # 18 — LOW false positives
        ("Bash", "if [ -d x ]; then echo else rm; fi"),
        ("Bash", "python x.py --shell sh <<'EOF'" + NL + "rm is a word here" + NL + "EOF"),
        ("Bash", "echo $(echo $(echo $(echo $(date))))"),
        ("Bash", "rg --regexp=os.remove scripts/"),
        # 20 — a value with no spaces before `;` resolves
        ("Bash", f'D={SC}/x; rm -rf "$D"'),
    ]


_Review3.ASK += [
    ("Bash", "pwsh -ec SQBFAFgA"),
    ("Bash", f'find "{_Review3.V}" -name __pycache__ -delete -o -delete'),
    ("Bash", f"python -c \"import os; os.system('rm -rf {_Review3.V}')\""),
]
_Review3.SILENT += [
    ("Bash", f'C="{_Review3.SC}/x"; rm -rf "$C"'),       # the C in C:/Users is a drive
]


@pytest.mark.parametrize("tool,cmd", _Review3.ASK)
def test_review3_deletes_ask(tool, cmd, monkeypatch):
    monkeypatch.setattr(guard, "ENV", {"TEMP": "C:/Users/u/AppData/Local/Temp"})
    assert classify(tool, cmd), cmd


@pytest.mark.parametrize("tool,cmd", _Review3.SILENT)
def test_review3_everyday_commands_stay_silent(tool, cmd, monkeypatch):
    monkeypatch.setattr(guard, "ENV", {"TEMP": "C:/Users/u/AppData/Local/Temp"})
    assert classify(tool, cmd) is None, cmd


@pytest.mark.parametrize("name,asks", [
    ("mcp__plugin_firebase_firebase__firestore_delete_document", True),
    ("mcp__claude_ai_Gmail__trash_thread", True),
    ("mcp__claude_ai_Google_Calendar__delete_event", True),
    ("mcp__claude_ai_Gmail__search_threads", False),
    ("mcp__plugin_firebase_firebase__firestore_get_document", False),
])
def test_mcp_deletes_ask(name, asks):
    assert bool(classify(name, "")) is asks


def test_hook_emits_ask_not_deny():
    """End to end through stdin: a delete gets permissionDecision "ask"."""
    payload = {"tool_name": "PowerShell",
               "tool_input": {"command": 'Remove-Item -LiteralPath "D:\\x" -Recurse -Force'}}
    out = subprocess.run([sys.executable, str(_SCRIPTS / "guard_destructive_delete.py")],
                         input=json.dumps(payload), capture_output=True, text=True, timeout=30)
    assert out.returncode == 0
    decision = json.loads(out.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "approval" in decision["permissionDecisionReason"]


def test_hook_is_silent_and_fails_open():
    for stdin in (json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}), "not json"):
        out = subprocess.run([sys.executable, str(_SCRIPTS / "guard_destructive_delete.py")],
                             input=stdin, capture_output=True, text=True, timeout=30)
        assert out.returncode == 0 and out.stdout.strip() == ""
