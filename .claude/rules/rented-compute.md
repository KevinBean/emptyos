---
paths:
  - "emptyos/capabilities/**"
  - "plugins/**"
  - "scripts/check_provider_trust.py"
---
# Rented Compute Without Compromising Privacy

Written 2026-07-25 after the RTX 5090 analysis concluded "rent, don't buy" for MV
rendering (~5.4 GPU-hours/year). **Shipped:** `Provider.trust`, ComfyUI provider
host/trust exposure, and `scripts/check_provider_trust.py` (preflight, `always` +
`security`, advisory).

## The problem this exists to prevent

EmptyOS classifies providers as local-or-cloud by **inspecting the host address**
(`Provider.is_cloud` → `emptyos/capabilities/consent.py::host_is_local`).
That works for the two cases it was designed for — your own machine, or a public
API endpoint.

It breaks on the third case. `host_is_local()` returns **True** for:

- private IPv4 ranges (10/8, 172.16/12, 192.168/16)
- **CGNAT 100.64.0.0/10 — Tailscale's tailnet range**
- **`*.ts.net`, `*.tailscale.net`**

So the convenient way to reach a rented GPU — join it to your tailnet and point a
plugin at its MagicDNS name — makes it **look local to the consent gate**. Cloud
consent never fires. Rule 19 ("no vault data to cloud") is never consulted. A
third party's machine is treated as your own because of its network address.

**This is not a hypothetical.** `[plugins.comfyui] host` is already config-driven,
so renting is a one-line config change today — and the natural one silently
defeats the gate.

**A second, worse gap found while implementing this:** `ComfyUIDrawProvider` and
`ComfyUIAnimateProvider` never set `host` at all, so `is_cloud` evaluated
`bool("") and …` → **False unconditionally**. Pointing ComfyUI at a *public*
rental URL would still have classified as local — no tunnel required. Both
providers now expose `host` and `trust` as properties (so they track a config
change rather than freezing at boot). Pinned by
`tests/test_unit_provider_trust.py`.

**Lesson that generalises:** a provider that omits `host` doesn't get "unknown"
treatment, it gets "local" treatment. When adding a Provider that reaches a
network service, populate `host` — or the safest-looking omission is the least
safe outcome.

## The correction: three trust domains, declared not inferred

Two categories can't express this. There are three:

| domain | you control | example | host may read your data? |
|---|---|---|---|
| **owned** | hardware + software | this desktop, the MacBook | no |
| **rented** | software only | vast.ai / RunPod GPU box | **yes — RAM, disk, snapshots** |
| **service** | neither | OpenAI, Kling, ollama-cloud | yes, under their policy |

The rule: **trust is declared for anything not on hardware you own. Address-based
inference is a fallback, never an authority.**

```toml
[plugins.comfyui]
host = "http://gpu-rental.ts.net:8188"
trust = "rented"          # REQUIRED — without it this reads as local
```

