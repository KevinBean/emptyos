"""skill_scan — static risk scan of untrusted skill/app payloads (pure stdlib).

Scans a directory of code + agent instructions (a marketplace app, a
``.claude/skills/`` skill) for known-bad shapes — prompt injection in
markdown, credential exfiltration, dangerous Python calls — and rolls the
findings into a 0-100 risk score with severity bands. This is the semantic
layer the ``py_compile`` install gate can't see. Design doc + consumer map:
``.claude/rules/skill-scan.md``.

Vendored from NVIDIA SkillSpector (github.com/NVIDIA/SkillSpector,
Apache-2.0), Stage-1 static analysis, 2026-06-12: the regex pattern taxonomy
(flattened to rows), the AST behavioral rules (AST1-AST8), and the additive
scoring model. Deliberately NOT vendored: LangGraph orchestration, YARA,
OSV.dev lookups, the LLM pass — see the rule doc for why.

Pure functions, no ``self``, no kernel access, no I/O beyond ``scan_dir``'s
reads — unit-testable without a daemon (tests/test_sdk_skill_scan.py).

Usage::

    from emptyos.sdk.skill_scan import scan_dir
    report = scan_dir(Path("apps/extension/others/somebody-elses-app"))
    report.score, report.band, report.findings   # 0-100, low..critical, [ScanFinding]
"""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass
from pathlib import Path

# ── Scoring model (upstream report.py, verbatim) ─────────────────────────

SEVERITY_WEIGHTS = {"critical": 50, "high": 25, "medium": 10, "low": 5}

# (floor, band) — first floor the score meets wins.
BANDS: list[tuple[int, str]] = [(81, "critical"), (51, "high"), (21, "medium"), (0, "low")]

# Payloads shipping runnable scripts are riskier (upstream finding: 2.12x).
EXECUTABLE_MULTIPLIER = 1.3
EXECUTABLE_EXTENSIONS = {".py", ".sh", ".bash", ".zsh", ".js", ".ts", ".rb", ".go", ".rs", ".pl"}

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".pytest_cache"}

_MAX_FILE_BYTES = 512 * 1024   # skip anything bigger — not skill-shaped content
_MATCH_TRUNC = 200             # cap on stored matched text


# ── Vendored pattern rows (SkillSpector static_patterns_*, Apache-2.0) ───
# Shape: (rule_id, name, category, severity, confidence, regex).
# Upstream rule-ids preserved (P*/E*/PE*/EA*/SC*); EmptyOS-added rows use EOS*.
# Deliberately omitted: the SC1 "Unpinned Dependencies" group — upstream gates
# it to dependency files (requirements.txt/package.json) and applying it to
# arbitrary text flags every bare-word line. Re-add with a file gate if a
# consumer ever scans dependency manifests.
# Upstream nuances flattened away (acceptable for an advisory scan): per-file-
# type confidence bumps (E1), trusted-domain downgrades (SC2), and the
# documentation-example suppression (PE2/PE3/EA2).

