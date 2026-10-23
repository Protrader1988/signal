"""
BOUNTY — suburban residual land value (NY suburbs · CT · NJ). v1, Oct 2026.

The suburban boards used to show "est. units at 20/acre" and an assessed value.
That is a list, not a deal. This module turns each parcel into a next action:

  realistic units   = acres x a density the town will actually approve for
                      for-sale townhomes / small multifamily (not 20/acre)
  revenue           = units x published sale price per unit for that market
  all-in build cost = units x (townhome shell + site/infra + soft/financing)
  max land offer    = revenue x (1 - selling cost - required profit) - build cost
  headroom          = max offer / asking proxy (full market value from the roll;
                      NJ: assessed value, flagged, because NJ ratios vary by town)
  action            = offer (headroom >= 1), negotiate (0.7-1.0), pass (< 0.7)

Every number here is a published, editable assumption. Sale prices are 2026
new-construction townhome / condo price points by submarket, deliberately set
toward the low end so a "offer" label survives a bad month. Verify zoning,
sewer and wetlands at town hall before any offer; the point of this model is to
tell you which 20 parcels are worth that phone call.
"""

SALE_PRICE_PER_UNIT = {
    # NY suburbs — new-build townhome / small-condo price points (low end), by municipality tier
    "NYS": {
        "default": 600_000,
        "tier": {
            # south Westchester / Sound Shore — strongest
            "SCARSDALE": 1_150_000, "RYE": 1_050_000, "LARCHMONT": 1_000_000, "MAMARONECK": 850_000,
            "BRONXVILLE": 1_050_000, "PELHAM": 850_000, "HARRISON": 900_000, "RYE BROOK": 850_000,
            "EASTCHESTER": 800_000, "NEW ROCHELLE": 700_000, "WHITE PLAINS": 750_000, "GREENBURGH": 750_000,
            "DOBBS FERRY": 850_000, "HASTINGS-ON-HUDSON": 850_000, "IRVINGTON": 900_000, "TARRYTOWN": 800_000,
            "SLEEPY HOLLOW": 750_000, "ARDSLEY": 800_000, "MOUNT PLEASANT": 750_000, "PLEASANTVILLE": 800_000,
            "BRIARCLIFF MANOR": 900_000, "OSSINING": 600_000, "CROTON-ON-HUDSON": 700_000,
            "YONKERS": 600_000, "MOUNT VERNON": 525_000, "PORT CHESTER": 650_000, "PEEKSKILL": 525_000,
            "CORTLANDT": 600_000, "YORKTOWN": 650_000, "SOMERS": 650_000, "BEDFORD": 850_000,
            "NEW CASTLE": 900_000, "NORTH CASTLE": 900_000, "LEWISBORO": 650_000, "NORTH SALEM": 650_000,
            "MOUNT KISCO": 650_000, "MOUNT KISCO/T": 650_000,
            # Putnam
            "CARMEL": 525_000, "SOUTHEAST": 525_000, "BREWSTER": 500_000, "KENT": 475_000,
            "PATTERSON": 475_000, "PUTNAM VALLEY": 500_000, "PHILIPSTOWN": 650_000, "COLD SPRING": 700_000,
            # Dutchess
            "BEACON": 550_000, "FISHKILL": 500_000, "WAPPINGER": 475_000, "EAST FISHKILL": 500_000,
            "POUGHKEEPSIE": 425_000, "HYDE PARK": 425_000, "LAGRANGE": 475_000, "PLEASANT VALLEY": 425_000,
            "RHINEBECK": 650_000, "RED HOOK": 500_000, "PAWLING": 475_000, "DOVER": 375_000,
        },
    },
    # CT Gold Coast — 8-30g set-aside projects sell/lease market units at a premium, 30% deed-restricted
    "CT": {
        "default": 650_000,
        "tier": {"GREENWICH": 1_300_000, "DARIEN": 1_150_000, "NEW CANAAN": 1_100_000, "WESTPORT": 1_100_000,
                 "RIDGEFIELD": 800_000, "STAMFORD": 700_000, "NEW HAVEN": 450_000},
        "set_aside_pct": 0.30, "set_aside_price_factor": 0.45,   # 8-30g affordable units at ~45% of market price
    },
    # NJ Bergen / Hudson — condo / townhome price points (low end)
    "NJ": {
        "default": 650_000,
        "tier": {"EDGEWATER": 850_000, "FORT LEE": 800_000, "ENGLEWOOD CLIFFS": 900_000, "CLIFFSIDE PARK": 700_000,
                 "WEEHAWKEN": 850_000, "HOBOKEN": 1_000_000, "JERSEY CITY": 800_000, "ENGLEWOOD": 650_000,
                 "LEONIA": 650_000, "TENAFLY": 850_000, "PALISADES PARK": 650_000, "FAIRVIEW": 550_000,
                 "RIDGEFIELD": 600_000, "RIDGEFIELD PARK": 550_000, "HACKENSACK": 550_000, "TEANECK": 600_000,
                 "BOGOTA": 500_000, "LITTLE FERRY": 500_000, "NORTH BERGEN": 600_000, "WEST NEW YORK": 650_000,
                 "UNION CITY": 600_000, "GUTTENBERG": 650_000, "BERGENFIELD": 550_000},
    },
}

