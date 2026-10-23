"""
BOUNTY — multi-path underwriting module (v4, Oct 2026).

Every parcel is underwritten under every financing path that could apply, and
the deal reports which path pencils AND what you can actually pay for the land.

v4 changes (honest fixes after checking v3 against real 2026 Bronx filings):
  - Hard cost is applied to GROSS area (ZFA x 1.08 for cellar/mech/bulkhead),
    not ZFA, and carries a financing/carry line. v3's $15M TDC for a 54-unit
    Bronx building was ~25% light.
  - Two cost bases, both published: INSTITUTIONAL ($/SF a third-party GC
    charges) and OWNER-BUILDER (what you build it for as your own GC). The
    owner-builder basis is the principal's real edge; walk-away land prices
    are reported on both so the gap is visible.
  - Feasibility is a YIELD-ON-COST gate (NOI / TDC >= target), not a DSCR trick.
    Banks size to 70% LTC and 1.20x DSCR; the equity check must be <= 45% of TDC.
  - Section 8 modeled correctly: the voucher pays the LESSER of the NYCHA/HPD
    payment standard and a rent-reasonableness comp, so there is no "uplift"
    above market. The benefit is collection certainty: 2% vacancy vs 5%.
  - Walk-away land is solved per path: the land price at which that path hits
    the target yield on cost. Headroom = walk-away / estimated land. That ratio
    is what makes a site actionable.

Program terms encoded from current sources (verified Oct 2026):
  485-x Option B (6-99 units): 20% of units affordable @ 80% AMI wtd avg,
    35-yr exemption, no construction-wage requirement under 100 units.
  485-x Option C (6-10 units, outside Manhattan, <=12,500 SF): 10-yr
    exemption, 50% stabilized, no AMI requirement.
  485-x Option A (100+): 25% @ 80% AMI + $40/hr wage floor (hard-cost premium).
  City of Yes UAP: ~+20% FAR in R6-R10, bonus floor area permanently affordable
    @ 60% AMI weighted average; overlaps with the 485-x set-aside.
  NYCHA VPS (new rentals, eff. Jan 1 2026): $2,604 studio / $2,734 1BR /
    $2,997 2BR / $3,753 3BR -> blended ~$2,850 for a 1BR-heavy mix.
  ELLA / 4% LIHTC stack: tax-exempt bonds + LIHTC equity + HPD subsidy loan;
    profit is the developer fee. Competitive award required (program play).
"""

ASSUMPTIONS = {
    # construction (per SF of GROSS building area)
    "hard_cost_psf": {"institutional": 350, "owner_builder": 290},
    "hard_cost_basis_note": "institutional = third-party GC bid; owner_builder = principal acts as GC (Ground Up Group), ~15-18% below",
    "gross_factor": 1.08,          # gross SF / zoning floor area (cellar, mech, bulkhead)
    "hard_cost_wage_premium": 1.10, # 485-x 100+ unit wage floors
    "soft_cost_pct": 0.20,          # A/E, permits, legal, insurance, contingency
    "financing_pct": 0.06,          # construction interest reserve + loan fees (% of hard+soft)
    "du_factor_sf": 680, "efficiency": 0.85,
    # income (blended per-unit monthly rents, 2026 — editable)
    "rent_mo": {
        "market": {"BX": 2280, "BK": 2790, "QN": 2580},
        "ami80": 2330, "ami60": 1750, "ami40": 1160,
        "s8_vps_blended": 2850,   # NYCHA VPS new-rental schedule eff. 2026-01-01, 1BR-heavy mix
        "s8_note": "Voucher pays the LESSER of VPS and a rent-reasonableness comp — modeled as min(market, VPS). Benefit is 2% vacancy, not a premium.",
    },
    "vacancy": 0.05, "vacancy_s8": 0.02,
    # opex split so tax exemptions can be modeled honestly (per SF of gross area)
    "opex_psf_extax": 10.5, "tax_psf_unabated": 7.0, "tax_psf_abated": 0.75,
    # debt
    "conv_rate": 0.0675, "conv_amort": 30, "conv_ltc": 0.70, "conv_min_dscr": 1.20,
    "bond_rate": 0.054, "bond_amort": 35, "bond_min_dscr": 1.15,
    # feasibility gates
    "target_yoc": 0.0625,          # NOI / TDC must clear this (debt 6.75% -> ~0 negative leverage)
    "max_equity_pct": 0.45,
    # affordable stack
    "lihtc_equity_pct_tdc": 0.30, "city_subsidy_per_unit": 185000,
    "aff_dev_fee_pct": 0.10,
    "dev_fee_pct": 0.07,
    "note": "Screening estimates, not appraisals. Costs on gross area; owner-builder basis assumes the principal is the GC. Program terms per HPD 485-x page and City of Yes UAP (verified Oct 2026).",
}