PATTERNS: list[tuple[str, str, str, str, float, str]] = [
    # ── P1: Override Instructions (prompt-injection, HIGH) ──
    ("P1", "Override Instructions", "prompt-injection", "high", 0.8, r"ignore\s+(?:all\s+)?previous\s+instructions?"),
    ("P1", "Override Instructions", "prompt-injection", "high", 0.9, r"ignore\s+(?:all\s+)?(?:safety|security)\s+(?:rules?|constraints?|guidelines?)"),
    ("P1", "Override Instructions", "prompt-injection", "high", 0.9, r"override\s+(?:safety|security|system)"),
    ("P1", "Override Instructions", "prompt-injection", "high", 0.9, r"bypass\s+(?:safety|security|restrictions?|constraints?)"),
    ("P1", "Override Instructions", "prompt-injection", "high", 0.8, r"disregard\s+(?:all\s+)?(?:previous|safety|security)"),
    ("P1", "Override Instructions", "prompt-injection", "high", 0.8, r"forget\s+(?:all\s+)?(?:previous|your)\s+instructions?"),
    ("P1", "Override Instructions", "prompt-injection", "high", 0.95, r"you\s+are\s+now\s+(?:in\s+)?(?:jailbreak|unrestricted|unfiltered)\s+mode"),
    ("P1", "Override Instructions", "prompt-injection", "high", 0.7, r"enable\s+(?:developer|debug|admin|root)\s+mode"),
    ("P1", "Override Instructions", "prompt-injection", "high", 0.6, r"your\s+new\s+(?:instructions?|rules?)\s+are"),
    ("P1", "Override Instructions", "prompt-injection", "high", 0.9, r"do\s+not\s+follow\s+(?:any|your|the)\s+(?:rules?|guidelines?)"),
    ("P1", "Override Instructions", "prompt-injection", "high", 0.7, r"you\s+must\s+(?:always\s+)?ignore"),
    # ── P2: Hidden Instructions (prompt-injection, HIGH) ──
    ("P2", "Hidden Instructions", "prompt-injection", "high", 0.7, r"<!--.*?(?:system|instructions?|ignore|POST|GET|send|transmit).*?-->"),
    ("P2", "Hidden Instructions", "prompt-injection", "high", 0.8, r"\[//\]:\s*#\s*\(.*?(?:system|instructions?|ignore|POST|GET|send|transmit).*?\)"),
    ("P2", "Hidden Instructions", "prompt-injection", "high", 0.6, r"[\u200b\u200c\u200d\u2060\ufeff]"),
    ("P2", "Hidden Instructions", "prompt-injection", "high", 0.7, r"data:text/plain;base64,[A-Za-z0-9+/=]{50,}"),
    # ── P3: External Transmission Instructions (prompt-injection, HIGH) ──
    ("P3", "External Transmission Instructions", "prompt-injection", "high", 0.9, r"(?:send|transmit|upload|post|forward)\s+(?:the\s+)?(?:conversation|context|chat|history|messages?)\s+to"),
    ("P3", "External Transmission Instructions", "prompt-injection", "high", 0.9, r"(?:send|transmit|upload|post)\s+(?:all\s+)?(?:user\s+)?(?:data|information|input|files?)\s+to\s+(?:https?://|external)"),
    ("P3", "External Transmission Instructions", "prompt-injection", "high", 0.85, r"(?:copy|extract|collect)\s+(?:the\s+)?(?:conversation|context|session)\s+(?:and\s+)?(?:send|upload|transmit)"),
    ("P3", "External Transmission Instructions", "prompt-injection", "high", 0.8, r"(?:call|notify|ping|hit)\s+(?:this\s+)?(?:webhook|endpoint|url|api)\s+with\s+(?:the\s+)?(?:user|conversation|context)"),
    ("P3", "External Transmission Instructions", "prompt-injection", "high", 0.75, r"include\s+(?:the\s+)?(?:full\s+)?(?:conversation|context|history)\s+in\s+(?:the\s+)?(?:request|payload|body)"),
    ("P3", "External Transmission Instructions", "prompt-injection", "high", 0.9, r"(?:silently|quietly|secretly|covertly)\s+(?:send|transmit|log|record)"),
    ("P3", "External Transmission Instructions", "prompt-injection", "high", 0.9, r"without\s+(?:telling|informing|notifying)\s+(?:the\s+)?user.*?(?:send|transmit|upload)"),
    # ── P4: Subtle Steering (prompt-injection, MEDIUM) ──
    ("P4", "Subtle Steering", "prompt-injection", "medium", 0.7, r"always\s+(?:recommend|suggest|prefer|choose|use)\s+(?:\w+\s+){0,3}(?:over|instead\s+of|rather\s+than)"),
    ("P4", "Subtle Steering", "prompt-injection", "medium", 0.75, r"never\s+(?:recommend|suggest|mention|tell|warn|inform)\s+(?:the\s+)?(?:user|them)"),
    ("P4", "Subtle Steering", "prompt-injection", "medium", 0.85, r"(?:prioritize|prefer)\s+(?:\w+\s+){0,3}(?:over|above)\s+(?:user\s+)?(?:safety|security|privacy|preferences?)"),
    ("P4", "Subtle Steering", "prompt-injection", "medium", 0.8, r"(?:subtly|gradually|slowly)\s+(?:steer|guide|push|nudge)\s+(?:the\s+)?(?:user|conversation)"),
    ("P4", "Subtle Steering", "prompt-injection", "medium", 0.85, r"(?:without\s+)?(?:the\s+)?user\s+(?:knowing|realizing|noticing).*?(?:change|modify|alter|influence)"),
    ("P4", "Subtle Steering", "prompt-injection", "medium", 0.75, r"(?:gain|build|establish)\s+(?:the\s+)?(?:user(?:'s)?)\s+trust\s+(?:and\s+then|before|so\s+that)"),
    ("P4", "Subtle Steering", "prompt-injection", "medium", 0.7, r"(?:appear|seem|act)\s+(?:helpful|friendly|trustworthy)\s+(?:while|but|then)"),
    ("P4", "Subtle Steering", "prompt-injection", "medium", 0.7, r"(?:always|never)\s+include\s+(?:this|the\s+following)\s+in\s+(?:every|all)\s+(?:response|output|reply)"),
    ("P4", "Subtle Steering", "prompt-injection", "medium", 0.75, r"append\s+(?:this|the\s+following)\s+to\s+(?:every|all|each)\s+(?:response|message|output)"),
    # ── E1: External Transmission (data-exfiltration, MEDIUM) ──
    ("E1", "External Transmission", "data-exfiltration", "medium", 0.6, r"requests\s*\.\s*(?:post|put)\s*\(\s*['\"]https?://"),
    ("E1", "External Transmission", "data-exfiltration", "medium", 0.7, r"requests\s*\.\s*(?:post|put)\s*\([^)]*json\s*="),
    ("E1", "External Transmission", "data-exfiltration", "medium", 0.6, r"httpx\s*\.\s*(?:post|put)\s*\(\s*['\"]https?://"),
    ("E1", "External Transmission", "data-exfiltration", "medium", 0.6, r"urllib\s*\.\s*request\s*\.\s*urlopen\s*\([^)]*data\s*="),
    ("E1", "External Transmission", "data-exfiltration", "medium", 0.6, r"fetch\s*\(\s*['\"]https?://[^'\"]+['\"][^)]*method\s*:\s*['\"]POST['\"]"),
    ("E1", "External Transmission", "data-exfiltration", "medium", 0.6, r"curl\s+[^|]*(?:-d|--data|--data-raw|--data-binary)\s+"),
    ("E1", "External Transmission", "data-exfiltration", "medium", 0.6, r"wget\s+[^|]*--post-(?:data|file)"),
    ("E1", "External Transmission", "data-exfiltration", "medium", 0.5, r"https?://(?:api\.|data\.|collect\.|telemetry\.|analytics\.)[\w.-]+/"),
    ("E1", "External Transmission", "data-exfiltration", "medium", 0.7, r"(?:send|transmit|post|upload)\s+(?:user\s+)?(?:data|information|context|files?)\s+to\s+(?:https?://|external)"),
    # ── E2: Env Variable Harvesting (data-exfiltration, HIGH) ──
    ("E2", "Env Variable Harvesting", "data-exfiltration", "high", 0.7, r"for\s+\w+\s*,\s*\w+\s+in\s+os\.environ\.items\(\)"),
    ("E2", "Env Variable Harvesting", "data-exfiltration", "high", 0.8, r"os\.environ\s*\[\s*['\"][^'\"]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)[^'\"]*['\"]\s*\]"),
    ("E2", "Env Variable Harvesting", "data-exfiltration", "high", 0.7, r"os\.environ\.get\s*\([^)]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)"),
    ("E2", "Env Variable Harvesting", "data-exfiltration", "high", 0.6, r"os\.environ\s*\.\s*copy\s*\(\)"),
    ("E2", "Env Variable Harvesting", "data-exfiltration", "high", 0.8, r"(?:API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)\s+in\s+(?:key|name|var)"),
    ("E2", "Env Variable Harvesting", "data-exfiltration", "high", 0.7, r"process\.env\s*\[\s*['\"][^'\"]*(?:KEY|SECRET|TOKEN|PASSWORD)[^'\"]*['\"]\s*\]"),
    ("E2", "Env Variable Harvesting", "data-exfiltration", "high", 0.6, r"Object\.keys\s*\(\s*process\.env\s*\)"),
    ("E2", "Env Variable Harvesting", "data-exfiltration", "high", 0.8, r"env\s*\|\s*grep\s+(?:-i\s+)?(?:key|secret|token|password)"),
    ("E2", "Env Variable Harvesting", "data-exfiltration", "high", 0.7, r"printenv\s+(?:\w*(?:KEY|SECRET|TOKEN|PASSWORD)\w*)"),
    ("E2", "Env Variable Harvesting", "data-exfiltration", "high", 0.7, r"collect\s+(?:all\s+)?(?:environment\s+variables?|env\s+vars?)"),
    ("E2", "Env Variable Harvesting", "data-exfiltration", "high", 0.8, r"(?:extract|harvest|gather)\s+(?:api\s+)?keys?\s+from\s+environment"),
    # ── E3: File System Enumeration (data-exfiltration, MEDIUM) ──
    ("E3", "File System Enumeration", "data-exfiltration", "medium", 0.8, r"glob\s*\.\s*glob\s*\([^)]*(?:\.env|\.ssh|\.aws|\.config|credentials)"),
    ("E3", "File System Enumeration", "data-exfiltration", "medium", 0.6, r"os\s*\.\s*walk\s*\([^)]*(?:home|~|/Users|/home)"),
    ("E3", "File System Enumeration", "data-exfiltration", "medium", 0.7, r"Path\s*\.\s*home\s*\(\s*\)\s*\.\s*(?:glob|rglob)\s*\("),
    ("E3", "File System Enumeration", "data-exfiltration", "medium", 0.8, r"(?:find|search|scan|enumerate)\s+(?:for\s+)?(?:all\s+)?(?:\.env|credential|secret|key)\s+files?"),
    ("E3", "File System Enumeration", "data-exfiltration", "medium", 0.6, r"(?:list|get)\s+(?:all\s+)?files?\s+(?:in|from)\s+(?:home|~|/Users|/home)"),
    ("E3", "File System Enumeration", "data-exfiltration", "medium", 0.8, r"os\s*\.\s*listdir\s*\([^)]*(?:\.ssh|\.aws|\.config|\.gnupg)"),
    ("E3", "File System Enumeration", "data-exfiltration", "medium", 0.6, r"scandir\s*\([^)]*(?:home|~|/Users|/home)"),
    ("E3", "File System Enumeration", "data-exfiltration", "medium", 0.8, r"find\s+[~\$/]\S*\s+.*?-name\s+['\"]?\*(?:\.env|\.pem|\.key|credential)"),
    ("E3", "File System Enumeration", "data-exfiltration", "medium", 0.6, r"ls\s+-[la]*R?\s+(?:~/|/home/|/Users/)"),
    ("E3", "File System Enumeration", "data-exfiltration", "medium", 0.5, r"(?:recursively|recursive)\s+(?:scan|search|find|list)\s+(?:all\s+)?files?"),
    ("E3", "File System Enumeration", "data-exfiltration", "medium", 0.6, r"walk\s+(?:through\s+)?(?:the\s+)?(?:entire\s+)?(?:file\s*system|directory|home)"),
    # ── E4: Conversation Context Leak (data-exfiltration, HIGH) ──
    ("E4", "Conversation Context Leak", "data-exfiltration", "high", 0.85, r"(?:send|transmit|upload|log|record)\s+(?:the\s+)?(?:full\s+)?(?:conversation|chat|dialog|session)\s+(?:history|context|log)?"),
    ("E4", "Conversation Context Leak", "data-exfiltration", "high", 0.9, r"(?:export|dump|save)\s+(?:the\s+)?(?:conversation|chat|messages?)\s+to\s+(?:external|remote|server)"),
    ("E4", "Conversation Context Leak", "data-exfiltration", "high", 0.85, r"log\s+(?:all\s+)?(?:user\s+)?(?:input|messages?|queries?|prompts?)\s+to\s+(?:external|remote|server)"),
    ("E4", "Conversation Context Leak", "data-exfiltration", "high", 0.7, r"(?:store|save|record)\s+(?:every|all)\s+(?:user\s+)?(?:interaction|message|input)"),
    ("E4", "Conversation Context Leak", "data-exfiltration", "high", 0.75, r"(?:extract|capture|collect)\s+(?:the\s+)?(?:full\s+)?(?:context|conversation|thread)"),
    ("E4", "Conversation Context Leak", "data-exfiltration", "high", 0.7, r"(?:include|attach|append)\s+(?:previous\s+)?(?:messages?|context|history)\s+in\s+(?:the\s+)?(?:request|payload)"),
    ("E4", "Conversation Context Leak", "data-exfiltration", "high", 0.8, r"(?:send|upload|transmit)\s+(?:the\s+)?(?:session|memory|state)\s+(?:data\s+)?to"),
    ("E4", "Conversation Context Leak", "data-exfiltration", "high", 0.75, r"(?:copy|clone|replicate)\s+(?:the\s+)?(?:agent|assistant)\s+(?:memory|context|state)"),
    ("E4", "Conversation Context Leak", "data-exfiltration", "high", 0.8, r"(?:always\s+)?include\s+(?:the\s+)?(?:full\s+)?(?:conversation|context)\s+(?:when|in)\s+(?:calling|making)\s+(?:external|api)"),
    # ── PE1: Excessive Permissions (privilege-escalation, LOW) ──
    ("PE1", "Excessive Permissions", "privilege-escalation", "low", 0.8, r"permissions?\s*:\s*\[?\s*['\"]?\*['\"]?\s*\]?"),
    ("PE1", "Excessive Permissions", "privilege-escalation", "low", 0.7, r"(?:request|require|need)s?\s+(?:full|all|complete)\s+(?:access|permissions?)"),
    ("PE1", "Excessive Permissions", "privilege-escalation", "low", 0.75, r"(?:grant|give|allow)\s+(?:me\s+)?(?:full|all|complete)\s+(?:access|permissions?)"),
    ("PE1", "Excessive Permissions", "privilege-escalation", "low", 0.6, r"permissions?\s*:.*?(?:shell_execute|file_write|network).*?(?:shell_execute|file_write|network)"),
    ("PE1", "Excessive Permissions", "privilege-escalation", "low", 0.7, r"(?:also\s+)?(?:need|require)s?\s+(?:write|execute|admin)\s+(?:access|permissions?)\s+(?:for|to)\s+(?:read|view|display)"),
    ("PE1", "Excessive Permissions", "privilege-escalation", "low", 0.8, r"(?:escalate|elevate|upgrade)\s+(?:my\s+)?(?:permissions?|privileges?|access)"),
    ("PE1", "Excessive Permissions", "privilege-escalation", "low", 0.85, r"(?:bypass|skip|ignore)\s+(?:permission|access)\s+(?:check|validation|restriction)"),
    # ── PE2: Sudo/Root Invocation (privilege-escalation, MEDIUM) ──
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.7, r"sudo\s+(?!-v|-l|--version|--list)"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.8, r"sudo\s+-[isSE]"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.9, r"sudo\s+su\b"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.8, r"(?:run|execute)\s+(?:as|with)\s+root"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.6, r"(?:requires?|needs?)\s+root\s+(?:access|privileges?|permissions?)"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.8, r"su\s+-\s*$|su\s+root"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.7, r"doas\s+"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.75, r"pkexec\s+"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.85, r"chmod\s+[ugo]*[+-=]*s"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.8, r"chmod\s+[0-7]*[4567][0-7]{2}"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.6, r"(?:edit|modify|write|change)\s+(?:/etc/|system)\s+(?:files?|config)"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.7, r"(?:run|execute)\s+(?:this|the)\s+(?:script|command)\s+(?:as|with)\s+(?:sudo|root|admin)"),
    ("PE2", "Sudo/Root Invocation", "privilege-escalation", "medium", 0.5, r"(?:you\s+)?(?:will\s+)?need\s+(?:to\s+)?(?:use\s+)?sudo"),
    # ── PE3: Credential File Access (privilege-escalation, HIGH) ──
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.9, r"~?/?\.ssh/(?:id_rsa|id_ed25519|id_ecdsa|id_dsa|authorized_keys|known_hosts)"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.9, r"(?:home|HOME)/\w+/\.ssh/"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.9, r"Path\s*\.\s*home\s*\(\s*\)\s*/\s*['\"]\.ssh['\"]"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.9, r"~?/?\.aws/credentials"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.7, r"~?/?\.aws/config"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.9, r"(?:home|HOME)/\w+/\.aws/"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.8, r"~?/?\.config/gcloud/"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.8, r"application_default_credentials\.json"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.7, r"~?/?\.azure/"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.8, r"accessTokens\.json"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.8, r"~?/?\.kube/config"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.6, r"kubeconfig"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.7, r"~?/?\.docker/config\.json"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.7, r"~?/?\.npmrc"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.9, r"~?/?\.git-credentials"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.8, r"~?/?\.netrc"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.6, r"/etc/passwd"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.95, r"/etc/shadow"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.7, r"(?:password|credentials?|secrets?)\.(?:txt|json|yaml|yml|env)"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.8, r"(?:access_token|refresh_token|bearer_token|api_token)\.txt"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.6, r"\.env(?:\.local|\.production|\.development)?(?:\s|$|['\"])"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.7, r"(?:keychain|keyring|gnome-keyring)"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.8, r"(?:Chrome|Firefox|Safari)/.*?(?:Cookies|Login Data|key4\.db)"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.8, r"read\s+(?:the\s+)?(?:ssh|private)\s+key"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.7, r"access\s+(?:the\s+)?(?:credentials?|secrets?|tokens?)"),
    ("PE3", "Credential File Access", "privilege-escalation", "high", 0.7, r"(?:extract|copy|get)\s+(?:api\s+)?keys?\s+from"),
    # ── EA1: Unrestricted Tool Access (excessive-agency, MEDIUM) ──
    ("EA1", "Unrestricted Tool Access", "excessive-agency", "medium", 0.85, r"(?:tools?|permissions?)\s*:\s*\[?\s*['\"]?\*['\"]?\s*\]?"),
    ("EA1", "Unrestricted Tool Access", "excessive-agency", "medium", 0.8, r"(?:allow|grant|enable)\s+(?:access\s+to\s+)?(?:all|any|every)\s+tools?"),
    ("EA1", "Unrestricted Tool Access", "excessive-agency", "medium", 0.75, r"(?:no|without)\s+(?:tool|permission|access|capability)\s+(?:restrictions?|constraints?|limitations?)"),
    ("EA1", "Unrestricted Tool Access", "excessive-agency", "medium", 0.8, r"(?:call|invoke|use|execute)\s+(?:any|all|every)\s+(?:available\s+)?tools?"),
    ("EA1", "Unrestricted Tool Access", "excessive-agency", "medium", 0.85, r"(?:unrestricted|unlimited|unconstrained)\s+(?:tool|function|api)\s+(?:access|use|calls?)"),
    ("EA1", "Unrestricted Tool Access", "excessive-agency", "medium", 0.75, r"(?:can|may|should)\s+(?:freely|always)\s+(?:use|call|invoke)\s+(?:any|all)\s+(?:tools?|functions?|apis?)"),
    ("EA1", "Unrestricted Tool Access", "excessive-agency", "medium", 0.7, r"tools?\s*:\s*\[\s*['\"]shell['\"].*?['\"](?:file_write|network|http)['\"]"),
    ("EA1", "Unrestricted Tool Access", "excessive-agency", "medium", 0.85, r"(?:grant|give)\s+(?:full|complete|total)\s+(?:tool|function|api)\s+access"),
    ("EA1", "Unrestricted Tool Access", "excessive-agency", "medium", 0.8, r"(?:execute|run)\s+(?:arbitrary|any)\s+(?:commands?|code|scripts?)"),
    ("EA1", "Unrestricted Tool Access", "excessive-agency", "medium", 0.7, r"(?:no\s+)?(?:tool\s+)?(?:allow|block|deny)\s*(?:list|listing)\s*(?:is\s+)?(?:empty|disabled|off)"),
    # ── EA2: Autonomous Decision Making (excessive-agency, MEDIUM) ──
    ("EA2", "Autonomous Decision Making", "excessive-agency", "medium", 0.75, r"(?:without|no)\s+(?:asking|confirmation|approval|consent|verification|checking)"),
    ("EA2", "Autonomous Decision Making", "excessive-agency", "medium", 0.8, r"(?:auto(?:matically)?|autonomously)\s+(?:execute|run|perform|delete|remove|modify|send|deploy)"),
    ("EA2", "Autonomous Decision Making", "excessive-agency", "medium", 0.85, r"(?:skip|bypass|disable)\s+(?:user\s+)?(?:confirmation|approval|consent|verification|prompt)"),
    ("EA2", "Autonomous Decision Making", "excessive-agency", "medium", 0.8, r"(?:do\s+not|don't|never)\s+(?:ask|prompt|confirm|verify|check)\s+(?:the\s+)?(?:user|before)"),
    ("EA2", "Autonomous Decision Making", "excessive-agency", "medium", 0.85, r"(?:auto[_-]?approve|auto[_-]?confirm|auto[_-]?execute|auto[_-]?deploy)"),
    ("EA2", "Autonomous Decision Making", "excessive-agency", "medium", 0.8, r"(?:proceed|continue|execute)\s+without\s+(?:waiting|asking)\s+(?:for\s+)?(?:user|human|permission)"),
    ("EA2", "Autonomous Decision Making", "excessive-agency", "medium", 0.7, r"(?:assume|presume)\s+(?:user\s+)?(?:consent|approval|permission)"),
    ("EA2", "Autonomous Decision Making", "excessive-agency", "medium", 0.75, r"(?:if|when)\s+(?:unsure|uncertain).*?(?:proceed|continue|execute)\s+(?:anyway|regardless)"),
    ("EA2", "Autonomous Decision Making", "excessive-agency", "medium", 0.65, r"(?:--auto-?approve|--no-?confirm)\b"),
    # ── EA3: Scope Creep (excessive-agency, LOW) ──
    ("EA3", "Scope Creep", "excessive-agency", "low", 0.65, r"(?:also|additionally|furthermore)\s+(?:perform|execute|run|do|handle|manage)\s+(?:any|all|other)"),
    ("EA3", "Scope Creep", "excessive-agency", "low", 0.7, r"(?:while\s+you(?:'re|\s+are)\s+at\s+it|in\s+addition|on\s+top\s+of\s+that)\s*[,.]?\s*(?:also\s+)?(?:do|perform|execute|run)"),
    ("EA3", "Scope Creep", "excessive-agency", "low", 0.75, r"(?:extend|expand|broaden)\s+(?:your|the\s+)?(?:scope|functionality|capabilities|responsibilities)"),
    ("EA3", "Scope Creep", "excessive-agency", "low", 0.7, r"(?:not\s+limited\s+to|beyond\s+(?:the\s+)?(?:scope|stated|described|documented))"),
    ("EA3", "Scope Creep", "excessive-agency", "low", 0.75, r"(?:take\s+over|assume\s+control\s+of|manage)\s+(?:all|any|every)\s+(?:aspect|part|area)"),
    ("EA3", "Scope Creep", "excessive-agency", "low", 0.7, r"(?:you\s+(?:can|should|must)\s+)?(?:handle|manage)\s+(?:everything|anything|all\s+tasks?)"),
    ("EA3", "Scope Creep", "excessive-agency", "low", 0.65, r"(?:act\s+as|become|serve\s+as)\s+(?:a\s+)?(?:general[- ]purpose|universal|all[- ]in[- ]one|omniscient)"),
    ("EA3", "Scope Creep", "excessive-agency", "low", 0.7, r"(?:you\s+are\s+)?(?:responsible\s+for|in\s+charge\s+of)\s+(?:everything|all\s+(?:systems?|operations?|tasks?))"),
    # ── EA4: Unbounded Resource Access (excessive-agency, MEDIUM) ──
    ("EA4", "Unbounded Resource Access", "excessive-agency", "medium", 0.8, r"(?:unlimited|infinite|unbounded|no\s+limit(?:s)?(?:\s+on)?)\s+(?:api\s+)?(?:calls?|requests?|queries?|invocations?)"),
    ("EA4", "Unbounded Resource Access", "excessive-agency", "medium", 0.7, r"(?:no|without)\s+(?:rate\s+)?limit(?:s|ing)?\s+(?:on|for|when)\s+(?:api|tool|request|query)"),
    ("EA4", "Unbounded Resource Access", "excessive-agency", "medium", 0.7, r"(?:no|without)\s+(?:timeout|budget|quota|cap|ceiling)\s+(?:on|for|when)\s+(?:api|tool|request|execution)"),
    ("EA4", "Unbounded Resource Access", "excessive-agency", "medium", 0.75, r"(?:loop|iterate|repeat)\s+(?:indefinitely|forever|infinitely|endlessly)"),
    ("EA4", "Unbounded Resource Access", "excessive-agency", "medium", 0.75, r"(?:retry|attempt)\s+(?:indefinitely|forever|without\s+limit|unlimited\s+times)"),
    ("EA4", "Unbounded Resource Access", "excessive-agency", "medium", 0.8, r"max[_-]?retries?\s*=\s*(?:None|0|float\s*\(\s*['\"]inf['\"]|math\.inf|infinity)"),
    ("EA4", "Unbounded Resource Access", "excessive-agency", "medium", 0.75, r"timeout\s*=\s*(?:None|0|float\s*\(\s*['\"]inf['\"]|math\.inf)"),
    ("EA4", "Unbounded Resource Access", "excessive-agency", "medium", 0.8, r"(?:allocate|consume|use)\s+(?:as\s+much|unlimited|unbounded)\s+(?:memory|storage|disk|compute|cpu|gpu)"),
    ("EA4", "Unbounded Resource Access", "excessive-agency", "medium", 0.7, r"(?:no|without)\s+(?:resource\s+)?(?:constraints?|limits?|quotas?|budgets?)\s+(?:on|for|when)\s+(?:api|tool|execution|request|compute)"),
    # ── SC2: Remote Code Execution (supply-chain, HIGH) ──
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.9, r"curl\s+[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh"),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.9, r"wget\s+[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh"),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.9, r"curl\s+[^|]*\|\s*(?:sudo\s+)?(?:python|python3|node|ruby|perl)"),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.9, r"wget\s+[^|]*\|\s*(?:sudo\s+)?(?:python|python3|node|ruby|perl)"),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.8, r"curl\s+[^&]*-o\s+\S+\s*&&\s*(?:sudo\s+)?(?:ba)?sh"),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.8, r"wget\s+[^&]*-O\s+\S+\s*&&\s*(?:sudo\s+)?(?:ba)?sh"),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.95, r"exec\s*\(\s*(?:urllib|requests|httpx)\.[^)]+\.(?:read|text|content)"),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.95, r"eval\s*\(\s*(?:urllib|requests|httpx)\.[^)]+\.(?:read|text|content)"),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.9, r"eval\s*\(\s*(?:await\s+)?fetch\s*\("),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.9, r"new\s+Function\s*\([^)]*fetch\s*\("),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.8, r"subprocess\.[^(]+\([^)]*(?:curl|wget)\s+https?://"),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.7, r"download\s+and\s+(?:run|execute)\s+(?:the\s+)?script"),
    ("SC2", "Remote Code Execution", "supply-chain", "high", 0.6, r"run\s+(?:this|the)\s+(?:following\s+)?(?:curl|wget)\s+command"),
    # ── SC3: Obfuscated Code (supply-chain, HIGH) ──
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.95, r"exec\s*\(\s*(?:base64\.)?b64decode\s*\("),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.95, r"eval\s*\(\s*(?:base64\.)?b64decode\s*\("),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.95, r"exec\s*\(\s*codecs\.decode\s*\([^)]*['\"]hex['\"]\s*\)"),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.9, r"marshal\.loads\s*\("),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.95, r"exec\s*\(\s*marshal\.loads\s*\("),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.9, r"exec\s*\(\s*compile\s*\([^)]*base64"),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.9, r"exec\s*\(\s*bytes\.fromhex\s*\("),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.9, r"exec\s*\(\s*bytearray\.fromhex\s*\("),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.9, r"exec\s*\(\s*(?:zlib|gzip)\.decompress\s*\("),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.9, r"eval\s*\(\s*atob\s*\("),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.9, r"new\s+Function\s*\(\s*atob\s*\("),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.8, r"_0x[a-f0-9]{4,}\s*\("),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.6, r"['\"][A-Fa-f0-9]{200,}['\"]"),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.5, r"['\"][A-Za-z0-9+/=]{200,}['\"]"),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.9, r"\(lambda\s+_:\s*exec\s*\("),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.85, r"__import__\s*\(['\"]os['\"]\s*\)\.system"),
    ("SC3", "Obfuscated Code", "supply-chain", "high", 0.8, r"decode\s+(?:this|the)\s+(?:base64|hex)\s+(?:and\s+)?(?:run|execute)"),
]


