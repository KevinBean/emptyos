"""Both-direction pins for the SECRET class in scripts/check-personal.py.

Per .claude/skills/eos-graduate-audit: a checker ships with tests pinning BOTH
"fires on the real regression" and "silent on healthy code".

The gap this guards (found 2026-08-14, reviewing fujitoid/key-amnesia): the
commit gate loaded `.eos-personal` only — identity / path / coordinate patterns
and **zero credential shapes** — so an `sk-ant-...` in a tracked file passed
`check-personal.py` cleanly, while `eos-security-review` and `eos-release`
already advertised "API keys, tokens in tracked files" as covered.

Two behaviours here are load-bearing and easy to regress:

  - an ALLOWLIST file is still scanned for secrets. The allowlist exists to let
    a file hold *personal* patterns; extending it to credentials would exempt
    `.claude/settings.local.json` and `data/personal-defaults.json` — the two
    files most likely to hold a genuine token.
  - findings carry a redacted preview, never the matched text. A leak report
    that echoes the key is a second leak (CI logs are durable).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
# Running the script directly puts `scripts/` on sys.path implicitly (script
# dir); loading it by spec does not, and it imports its sibling `check_base`.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SCRIPT = SCRIPTS_DIR / "check-personal.py"
_spec = importlib.util.spec_from_file_location("check_personal", SCRIPT)
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)

# Synthetic, never-real credentials. Each is secret-*shaped* so the patterns
# match; none has ever been issued.
FAKE_ANTHROPIC = "sk-ant-" + "A" * 40  # check-secrets: ignore
FAKE_AWS = "AKIA" + "B" * 16  # check-secrets: ignore
FAKE_GITHUB = "ghp_" + "c" * 36  # check-secrets: ignore
PEM_HEADER = "-----BEGIN RSA PRIVATE KEY-----"

PERSONAL = [re.compile(r"(?i)\bAcmeCorp\b")]


def _scan(content: str, *, path: str = "some/file.py", allowed: bool = False):
    return cp.scan_content(path, content, PERSONAL, allowed=allowed)


class TestFiresOnRealLeak:
    def test_anthropic_key(self):
        _, secrets = _scan(f'KEY = "{FAKE_ANTHROPIC}"\n')
        assert [s[2] for s in secrets] == ["Anthropic API key"]
        assert secrets[0][1] == 1

    def test_aws_and_github_and_pem(self):
        _, secrets = _scan(f"a={FAKE_AWS}\nb={FAKE_GITHUB}\n{PEM_HEADER}\n")
        assert {s[2] for s in secrets} == {
            "AWS access key",
            "GitHub token",
            "Private key block",
        }

    def test_allowlisted_file_is_still_secret_scanned(self):
        """The hole this closes: personal-exempt must not mean secret-exempt."""
        violations, secrets = _scan(
            f'# AcmeCorp\nKEY = "{FAKE_ANTHROPIC}"\n', allowed=True
        )
        assert violations == [], "allowlist should mute the personal class"
        assert len(secrets) == 1, "allowlist must NOT mute the secret class"

    def test_reports_redacted_preview_never_the_value(self):
        _, secrets = _scan(f'KEY = "{FAKE_ANTHROPIC}"\n')
        preview = secrets[0][3]
        assert FAKE_ANTHROPIC not in preview
        assert "*" in preview
        assert preview.startswith(FAKE_ANTHROPIC[:4])


class TestSilentOnHealthyCode:
    def test_ordinary_source(self):
        _, secrets = _scan(
            "def add(a, b):\n"
            '    """Return the sum."""\n'
            "    return a + b\n"
        )
        assert secrets == []

    def test_env_var_reference_is_not_a_secret(self):
        """The whole point of `api_key_env` — a NAME is not a VALUE."""
        _, secrets = _scan(
            'api_key_env = "OPENAI_API_KEY"\n'
            'token = os.environ["ANTHROPIC_API_KEY"]\n'
        )
        assert secrets == []

    def test_pattern_definitions_do_not_match_themselves(self):
        """outbound_scan.py holds these regexes as source and must stay clean."""
        src = (
            Path(__file__).resolve().parent.parent
            / "emptyos"
            / "capabilities"
            / "outbound_scan.py"
        ).read_text(encoding="utf-8")
        _, secrets = _scan(src, path="emptyos/capabilities/outbound_scan.py")
        assert secrets == []


class TestInlineIgnoreMarker:
    def test_marker_on_the_same_line(self):
        _, secrets = _scan(f'KEY = "{FAKE_ANTHROPIC}"  # {cp.SECRET_IGNORE_MARKER}\n')
        assert secrets == []

    def test_marker_on_the_line_above(self):
        _, secrets = _scan(
            f"# {cp.SECRET_IGNORE_MARKER} — fixture\nKEY = \"{FAKE_ANTHROPIC}\"\n"
        )
        assert secrets == []

    def test_marker_two_lines_above_does_not_reach(self):
        """Scoped to one line of reach, so a marker can't blanket a whole file."""
        _, secrets = _scan(
            f"# {cp.SECRET_IGNORE_MARKER}\n"
            "filler = 1\n"
            f'KEY = "{FAKE_ANTHROPIC}"\n'
        )
        assert len(secrets) == 1

    def test_marker_does_not_mute_the_personal_class(self):
        violations, _ = _scan(f"# AcmeCorp  {cp.SECRET_IGNORE_MARKER}\n")
        assert len(violations) == 1


