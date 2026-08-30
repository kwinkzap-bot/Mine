from typing import Optional, Tuple, Union

class CPRService:
    @staticmethod
    def calculate_cpr(high: Union[float, int], low: Union[float, int], close: Union[float, int]) -> Tuple[float, float, float]:
        """Calculates Central Pivot Range (PP, BC, TC)."""
        pp = (high + low + close) / 3
        bc = (high + low) / 2
        tc = (2 * pp) - bc
        
        # Ensure BC/TC are returned as lower/upper for consistency, although the formulas define them
        lower = min(bc, tc)
        upper = max(bc, tc)
        
        return pp, lower, upper # pp, BC, TC (where BC/TC are lower/upper bounds)

# ── CPR width ────────────────────────────────────────────────────────────
# Width = |TC - BC| / close * 100. TC - BC reduces to (2C - H - L)/3, so
# |TC - BC| <= (H - L)/3 — the cap is only reached when the period closes
# exactly on its high or low. An index ranging 0.5-1.2% therefore cannot
# produce a width above ~0.40%, which is why absolute stock-sized cut-offs
# (< 0.5% = Narrow) classified every index day as Narrow.
#
# Comparing a period's CPR to the average of the preceding ones is the usual
# Pivot Boss reading and self-scales: an index, a futures contract and a
# 3%-a-day midcap are each measured against their own normal. Lives here
# rather than in routes/api.py so the scanners can share it — see
# filters/narrow_cpr_scanner.py and tests/test_cpr_width_classification.py.
CPR_WIDTH_NARROW_RATIO = 0.8  # < 0.8x its own average -> Narrow
CPR_WIDTH_WIDE_RATIO   = 1.2  # > 1.2x -> Wide; between -> Medium

# Absolute fallback, used only when there is too little history to average — a
# newly listed contract, or a provider returning a short series. Deliberately
# scaled to what the metric can actually reach (see the ceiling above) rather
# than the stock-sized numbers this replaced.
CPR_WIDTH_NARROW_MAX = 0.15  # < 0.15% -> Narrow
CPR_WIDTH_MEDIUM_MAX = 0.30  # 0.15-0.30% -> Medium; >= 0.30% -> Wide


def cpr_width_pct(high: Union[float, int], low: Union[float, int],
                  close: Union[float, int]) -> Optional[float]:
    """CPR width as a percentage of the close, for one period's OHLC."""
    if not close:
        return None
    _pp, bc, tc = CPRService.calculate_cpr(high, low, close)
    return abs(tc - bc) / close * 100


def classify_cpr_width(width_pct: float, avg_width_pct: Optional[float] = None) -> str:
    """Narrow / Medium / Wide for one CPR width.

    With `avg_width_pct` (the instrument's own recent average) the call is
    relative — the only reading that means anything across instruments whose CPR
    widths differ by an order of magnitude. Without it, falls back to the
    absolute scale, which only happens when history is too short to average.
    """
    if avg_width_pct and avg_width_pct > 0:
        ratio = width_pct / avg_width_pct
        if ratio < CPR_WIDTH_NARROW_RATIO:
            return 'Narrow'
        if ratio > CPR_WIDTH_WIDE_RATIO:
            return 'Wide'
        return 'Medium'
    if width_pct < CPR_WIDTH_NARROW_MAX:
        return 'Narrow'
    if width_pct < CPR_WIDTH_MEDIUM_MAX:
        return 'Medium'
    return 'Wide'