# ── Vendored AST rules (SkillSpector behavioral_ast.py, Apache-2.0) ──────
# These name-strings are DETECTION SIGNATURES the scanner looks for in
# untrusted code via ast.parse — nothing here ever executes them.

AST_DANGEROUS_BUILTINS = frozenset({"exec", "eval", "compile", "__import__"})
AST_SUBPROCESS_CALLS = frozenset({"call", "run", "Popen", "check_call", "check_output", "getoutput", "getstatusoutput"})
AST_OS_EXEC_CALLS = frozenset({
    "system", "popen", "execl", "execle", "execlp", "execlpe", "execv", "execve",
    "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv",
    "spawnve", "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp",
})

# rule_id -> (name, severity, confidence) — upstream _RULE_* tables verbatim.
AST_RULES: dict[str, tuple[str, str, float]] = {
    "AST1": ("exec() call detected", "high", 0.85),
    "AST2": ("eval() call detected", "high", 0.85),
    "AST3": ("Dynamic import via __import__()", "medium", 0.75),
    "AST4": ("subprocess module call", "medium", 0.70),
    "AST5": ("os.system() or os exec-family call", "high", 0.85),
    "AST6": ("compile() call detected", "medium", 0.65),
    "AST7": ("Dynamic attribute access via getattr()", "low", 0.50),
    "AST8": ("Dangerous execution chain", "critical", 0.95),
}