def _pmt(loan, rate, years):
    r = rate/12; n = years*12
    if loan <= 0: return 0.0
    return loan * (r*(1+r)**n)/((1+r)**n - 1) * 12

def _k(rate, years):
    r = rate/12; n = years*12
    return (r*(1+r)**n)/((1+r)**n - 1) * 12

def _size_loan(noi, rate, years, min_dscr, ltc_cap):
    """Bank sizing: lesser of LTC cap and DSCR-constrained loan."""
    dscr_loan = (noi/min_dscr)/_k(rate, years) if noi > 0 else 0
    return max(0.0, min(ltc_cap, dscr_loan))

def _costs(gross_sf, A, basis, hard_mult=1.0):
    hard = gross_sf * A["hard_cost_psf"][basis] * hard_mult
    soft = hard * A["soft_cost_pct"]
    fin = (hard + soft) * A["financing_pct"]
    return hard, soft, fin

def _path(name, label, units_m, rent_m, units_a, rent_a, zfa, land, A, tax_psf,
          vacancy=None, hard_mult=1.0, rate=None, amort=None, min_dscr=None, ltc=None):
    rate = rate or A["conv_rate"]; amort = amort or A["conv_amort"]
    min_dscr = min_dscr or A["conv_min_dscr"]; ltc = ltc if ltc is not None else A["conv_ltc"]
    vac = A["vacancy"] if vacancy is None else vacancy
    gross = zfa * A["gross_factor"]
    hard, soft, fin = _costs(gross, A, "owner_builder", hard_mult)
    build = hard + soft + fin
    tdc = land + build
    hard_i, soft_i, fin_i = _costs(gross, A, "institutional", hard_mult)
    build_i = hard_i + soft_i + fin_i
    gpr = (units_m*rent_m + units_a*rent_a) * 12
    egi = gpr * (1 - vac)
    opex = gross * (A["opex_psf_extax"] + tax_psf)
    noi = egi - opex
    loan = _size_loan(noi, rate, amort, min_dscr, tdc*ltc)
    ds = _pmt(loan, rate, amort)
    equity = tdc - loan
    dscr = noi/ds if ds > 0 else 0
    cf = noi - ds
    coc = cf/equity if equity > 0 else 0
    eq_ratio = equity/tdc if tdc > 0 else 1
    yoc = noi/tdc if tdc > 0 else 0
    # walk-away land: price at which this path exactly hits the target yield
    walk = max(0.0, noi/A["target_yoc"] - build) if noi > 0 else 0.0
    walk_i = max(0.0, noi/A["target_yoc"] - build_i) if noi > 0 else 0.0
    feasible = noi > 0 and yoc >= A["target_yoc"] - 0.0005 and dscr >= min_dscr - 0.01 \
               and eq_ratio <= A["max_equity_pct"]
    return {"path": name, "label": label, "units_market": units_m, "units_affordable": units_a,
            "gross_sf": int(gross), "hard": int(hard), "soft": int(soft), "financing": int(fin),
            "tdc": int(tdc), "tdc_institutional": int(land + build_i),
            "loan": int(loan), "equity": int(equity),
            "equity_pct": round(eq_ratio*100,1), "noi": int(noi), "dscr": round(dscr,2),
            "yoc_pct": round(yoc*100,2), "cash_flow": int(cf), "coc_pct": round(coc*100,1),
            "walk_away_land": int(walk), "walk_away_land_institutional": int(walk_i),
            "feasible": feasible}