# Density the town will plausibly approve for a townhome / small-multifamily project (units/acre).
DENSITY = {
    "NYS": {"default": 8,
            "tier": {"YONKERS": 16, "MOUNT VERNON": 16, "NEW ROCHELLE": 16, "WHITE PLAINS": 16, "PEEKSKILL": 14,
                     "PORT CHESTER": 14, "OSSINING": 12, "TARRYTOWN": 12, "SLEEPY HOLLOW": 12, "MAMARONECK": 12,
                     "HARRISON": 10, "POUGHKEEPSIE": 14, "BEACON": 12, "MOUNT KISCO": 12, "DOBBS FERRY": 12,
                     "HASTINGS-ON-HUDSON": 12, "PELHAM": 12, "LARCHMONT": 12, "BRONXVILLE": 12, "RYE": 8,
                     "SCARSDALE": 6, "BEDFORD": 4, "NEW CASTLE": 4, "NORTH CASTLE": 4, "LEWISBORO": 4,
                     "NORTH SALEM": 4, "SOMERS": 6, "YORKTOWN": 6, "CORTLANDT": 6, "PUTNAM VALLEY": 4,
                     "PHILIPSTOWN": 4, "KENT": 4, "PATTERSON": 4, "CARMEL": 6, "SOUTHEAST": 6}},
    # CT: 8-30g lets a 30%-set-aside project override zoning, but backcountry Greenwich/Darien/New Canaan
    # has no sewer (septic caps density) and multi-year appeals. Densities below are what has actually
    # been approved or settled in those towns, not the statute's ceiling.
    "CT":  {"default": 6, "with_8_30g": 10,
            "tier": {"STAMFORD": 20, "NEW HAVEN": 20, "GREENWICH": 6, "DARIEN": 8, "NEW CANAAN": 8,
                     "WESTPORT": 8, "RIDGEFIELD": 8}},
    "NJ":  {"default": 20, "cap": 40},
}