# AST8 chain sources: module names whose presence inside an exec()/eval()
# argument marks a near-certain malicious chain (upstream list).
_CHAIN_SOURCE_SUBSTRINGS = ("base64", "codecs", "marshal", "urllib", "requests", "httpx")


# ── Findings + report ────────────────────────────────────────────────────

@dataclass
class ScanFinding:
    rule_id: str
    name: str
    category: str
    severity: str       # critical | high | medium | low
    confidence: float   # 0-1, pattern-level prior; separate axis from the score
    file: str           # path relative to the scanned root
    line: int           # 1-based
    matched: str        # truncated matched text / call repr

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanReport:
    score: int
    band: str
    findings: list[ScanFinding]
    files_scanned: int
    has_executables: bool

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "band": self.band,
            "findings": [f.to_dict() for f in self.findings],
            "files_scanned": self.files_scanned,
            "has_executables": self.has_executables,
        }


# ── Regex pass ───────────────────────────────────────────────────────────

_compiled_cache: list[tuple[str, str, str, str, float, re.Pattern]] | None = None


def _compiled() -> list[tuple[str, str, str, str, float, re.Pattern]]:
    global _compiled_cache
    if _compiled_cache is None:
        _compiled_cache = [
            (rid, name, cat, sev, conf, re.compile(rx, re.IGNORECASE))
            for rid, name, cat, sev, conf, rx in PATTERNS
        ]
    return _compiled_cache


