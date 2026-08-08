"""Three-phase prospective fault current at MV/LV transformer terminals.

Implements `ALGORITHM.md` (kept beside this file; read it before changing
anything here), which specifies the method of ABB Technical Application Paper
No. 2 clause 2.2. Pure module: no model call, no I/O, no kernel import —
unit-testable without a daemon and the sole compute path behind the app.

The anchor is that paper's published worked example, 14.95 kA. Carrying full
precision lands ~0.1 % below it because the paper prints its intermediates
rounded; ALGORITHM.md section 6.1 explains why the band accommodates that
rather than chasing the printed digits.
"""

from __future__ import annotations

import math

SQRT3 = math.sqrt(3.0)
# Split of the network impedance into R and X, exactly as the source states it:
#     X_knet = 0.995 * Z_knet      R_knet = 0.1 * X_knet
# Deliberately NOT R = sqrt(Z^2 - X^2), which is the obvious-looking identity
# and gives 0.0999*Z instead of 0.0995*Z. The difference never shows in the
# answer, but the doc claims fidelity to a published method, so the code
# follows what the method says rather than what looks equivalent.
NETWORK_X_OVER_Z = 0.995
NETWORK_R_OVER_X = 0.1


def _referred(value: float, ratio: float) -> float:
    """Refer an impedance from the MV base to the LV base (ALGORITHM.md 4.1)."""
    return value / (ratio**2)


def _n(v: float, sig: int = 6) -> str:
    """Format a number the way a hand calc would write it.

    Small impedances need exponential form or the report becomes a wall of
    leading zeros; everything else reads better as a plain decimal.
    """
    if v == 0:
        return "0"
    if abs(v) < 1e-3 or abs(v) >= 1e6:
        return f"{v:.{sig - 2}e}"
    s = f"{v:.{sig}g}"
    return s


def _step(group, symbol, formula, substitution, value, unit, note=""):
    """One checkable line of the calculation report (ALGORITHM.md stage 6)."""
    return {
        "group": group,
        "symbol": symbol,
        "formula": formula,
        "substitution": substitution,
        "value": value,
        "value_str": _n(value),
        "unit": unit,
        "note": note,
    }


def network_impedance(*, c: float, u_net: float, i_k_net: float) -> float:
    """Z_Q from the CURRENT form — ALGORITHM.md section 4.2.

    Deliberately not the printed power form c^2*U^2/S: the paper's own worked
    example uses this one, and the two disagree unless S is exactly
    sqrt(3)*U*I. Implementing the other form reproduces neither the paper's
    intermediate impedance nor its answer.
    """
    return c * u_net / (SQRT3 * i_k_net)


def transformer_impedance(
    *, s_n: float, u_2n: float, vk_pct: float, pk_pct: float
) -> dict:
    """Transformer R, X, Z at the LV base from its test data (section 4.3)."""
    z_t = (u_2n**2 * vk_pct) / (100.0 * s_n)
    p_t = (pk_pct * s_n) / 100.0
    i_2n = s_n / (SQRT3 * u_2n)
    r_t = p_t / (3.0 * i_2n**2)
    if r_t > z_t:
        raise ValueError(
            "transformer load loss implies R_T greater than Z_T — the test "
            "data (vk%, pk%) is inconsistent"
        )
    return {
        "z_ohm": z_t,
        "r_ohm": r_t,
        "x_ohm": math.sqrt(z_t**2 - r_t**2),
        "i_2n_a": i_2n,
        "load_loss_w": p_t,
    }