`Provider.is_cloud` already documents an override hook ("Override `is_cloud` to
force a classification"), so the change is small: when `trust` is declared,
`is_cloud = trust != "owned"`; when absent, fall back to `host_is_local()`.

## What may cross each boundary

Privacy is about **what data moves**, not where compute happens. Classify the
payload, then route.

| payload | owned | rented | service |
|---|---|---|---|
| Vault notes, raw | ✅ | ❌ **never** | ❌ **never** (Rule 19) |
| Prompts derived from vault content | ✅ | ⚠️ consent | ⚠️ consent |
| Generated stills / intermediate art | ✅ | ✅ | ✅ |
| Content already destined for publication (a song's audio, lyrics of a track being released) | ✅ | ✅ | ⚠️ per-service terms |
| Credentials, `emptyos.toml`, API keys, auth tokens | ✅ | ❌ **never** | ❌ **never** |
| Licensed model weights | ✅ | ⚠️ check licence permits third-party hosting | n/a |

**Why MV rendering is the safe case, specifically:** its inputs are generated
stills and scene prompts derived from the lyrics of a song you are about to
publish. The material is *already destined to be public*. That is what makes
renting appropriate for this workload — it is not a general permission, and it
does not generalise to "rented compute is fine."

**The inverse, stated plainly:** an LLM answering questions over your vault is the
one workload that must never leave owned hardware — which is exactly why "rent a
GPU for privacy" is incoherent. Renting for privacy means running your private
data on a stranger's machine.

## Operational contract for a rented box

Assume the host can read RAM and disk. Design so that costs you nothing.

1. **Ephemeral.** Created for a job, destroyed after. Never long-lived, never a
   standing member of your infrastructure.
2. **Nothing personal on it, ever.** No vault mount, no `emptyos.toml`, no API
   key, no auth token, no SSH key you use elsewhere. Generate a throwaway
   credential per box.
3. **Push inputs, pull outputs, destroy.** The box gets exactly the job payload —
   not a sync, not a clone.
4. **Isolate the network path.** Prefer a dedicated tunnel with its own
   credential. If you do put it on the tailnet, it must carry `trust = "rented"`
   *and* an ACL tag — the tailnet is otherwise a trusted zone and this machine
   is not.
5. **Verify destruction.** A "stopped" instance is not a destroyed one; storage
   often persists and is billable *and* readable.
6. **Check the model licence.** Klein 9B is FLUX Non-Commercial — fine for
   personal work, but pushing weights to a third-party host is a distribution
   question worth reading the terms on.

## Implementation — deliberately small

**v1 is config plus a declaration. No new subsystem.**

| piece | status |
|---|---|
| `Provider.trust` honoured by `is_cloud`, unknown values fail closed | ✅ `emptyos/capabilities/__init__.py` |
| ComfyUI providers expose `host` + `trust` | ✅ `plugins/comfyui/plugin.py` |
| Consent gate fires correctly for rented hosts | ✅ falls out of the above |
| Point ComfyUI at a rented box for a render, then back | ✅ already config-driven, zero code |
| Guard against recurrence | ✅ `scripts/check_provider_trust.py`, preflight `always`+`security` |

Declaring it:

```toml
[plugins.comfyui]
host  = "http://gpu-rental.ts.net:8188"
trust = "rented"          # without this, the tailnet address reads as local
```

**The guard flags only the ambiguous zone** — private / CGNAT / `*.ts.net` /
`*.local` hosts with no declaration. Loopback needs no declaration, and public
hosts are already classified correctly by inference, so requiring one there
would fire on every OpenAI endpoint: 5 findings on a healthy config, all noise
(`.claude/rules/audits.md`). It is **advisory, never gating** — "is this box
yours?" is a question only the operator can answer, and a legitimate private-LAN
service shouldn't break a build until annotated. Both directions pinned by
`tests/test_unit_check_provider_trust.py`.

**Do not build (no consumer yet):**

- A remote job dispatcher or broker. `kernel/workers.py` is a local in-process
  queue; making it network-transparent is a large change serving one hypothetical
  workload. Point the plugin at the rented host for the duration of a job instead.
- Auto-provisioning / teardown automation. Do it by hand until the manual process
  is annoying enough to be worth encoding — and until you know the real shape.
- Confidential computing / encrypted VMs. Enormous complexity, and by the payload
  table above the data going to a rented box is publication-bound anyway. The
  mitigation is *not sending sensitive data*, not encrypting it in flight to a
  host that could read it at rest.

## Decision rule, one line

> **Rent compute for work whose inputs are already destined to be public. Keep
> everything that touches the vault on hardware you own.**

That single sentence resolves every case in the payload table, and it is why the
5090 verdict and this rule are consistent rather than in tension: MV rendering
rents well because its inputs are publishable; the local LLM stays home because
its inputs are not.

## Cross-references

- `emptyos/capabilities/consent.py::host_is_local` — the inference this rule
  overrides for non-owned hardware
- `emptyos/capabilities/__init__.py::Provider.is_cloud` — the documented
  override hook the `trust` key would use
- CLAUDE.md Rule 18 (cloud consent mandatory) / Rule 19 (no vault data to cloud)
- `.claude/rules/demo-mode.md` — the orthogonal knobs (`network.mode`,
  `demo.enabled`, `.eos-personal`); `trust` is a fourth, about *compute location*
- `project_rtx5090_verdict` (memory) — the analysis that made this necessary