# Land floors ($/acre). Assessment-roll "market value" on vacant land is often a fraction of what
# an owner will take (Greenwich backcountry at $250k/acre, a White Plains lot at $35k). The asking
# proxy is max(roll value, floor x acres) so headroom is measured against a price a seller might say yes to.
LAND_FLOOR_PER_ACRE = {
    "NYS": {"default": 250_000,
            "tier": {"SCARSDALE": 1_500_000, "RYE": 1_200_000, "LARCHMONT": 1_200_000, "BRONXVILLE": 1_500_000,
                     "MAMARONECK": 900_000, "PELHAM": 900_000, "HARRISON": 800_000, "RYE BROOK": 800_000,
                     "EASTCHESTER": 800_000, "NEW ROCHELLE": 800_000, "WHITE PLAINS": 1_200_000, "YONKERS": 700_000,
                     "MOUNT VERNON": 600_000, "GREENBURGH": 700_000, "DOBBS FERRY": 800_000, "HASTINGS-ON-HUDSON": 800_000,
                     "IRVINGTON": 800_000, "TARRYTOWN": 800_000, "SLEEPY HOLLOW": 700_000, "ARDSLEY": 700_000,
                     "PORT CHESTER": 700_000, "MOUNT PLEASANT": 500_000, "PLEASANTVILLE": 600_000, "BRIARCLIFF MANOR": 600_000,
                     "OSSINING": 450_000, "CROTON-ON-HUDSON": 400_000, "PEEKSKILL": 400_000, "CORTLANDT": 300_000,
                     "YORKTOWN": 300_000, "SOMERS": 300_000, "BEDFORD": 400_000, "NEW CASTLE": 450_000, "NORTH CASTLE": 450_000,
                     "MOUNT KISCO": 500_000, "LEWISBORO": 250_000, "NORTH SALEM": 250_000,
                     "CARMEL": 200_000, "SOUTHEAST": 200_000, "BREWSTER": 250_000, "KENT": 150_000, "PATTERSON": 150_000,
                     "PUTNAM VALLEY": 150_000, "PHILIPSTOWN": 250_000, "COLD SPRING": 400_000,
                     "BEACON": 350_000, "FISHKILL": 250_000, "WAPPINGER": 200_000, "EAST FISHKILL": 200_000,
                     "POUGHKEEPSIE": 250_000, "HYDE PARK": 150_000, "LAGRANGE": 150_000, "RHINEBECK": 250_000,
                     "RED HOOK": 150_000, "PAWLING": 150_000, "DOVER": 100_000, "PLEASANT VALLEY": 125_000}},
    "CT":  {"default": 400_000,
            "tier": {"GREENWICH": 1_200_000, "DARIEN": 1_200_000, "NEW CANAAN": 1_000_000, "WESTPORT": 1_000_000,
                     "RIDGEFIELD": 450_000, "STAMFORD": 1_500_000, "NEW HAVEN": 800_000}},
    "NJ":  {"default": 1_200_000,
            "tier": {"EDGEWATER": 3_500_000, "FORT LEE": 3_000_000, "WEEHAWKEN": 4_000_000, "HOBOKEN": 8_000_000,
                     "JERSEY CITY": 5_000_000, "WEST NEW YORK": 3_500_000, "UNION CITY": 3_000_000, "GUTTENBERG": 3_500_000,
                     "NORTH BERGEN": 2_500_000, "CLIFFSIDE PARK": 2_500_000, "ENGLEWOOD CLIFFS": 2_000_000,
                     "PALISADES PARK": 2_000_000, "FAIRVIEW": 1_800_000, "RIDGEFIELD": 1_500_000, "LEONIA": 1_500_000,
                     "ENGLEWOOD": 1_200_000, "HACKENSACK": 1_200_000, "TEANECK": 1_200_000, "BOGOTA": 1_000_000}},
}
MAX_UNITS = {"NYS": 60, "CT": 40, "NJ": 150}   # above this the project is a different business (and a different approval fight)

BUILD = {
    "townhome_sf": 1_800, "hard_psf": 260,          # owner-builder basis, wood frame
    "site_infra_per_unit": 50_000,                 # roads, utilities, stormwater, landscaping
    "soft_fin_pct": 0.18,                          # A/E, approvals, legal, interest, fees
    "selling_cost_pct": 0.06, "profit_pct": 0.15,  # broker + closing; required developer margin on revenue
    "multifamily_hard_psf": 290, "multifamily_unit_sf": 900,  # NJ / dense towns: mid-rise rental or condo
    "note": "Owner-builder costs (principal acts as GC). Third-party GC adds ~15-18% to hard cost.",
}

def _tier(table, region, town):
    t = (town or "").upper().strip()
    reg = table.get(region, {})
    for k, v in reg.get("tier", {}).items():
        if k in t: return v
    return reg.get("default")