def prospective_fault_current(
    *,
    u_net: float,
    i_k_net: float,
    c: float,
    r_mv: float,
    x_mv: float,
    s_n: float,
    u_2n: float,
    vk_pct: float,
    pk_pct: float,
    r_lv: float = 0.0,
    x_lv: float = 0.0,
) -> dict:
    """Three-phase prospective fault current at the LV terminals.

    Returns the working, not just the answer — every element's contribution
    at the LV base, so the result can be read the way an engineer reads a
    hand calc rather than taken on faith.
    """
    _validate(
        u_net=u_net, i_k_net=i_k_net, c=c, r_mv=r_mv, x_mv=x_mv,
        s_n=s_n, u_2n=u_2n, vk_pct=vk_pct, pk_pct=pk_pct, r_lv=r_lv, x_lv=x_lv,
    )

    ratio = u_net / u_2n
    z_q = network_impedance(c=c, u_net=u_net, i_k_net=i_k_net)
    x_q = z_q * NETWORK_X_OVER_Z
    r_q = x_q * NETWORK_R_OVER_X
    tr = transformer_impedance(s_n=s_n, u_2n=u_2n, vk_pct=vk_pct, pk_pct=pk_pct)

    elements = [
        ("Supply network", _referred(r_q, ratio), _referred(x_q, ratio)),
        ("MV cable", _referred(r_mv, ratio), _referred(x_mv, ratio)),
        ("Transformer", tr["r_ohm"], tr["x_ohm"]),
        ("LV cable", r_lv, x_lv),
    ]

    r_total = sum(e[1] for e in elements)
    x_total = sum(e[2] for e in elements)
    z_total = math.hypot(r_total, x_total)
    if z_total <= 0:
        raise ValueError("total impedance is zero — no finite fault current")

    i_k3 = c * u_2n / (SQRT3 * z_total)

    contributions = []
    for name, r, x in elements:
        z = math.hypot(r, x)
        contributions.append({
            "name": name,
            "r_ohm": round(r, 9),
            "x_ohm": round(x, 9),
            "z_ohm": round(z, 9),
            "share_pct": round(100.0 * z / z_total, 2),
        })

    # ── The calculation report: inputs through to result, every line
    # showing its formula, its substitution and its value, so a reviewing
    # engineer can check the arithmetic rather than trust it. Built from the
    # same locals as the answer above, so the report cannot drift from it.
    r_q_ref = _referred(r_q, ratio)
    x_q_ref = _referred(x_q, ratio)
    r_mv_ref = _referred(r_mv, ratio)
    x_mv_ref = _referred(x_mv, ratio)
    G_IN, G_NET, G_MV, G_TR, G_LV, G_SUM = (
        "Given", "Supply network", "MV cable", "Transformer", "LV cable", "Total and result")

    steps = [
        _step(G_IN, "U_net", "supply network nominal voltage", "given", u_net, "V"),
        _step(G_IN, "I_k_net", "network fault current", "given", i_k_net, "A"),
        _step(G_IN, "c", "voltage factor", "given", c, ""),
        _step(G_IN, "S_n", "transformer rated power", "given", s_n, "VA"),
        _step(G_IN, "U_2n", "transformer secondary rated voltage", "given", u_2n, "V"),
        _step(G_IN, "v_k", "transformer voltage drop on test", "given", vk_pct, "%"),
        _step(G_IN, "p_k", "transformer load loss on test", "given", pk_pct, "%"),

        _step(G_NET, "K", "U_net / U_2n", f"{_n(u_net)} / {_n(u_2n)}", ratio, "",
              "the referral coefficient — everything upstream is divided by K²"),
        _step(G_NET, "Z_knet", "c · U_net / (√3 · I_k_net)",
              f"{_n(c)} · {_n(u_net)} / (√3 · {_n(i_k_net)})", z_q, "Ω",
              "the current form; the paper also prints a power form that differs by a factor c"),
        _step(G_NET, "Z_knet(LV)", "Z_knet / K²", f"{_n(z_q)} / {_n(ratio)}²",
              _referred(z_q, ratio), "Ω"),
        _step(G_NET, "X_knet(LV)", "0.995 · Z_knet(LV)",
              f"0.995 · {_n(_referred(z_q, ratio))}", x_q_ref, "Ω"),
        _step(G_NET, "R_knet(LV)", "0.1 · X_knet(LV)", f"0.1 · {_n(x_q_ref)}", r_q_ref, "Ω"),

        _step(G_MV, "R_CMV(LV)", "R_CMV / K²", f"{_n(r_mv)} / {_n(ratio)}²", r_mv_ref, "Ω"),
        _step(G_MV, "X_CMV(LV)", "X_CMV / K²", f"{_n(x_mv)} / {_n(ratio)}²", x_mv_ref, "Ω"),

        _step(G_TR, "Z_TR", "U_2n² · v_k / (100 · S_n)",
              f"{_n(u_2n)}² · {_n(vk_pct)} / (100 · {_n(s_n)})", tr["z_ohm"], "Ω"),
        _step(G_TR, "P_TR", "p_k · S_n / 100", f"{_n(pk_pct)} · {_n(s_n)} / 100",
              tr["load_loss_w"], "W"),
        _step(G_TR, "I_2n", "S_n / (√3 · U_2n)", f"{_n(s_n)} / (√3 · {_n(u_2n)})",
              tr["i_2n_a"], "A"),
        _step(G_TR, "R_TR", "P_TR / (3 · I_2n²)",
              f"{_n(tr['load_loss_w'])} / (3 · {_n(tr['i_2n_a'])}²)", tr["r_ohm"], "Ω"),
        _step(G_TR, "X_TR", "√(Z_TR² − R_TR²)",
              f"√({_n(tr['z_ohm'])}² − {_n(tr['r_ohm'])}²)", tr["x_ohm"], "Ω"),

        _step(G_LV, "R_CLV", "LV cable resistance", "given", r_lv, "Ω"),
        _step(G_LV, "X_CLV", "LV cable reactance", "given", x_lv, "Ω"),

        _step(G_SUM, "R_Tk", "ΣR = R_knet + R_CMV + R_TR + R_CLV",
              " + ".join(_n(v) for v in (r_q_ref, r_mv_ref, tr["r_ohm"], r_lv)), r_total, "Ω"),
        _step(G_SUM, "X_Tk", "ΣX = X_knet + X_CMV + X_TR + X_CLV",
              " + ".join(_n(v) for v in (x_q_ref, x_mv_ref, tr["x_ohm"], x_lv)), x_total, "Ω"),
        _step(G_SUM, "Z_Tk", "√(R_Tk² + X_Tk²)",
              f"√({_n(r_total)}² + {_n(x_total)}²)", z_total, "Ω"),
        _step(G_SUM, "I_k3", "c · U_2n / (√3 · Z_Tk)",
              f"{_n(c)} · {_n(u_2n)} / (√3 · {_n(z_total)})", i_k3, "A",
              "the three-phase prospective fault current"),
    ]

    return {
        "i_k3_a": round(i_k3, 2),
        "i_k3_ka": round(i_k3 / 1000.0, 4),
        "r_total_ohm": round(r_total, 9),
        "x_total_ohm": round(x_total, 9),
        "z_total_ohm": round(z_total, 9),
        "ratio": round(ratio, 4),
        "transformer_i_2n_a": round(tr["i_2n_a"], 2),
        "contributions": contributions,
        "dominant": max(contributions, key=lambda e: e["share_pct"])["name"],
        "steps": steps,
    }