class TestSecretGateSurvivesMissingPersonalFile:
    """The two classes must fail independently.

    `main()` used to `sys.exit(0)` whenever `.eos-personal` produced no
    patterns — harmless while that file was the only source, fail-open the
    moment secrets joined. Not hypothetical: docs/PRIVACY.md's "known open gap"
    proposes dropping `.eos-personal` from the public snapshot, which under the
    old early-exit would have disabled credential scanning in exactly the tree
    being published.

    Drives `main()` itself — a `scan_content` test cannot catch a re-introduced
    early exit, because the exit is in `main()`. Runs in-process rather than as
    a subprocess because `git_tracked()` resolves from the script's own repo
    root, so a temp cwd would just re-scan EmptyOS.
    """

    def _main(self, tmp_path: Path, monkeypatch, *, with_patterns_file: bool):
        leak = tmp_path / "leak.py"
        leak.write_text(f'KEY = "{FAKE_ANTHROPIC}"\n', encoding="utf-8")

        patterns_file = tmp_path / ".eos-personal"
        if with_patterns_file:
            patterns_file.write_text(r"(?i)\bAcmeCorp\b" + "\n", encoding="utf-8")

        monkeypatch.setattr(cp, "PATTERNS_FILE", str(patterns_file))
        monkeypatch.setattr(cp, "get_files", lambda *a, **k: [str(leak)])
        monkeypatch.setattr(sys, "argv", ["check-personal.py"])

        code = 0
        try:
            cp.main()
        except SystemExit as e:
            code = e.code
        return code

    def test_secret_caught_without_patterns_file(self, tmp_path, monkeypatch, capsys):
        code = self._main(tmp_path, monkeypatch, with_patterns_file=False)
        out = capsys.readouterr().out
        assert code == 1, (
            "missing .eos-personal must not disable the secret gate:\n" + out
        )
        assert "SECRET DETECTED" in out

    def test_secret_caught_with_patterns_file(self, tmp_path, monkeypatch, capsys):
        code = self._main(tmp_path, monkeypatch, with_patterns_file=True)
        assert code == 1
        assert "SECRET DETECTED" in capsys.readouterr().out

    def test_reported_value_is_redacted_end_to_end(self, tmp_path, monkeypatch, capsys):
        """The reporting path, not just the helper, must not echo the match."""
        self._main(tmp_path, monkeypatch, with_patterns_file=True)
        assert FAKE_ANTHROPIC not in capsys.readouterr().out


class TestPersonalClassUnchanged:
    def test_personal_still_detected(self):
        violations, secrets = _scan("client = AcmeCorp\n")
        assert len(violations) == 1
        assert secrets == []

    def test_allowlist_still_mutes_personal(self):
        violations, _ = _scan("client = AcmeCorp\n", allowed=True)
        assert violations == []