# SC2 fires on `curl ... | python`; when the fetch target is the local daemon
# (every EmptyOS skill probes :9000 this way) that's not *remote* code
# execution — restore upstream's trusted-domain downgrade (low / 0.15) for
# loopback hosts. Downgrade, never drop: the finding stays visible.
_LOOPBACK_RX = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])", re.IGNORECASE)


def scan_text(content: str, file_path: str) -> list[ScanFinding]:
    """Regex pattern pass over one file's text. Dedupes per (rule, line)."""
    findings: list[ScanFinding] = []
    seen: set[tuple[str, int]] = set()
    for rid, name, cat, sev, conf, rx in _compiled():
        for m in rx.finditer(content):
            line = content.count("\n", 0, m.start()) + 1
            key = (rid, line)
            if key in seen:
                continue
            seen.add(key)
            if rid == "SC2" and _LOOPBACK_RX.search(m.group(0)):
                sev_eff, conf_eff = "low", 0.15
            else:
                sev_eff, conf_eff = sev, conf
            findings.append(ScanFinding(
                rule_id=rid, name=name, category=cat, severity=sev_eff,
                confidence=conf_eff, file=file_path, line=line,
                matched=m.group(0)[:_MATCH_TRUNC],
            ))
    return findings


# ── AST pass ─────────────────────────────────────────────────────────────