def sensitivity(
    *, base: dict, element: str, factor_lo: float = 0.2, factor_hi: float = 5.0,
    points: int = 40,
) -> list[dict]:
    """Sweep one element's impedance and report the resulting current.

    Exists so the chart plots engine output rather than re-deriving the
    physics in the page. `element` is one of 'network', 'mv_cable',
    'transformer', 'lv_cable'.
    """
    keys = {
        "network": ("i_k_net",),          # scaling current scales Z inversely
        "mv_cable": ("r_mv", "x_mv"),
        "transformer": ("vk_pct",),
        "lv_cable": ("r_lv", "x_lv"),
    }
    if element not in keys:
        raise ValueError(f"unknown element '{element}' (expected one of {sorted(keys)})")
    points = max(2, min(int(points), 200))

    out: list[dict] = []
    lo, hi = math.log10(factor_lo), math.log10(factor_hi)
    for i in range(points):
        f = 10 ** (lo + (hi - lo) * i / (points - 1))
        kwargs = dict(base)
        for k in keys[element]:
            # A bigger network fault current means a WEAKER source impedance,
            # so that one scales inversely to keep "factor" meaning "more
            # impedance" consistently across every element.
            kwargs[k] = base[k] / f if element == "network" else base[k] * f
        try:
            r = prospective_fault_current(**kwargs)
        except ValueError:
            continue
        out.append({
            "factor": round(f, 4),
            "i_k3_ka": r["i_k3_ka"],
            "z_total_ohm": r["z_total_ohm"],
        })
    return out


def _validate(**kw) -> None:
    """Enforce ALGORITHM.md section 5. Each refusal names what it protects."""
    for name in ("u_net", "i_k_net", "s_n", "u_2n", "vk_pct", "c"):
        if kw[name] <= 0:
            raise ValueError(f"{name} must be positive")
    if kw["pk_pct"] < 0:
        raise ValueError("pk_pct must not be negative")
    for name in ("r_mv", "x_mv", "r_lv", "x_lv"):
        if kw[name] < 0:
            raise ValueError(f"{name} must not be negative (impedances are passive here)")