def underwrite_all_paths(boro, lot_sf, resid_far, land_cost, A=ASSUMPTIONS, rent_market=None):
    """Return all applicable paths + best, for a parcel.
    rent_market: optional per-parcel market monthly rent (neighborhood-tuned)."""
    base_sf = lot_sf * resid_far
    units = int(base_sf / A["du_factor_sf"])
    rm = rent_market if rent_market else A["rent_mo"]["market"][boro]
    s8 = min(rm, A["rent_mo"]["s8_vps_blended"])
    paths = []
    if units < 6: return None

    # 1. Conventional market (unabated taxes) — the honesty baseline
    paths.append(_path("conv", "Conventional market, no abatement",
                       units, rm, 0, 0, base_sf, land_cost, A, A["tax_psf_unabated"]))

    # 2. 485-x Option C (6-10 units, outside Manhattan, <=12,500 SF): 10-yr exemption, no AMI test
    if 6 <= units <= 10 and base_sf <= 12500:
        paths.append(_path("485x_small", "485-x Option C small building (10-yr exemption, 50% stabilized, market rents)",
                           units, rm, 0, 0, base_sf, land_cost, A, A["tax_psf_abated"]))
        paths.append(_path("485x_small_s8", "485-x Option C + Section 8 tenants (VPS-capped, 2% vacancy)",
                           units, s8, 0, 0, base_sf, land_cost, A, A["tax_psf_abated"], vacancy=A["vacancy_s8"]))

    # 3. 485-x Option B (<100 units): 20% @ 80% AMI, 35-yr exemption
    if units < 100:
        ua = max(1, round(units*0.20)); um = units - ua
        paths.append(_path("485x", "485-x Option B (20% @ 80% AMI, 35-yr exemption)",
                           um, rm, ua, A["rent_mo"]["ami80"], base_sf, land_cost, A, A["tax_psf_abated"]))
        # 4. Section 8 overlay: market units leased to voucher holders at min(market, VPS)
        paths.append(_path("485x_s8", "485-x Option B + Section 8 tenants (VPS-capped, 2% vacancy)",
                           um, s8, ua, A["rent_mo"]["ami80"], base_sf, land_cost, A,
                           A["tax_psf_abated"], vacancy=A["vacancy_s8"]))
    else:
        ua = max(1, round(units*0.25)); um = units - ua
        paths.append(_path("485x_100", "485-x Option A 100+ units (25% @ 80% AMI + $40/hr wage floor)",
                           um, rm, ua, A["rent_mo"]["ami80"], base_sf, land_cost, A,
                           A["tax_psf_abated"], hard_mult=A["hard_cost_wage_premium"]))

    # 5. City of Yes UAP + 485-x: +20% FAR, bonus floor area affordable @ 60% AMI (overlaps 485-x set-aside)
    bonus_sf = base_sf * 0.20
    u_bonus = int(bonus_sf / A["du_factor_sf"])
    if u_bonus >= 1:
        tot_sf = base_sf + bonus_sf
        tot_units = units + u_bonus
        ua = max(u_bonus, round(tot_units*0.20))
        um = tot_units - ua
        blend_aff = (A["rent_mo"]["ami60"]*u_bonus + A["rent_mo"]["ami80"]*max(0,ua-u_bonus)) / ua
        paths.append(_path("uap", "City of Yes UAP +20% FAR + 485-x",
                           um, rm, ua, blend_aff, tot_sf, land_cost, A, A["tax_psf_abated"]))
        paths.append(_path("uap_s8", "UAP +20% FAR + 485-x + Section 8 tenants",
                           um, min(rm, A["rent_mo"]["s8_vps_blended"]), ua, blend_aff, tot_sf, land_cost, A,
                           A["tax_psf_abated"], vacancy=A["vacancy_s8"]))

    # 6. ELLA / 4% LIHTC all-affordable stack (profit = developer fee; competitive award)
    gross = base_sf * A["gross_factor"]
    hard, soft, fin = _costs(gross, A, "institutional")   # HPD deals price at third-party GC + prevailing-ish costs
    tdc = land_cost + hard + soft + fin
    dev_fee = tdc * A["aff_dev_fee_pct"]
    tdc_af = tdc + dev_fee
    gpr = units * A["rent_mo"]["ami60"] * 12
    noi = gpr*(1-A["vacancy"]) - gross*(A["opex_psf_extax"] + A["tax_psf_abated"])
    bonds = _size_loan(noi, A["bond_rate"], A["bond_amort"], A["bond_min_dscr"], tdc_af)
    lihtc = tdc_af * A["lihtc_equity_pct_tdc"]
    subsidy = units * A["city_subsidy_per_unit"]
    gap = tdc_af - (bonds + lihtc + subsidy)
    paths.append({"path": "ella", "label": "ELLA / 4% LIHTC all-affordable stack (HPD award required; profit = dev fee)",
                  "units_market": 0, "units_affordable": units, "tdc": int(tdc_af),
                  "loan": int(bonds), "equity": 0, "equity_pct": 0.0,
                  "noi": int(noi), "dscr": A["bond_min_dscr"] if bonds>0 else 0,
                  "cash_flow": 0, "coc_pct": None, "dev_fee": int(dev_fee),
                  "funding_gap": int(gap), "feasible": gap <= 0 and noi > 0,
                  "program_dependent": True})

    market = [p for p in paths if not p.get("program_dependent")]
    market_feas = [p for p in market if p["feasible"]]
    ella_feas = [p for p in paths if p["feasible"] and p.get("program_dependent")]
    # best market path = highest walk-away land (what you can pay), tie-break on cash flow
    best_mkt = max(market, key=lambda p: (p["walk_away_land"], p.get("cash_flow", 0)))
    best = best_mkt if market_feas else (ella_feas[0] if ella_feas else None)
    tier = "market" if market_feas else ("program" if ella_feas else "none")
    return {"units_base": units, "paths": paths,
            "best_path": best["path"] if best else best_mkt["path"],
            "best_label": best["label"] if best else best_mkt["label"],
            "tier": tier, "any_feasible": bool(best),
            "walk_away_land": best_mkt["walk_away_land"],
            "walk_away_land_institutional": best_mkt["walk_away_land_institutional"],
            "best_market_path": best_mkt["path"],
            "market_equity": (min(p["equity"] for p in market_feas) if market_feas else best_mkt["equity"])}

