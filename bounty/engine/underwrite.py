"""
BOUNTY — multi-path underwriting module (v3).

Each parcel is underwritten under EVERY financing path that could apply, and
the deal reports which path actually pencils. This mirrors how NYC development
really works in 2026: market-rate alone rarely pencils; the 485-x exemption,
City of Yes UAP bonus FAR, and the affordable stack are what close deals.

Program terms encoded from current sources (Jan 2025 rules, verified Jul 2026):
  485-x "ANNY": <100 units -> 20% of units affordable @ <=80% AMI wtd avg,
    35-yr exemption (25 @ 100%, 10 @ affordability %). 6-10 unit buildings
    outside Manhattan <=12,500 SF residential: 10-yr full exemption, NO
    affordability (50% stabilized). 100+ units: 25% @ 80% AMI + construction
    wage floors ($35+/hr) — modeled with a hard-cost premium.
  City of Yes UAP: +20% FAR in R6-R10, bonus floor area affordable @ ~60% AMI.
  ELLA/4% LIHTC stack: tax-exempt bonds + LIHTC equity + city subsidy loans;
    profit is primarily the developer fee.

All dollar assumptions are published in ASSUMPTIONS and shown in the terminal.
Loans are sized to the LESSER of LTC and a DSCR constraint (how banks actually
size), so no more fantasy 0.75-DSCR "deals".
"""

ASSUMPTIONS = {
    # construction
    "hard_cost_psf": 300, "hard_cost_wage_premium": 1.12,  # 100+ unit 485-x wage floors
    "soft_cost_pct": 0.25, "du_factor_sf": 680, "efficiency": 0.85,
    # income (blended per-unit monthly rents, 2026 approximations — editable)
    "rent_mo": {
        "market": {"BX": 2280, "BK": 2790, "QN": 2580},
        "ami80": 2330, "ami60": 1750, "ami40": 1160,
        "s8_uplift": 1.12,   # Section 8 VPS over market in BX/upper Manhattan-type areas
    },
    "vacancy": 0.05,
    # opex split so tax exemptions can be modeled honestly
    "opex_psf_extax": 9.0, "tax_psf_unabated": 4.50, "tax_psf_abated": 0.50,
    # debt
    "conv_rate": 0.0675, "conv_amort": 30, "conv_ltc": 0.85, "conv_min_dscr": 1.20,
    "bond_rate": 0.054, "bond_amort": 35, "bond_min_dscr": 1.15,
    # affordable stack
    "lihtc_equity_pct_tdc": 0.30, "city_subsidy_per_unit": 130000,
    "aff_dev_fee_pct": 0.10,
    "dev_fee_pct": 0.07,
    "note": "Screening estimates. Program terms per HPD/485-x rules (Jan 2025) and City of Yes UAP; rents/costs are published, editable assumptions — not appraisals.",
}

def _pmt(loan, rate, years):
    r = rate/12; n = years*12
    if loan <= 0: return 0.0
    return loan * (r*(1+r)**n)/((1+r)**n - 1) * 12

def _size_loan(noi, rate, years, min_dscr, ltc_cap):
    """Bank sizing: lesser of LTC cap and DSCR-constrained loan."""
    r = rate/12; n = years*12
    k = (r*(1+r)**n)/((1+r)**n - 1) * 12
    dscr_loan = (noi/min_dscr)/k if noi > 0 else 0
    return max(0.0, min(ltc_cap, dscr_loan))

def _path(name, label, units_m, rent_m, units_a, rent_a, gross_sf, land, A,
          tax_psf, hard_mult=1.0, rate=None, amort=None, min_dscr=None, ltc=None):
    rate = rate or A["conv_rate"]; amort = amort or A["conv_amort"]
    min_dscr = min_dscr or A["conv_min_dscr"]; ltc = ltc if ltc is not None else A["conv_ltc"]
    hard = gross_sf * A["hard_cost_psf"] * hard_mult
    soft = hard * A["soft_cost_pct"]
    tdc = land + hard + soft
    gpr = (units_m*rent_m + units_a*rent_a) * 12
    egi = gpr * (1 - A["vacancy"])
    opex = gross_sf * (A["opex_psf_extax"] + tax_psf)
    noi = egi - opex
    loan = _size_loan(noi, rate, amort, min_dscr, tdc*ltc)
    ds = _pmt(loan, rate, amort)
    equity = tdc - loan
    dscr = noi/ds if ds > 0 else 0
    cf = noi - ds
    coc = cf/equity if equity > 0 else 0
    eq_ratio = equity/tdc if tdc > 0 else 1
    feasible = noi > 0 and dscr >= min_dscr - 0.01 and eq_ratio <= 0.35
    return {"path": name, "label": label, "units_market": units_m, "units_affordable": units_a,
            "tdc": int(tdc), "loan": int(loan), "equity": int(equity),
            "equity_pct": round(eq_ratio*100,1), "noi": int(noi), "dscr": round(dscr,2),
            "cash_flow": int(cf), "coc_pct": round(coc*100,1), "feasible": feasible}