def density(region, town, eight_30g=False):
    d = DENSITY.get(region, {})
    tiered = _tier(DENSITY, region, town)
    if tiered: return tiered
    base = d.get("default", 8)
    if region == "CT" and eight_30g:
        base = max(base, d.get("with_8_30g", 10))
    return base

def residual(region, town, acres, asking_proxy, eight_30g=False, density_override=None):
    """Return the residual-land dict for a parcel."""
    dens = density_override or density(region, town, eight_30g)
    if region == "NJ": dens = min(dens, DENSITY["NJ"]["cap"])
    units = min(int(acres * dens), MAX_UNITS.get(region, 60))
    if units < 1:
        return None
    floor = (_tier(LAND_FLOOR_PER_ACRE, region, town) or LAND_FLOOR_PER_ACRE[region]["default"]) * acres
    roll = asking_proxy or 0
    asking_proxy = max(roll, floor)
    asking_source = "roll market value" if roll >= floor else "land floor ($/acre published assumption; roll value is below it)"
    price = _tier(SALE_PRICE_PER_UNIT, region, town) or SALE_PRICE_PER_UNIT[region]["default"]
    dense = dens >= 14
    if dense:
        unit_sf, hard_psf = BUILD["multifamily_unit_sf"], BUILD["multifamily_hard_psf"]
        price = int(price * 0.80)   # condo/flat price point vs townhome
    else:
        unit_sf, hard_psf = BUILD["townhome_sf"], BUILD["hard_psf"]
    revenue = units * price
    if region == "CT" and eight_30g:
        sa = SALE_PRICE_PER_UNIT["CT"]["set_aside_pct"]; f = SALE_PRICE_PER_UNIT["CT"]["set_aside_price_factor"]
        revenue = units * price * ((1 - sa) + sa * f)
    hard = units * unit_sf * hard_psf
    site = units * BUILD["site_infra_per_unit"]
    soft = (hard + site) * BUILD["soft_fin_pct"]
    build = hard + site + soft
    net_rev = revenue * (1 - BUILD["selling_cost_pct"] - BUILD["profit_pct"])
    max_land = net_rev - build
    head = (max_land / asking_proxy) if asking_proxy and asking_proxy > 0 else None
    if max_land <= 0:
        action, why = "pass", "build cost exceeds net sale revenue at this density"
    elif units >= MAX_UNITS.get(region, 60) and region == "CT":
        action, why = ("negotiate", f"capped at {units} units; an 8-30g project this size is a multi-year approval fight — only with sewer and a town-center site") if head and head >= 0.7 else ("pass", "capped size; residual below floor")
    elif head is None:
        action, why = "negotiate", "no asking proxy on the roll; residual is positive"
    elif head >= 1.0:
        action, why = "offer", f"residual land value is {head:.0%} of roll market value"
    elif head >= 0.70:
        action, why = "negotiate", f"residual is {head:.0%} of roll value; needs a motivated seller"
    else:
        action, why = "pass", f"residual is {head:.0%} of roll value"
    return {"realistic_density_uac": dens, "realistic_units": units, "sale_price_per_unit": int(price),
            "asking_proxy": int(asking_proxy), "asking_source": asking_source,
            "revenue": int(revenue), "build_cost": int(build), "max_offer": int(max(0, max_land)),
            "headroom": (round(head, 2) if head is not None else None),
            "action": action, "action_why": why,
            "profit_at_max_offer": int(revenue * BUILD["profit_pct"]),
            "product": "mid-rise condo/rental" if dense else "townhomes"}

ASSUMPTIONS = {"sale_price_per_unit": SALE_PRICE_PER_UNIT, "density": DENSITY, "build": BUILD,
               "land_floor_per_acre": LAND_FLOOR_PER_ACRE, "max_units": MAX_UNITS,
               "note": "Residual land value = net sale revenue (after 6% selling cost and 15% profit) minus owner-builder construction. Max offer is the land price at which the project still earns the target margin. Verify zoning, sewer, wetlands before offering."}