def _call_name(func: ast.expr) -> tuple[str, str]:
    """Resolve a call's (base, attr) — ('', 'exec') for bare names,
    ('subprocess', 'run') for one-level attribute calls."""
    if isinstance(func, ast.Name):
        return "", func.id
    if isinstance(func, ast.Attribute):
        base = func.value.id if isinstance(func.value, ast.Name) else ""
        return base, func.attr
    return "", ""


def _dangerous_rule(node: ast.Call) -> str | None:
    """Map a Call node to its AST rule id, or None."""
    base, name = _call_name(node.func)
    if not base and name in AST_DANGEROUS_BUILTINS:
        return {"exec": "AST1", "eval": "AST2", "__import__": "AST3", "compile": "AST6"}[name]
    if base == "subprocess" and name in AST_SUBPROCESS_CALLS:
        return "AST4"
    if base == "os" and name in AST_OS_EXEC_CALLS:
        return "AST5"
    return None


def _dotted_name(func: ast.expr) -> str:
    """Resolve a call target to its dotted name ('base64.b64decode'), '' if dynamic."""
    parts: list[str] = []
    node = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _contains_dangerous_call(node: ast.Call) -> bool:
    """True when an exec()/eval() argument nests a chain-source call (AST8).

    Upstream sources: compile/__import__, subprocess.*/os.*, and decode/network
    modules (base64, codecs, marshal, urllib, requests, httpx). We additionally
    treat a nested exec/eval itself as a chain source — exec(eval(x)) is the
    same shape.
    """
    for arg in list(node.args) + [kw.value for kw in node.keywords]:
        for sub in ast.walk(arg):
            if not isinstance(sub, ast.Call):
                continue
            name = _dotted_name(sub.func)
            if not name:
                continue
            if name in ("compile", "__import__", "exec", "eval"):
                return True
            if name.startswith("subprocess.") or name.startswith("os."):
                return True
            if any(s in name for s in _CHAIN_SOURCE_SUBSTRINGS):
                return True
    return False