def underwrite_all_paths(boro, lot_sf, resid_far, land_cost, A=ASSUMPTIONS):
    """Return all applicable paths + best, for a parcel."""
    base_sf = lot_sf * resid_far
    units = int(base_sf / A["du_factor_sf"])
    rm = A["rent_mo"]["market"][boro]
    paths = []
    if units < 6: return None

    # 1. Conventional market (unabated taxes) — the honesty baseline
    paths.append(_path("conv", "Conventional market (no abatement)",
                       units, rm, 0, 0, base_sf, land_cost, A, A["tax_psf_unabated"]))

    # 2. 485-x small (6-10 units, outside Manhattan, <=12,500 SF): full abatement, no affordability
    if 6 <= units <= 10 and base_sf <= 12500:
        paths.append(_path("485x_small", "485-x small building (10-yr full exemption, market rents)",
                           units, rm, 0, 0, base_sf, land_cost, A, A["tax_psf_abated"]))

    # 3. 485-x standard (<100 units): 20% @ 80% AMI, 35-yr exemption
    if units < 100:
        ua = max(1, round(units*0.20)); um = units - ua
        paths.append(_path("485x", "485-x (20% @ 80% AMI, 35-yr exemption)",
                           um, rm, ua, A["rent_mo"]["ami80"], base_sf, land_cost, A, A["tax_psf_abated"]))
    else:
        ua = max(1, round(units*0.25)); um = units - ua
        paths.append(_path("485x_100", "485-x 100+ units (25% @ 80% AMI + wage floors)",
                           um, rm, ua, A["rent_mo"]["ami80"], base_sf, land_cost, A,
                           A["tax_psf_abated"], hard_mult=A["hard_cost_wage_premium"]))

    # 4. City of Yes UAP + 485-x: +20% FAR, bonus floor affordable @ ~60% AMI
    bonus_sf = base_sf * 0.20
    u_bonus = int(bonus_sf / A["du_factor_sf"])
    if u_bonus >= 1:
        tot_sf = base_sf + bonus_sf
        tot_units = units + u_bonus
        ua = max(u_bonus, round(tot_units*0.20))  # bonus units + 485-x requirement overlap
        um = tot_units - ua
        blend_aff = (A["rent_mo"]["ami60"]*u_bonus + A["rent_mo"]["ami80"]*max(0,ua-u_bonus)) / ua
        paths.append(_path("uap", "City of Yes UAP +20% FAR + 485-x",
                           um, rm, ua, blend_aff, tot_sf, land_cost, A, A["tax_psf_abated"]))

    # 5. Section 8 overlay on 485-x (VPS rents on market units) — BX/QN especially
    if units < 100:
        ua = max(1, round(units*0.20)); um = units - ua
        paths.append(_path("485x_s8", "485-x + Section 8 VPS rents",
                           um, rm*A["rent_mo"]["s8_uplift"], ua, A["rent_mo"]["ami80"],
                           base_sf, land_cost, A, A["tax_psf_abated"]))

    # 6. ELLA / 4% LIHTC all-affordable stack (profit = developer fee)
    hard = base_sf * A["hard_cost_psf"]; soft = hard * A["soft_cost_pct"]
    tdc = land_cost + hard + soft
    dev_fee = tdc * A["aff_dev_fee_pct"]
    tdc_af = tdc + dev_fee
    gpr = units * A["rent_mo"]["ami60"] * 12
    noi = gpr*(1-A["vacancy"]) - base_sf*(A["opex_psf_extax"] + A["tax_psf_abated"])
    bonds = _size_loan(noi, A["bond_rate"], A["bond_amort"], A["bond_min_dscr"], tdc_af)
    lihtc = tdc_af * A["lihtc_equity_pct_tdc"]
    subsidy = units * A["city_subsidy_per_unit"]
    gap = tdc_af - (bonds + lihtc + subsidy)
    paths.append({"path": "ella", "label": "ELLA / 4% LIHTC all-affordable stack (competitive subsidy award required)",
                  "units_market": 0, "units_affordable": units, "tdc": int(tdc_af),
                  "loan": int(bonds), "equity": 0, "equity_pct": 0.0,
                  "noi": int(noi), "dscr": A["bond_min_dscr"] if bonds>0 else 0,
                  "cash_flow": 0, "coc_pct": None, "dev_fee": int(dev_fee),
                  "funding_gap": int(gap), "feasible": gap <= 0 and noi > 0,
                  "program_dependent": True})

    # Institutional honesty: a deal that only works with a competitive subsidy
    # award is a PROGRAM PLAY, not a self-executing deal. Rank market paths first.
    market_feas = [p for p in paths if p["feasible"] and not p.get("program_dependent")]
    ella_feas = [p for p in paths if p["feasible"] and p.get("program_dependent")]
    best = max(market_feas, key=lambda p: p.get("cash_flow", 0)) if market_feas else \
           (ella_feas[0] if ella_feas else None)
    tier = "market" if market_feas else ("program" if ella_feas else "none")
    return {"units_base": units, "paths": paths,
            "best_path": best["path"] if best else None,
            "best_label": best["label"] if best else "No path pencils at this land basis — negotiate toward walk-away price",
            "tier": tier, "any_feasible": bool(best),
            "market_equity": (max(0, min(p["equity"] for p in market_feas)) if market_feas else None)}