def action_for(est_land, walk_away, city_owned=False, rfp_eligible=False, program_ok=False):
    """Translate underwriting into a next step.
    offer      walk-away >= estimated land: make an offer at/under est land, cap at walk-away
    negotiate  walk-away within 70-100% of est land: offer at walk-away, needs a motivated seller
    program    only the subsidy stack works: pursue only with an HPD-financed JV
    rfp        city-owned by a disposing agency: track for HPD/EDC RFP or Fast Track
    pass       nothing works at this basis"""
    if city_owned:
        return {"action": "rfp" if rfp_eligible else "pass",
                "max_offer": 0, "headroom": None,
                "why": "City-owned: disposition by RFP/Fast Track, not private sale" if rfp_eligible
                       else "Government-owned by a non-disposing agency: not acquirable"}
    if est_land <= 0:
        return {"action": "pass", "max_offer": 0, "headroom": None, "why": "no land basis"}
    head = walk_away / est_land
    if head >= 1.0:
        return {"action": "offer", "max_offer": int(min(walk_away, est_land*1.10)), "headroom": round(head,2),
                "why": f"pencils at comps; walk-away is {head:.0%} of est. land"}
    if head >= 0.70:
        return {"action": "negotiate", "max_offer": int(walk_away), "headroom": round(head,2),
                "why": f"needs land at {head:.0%} of comps; only with a motivated seller"}
    if program_ok:
        return {"action": "program", "max_offer": int(walk_away), "headroom": round(head,2),
                "why": "only the HPD subsidy stack works at this land price"}
    return {"action": "pass", "max_offer": int(walk_away), "headroom": round(head,2),
            "why": f"walk-away is {head:.0%} of est. land; no path clears {ASSUMPTIONS['target_yoc']*100:.2f}% yield on cost"}