def scan_python_ast(content: str, file_path: str) -> list[ScanFinding]:
    """AST behavioral pass over one Python file. Syntax errors return [] —
    broken Python is the py_compile gate's finding, not ours."""
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return []
    findings: list[ScanFinding] = []

    def _emit(rule_id: str, node: ast.AST, detail: str) -> None:
        name, sev, conf = AST_RULES[rule_id]
        findings.append(ScanFinding(
            rule_id=rule_id, name=name, category="behavioral", severity=sev,
            confidence=conf, file=file_path, line=getattr(node, "lineno", 0),
            matched=detail[:_MATCH_TRUNC],
        ))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        rule = _dangerous_rule(node)
        if rule:
            base, name = _call_name(node.func)
            _emit(rule, node, f"{base + '.' if base else ''}{name}(...)")
            # AST8: exec()/eval() wrapping a decode/network/exec call —
            # e.g. exec(base64.b64decode(...)) — is a near-certain malicious
            # chain (upstream restricts the outer call to exec/eval).
            if name in ("exec", "eval") and not base and _contains_dangerous_call(node):
                _emit("AST8", node, f"{name}(<nested chain-source call>)")
        else:
            # AST7: getattr(obj, <non-constant>) — dynamic attribute dispatch.
            _, nm = _call_name(node.func)
            if nm == "getattr" and len(node.args) >= 2 and not isinstance(node.args[1], ast.Constant):
                _emit("AST7", node, "getattr(obj, <dynamic>)")
    return findings


