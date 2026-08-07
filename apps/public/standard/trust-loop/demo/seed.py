"""Demo seed — the KB formula note the trust-loop app cites.

Runs only when [demo] seed_on_boot is set. Creates the note the app's manifest
references ("[[ieee-80-touch-step-voltages]]") so the KB "?" popovers and the
loop strip's stage-1 link resolve in a fresh demo vault. Idempotent: create
fails soft when the note already exists.
"""

BODY = """\
## Statement

Tolerable touch and step voltages from IEEE Std 80's closed forms. The
tolerable body current for a shock of duration t seconds is

```
I_B = k / sqrt(t)        k = 0.116 (50 kg body)  |  0.157 (70 kg body)
```

valid for 0.03 s <= t <= 3.0 s. With a protective surface layer of
resistivity rho_s and thickness h_s over native soil rho, the derating factor

```
C_s = 1 - 0.09 * (1 - rho/rho_s) / (2*h_s + 0.09)
```

(C_s = 1 with no layer, and rho_s collapses to rho). The tolerable limits are

```
E_touch = (1000 + 1.5 * C_s * rho_s) * I_B
E_step  = (1000 + 6.0 * C_s * rho_s) * I_B
```

## Acceptance target

The standard's own resolved case — rho = 100 ohm-m, no surface layer,
t = 0.5 s, 50 kg body — gives **E_touch = 188.65 V** and **E_step = 262.48 V**.
The trust-loop calculator's conformance gate reproduces this case within 0.5%.

## Pitfalls

- The 1000 ohm term is the body resistance; the 1.5x / 6x factors are the
  foot-contact resistances (two feet in parallel for touch, in series for
  step) — do not swap them.
- C_s applies to the *surface* resistivity, not the native soil.
- These are the *tolerable* limits only; a design also needs the *actual*
  mesh and step voltages, which this demo deliberately does not compute.
"""


async def seed(app):
    await app.call_app(
        "kb",
        "create_note",
        kind="formula",
        title="IEEE 80 tolerable touch and step voltages",
        slug="ieee-80-touch-step-voltages",
        domain="power-systems",
        topic="earthing-safety",
        body=BODY,
        references=["IEEE Std 80 — tolerable body current + touch/step criteria"],
        author="ai",
    )