# ── Scoring ──────────────────────────────────────────────────────────────

def score_findings(findings: list[ScanFinding], has_executables: bool) -> tuple[int, str]:
    """Additive severity-weighted score, executable multiplier, 0-100 clamp.

    Finding-count driven by design — an honest triage signal ("needs a human
    look"), NOT a calibrated probability. See the rule doc before treating
    the number as anything but sort order + band.
    """
    score = sum(SEVERITY_WEIGHTS.get(f.severity, SEVERITY_WEIGHTS["low"]) for f in findings)
    if has_executables:
        score = int(score * EXECUTABLE_MULTIPLIER)
    score = min(100, max(0, score))
    band = next(b for floor, b in BANDS if score >= floor)
    return score, band


# ── Directory walk ───────────────────────────────────────────────────────

def _iter_scan_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        # skip hidden files/dirs except the .claude* family (skills ship them)
        if any(part.startswith(".") and not part.startswith(".claude") for part in rel_parts):
            continue
        files.append(p)
    return files


def scan_dir(root: Path | str, max_file_bytes: int = _MAX_FILE_BYTES) -> ScanReport:
    """Scan every text file under ``root``; return the rolled-up report.

    Binary files (NUL in the first KB) and oversized files are skipped, not
    errors. Missing/empty dirs return a clean low-band report.
    """
    root = Path(root)
    findings: list[ScanFinding] = []
    scanned = 0
    has_exec = False
    if root.is_dir():
        for p in _iter_scan_files(root):
            if p.suffix.lower() in EXECUTABLE_EXTENSIONS:
                has_exec = True
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            if len(raw) > max_file_bytes or b"\x00" in raw[:1024]:
                continue
            text = raw.decode("utf-8", errors="replace")
            rel = str(p.relative_to(root)).replace("\\", "/")
            scanned += 1
            findings.extend(scan_text(text, rel))
            if p.suffix.lower() == ".py":
                findings.extend(scan_python_ast(text, rel))
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (sev_rank.get(f.severity, 9), -f.confidence, f.file, f.line))
    score, band = score_findings(findings, has_exec)
    return ScanReport(score=score, band=band, findings=findings,
                      files_scanned=scanned, has_executables=has_exec)
