"""The CPR sheet's trade rule: the trade columns filled by rule, for a session that has none.

The rule is the one the hand-traded January rows follow, as read back
through the Trend page's reasons: trade the REJECTION, not the break.

    Zone       the daily CPR and the weekly (1-hour chart) CPR, read as one
               band when they overlap or nearly touch (cprs_merged); the
               daily one alone otherwise. Where the 09:15 close sits
               against it decides which setups are looked for.

    Open BELOW the CPR
      SELL     a strong red candle that pokes up into the DAILY CPR (its
               high at or past BC — reaching only the weekly band merged
               with it is not a touch) and closes back below the band.
               A BIG one (over SIGNAL_MAX_PCT) is not entered off: wait
               for a small candle back into the CPR that closes in its
               lower half and not above the CPR, and SELL under that — SL
               over its high, target
               the nearer of S1 / PDL (1 Sep 2026: 09:15's 55-pt
               rejection; 09:30 small candle to 24,077 -> SELL 24,057, SL
               24,078, target S1 24,006). Otherwise
               entry a buffer under that candle's low; SL a
               buffer over the first pivot above the candle's high (the
               candle's high itself when it already cleared the pivots).
      BUY      a candle that dips to PDL or S1 — its low at or through the
               level, not merely near it — and closes back above it:
               strong, or closing on its high after the dip (16 Feb 2026),
               and no bigger than SIGNAL_MAX_PCT of price (14 Jul 2026: a
               68-pt 09:15 candle off S1 is not entered).
               Entry 1 pt over its high; SL 0.02% under the lowest level it
               rejected. Target: the CPR, however far — or 1:2 when the
               CPR is nearer than the risk ("CPR is very close to the
               entry, so 1:2").
    Open ABOVE the CPR — the mirror: BUY the rejection from the CPR,
               SELL the rejection from PDH / R1.
    WEEKLY CPR ON THE PDH/R1 BOX — the weekly CPR lies over the PDH/R1 box
               (or under the PDL/S1 box) so the two make one big zone, and
               the 09:15 candle opens inside it. A strong red candle that
               reaches PDH and closes through it is the SELL, however big
               it is: under its low, SL over its own high, target the
               nearest of the CPR lines / PDL / 1:2 that lies at least
               NEAR_TARGET_X x the risk away (13 Jan 2026: weekly
               25,788-25,998 on PDH 25,813 / R1 25,911, open 25,897; 09:25
               strong red 25,837 -> 25,763 through PDH -> SELL 25,759, SL
               25,839, target the CPR BC 25,643). Mirror at PDL/S1.
    FAR BOXES — a narrow CPR with both boxes at least FAR_BOX_PCT of price
               (and FAR_BOX_RATIO x the CPR's height) away: the CPR is a
               thin line in open space, not a level to fade. The CPR
               rejection is off; the day trades only a break of a box or
               a rejection from one (14 Jan 2026: CPR 25,739-25,752, 13
               pts, boxes 135-148 pts away; the 09:40 fade of the CPR
               is not taken).
    ONE ZONE — the daily CPR, the weekly CPR and a box (PDH/R1 or PDL/S1)
               all interlink into one continuous band and the 09:15
               candle closes inside it: an entry INSIDE that zone has
               nothing to trade against and is refused, whatever the
               setup; the day's trade is the BREAK OUT of the zone — the
               first close beyond it by RETEST_UNTIL, BUY over that
               candle (SL under its low) or SELL under it (SL over its
               high), target the next level beyond or 1:2. An entry from
               another setup that already sits outside the zone stands
               (4 Aug 2025: the 09:30 cross-candle BUY at 24,671 inside
               24,597-24,784, no trade; 6 Mar 2025: zone 22,212-22,466,
               the 09:20 PDH-rejection SELL inside it refused, 13:05
               closed 22,477 over R1 -> BUY 22,479, SL 22,456; 3 Mar
               2025: the SELL at 22,151 under the zone stands).
    INTERLINKED CPRs (the daily and weekly CPRs overlap into one band):
               the rejection candle must be SMALL (at most SIGNAL_MAX_PCT
               of price) — a big candle into the band is not entered —
               and may come as late as LATE_SETUP_UNTIL. It reaches the
               daily CPR (within TOUCH_PCT of TC) and closes back over the
               whole band -> BUY over its high. The stop sits under its
               low — or under the session VWAP when that runs just beneath
               the low (within VWAP_STOP_PCT), the line the candle is
               really leaning on. Target the next level or 1:2 (6 Apr
               2026: CPRs 22,482-22,636 and 22,562-22,663; 09:30's 88-pt
               rejection skipped; 11:50 small candle low 22,645, closed
               22,668 -> BUY 22,677, SL 22,636 = VWAP, target PDH 22,782).
               Mirror below the band with a small red candle.
    INSIDE the CPR, WICKED BOTH WAYS: the 09:15 candle opens and closes
               inside the CPR but its high is over TC and its low under BC
               — the CPR lines are spent, and the day is decided at the
               box. The first close over the PDH/R1 box is the break
               candle; the candle after it decides: through the break
               candle's high -> BUY over it (SL under the break candle's
               low, target the next level or 1:2); a close back under the
               box is the failure -> SELL under that candle, SL over the
               break candle's high, target the nearest of the CPR / PDL /
               1:2 (26 May 2026: 10:35 closed 24,085 over R1 24,083; 10:40
               closed 24,073 back under -> SELL 24,070, SL 24,091, target
               1:2 24,028). Mirror at the PDL/S1 box. A continuation with
               a VIRGIN CPR's near edge inside its risk is refused — that
               is support (resistance) too close — and the day then looks
               the other way: the first close back over the whole lower
               box (under the whole upper box) is the trade, stop under
               the break's low (over its high), target the CPR (29 Jan
               2026: 09:55 broke under PDL/S1 with 28 Jan's virgin CPR
               25,090-25,147 27 pts below — no sell; 10:55 closed 25,234
               back over S1 -> BUY 25,242, SL 25,158, target TC 25,322).
    INSIDE the CPR at the 09:15 close: the first strong candle closing out
               of it is the entry — over its high (under its low), SL at the
               09:15 candle's other end, target the nearer of PDH/R1
               (PDL/S1). 17 Feb 2026: opened inside the weekly CPR above
               the daily one, 09:15 closed inside the daily CPR, 09:40
               closed above it -> BUY 25,647, SL 25,591, target PDH 25,697.
               When the 09:15 candle is BIG (over BIG_CANDLE_PCT) its far
               end is no stop, so the break is not entered on the breakout
               candle: any close out of the CPR is the break, and the next
               candle that comes back to the line — the cross candle — is
               the entry, over its high (under its low), SL under its low
               (over its high) (7 Apr 2026: 125-pt 09:15; 10:00 closed
               22,914 over TC 22,902; 10:05 cross candle 22,929-22,894 ->
               BUY 22,930, SL 22,892). A close back inside voids the break.
               NO ROOM: on a WIDE CPR day (the sheet's relative reading)
               the break is not taken when the next box — PDH/R1 above,
               PDL/S1 below — is closer to the entry than the risk: price
               is boxed in between the CPR and the box (4 Sep 2026: BUY
               23,964 with R1 23,975 11 pts away against a 62-pt stop —
               the day never left that space — no trade).
    BETWEEN the two CPRs (not merged; above the weekly one, under the
               daily one): the daily CPR is the lid — BUY only on a strong
               close above it, entry over that candle's high, SL under the
               CPR. Nothing else counts (4 Feb 2026: never closed above
               25,815-25,991 -> no trade). Mirror when the daily CPR is the
               lower one: SELL on a close below it.
    NARROW CPR (under 0.13% of price — "Narrow", "Small box") with the
               open clear of both CPRs: a trend day is expected. If the
               09:15 candle rejects the other side (wick >= half its range,
               close at the far end), the first strong candle in the trend's
               direction is the entry: 1 pt over its high (under its low),
               stop 0.025% under its low, target 1:2 (10 Feb 2026: 09:30
               bull candle -> BUY 25,945, SL 25,903, target 26,029; SL).
               While PDH/R1 still sits above the 09:15 close, that is the
               wall: the strong candle must close through it, and a candle
               turned back from it (bigger wick on that side, close under
               it) is instead a SELL under its low, SL over the high so
               far, target the first CPR line (11 Feb 2026: 09:20 candle
               rejected from R1 -> SELL 25,974, SL 26,011, target 25,933).
               When the 09:15 candle is itself the strong break through
               PDL/S1 (PDH/R1), that is the entry, on whichever side of
               the CPRs price sits: under its low — or under a level lying
               just beneath it — SL over its high, target 1:2 (12 Feb 2026:
               SELL 25,844 under S2, SL 25,909; squared off at 15:15).
    09:15 BREAK WITH A LONG WICK — any CPR width. The 09:15 candle opens
               inside the PDL/S1 box and closes under it, but is not strong:
               a lower wick of half its range or more says the level was
               bought once already. Not an entry by itself — wait for the
               09:20 candle. A small red one (half the 09:15 range at most)
               that holds inside that wick, under the 09:15 close and over
               its low, confirms: SELL under its low, SL over the 09:15
               high, target 1:2 (7 Sept 2026: 09:15 O 23,883 between PDL
               23,896 and S1 23,860, C 23,859, low 23,819; 09:20 red 23,859
               -> 23,838 -> SELL 23,837, SL 23,892, target 23,727). Mirror
               for a green 09:15 through PDH/R1 with a long upper wick.
    09:15 BOX REJECTION — the 09:15 candle itself, strong (body >= 60%),
               opens in or at the PDL/S1 box, touches it (PDL, S1 or the
               Cam S3 inside it) and closes back over the box: BUY over
               its high, SL under its low, target Cam R3 when it sits in
               or at the CPR (the zone's top), else the CPR's far line —
               1:2 if too near. It stands even inside a one-zone day: it
               trades the zone's own edges (18 Jun 2025: O 24,788 in
               24,784-24,814, L 24,777 on S1, C 24,830 -> BUY 24,839, SL
               24,775, target Cam R3 24,900). Mirror off PDH/R1.
    09:15 BIG BREAK — any CPR width. The 09:15 candle opens over PDL/S1
               (at or above the box's top) and closes under both, but is
               too big to trade off (range over BIG_CANDLE_PCT of price):
               a stop over its high is more risk than the move is worth,
               so its low is NOT the entry. The 09:20 candle decides: a
               small one (half the 09:15 range at most) holding under
               PDL/S1 confirms the break -> SELL under its low, SL over its
               own high, target 1:2 — or yesterday's virgin CPR when that
               sits between, the nearer support (31 Aug 2026: 09:15 O
               24,118 over S1 24,106 / PDL 24,077, C 24,065, a 90-pt
               candle; 09:20 small 24,085-24,058 -> SELL 24,056, SL
               24,087, target 1:2 23,994 = S3). Mirror for a big green
               09:15 through PDH/R1 with a small 09:20 holding above.
               The same when the big 09:15 candle STRADDLES the box —
               opens inside PDL/S1, trades both over and under it, and
               closes back inside: nothing is decided yet, so a small red
               09:20 closing under the box is the entry, under its low,
               SL over its high, target 1:2 or a nearer virgin CPR (19 Aug
               2026: 09:15 O 24,152 in PDL/S1 24,117-24,155, H 24,173,
               L 24,103, C 24,119; 09:20 red 24,125-24,105, C 24,106 ->
               SELL 24,104, SL 24,127, target 1:2 24,058). Mirror above.
    09:15 OUT OF THE PDH/R1 BOX — narrow CPR, open above both CPRs, the
               09:15 candle opens inside the PDH/R1 box and closes above
               it. Two ways from there, whichever comes first: a bar
               breaking the 09:15 high is a BUY over it (SL under the
               09:15 low, target the next level or 1:2); otherwise, once a
               close has fallen back under the box, the first red candle
               that climbs back to the box and closes under it again is
               the SELL — under its low, SL over its own high, target the
               CPR (TC), and PDL instead if the bar reaching the CPR is a
               strong close through it (24 Aug 2026: 09:15 O 24,285 in
               PDH/R1 24,284-24,288, C 24,303; 09:30 closed 24,277 under
               the box; 09:40 red candle to 24,286, closed 24,282 -> SELL
               24,273, SL 24,288, target TC 24,250 -> PDL 24,207 on the
               break). Mirror under the PDL/S1 box. The continuation is
               not joined when the 09:15 candle is over BIG_CANDLE_PCT or
               the breaking candle over SIGNAL_MAX_PCT — two big candles
               are a spike to fade — and the day then waits for the
               reversal and the rejection from the box, until
               LATE_SETUP_UNTIL (23 Feb 2026: 81-pt 09:15, 44-pt 09:20;
               10:40 closed under the box; 11:20 red candle back to it
               -> SELL 25,659, SL 25,680, target TC 25,555).
    BIG 09:15 DOWN TO THE BOX — the retest. A big red 09:15 candle (over
               BIG_CANDLE_PCT) that opens above the PDL/S1 box (inside the
               CPR, say) and closes AT the box — inside it or a touch
               either side — has no sell entry: the candle is too big to
               trade off, and the 09:20 candle is not a small red close
               under the box (that would be the case above). The trade is
               the other way, and not at the breakout: wait for a close
               above the box, then for the RETEST — a small green candle
               (half the 09:15 range at most) that dips back to the box's
               top and closes above it near its own high — and BUY over
               that candle's high, SL under its low, target the CPR (BC),
               by RETEST_UNTIL. The box must be LOST first — a close under
               it — for a later close over it to count as the break: the
               trade is the reclaim of a level that gave way, not a bounce.
               The break is a close clear of the box (a TOUCH_PCT past it,
               not a graze), and a close back under the box before the
               retest voids it (3 Jun 2026: 09:15 O 23,416 in the
               CPR, C 23,299 on S1 23,289 / PDL 23,229, a 160-pt candle;
               12:35 closed over the box; 13:10 small green retest
               23,298-23,276 closed on its high -> BUY 23,300, SL 23,274,
               target BC 23,393). Mirror for a big green 09:15 up to the
               PDH/R1 box: SELL under the small red retest, target TC.
    GAP open  past both R1 and PDH (or both S1 and PDL): wait for price to
               come back to that R1/PDH zone. A strong close back above it
               is the reversal -> BUY; a close under it is the breakdown ->
               SELL; never reached -> no trade (3 Feb 2026: open 26,308,
               R1/PDH 25,108-25,238 never seen). Gap-down is the mirror.
               While the zone is still out of reach, a BASE is the other
               way in: after the 09:15 candle, two or more small candles
               (none strong, none bigger than the 09:15 one) with at least
               two of them red, then the first strong green candle -> BUY
               over its high AND the 09:15 high (price must clear the
               first candle), SL under its low, target the near edge of
               the S1/PDL zone — the gap fill (11 Sept 2026: gap-down open
               23,270, seven small candles 09:20-09:50, six red, 09:55
               strong green to 23,263 -> BUY 23,279 over the 09:15 high
               23,277, SL 23,243, target PDL 23,380).
               Gap-up is the mirror: a base under R1/PDH, small candles
               with two green, the first strong red -> SELL, target PDH/R1.
               When the zone reversal candle is BIG (over BIG_CANDLE_PCT)
               and Camarilla R3 sits inside the R1/PDH box, it is not
               entered over: that close over the box is the breakout, and
               the trade is the retest — a candle by RETEST_UNTIL whose
               low comes to Cam R3 and that closes back over the box ->
               BUY over its high, SL under Cam R3, target 1:2 (10 Mar
               2026: 10:15 reversed on a 64-pt candle; 12:45 dipped to
               24,143 by Cam R3 24,133 and closed 24,185 over R1 -> BUY
               24,186, SL 24,131, target 24,296). Mirror with Cam S3.

    VIRGIN CPR at PDH/R1 — the late rejection, by LATE_SETUP_UNTIL and
               the one setup that may follow a stopped-out trade. Narrow
               CPR (small box) only, and the rejection candle no bigger
               than SIGNAL_MAX_PCT of price (29 Jun 2026: wide CPR, big
               box, a 60-pt candle -> excluded). When
               yesterday's CPR was never touched yesterday (a virgin CPR)
               and sits on today's PDH/R1 box, a strong red candle that
               pokes into that stack and closes back under PDH/R1, with
               price above today's CPR, is a SELL under its low; SL over
               its own high; target the CPR's far line (BC) — and if the
               candle that reaches it is a strong red close through the
               CPR, the target moves on to PDL (1 Sept 2026: 31 Aug's CPR
               24,133-24,161 over PDH 24,129 / R1 24,142; 11:30 candle to
               24,143, closed 24,125 -> SELL 24,122, SL 24,145; 13:10
               broke the CPR -> target PDL 23,994, hit 13:50). Mirror: a
               virgin CPR under PDL/S1, a strong green rejection -> BUY,
               target TC, then PDH on a strong break up.
    Target     the next level in the trade's direction (PDL/S1/S2/S3/Cam S3
               below, PDH/R1/R2/R3/Cam R3 above). A target at the near
               edge of the PDL/S1 (PDH/R1) box moves to the Camarilla S3
               (R3) when that lies inside the box — price reaching the
               box's edge runs on to the Cam line (3 Mar 2025: PDL 22,105
               -> Cam S3 22,030). If that is further than
               FAR_TARGET_X times the risk, a 1:1 target is used instead —
               "245 points is very long, keep 1:1".
    Virgin CPR whatever the setup, a virgin CPR (an earlier session's CPR
    as target  no session since has traded through) standing between the
               entry and the target is the nearer support/resistance, and
               the target moves to its near edge — unless it is closer than
               NEAR_TARGET_X x the risk, which is not worth the trade
               (30 Apr 2026: SELL 23,929, 1:2 at 23,741, but 15 Apr's
               virgin CPR 23,732-23,806 in between -> target 23,806, hit
               11:15 at the day's low).
    VWAP ahead whatever the setup, the session VWAP lying between the
               setup candle and the entry — the candle on one side, the
               stop order on the other — means the trigger itself has to
               cross it: the entry is refused, and the day may
               still take the late virgin-CPR rejection (20 May 2026:
               gap-down, 10:00 reversal -> SELL 23,484 with the VWAP at
               23,487 — no trade).
    REVERSAL AFTER A STOP — a second trade: when the bar that stops the
               first trade is a strong candle the other way and a virgin
               CPR lies ahead of it within REVERSAL_MAX_RR risks, the
               reversal is traded over that candle's high (under its
               low), SL at its other end, target the virgin CPR's near
               edge — after the virgin-CPR late rejection has had its
               chance, and only up to LATE_SETUP_UNTIL (20 Jan 2025: the 09:20 SELL
               stopped by the 09:55 strong green -> BUY 23,237, SL
               23,204, target 17 Jan's virgin CPR 23,318).
    Stop cap   whatever the setup, a stop more than MAX_RISK points from
               the entry is halved — the level-based stop of a wide day is
               more than the trade is worth (28 Jan 2026: BUY 25,349, SL
               25,195 = 154 pts -> 25,272, 77 pts). The target stays.
    Window     the first qualifying candle up to SETUP_UNTIL; the entry must
               be traded by a later bar. Result is the session's bars:
               target or SL, whichever a later bar reaches first; neither
               by 15:15 -> squared off there ("EOD", P&L written in M).

2 Feb 2026 is the worked example: open 24,796 under the merged CPR
(24,886-25,015); the 09:55 candle spikes to 24,939 and closes 24,863 —
SELL under it at 24,845, SL over the pivot at 24,963, Cam S3 at 24,586 is
259 points away so the target is 1:1 at 24,727.

Pure: `propose(chart, bars)` takes the chart reading `cpr_backtest_service.analyse`
produced for the session and that session's 5-minute bars. Two callers:
scripts/propose_cpr_trades.py writes the proposal into the workbook, and
cpr_backtest_service.extend appends it to the Trend page's JSON when the
page's Update button asks for the sessions the sheet has not reached yet.
"""

import math

from trading_app.service import cpr_backtest_service as svc

BUFFER_PCT = 0.05        # entry / SL sit this far beyond the candle or pivot (~12 pts)
TOUCH_PCT = 0.05         # a candle within this of a level has touched it
FAR_TARGET_X = 1.5       # a level further than this x the risk -> 1:1 target instead
NEAR_TARGET_X = 0.6      # a level nearer than this x the risk -> 1:2 target instead
STRONG_BODY = 0.6        # the rejection candle must close with a body >= 60% of its range
GAP_MIN_PCT = 0.25       # an open this far past R1/PDH (S1/PDL) is a gap day, not an "above" day
NARROW_PCT = 0.13        # CPR under this % of price is Narrow on the sheet's scale -> a trend day is expected
WICK_MIN = 0.5           # the 09:15 candle's rejection wick must be at least half its range
INSIDE_MAX = 0.5         # the 09:20 confirmation candle is at most half the 09:15 range
TREND_SL_PCT = 0.025     # trend-day stop sits this close under the breakout candle (~6 pts)
TREND_RR = 2.0           # trend-day target is 1:2
BASE_MIN_BARS = 2        # a gap-day base is at least this many small candles after 09:15 ...
BASE_MIN_COUNTER = 2     # ... at least this many of them against the fill (red on a gap-down)
SETUP_UNTIL = '11:00'    # last bar that may be the rejection candle
LATE_SETUP_UNTIL = '13:00'   # ... the virgin-CPR rejection may come this late
RETEST_UNTIL = '14:00'       # ... the retest after a big 09:15 candle's box breaks may come this late
SIGNAL_MAX_PCT = 0.15    # a rejection candle bigger than this % of price is too large to trade off (29 Jun 2026: 60 pts)
BIG_CANDLE_PCT = 0.25    # a 09:15 candle this big (~60 pts) is entered off the 09:20 candle, not its own extreme (31 Aug 2026: 90 pts, 19 Aug: 70)
BOX_REJECT_MAX_PCT = 0.3 # ... but a 09:15 box REJECTION is traded off its own candle up to this size (18 Jun 2025: 61 pts; 20 Jan 2025: 89 pts, too big)
REVERSAL_MAX_RR = 3.0    # the reversal-after-stop's virgin-CPR target must lie within this many risks
MAX_RISK = 100.0         # a stop further than this from the entry is halved (28 Jan 2026: 154 pts -> 77)
VWAP_STOP_PCT = 0.1      # a VWAP within this % under the rejection candle's low is where its stop goes (6 Apr 2026)
FAR_BOX_PCT = 0.4        # a narrow CPR with both boxes at least this % of price away (and FAR_BOX_RATIO x its height) ...
FAR_BOX_RATIO = 5        # ... is too thin to trade against: entries come from the boxes only (14 Jan 2026)
RULE_TAG = 'Trade by rule'


def vwap_at(bars, k):
    """The session VWAP through bar k (typical price weighted by volume),
    or None without volumes."""
    pv = vol = 0.0
    for b in bars[:k + 1]:
        v = b.get('volume') or 0
        pv += (b['high'] + b['low'] + b['close']) / 3 * v
        vol += v
    return pv / vol if vol else None


def propose(chart, bars):
    """(trade, why, setup-bar index) from a session's bars, or (None, why, None)."""
    lv, w = chart['levels'], chart.get('weekly')
    named = svc._reason_ladder(lv, w, chart['first_candle'], chart['day']['open'])
    merged = bool(w and svc.cprs_merged(named))
    if merged:
        band_lo, band_hi = min(lv['bc'], w['bc']), max(lv['tc'], w['tc'])
        pivots = sorted({lv['pp'], w['pp']})
    else:
        band_lo, band_hi, pivots = lv['bc'], lv['tc'], [lv['pp']]

    # The stop hides behind the pivot beyond the rejection candle. Two
    # pivots within 0.1% are one zone, so the stop goes past the outer one
    # (2 Feb: P 24,946 and WP 24,952 -> SL 24,964); further apart, only the
    # first pivot beyond the candle counts, not the far one.
    px0 = bars[0]['close']
    one_zone = len(pivots) > 1 and pivots[-1] - pivots[0] <= px0 * 0.1 / 100

    def pivot_above(y):
        beyond = [p for p in pivots if p >= y]
        return (max(beyond) if one_zone else min(beyond)) if beyond else y

    def pivot_below(y):
        beyond = [p for p in pivots if p <= y]
        return (min(beyond) if one_zone else max(beyond)) if beyond else y
    c1 = bars[0]
    px = c1['close']
    buf, tol = px * BUFFER_PCT / 100, px * TOUCH_PCT / 100
    narrow = chart['width_pct'] < NARROW_PCT
    # A narrow CPR with the boxes far away on both sides: the CPR is a thin
    # line in open space, not a level to fade — the day's entries come from
    # the boxes only (a break of one, or a rejection from one).
    cpr_h = lv['tc'] - lv['bc']
    box_gap = min(lv['pdh'] - lv['tc'], lv['bc'] - lv['pdl'])
    far_boxes = narrow and box_gap >= px * FAR_BOX_PCT / 100 and cpr_h > 0 and box_gap >= FAR_BOX_RATIO * cpr_h
    rng1 = c1['high'] - c1['low']
    lo_wall, hi_wall = (min(lv['pdl'], lv['s1']), max(lv['pdl'], lv['s1'])), (min(lv['pdh'], lv['r1']), max(lv['pdh'], lv['r1']))

    # A break by a candle too BIG to trade off its own low, before any
    # other reading of the 09:15 candle: it opened over PDL/S1 and closed under both, but its range
    # is past BIG_CANDLE_PCT, so a stop over its high is more risk than
    # the move is worth. The 09:20 candle decides again: a small one
    # holding under PDL/S1 is the entry — SELL under its low, SL over its
    # own high (not the 09:15 high), target 1:2 or the nearer virgin CPR
    # (31 Aug 2026). Mirror for a big green break over PDH/R1.
    if rng1 > px * BIG_CANDLE_PCT / 100 and len(bars) > 1:
        c2 = bars[1]
        rng2 = c2['high'] - c2['low']
        small2 = 0 < rng2 <= rng1 * INSIDE_MAX
        y = chart.get('prev_cpr') or {}
        red1, green1 = c1['close'] < c1['open'], c1['close'] > c1['open']
        # The break: opened at/over a box's top and closed under that box —
        # the lower box, or the upper box on a day that opens in it (20 Jan
        # 2025: opened 23,290 in PDH/R1 23,292-23,297, closed 23,228 under
        # it on an 89-pt candle). Mirror upward through either box.
        # Through the upper box downward (lower box upward) only when the
        # candle stays clear of the CPR band — one that ran into the CPRs is
        # the "down to the box / CPR" shape instead (6 Apr 2026).
        broke_dn = red1 and ((c1['open'] >= lo_wall[1] - tol and c1['close'] < lo_wall[0])
                             or (c1['open'] >= hi_wall[0] - tol and c1['close'] < hi_wall[0] and c1['low'] >= band_hi - tol))
        broke_up = green1 and ((c1['open'] <= hi_wall[0] + tol and c1['close'] > hi_wall[1])
                               or (c1['open'] <= lo_wall[1] + tol and c1['close'] > lo_wall[1] and c1['high'] <= band_lo + tol))
        # The straddle: opened inside the box, traded both over and under it,
        # closed back inside — undecided, so the 09:20 candle must be red (green).
        in_lo = lo_wall[0] - tol <= c1['open'] <= lo_wall[1] + tol
        in_hi = hi_wall[0] - tol <= c1['open'] <= hi_wall[1] + tol
        straddle_dn = red1 and in_lo and c1['high'] >= lo_wall[1] and c1['low'] <= lo_wall[0] and lo_wall[0] <= c1['close'] <= lo_wall[1]
        straddle_up = green1 and in_hi and c1['high'] >= hi_wall[1] and c1['low'] <= hi_wall[0] and hi_wall[0] <= c1['close'] <= hi_wall[1]
        # Down TO the box from above (closed at it, not through): a small red
        # 09:20 closing under the box is still the sell; otherwise the day is
        # the retest case below (3 Jun 2026).
        to_lo = red1 and c1['open'] > lo_wall[1] + tol and lo_wall[0] - tol <= c1['close'] <= lo_wall[1] + tol
        to_hi = green1 and c1['open'] < hi_wall[0] - tol and hi_wall[0] - tol <= c1['close'] <= hi_wall[1] + tol
        dn_box = hi_wall if c1['close'] > lo_wall[1] else lo_wall            # the box the 09:15 candle broke down through
        up_box = lo_wall if c1['close'] < hi_wall[0] else hi_wall
        if ((broke_dn or ((straddle_dn or to_lo) and c2['close'] < c2['open']))
                and small2 and c2['close'] < dn_box[0] and c2['high'] <= dn_box[1] + tol):
            entry = math.floor(c2['low'] - 1)
            sl = math.ceil(c2['high'] + 1.5)
            target = entry - TREND_RR * (sl - entry)
            twhy = f'1:{TREND_RR:g}'
            if y.get('virgin') and target < y['tc'] < entry:
                target, twhy = y['tc'], f"the virgin CPR {y['bc']:,.0f}-{y['tc']:,.0f} (yesterday's, untouched) — nearer than 1:{TREND_RR:g}"
            dn_name = 'PDL/S1' if dn_box is lo_wall else 'PDH/R1'
            c1_why = (f"09:15 candle opened at {c1['open']:,.0f} over {dn_name} {dn_box[0]:,.0f}-{dn_box[1]:,.0f} and closed below both at {c1['close']:,.0f}"
                      if broke_dn else
                      f"09:15 candle opened at {c1['open']:,.0f} and closed at PDL/S1 {lo_wall[0]:,.0f}-{lo_wall[1]:,.0f} ({c1['close']:,.0f})"
                      if to_lo and not straddle_dn else
                      f"09:15 candle opened at {c1['open']:,.0f} inside PDL/S1 {lo_wall[0]:,.0f}-{lo_wall[1]:,.0f}, traded over and under it "
                      f"({c1['high']:,.0f}-{c1['low']:,.0f}) and closed back inside at {c1['close']:,.0f}")
            why = (f"{c1_why}, but a {rng1:.0f}-pt candle is too big to enter under — waited for 09:20; "
                   f"{c2['time']} small red candle {'held' if broke_dn else 'closed'} under {dn_name if broke_dn else 'PDL/S1'} ({c2['high']:,.0f}-{c2['low']:,.0f}) -> SELL under it; "
                   f"SL over its high; target {twhy}")
            return ({'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, 1)
        if ((broke_up or ((straddle_up or to_hi) and c2['close'] > c2['open']))
                and small2 and c2['close'] > up_box[1] and c2['low'] >= up_box[0] - tol):
            entry = math.ceil(c2['high'] + 1)
            sl = math.floor(c2['low'] - 1.5)
            target = entry + TREND_RR * (entry - sl)
            twhy = f'1:{TREND_RR:g}'
            if y.get('virgin') and entry < y['bc'] < target:
                target, twhy = y['bc'], f"the virgin CPR {y['bc']:,.0f}-{y['tc']:,.0f} (yesterday's, untouched) — nearer than 1:{TREND_RR:g}"
            up_name = 'PDH/R1' if up_box is hi_wall else 'PDL/S1'
            c1_why = (f"09:15 candle opened at {c1['open']:,.0f} under {up_name} {up_box[0]:,.0f}-{up_box[1]:,.0f} and closed above both at {c1['close']:,.0f}"
                      if broke_up else
                      f"09:15 candle opened at {c1['open']:,.0f} and closed at PDH/R1 {hi_wall[0]:,.0f}-{hi_wall[1]:,.0f} ({c1['close']:,.0f})"
                      if to_hi and not straddle_up else
                      f"09:15 candle opened at {c1['open']:,.0f} inside PDH/R1 {hi_wall[0]:,.0f}-{hi_wall[1]:,.0f}, traded over and under it "
                      f"({c1['high']:,.0f}-{c1['low']:,.0f}) and closed back inside at {c1['close']:,.0f}")
            why = (f"{c1_why}, but a {rng1:.0f}-pt candle is too big to enter over — waited for 09:20; "
                   f"{c2['time']} small green candle {'held' if broke_up else 'closed'} over {up_name if broke_up else 'PDH/R1'} ({c2['low']:,.0f}-{c2['high']:,.0f}) -> BUY over it; "
                   f"SL under its low; target {twhy}")
            return ({'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, 1)

    # A big 09:15 candle that ran from above the PDL/S1 box down TO it
    # (closed at the box, not through it), with no small red 09:20 under
    # the box: no sell. The trade is the retest of the box from above,
    # once a close has cleared it (3 Jun 2026). Mirror at PDH/R1.
    if rng1 > px * BIG_CANDLE_PCT / 100 and len(bars) > 1:
        red1, green1 = c1['close'] < c1['open'], c1['close'] > c1['open']
        to_lo = red1 and c1['open'] > lo_wall[1] + tol and lo_wall[0] - tol <= c1['close'] <= lo_wall[1] + tol
        to_hi = green1 and c1['open'] < hi_wall[0] - tol and hi_wall[0] - tol <= c1['close'] <= hi_wall[1] + tol
        if to_lo or to_hi:
            is_buy = bool(to_lo)
            box, box_name = (lo_wall, 'PDL/S1') if is_buy else (hi_wall, 'PDH/R1')
            head = (f"09:15 candle opened at {c1['open']:,.0f} and closed at {box_name} {box[0]:,.0f}-{box[1]:,.0f} "
                    f"({c1['close']:,.0f}) — a {rng1:.0f}-pt candle, too big to enter {'under' if is_buy else 'over'}, "
                    f"and 09:20 {'green' if is_buy else 'red'} — no {'sell' if is_buy else 'buy'}")
            def retest_target(entry, sl, line, is_buy):
                """The CPR's near line — or 1:2 when that line is nearer than
                the risk allows (28 Jul 2026: TC 3 pts under the entry)."""
                risk = abs(entry - sl)
                reward = (line[1] - entry) if is_buy else (entry - line[1])
                if reward < NEAR_TARGET_X * risk:
                    target = entry + TREND_RR * risk if is_buy else entry - TREND_RR * risk
                    return target, f"1:{TREND_RR:g} (the CPR {line[0]} {line[1]:,.0f} is only {reward:.0f} pts away, inside the {risk:.0f}-pt risk)"
                return line[1], f"the CPR ({line[0]} {line[1]:,.0f})"

            # The box has to be LOST first — a close through its far side —
            # before a close back over it counts as the break to retest:
            # the reclaim of a level that gave way, not a bounce off it.
            crossed = (c1['close'] < box[0]) if is_buy else (c1['close'] > box[1])
            broke_at = None
            for i, b in enumerate(bars[1:], start=1):
                if b['time'] > RETEST_UNTIL:
                    break
                rng = b['high'] - b['low']
                if is_buy:
                    if broke_at is None:
                        if b['close'] < box[0]:
                            crossed = True
                        elif crossed and b['close'] > box[1] + tol:   # a clear close over the box, not a graze
                            broke_at = b['time']
                        continue
                    if b['close'] < box[0]:                       # back under the box: the break failed
                        broke_at = None
                        continue
                    retest = (b['close'] > b['open'] and 0 < rng <= rng1 * INSIDE_MAX and b['low'] <= box[1] + tol
                              and b['close'] > box[1] and (b['close'] - b['low']) / rng >= STRONG_BODY)
                    if retest:
                        entry, sl = math.ceil(b['high'] + 1), math.floor(b['low'] - 1.5)
                        target, twhy = retest_target(entry, sl, ('BC', lv['bc']), True)
                        why = (f"{head}; price lost the box first, then {broke_at} closed back over it; {b['time']} small green candle "
                               f"retested it (low {b['low']:,.0f}, closed {b['close']:,.0f} on its high) -> BUY over it; "
                               f"SL under its low; target {twhy}")
                        return ({'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i)
                else:
                    if broke_at is None:
                        if b['close'] > box[1]:
                            crossed = True
                        elif crossed and b['close'] < box[0] - tol:
                            broke_at = b['time']
                        continue
                    if b['close'] > box[1]:
                        broke_at = None
                        continue
                    retest = (b['close'] < b['open'] and 0 < rng <= rng1 * INSIDE_MAX and b['high'] >= box[0] - tol
                              and b['close'] < box[0] and (b['high'] - b['close']) / rng >= STRONG_BODY)
                    if retest:
                        entry, sl = math.floor(b['low'] - 1), math.ceil(b['high'] + 1.5)
                        target, twhy = retest_target(entry, sl, ('TC', lv['tc']), False)
                        why = (f"{head}; price cleared the box first, then {broke_at} closed back under it; {b['time']} small red candle "
                               f"retested it (high {b['high']:,.0f}, closed {b['close']:,.0f} on its low) -> SELL under it; "
                               f"SL over its high; target {twhy}")
                        return ({'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i)
            return None, (f"{head}; " + (f"no retest of {box_name} after a break by {RETEST_UNTIL}" if crossed
                                         else f"price never {'lost' if is_buy else 'cleared'} {box_name} first, so no break to retest by {RETEST_UNTIL}")
                          + " — no trade"), None

    # The 09:15 candle itself is a strong REJECTION from the box: it opens
    # in or at the PDL/S1 box, touches it (PDL, S1 or the Cam S3 inside
    # it) and closes back above the box on a body of STRONG_BODY or more.
    # BUY over its high, SL under its low, target Cam R3 when that sits in
    # or at the CPR (the zone's top), else the CPR's far line (18 Jun
    # 2025: 09:15 O 24,788 in PDL/S1 24,784-24,814, L 24,777 on S1, C
    # 24,830 over the box -> BUY 24,839, SL 24,775, target Cam R3 24,900).
    # Mirror for a strong red 09:15 rejecting the PDH/R1 box.
    if rng1 > 0 and abs(c1['close'] - c1['open']) / rng1 >= STRONG_BODY and rng1 <= px * BOX_REJECT_MAX_PCT / 100:
        lo_levels = [('PDL', lv['pdl']), ('S1', lv['s1']), ('Cam S3', lv['cs3'])]
        hi_levels = [('PDH', lv['pdh']), ('R1', lv['r1']), ('Cam R3', lv['cr3'])]
        if (c1['close'] > c1['open'] and lo_wall[0] - tol <= c1['open'] <= lo_wall[1] + tol
                and any(c1['low'] <= y for _, y in lo_levels) and c1['close'] > lo_wall[1]):
            entry, sl = math.ceil(c1['high'] + 1), math.floor(c1['low'] - 1.5)
            cam_at_cpr = lv['bc'] - tol <= lv['cr3'] <= lv['tc'] + tol
            level = ('Cam R3', max(lv['cr3'], lv['tc'])) if cam_at_cpr else ('CPR TC', lv['tc'])
            target, twhy = _level_or_rr(entry, sl, level, True)
            touched = ' and '.join(n for n, y in lo_levels if c1['low'] <= y)
            why = (f"09:15 strong green candle opened at {c1['open']:,.0f} in PDL/S1 {lo_wall[0]:,.0f}-{lo_wall[1]:,.0f}, touched {touched} "
                   f"(low {c1['low']:,.0f}) and closed over the box at {c1['close']:,.0f} -> BUY over it; SL under its low; "
                   f"target {twhy}{' (Cam R3 sits at the CPR)' if cam_at_cpr else ''}")
            return {'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, 0
        if (c1['close'] < c1['open'] and hi_wall[0] - tol <= c1['open'] <= hi_wall[1] + tol
                and any(c1['high'] >= y for _, y in hi_levels) and c1['close'] < hi_wall[0]):
            entry, sl = math.floor(c1['low'] - 1), math.ceil(c1['high'] + 1.5)
            cam_at_cpr = lv['bc'] - tol <= lv['cs3'] <= lv['tc'] + tol
            level = ('Cam S3', min(lv['cs3'], lv['bc'])) if cam_at_cpr else ('CPR BC', lv['bc'])
            target, twhy = _level_or_rr(entry, sl, level, False)
            touched = ' and '.join(n for n, y in hi_levels if c1['high'] >= y)
            why = (f"09:15 strong red candle opened at {c1['open']:,.0f} in PDH/R1 {hi_wall[0]:,.0f}-{hi_wall[1]:,.0f}, touched {touched} "
                   f"(high {c1['high']:,.0f}) and closed under the box at {c1['close']:,.0f} -> SELL under it; SL over its high; "
                   f"target {twhy}{' (Cam S3 sits at the CPR)' if cam_at_cpr else ''}")
            return {'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, 0

    # Narrow CPR and the 09:15 candle itself is the strong break through
    # PDL/S1 (or PDH/R1): the move has started — trade it off that candle,
    # whichever side of the CPRs price is on (12 Feb 2026).
    if narrow and rng1 > 0 and abs(c1['close'] - c1['open']) / rng1 >= STRONG_BODY:
        if c1['close'] < c1['open'] and c1['open'] >= lo_wall[0] - tol and c1['close'] < lo_wall[0]:
            step = [(n, y) for n, y in named if c1['low'] - px * TOUCH_PCT / 100 <= y < c1['low']]
            entry = math.floor(min(step, key=lambda t: t[1])[1]) if step else math.floor(c1['low'] - 1)
            sl = math.ceil(c1['high'] + 1.5)
            target = entry - TREND_RR * (sl - entry)
            why = (f"narrow CPR ({chart['box_pts']} pts), small box — big move expected; 09:15 strong red candle touched "
                   f"PDL/S1 {lo_wall[0]:,.0f}-{lo_wall[1]:,.0f} and closed below it at {c1['close']:,.0f} -> SELL under it"
                   + (f" (under {step[0][0]} {step[0][1]:,.0f})" if step else '') + "; SL over its high; target 1:2")
            return ({'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, 0)
        if c1['close'] > c1['open'] and c1['open'] <= hi_wall[1] + tol and c1['close'] > hi_wall[1]:
            step = [(n, y) for n, y in named if c1['high'] < y <= c1['high'] + px * TOUCH_PCT / 100]
            entry = math.ceil(max(step, key=lambda t: t[1])[1]) if step else math.ceil(c1['high'] + 1)
            sl = math.floor(c1['low'] - 1.5)
            target = entry + TREND_RR * (entry - sl)
            why = (f"narrow CPR ({chart['box_pts']} pts), small box — big move expected; 09:15 strong green candle touched "
                   f"PDH/R1 {hi_wall[0]:,.0f}-{hi_wall[1]:,.0f} and closed above it at {c1['close']:,.0f} -> BUY over it"
                   + (f" (over {step[0][0]} {step[0][1]:,.0f})" if step else '') + "; SL under its low; target 1:2")
            return ({'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, 0)

    # The same break with a long wick on the break's side, at any CPR
    # width: the 09:15 candle opened inside the PDL/S1 box and closed under
    # it, but a lower wick of half its range or more says the level was
    # bought once already — not an entry by itself. The 09:20 candle
    # decides: a small red one holding inside that wick (under the 09:15
    # close, over its low) confirms the break, and the entry is under it
    # (7 Sept 2026). Mirror for a green break over PDH/R1.
    if rng1 > 0 and len(bars) > 1 and abs(c1['close'] - c1['open']) / rng1 < STRONG_BODY:
        c2 = bars[1]
        rng2 = c2['high'] - c2['low']
        lower_wick, upper_wick = min(c1['open'], c1['close']) - c1['low'], c1['high'] - max(c1['open'], c1['close'])
        small2 = rng2 <= rng1 * INSIDE_MAX
        if (c1['close'] < c1['open'] and lo_wall[0] - tol <= c1['open'] <= lo_wall[1] + tol and c1['close'] < lo_wall[0]
                and lower_wick / rng1 >= WICK_MIN
                and c2['close'] < c2['open'] and small2 and c2['low'] >= c1['low'] - tol and c2['high'] <= c1['close'] + tol):
            entry = math.floor(c2['low'] - 1)
            sl = math.ceil(c1['high'] + 1.5)
            target = entry - TREND_RR * (sl - entry)
            why = (f"09:15 candle opened at {c1['open']:,.0f} inside PDL/S1 {lo_wall[0]:,.0f}-{lo_wall[1]:,.0f} and closed below it "
                   f"at {c1['close']:,.0f}, but with a {lower_wick:.0f}-pt lower wick — waited for 09:20; "
                   f"{c2['time']} small red candle held inside the wick ({c2['high']:,.0f}-{c2['low']:,.0f}) -> SELL under it; "
                   f"SL over the 09:15 high; target 1:{TREND_RR:g}")
            return ({'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, 1)
        if (c1['close'] > c1['open'] and hi_wall[0] - tol <= c1['open'] <= hi_wall[1] + tol and c1['close'] > hi_wall[1]
                and upper_wick / rng1 >= WICK_MIN
                and c2['close'] > c2['open'] and small2 and c2['high'] <= c1['high'] + tol and c2['low'] >= c1['close'] - tol):
            entry = math.ceil(c2['high'] + 1)
            sl = math.floor(c1['low'] - 1.5)
            target = entry + TREND_RR * (entry - sl)
            why = (f"09:15 candle opened at {c1['open']:,.0f} inside PDH/R1 {hi_wall[0]:,.0f}-{hi_wall[1]:,.0f} and closed above it "
                   f"at {c1['close']:,.0f}, but with a {upper_wick:.0f}-pt upper wick — waited for 09:20; "
                   f"{c2['time']} small green candle held inside the wick ({c2['low']:,.0f}-{c2['high']:,.0f}) -> BUY over it; "
                   f"SL under the 09:15 low; target 1:{TREND_RR:g}")
            return ({'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, 1)

    # A gap beyond the first pivot pair is its own case: the open sits past
    # R1 AND PDH (or past S1 AND PDL), so the CPR is out of reach and the
    # R1/PDH zone (S1/PDL below) is what price has to come back to first.
    gap_hi, gap_lo = max(lv['r1'], lv['pdh']), min(lv['s1'], lv['pdl'])
    gap_min = c1['open'] * GAP_MIN_PCT / 100
    if c1['open'] > gap_hi + gap_min:
        side_open, zone = 'gap-up', (min(lv['r1'], lv['pdh']), gap_hi)
    elif c1['open'] < gap_lo - gap_min:
        side_open, zone = 'gap-down', (gap_lo, max(lv['s1'], lv['pdl']))
    elif w and not svc.cprs_merged(named) and w['tc'] < c1['close'] < lv['bc']:
        # Above the weekly CPR but under the daily one: the daily CPR is the
        # lid, and only a strong close above it is an entry (BUY).
        side_open = 'between-up'
    elif w and not svc.cprs_merged(named) and lv['tc'] < c1['close'] < w['bc']:
        side_open = 'between-down'
    elif lv['bc'] <= c1['close'] <= lv['tc']:
        side_open = 'inside'                 # inside the DAILY CPR: its own lines are the trigger
    elif c1['close'] < band_lo:
        side_open = 'below'
    elif c1['close'] > band_hi:
        side_open = 'above'
    else:
        side_open = 'inside'

    supports = [(n, lv[k]) for k, n in (('pdl', 'PDL'), ('s1', 'S1'), ('cs3', 'Cam S3'), ('s2', 'S2'), ('s3', 'S3'))]
    resistances = [(n, lv[k]) for k, n in (('pdh', 'PDH'), ('r1', 'R1'), ('cr3', 'Cam R3'), ('r2', 'R2'), ('r3', 'R3'))]

    def next_level(entry, is_buy, pool):
        ahead = [(n, y) for n, y in pool
                 if (y >= entry + 10 if is_buy else y <= entry - 10)]
        if not ahead:
            return None
        return min(ahead, key=lambda t: t[1]) if is_buy else max(ahead, key=lambda t: t[1])

    def through_the_box(level):
        """A target at the near edge of the PDL/S1 (PDH/R1) box moves to the
        Camarilla S3 (R3) inside that box: price that reaches the box's edge
        runs on to the Cam line inside it, so that is where the trade is
        booked (3 Mar 2025: PDL 22,105 with Cam S3 22,030 inside the box
        22,003-22,105 -> target Cam S3)."""
        if not level:
            return level
        name, y = level
        if name in ('PDL', 'S1') and lo_wall[0] < lv['cs3'] < lo_wall[1]:
            return ('Cam S3 (inside the PDL/S1 box)', lv['cs3'])
        if name in ('PDH', 'R1') and hi_wall[0] < lv['cr3'] < hi_wall[1]:
            return ('Cam R3 (inside the PDH/R1 box)', lv['cr3'])
        return level

    def build(side, entry, sl, level, why):
        """The trade. Its natural target is the next level — unless that
        level is nearer than the risk (then 1:2: "the CPR is very close
        to the entry, so 1:2") or further than FAR_TARGET_X x the risk
        (then 1:1: "245 points is very long, keep 1:1")."""
        risk = abs(entry - sl)
        is_buy = side == 'BUY'
        level = through_the_box(level)
        reward = ((level[1] - entry) if is_buy else (entry - level[1])) if level else None
        if reward is not None and reward < NEAR_TARGET_X * risk:
            target = entry + TREND_RR * risk if is_buy else entry - TREND_RR * risk
            twhy = f'1:{TREND_RR:g} ({level[0]} {level[1]:,.0f} is only {reward:.0f} pts away, inside the {risk:.0f}-pt risk)'
        elif reward is not None and reward <= FAR_TARGET_X * risk:
            target, twhy = level[1], f'{level[0]} {level[1]:,.0f}'
        else:
            target = entry + risk if is_buy else entry - risk
            twhy = '1:1' + (f' ({level[0]} {level[1]:,.0f} is {abs(level[1] - entry):.0f} pts away)' if level else '')
        return ({'trade': side, 'entry': float(round(entry)), 'target': float(round(target)),
                 'sl': float(round(sl))}, f'{why}; target {twhy}')

    def level_or_rr(entry, sl, level, is_buy):
        """The next level, or 1:2 when that level is nearer than the risk
        allows — the target convention of the cross-candle and retest
        entries, whose stops are tight enough that a far level is still
        worth the trade (7 Apr 2026: PDH 68 pts on a 38-pt stop)."""
        risk = abs(entry - sl)
        level = through_the_box(level)
        reward = ((level[1] - entry) if is_buy else (entry - level[1])) if level else None
        if reward is None or reward < NEAR_TARGET_X * risk:
            target = entry + TREND_RR * risk if is_buy else entry - TREND_RR * risk
            return target, (f"1:{TREND_RR:g} ({level[0]} {level[1]:,.0f} is only {reward:.0f} pts away, inside the {risk:.0f}-pt risk)"
                            if level else f"1:{TREND_RR:g}")
        return level[1], f"{level[0]} {level[1]:,.0f}"

    def cam_retest(k, brk, is_buy):
        """Gap day, the zone reversal candle too big to enter over and
        Camarilla R3 (S3) inside the R1/PDH (S1/PDL) box: the reversal is
        the breakout, and the entry is the retest — a candle by
        RETEST_UNTIL whose low comes to Cam R3 (within TOUCH_PCT) and that
        closes back over the box's top -> BUY over its high, SL under Cam
        R3, target 1:2. A close under the box first voids it. Mirror for
        a gap-down with Cam S3 in the box."""
        cam = lv['cr3'] if is_buy else lv['cs3']
        name = 'Cam R3' if is_buy else 'Cam S3'
        head = (f"{'gap-up' if is_buy else 'gap-down'}; {brk['time']} candle came to {'R1/PDH' if is_buy else 'S1/PDL'} "
                f"{zone[0]:,.0f}-{zone[1]:,.0f} and reversed {'up' if is_buy else 'down'} on a {brk['high'] - brk['low']:.0f}-pt candle — "
                f"too big to enter {'over' if is_buy else 'under'}; {name} {cam:,.0f} sits inside the box, so that close "
                f"{'over' if is_buy else 'under'} the box is the breakout and the retest to {name} is the trade")
        for j, x in enumerate(bars[k + 1:], start=k + 1):
            if x['time'] > RETEST_UNTIL:
                break
            if is_buy:
                if x['close'] < zone[0]:
                    return None, f"{head}; {x['time']} closed back under the box — the break failed — no trade", None
                if x['low'] <= cam + tol and x['close'] > zone[1]:
                    entry, sl = math.ceil(x['high'] + 1), math.floor(cam - 1.5)
                    target = entry + TREND_RR * (entry - sl)
                    why = (f"{head}; {x['time']} candle retested {name} (low {x['low']:,.0f}) and closed back over R1 at {x['close']:,.0f} "
                           f"-> BUY over it; SL under {name}; target 1:{TREND_RR:g}")
                    return {'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, j
            else:
                if x['close'] > zone[1]:
                    return None, f"{head}; {x['time']} closed back over the box — the break failed — no trade", None
                if x['high'] >= cam - tol and x['close'] < zone[0]:
                    entry, sl = math.floor(x['low'] - 1), math.ceil(cam + 1.5)
                    target = entry - TREND_RR * (sl - entry)
                    why = (f"{head}; {x['time']} candle retested {name} (high {x['high']:,.0f}) and closed back under S1 at {x['close']:,.0f} "
                           f"-> SELL under it; SL over {name}; target 1:{TREND_RR:g}")
                    return {'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, j
        return None, f"{head}; no retest of {name} with a close back {'over' if is_buy else 'under'} the box by {RETEST_UNTIL} — no trade", None

    def no_room(entry, sl, is_buy):
        """A WIDE CPR day (the sheet's relative reading) with the next box
        closer to the entry than the risk: the break has nowhere to go —
        price is boxed in between the CPR and PDH/R1 (PDL/S1) — so the
        inside-CPR breakout is not taken (4 Sep 2026: BUY 23,964, R1 23,975
        11 pts away against a 62-pt stop). Returns the reason, or None."""
        if chart.get('cpr_type') != 'Wide':
            return None
        risk = abs(entry - sl)
        box = hi_wall if is_buy else lo_wall
        room = (box[0] - entry) if is_buy else (entry - box[1])
        if room < risk:
            return (f"wide CPR ({chart['box_pts']} pts) and a big box: {'PDH/R1' if is_buy else 'PDL/S1'} "
                    f"{box[0]:,.0f}-{box[1]:,.0f} is only {max(room, 0):.0f} pts {'over' if is_buy else 'under'} the "
                    f"{entry:,.0f} entry, inside the {risk:.0f}-pt risk — price is boxed in between the CPR and the box, no room — no trade")
        return None

    def nearest_level_target(entry, sl, is_buy, levels):
        """The nearest of `levels` beyond NEAR_TARGET_X x the risk; 1:2 only
        when none of them is."""
        risk = abs(entry - sl)
        levels = [through_the_box(l) for l in levels]
        cands = [(n, y) for n, y in levels if (y > entry if is_buy else y < entry) and abs(y - entry) >= NEAR_TARGET_X * risk]
        if not cands:
            return (entry + TREND_RR * risk if is_buy else entry - TREND_RR * risk), f'1:{TREND_RR:g}'
        n, y = min(cands, key=lambda t: abs(t[1] - entry))
        return y, f'{n} {y:,.0f}'

    def nearest_target(entry, sl, is_buy, levels):
        """The nearest of `levels` (name, price) beyond the entry that is at
        least NEAR_TARGET_X x the risk away — 1:2 included as a candidate —
        for the setups whose target is 'whichever is near'."""
        risk = abs(entry - sl)
        cands = [(n, y) for n, y in levels if (y > entry if is_buy else y < entry) and abs(y - entry) >= NEAR_TARGET_X * risk]
        rr = entry + TREND_RR * risk if is_buy else entry - TREND_RR * risk
        cands.append((f'1:{TREND_RR:g}', rr))
        n, y = min(cands, key=lambda t: abs(t[1] - entry))
        return y, (f'{n} {y:,.0f}' if not n.startswith('1:') else n)

    def zone_rejection(i, b, is_sell):
        """The weekly CPR merged with the PDH/R1 box (PDL/S1 below) makes one
        big zone, and the 09:15 candle opened inside it: a strong candle
        rejected from PDH (PDL) — reaching it and closing through it — is
        the trade, big or not: SELL under its low (BUY over its high), SL
        over its own high (under its low), target the nearest of the CPR
        lines / PDL (PDH) / 1:2 beyond NEAR_TARGET_X x the risk (13 Jan 2026)."""
        if is_sell:
            entry, sl = math.floor(b['low'] - 1), math.ceil(b['high'] + 1.5)
            target, twhy = nearest_target(entry, sl, False, [('CPR TC', lv['tc']), ('CPR BC', lv['bc']), ('PDL', lv['pdl']), ('S1', lv['s1'])])
            why = (f"weekly CPR {w['bc']:,.0f}-{w['tc']:,.0f} merged with PDH/R1 {hi_wall[0]:,.0f}-{hi_wall[1]:,.0f}, open inside that zone; "
                   f"{b['time']} strong red candle rejected from PDH {lv['pdh']:,.0f} (high {b['high']:,.0f}, closed {b['close']:,.0f} under it) "
                   f"-> SELL under it; SL over its high; target {twhy}")
            return {'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i
        entry, sl = math.ceil(b['high'] + 1), math.floor(b['low'] - 1.5)
        target, twhy = nearest_target(entry, sl, True, [('CPR BC', lv['bc']), ('CPR TC', lv['tc']), ('PDH', lv['pdh']), ('R1', lv['r1'])])
        why = (f"weekly CPR {w['bc']:,.0f}-{w['tc']:,.0f} merged with PDL/S1 {lo_wall[0]:,.0f}-{lo_wall[1]:,.0f}, open inside that zone; "
               f"{b['time']} strong green candle rejected from PDL {lv['pdl']:,.0f} (low {b['low']:,.0f}, closed {b['close']:,.0f} over it) "
               f"-> BUY over it; SL under its low; target {twhy}")
        return {'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i

    def base_trade(i, b, strong, is_buy):
        """Gap day, zone not yet reached: the bar either extends the base
        (small, not strong) or, as the first strong candle in the fill's
        direction after a base of BASE_MIN_BARS with BASE_MIN_COUNTER
        against it, is the entry — over its high (under its low), stop
        under its low (over its high), target the zone's near edge."""
        nonlocal base_n, base_counter, base_ok
        if not strong:
            base_n += 1
            base_counter += (b['close'] < b['open']) if is_buy else (b['close'] > b['open'])
            base_ok = base_ok and b['high'] - b['low'] <= rng1
            return None
        fires = base_ok and base_n >= BASE_MIN_BARS and base_counter >= BASE_MIN_COUNTER and (b['close'] > b['open']) == is_buy
        base_ok = False                          # the first strong candle is the entry or there is none
        if not fires:
            return None
        # The entry waits for price to clear the 09:15 candle as well as the
        # strong one — a BUY over both highs, a SELL under both lows.
        if is_buy:
            entry, sl = math.ceil(max(b['high'], c1['high']) + 1), math.floor(b['low'] - 1.5)
            level = ('PDL' if lv['pdl'] <= lv['s1'] else 'S1', zone[0])
        else:
            entry, sl = math.floor(min(b['low'], c1['low']) - 1), math.ceil(b['high'] + 1.5)
            level = ('PDH' if lv['pdh'] >= lv['r1'] else 'R1', zone[1])
        risk = abs(entry - sl)
        reward = (level[1] - entry) if is_buy else (entry - level[1])
        if reward < NEAR_TARGET_X * risk:
            target = entry + TREND_RR * risk if is_buy else entry - TREND_RR * risk
            twhy = f'1:{TREND_RR:g} ({level[0]} {level[1]:,.0f} is only {reward:.0f} pts away, inside the {risk:.0f}-pt risk)'
        else:
            target, twhy = level[1], f'{level[0]} {level[1]:,.0f} (the gap fill)'
        colour = 'green' if is_buy else 'red'
        why = (f"{'gap-down' if is_buy else 'gap-up'} open {c1['open']:,.0f} {'under S1/PDL' if is_buy else 'over R1/PDH'} "
               f"{zone[0]:,.0f}-{zone[1]:,.0f}, never reached; {bars[1]['time']}-{bars[i - 1]['time']} base of {base_n} small candles "
               f"({base_counter} {'red' if is_buy else 'green'}); {b['time']} strong {colour} candle -> "
               f"{'BUY over' if is_buy else 'SELL under'} it"
               + (f" and the 09:15 {'high' if is_buy else 'low'} {c1['high' if is_buy else 'low']:,.0f}"
                  if (c1['high'] > b['high'] if is_buy else c1['low'] < b['low']) else '')
               + f"; SL {'under its low' if is_buy else 'over its high'}; target {twhy}")
        return ({'trade': 'BUY' if is_buy else 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why)

    # Narrow CPR, open above both CPRs, and the 09:15 candle opened INSIDE
    # the PDH/R1 box and closed out above it: the box is the hinge. A bar
    # breaking the 09:15 high continues the open -> BUY over it. Otherwise
    # price has to fall back under the box first; the first red candle
    # that then climbs back to the box and closes under it is the SELL,
    # target the CPR, PDL if the CPR breaks (24 Aug 2026). Mirror below.
    if narrow and w and rng1 > 0 and len(bars) > 1:
        up_box = hi_wall[0] - tol <= c1['open'] <= hi_wall[1] + tol and c1['close'] > hi_wall[1] and c1['close'] > max(band_hi, w['tc'])
        dn_box = lo_wall[0] - tol <= c1['open'] <= lo_wall[1] + tol and c1['close'] < lo_wall[0] and c1['close'] < min(band_lo, w['bc'])
        if up_box or dn_box:
            is_up = bool(up_box)
            box = hi_wall if is_up else lo_wall
            box_name = 'PDH/R1' if is_up else 'PDL/S1'
            head = (f"narrow CPR ({chart['box_pts']} pts), open {'above' if is_up else 'below'} both CPRs; 09:15 candle opened at "
                    f"{c1['open']:,.0f} inside {box_name} {box[0]:,.0f}-{box[1]:,.0f} and closed {'above' if is_up else 'below'} it at {c1['close']:,.0f}")
            back = False                                    # a close back on the box's other side
            # The continuation (a break of the 09:15 extreme) is not taken
            # when the 09:15 candle is big, or the candle breaking it is:
            # two big candles in a row is a spike to fade, not a trend to
            # join — the day then waits for the reversal and the rejection
            # from the box the other way (23 Feb 2026).
            big1 = rng1 > px * BIG_CANDLE_PCT / 100
            skipped_cont = None
            for i, b in enumerate(bars[1:], start=1):
                if b['time'] > (LATE_SETUP_UNTIL if skipped_cont else SETUP_UNTIL):
                    break
                big_b = b['high'] - b['low'] > px * SIGNAL_MAX_PCT / 100
                if is_up and b['high'] > c1['high'] and not back:
                    if big1 or big_b:
                        if skipped_cont is None:
                            skipped_cont = (f"{b['time']} bar broke the 09:15 high {c1['high']:,.0f} but "
                                            f"{'the 09:15 candle (' + format(rng1, '.0f') + ' pts)' if big1 else 'that candle (' + format(b['high'] - b['low'], '.0f') + ' pts)'} "
                                            f"is too big to join — waiting for the reversal")
                    else:
                        entry, sl = math.ceil(c1['high'] + 1), math.floor(c1['low'] - 1.5)
                        made = build('BUY', entry, sl, next_level(entry, True, resistances),
                                     f"{head}; {b['time']} bar broke the 09:15 high {c1['high']:,.0f} -> BUY over it; SL under the 09:15 low")
                        return (*made, 0)
                if not is_up and b['low'] < c1['low'] and not back:
                    if big1 or big_b:
                        if skipped_cont is None:
                            skipped_cont = (f"{b['time']} bar broke the 09:15 low {c1['low']:,.0f} but "
                                            f"{'the 09:15 candle (' + format(rng1, '.0f') + ' pts)' if big1 else 'that candle (' + format(b['high'] - b['low'], '.0f') + ' pts)'} "
                                            f"is too big to join — waiting for the reversal")
                    else:
                        entry, sl = math.floor(c1['low'] - 1), math.ceil(c1['high'] + 1.5)
                        made = build('SELL', entry, sl, next_level(entry, False, supports),
                                     f"{head}; {b['time']} bar broke the 09:15 low {c1['low']:,.0f} -> SELL under it; SL over the 09:15 high")
                        return (*made, 0)
                if is_up:
                    if not back:
                        back = b['close'] < box[0]
                        continue
                    if b['close'] < b['open'] and b['high'] >= box[0] - tol and b['close'] < box[0]:
                        entry, sl = math.floor(b['low'] - 1), math.ceil(b['high'] + 1)
                        why = (f"{head}; {skipped_cont or 'never broke the 09:15 high'}; price closed back under {box_name} and {b['time']} red candle "
                               f"climbed to it (high {b['high']:,.0f}) and closed under it at {b['close']:,.0f} -> SELL under it; "
                               f"SL over its high; target the CPR (TC {lv['tc']:,.0f}), PDL {lv['pdl']:,.0f} if a strong red candle breaks the CPR")
                        return ({'trade': 'SELL', 'entry': float(entry), 'target': float(round(lv['tc'])), 'sl': float(sl),
                                 'extend': {'level': float(round(lv['pdl'])), 'name': 'PDL', 'through': float(lv['bc'])}}, why, i)
                else:
                    if not back:
                        back = b['close'] > box[1]
                        continue
                    if b['close'] > b['open'] and b['low'] <= box[1] + tol and b['close'] > box[1]:
                        entry, sl = math.ceil(b['high'] + 1), math.floor(b['low'] - 1)
                        why = (f"{head}; {skipped_cont or 'never broke the 09:15 low'}; price closed back over {box_name} and {b['time']} green candle "
                               f"dipped to it (low {b['low']:,.0f}) and closed over it at {b['close']:,.0f} -> BUY over it; "
                               f"SL under its low; target the CPR (BC {lv['bc']:,.0f}), PDH {lv['pdh']:,.0f} if a strong green candle breaks the CPR")
                        return ({'trade': 'BUY', 'entry': float(entry), 'target': float(round(lv['bc'])), 'sl': float(sl),
                                 'extend': {'level': float(round(lv['pdh'])), 'name': 'PDH', 'through': float(lv['tc'])}}, why, i)
            if skipped_cont:
                return None, f"{head}; {skipped_cont}; no rejection from {box_name} the other way by {LATE_SETUP_UNTIL} — no trade", None
            return None, f"{head}; neither the 09:15 {'high' if is_up else 'low'} broke nor a {'red' if is_up else 'green'} candle rejected from {box_name} by {SETUP_UNTIL} — no trade", None

    # Narrow CPR with the open clear of both CPRs: a trend day is expected.
    # If the 09:15 candle rejects the other side (a long wick, close at the
    # far end), the first strong candle in the trend's direction is the
    # entry — over its high (under its low), stop tight, target 1:2.
    if narrow and side_open in ('above', 'below') and w:
        clear = c1['close'] > max(band_hi, w['tc']) if side_open == 'above' else c1['close'] < min(band_lo, w['bc'])
        rng1 = c1['high'] - c1['low']
        lower_wick = min(c1['open'], c1['close']) - c1['low']
        upper_wick = c1['high'] - max(c1['open'], c1['close'])
        up_reject = rng1 > 0 and lower_wick / rng1 >= WICK_MIN and (c1['close'] - c1['low']) / rng1 >= 0.6
        dn_reject = rng1 > 0 and upper_wick / rng1 >= WICK_MIN and (c1['high'] - c1['close']) / rng1 >= 0.6
        if clear and ((side_open == 'above' and up_reject) or (side_open == 'below' and dn_reject)):
            is_buy = side_open == 'above'
            wick = f"{lower_wick:.0f}" if is_buy else f"{upper_wick:.0f}"
            head = (f"narrow CPR ({chart['box_pts']} pts), open {'above' if is_buy else 'below'} both CPRs — trend day expected; "
                    f"09:15 candle rejected the {'down' if is_buy else 'up'}side (wick {wick} pts)")
            # The first pivot pair still ahead of the 09:15 close is the wall
            # the trend has to clear — PDH/R1 above, PDL/S1 below.
            if is_buy:
                wall = (min(lv['pdh'], lv['r1']), max(lv['pdh'], lv['r1'])) if c1['close'] < min(lv['pdh'], lv['r1']) else None
            else:
                wall = (min(lv['pdl'], lv['s1']), max(lv['pdl'], lv['s1'])) if c1['close'] > max(lv['pdl'], lv['s1']) else None
            extreme = c1['high'] if is_buy else c1['low']
            for i, b in enumerate(bars[1:], start=1):
                if b['time'] > SETUP_UNTIL:
                    break
                rng = b['high'] - b['low']
                up_w, dn_w = b['high'] - max(b['open'], b['close']), min(b['open'], b['close']) - b['low']
                # A candle that reaches the wall and is turned back — the
                # bigger wick on the wall's side, close short of it — is a
                # rejection: fade it back to the CPR (11 Feb 2026).
                if wall and rng > 0:
                    if is_buy and b['high'] >= wall[0] - tol and b['close'] < wall[0] and up_w > dn_w:
                        entry = math.floor(b['low'] - 1)
                        sl = math.ceil(max(extreme, wall[1]) + 1.5)
                        line = svc.first_cpr_line(entry, False, named)
                        target = round(line[1]) if line else entry - (sl - entry)
                        why = (f"{head}, still under PDH/R1 {wall[0]:,.0f}-{wall[1]:,.0f}; {b['time']} candle rejected from it "
                               f"(high {b['high']:,.0f}, top wick, closed {b['close']:,.0f}) -> SELL under it; SL over the high; "
                               f"target {'the CPR ' + line[0] if line else '1:1'}")
                        return ({'trade': 'SELL', 'entry': float(entry), 'target': float(target), 'sl': float(sl)}, why, i)
                    if not is_buy and b['low'] <= wall[1] + tol and b['close'] > wall[1] and dn_w > up_w:
                        entry = math.ceil(b['high'] + 1)
                        sl = math.floor(min(extreme, wall[0]) - 1.5)
                        line = svc.first_cpr_line(entry, True, named)
                        target = round(line[1]) if line else entry + (entry - sl)
                        why = (f"{head}, still over PDL/S1 {wall[0]:,.0f}-{wall[1]:,.0f}; {b['time']} candle rejected from it "
                               f"(low {b['low']:,.0f}, bottom wick, closed {b['close']:,.0f}) -> BUY over it; SL under the low; "
                               f"target {'the CPR ' + line[0] if line else '1:1'}")
                        return ({'trade': 'BUY', 'entry': float(entry), 'target': float(target), 'sl': float(sl)}, why, i)
                extreme = max(extreme, b['high']) if is_buy else min(extreme, b['low'])
                strong = rng > 0 and abs(b['close'] - b['open']) / rng >= STRONG_BODY
                if not strong or (b['close'] > b['open']) != is_buy:
                    continue
                if wall and ((is_buy and b['close'] <= wall[1]) or (not is_buy and b['close'] >= wall[0])):
                    continue                     # strong, but has not cleared the wall
                tight = px * TREND_SL_PCT / 100
                entry = math.ceil(b['high'] + 1) if is_buy else math.floor(b['low'] - 1)
                sl = math.floor(b['low'] - tight) if is_buy else math.ceil(b['high'] + tight)
                risk = abs(entry - sl)
                target = entry + TREND_RR * risk if is_buy else entry - TREND_RR * risk
                why = (f"{head}; {b['time']} strong {'bull' if is_buy else 'bear'} candle"
                       + (f" through PDH/R1 {wall[0]:,.0f}-{wall[1]:,.0f}" if wall and is_buy else f" through PDL/S1 {wall[0]:,.0f}-{wall[1]:,.0f}" if wall else '')
                       + f" -> {'BUY over' if is_buy else 'SELL under'} it; SL {'under' if is_buy else 'over'} it; target 1:{TREND_RR:g}")
                return ({'trade': 'BUY' if is_buy else 'SELL', 'entry': float(entry),
                         'target': float(round(target)), 'sl': float(sl)}, why, i)
            return None, (f"{head} — no strong {'bull' if is_buy else 'bear'} candle"
                          + (" through PDH/R1" if wall and is_buy else " through PDL/S1" if wall else '')
                          + f" and no rejection from it by {SETUP_UNTIL} — no trade"), None

    # 09:15 opened and closed inside the CPR but wicked out of it both ways:
    # the CPR lines are spent, and the day is decided at the box. The first
    # close over the PDH/R1 box (under the PDL/S1 box) is the break candle;
    # the candle after it decides — through the break candle's high (low) is
    # the continuation, a close back inside the box is the failure and the
    # trade the other way, stop over the break candle's high (26 May 2026).
    if side_open == 'inside' and c1['high'] > lv['tc'] + 2 and c1['low'] < lv['bc'] - 2:     # real wicks, not a tick
        brk, brk_side = None, None
        blocked = None                       # a continuation refused for a virgin CPR in its way; the day then looks the other way
        head = (f"09:15 opened and closed inside the CPR {lv['bc']:,.0f}-{lv['tc']:,.0f} but wicked out of it both ways "
                f"({c1['high']:,.0f}-{c1['low']:,.0f}) — the box decides")

        def virgin_in_the_way(entry, sl, is_buy):
            """A virgin CPR's near edge inside the risk, in the trade's
            direction: support (resistance) too close for the continuation
            (29 Jan 2026: 28 Jan's 25,090-25,147 27 pts under a SELL at
            25,174 against a 66-pt stop)."""
            risk = abs(entry - sl)
            for v in chart.get('virgin_cprs') or []:
                edge = v['bc'] if is_buy else v['tc']
                if (entry < edge < entry + risk) if is_buy else (entry - risk < edge < entry):
                    return v
            return None

        for i, b in enumerate(bars[1:], start=1):
            if b['time'] > SETUP_UNTIL:
                break
            if blocked == 'down':
                # The downside continuation was refused: the trade is the
                # rejection back over the WHOLE lower box -> BUY over that
                # candle, stop under the swing low since the break, the
                # CPR the target.
                swing = min(swing, b['low'])
                if b['close'] > lo_wall[1]:
                    entry, sl = math.ceil(b['high'] + 1), math.floor(swing - 1.5)
                    target, twhy = nearest_target(entry, sl, True, [('CPR BC', lv['bc']), ('CPR TC', lv['tc']), ('PDH', lv['pdh']), ('R1', lv['r1'])])
                    why = (f"{head}; {brk['time']} candle closed under PDL/S1 {lo_wall[0]:,.0f}-{lo_wall[1]:,.0f} but the virgin CPR "
                           f"{blocked_v['bc']:,.0f}-{blocked_v['tc']:,.0f} ({blocked_v['date']}) sits just under it — no sell; "
                           f"{b['time']} candle rejected back over the box at {b['close']:,.0f} -> BUY over it; SL under the swing low {swing:,.0f}; target {twhy}")
                    return {'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i
                continue
            if blocked == 'up':
                swing = max(swing, b['high'])
                if b['close'] < hi_wall[0]:
                    entry, sl = math.floor(b['low'] - 1), math.ceil(swing + 1.5)
                    target, twhy = nearest_target(entry, sl, False, [('CPR TC', lv['tc']), ('CPR BC', lv['bc']), ('PDL', lv['pdl']), ('S1', lv['s1'])])
                    why = (f"{head}; {brk['time']} candle closed over PDH/R1 {hi_wall[0]:,.0f}-{hi_wall[1]:,.0f} but the virgin CPR "
                           f"{blocked_v['bc']:,.0f}-{blocked_v['tc']:,.0f} ({blocked_v['date']}) sits just over it — no buy; "
                           f"{b['time']} candle rejected back under the box at {b['close']:,.0f} -> SELL under it; SL over the swing high {swing:,.0f}; target {twhy}")
                    return {'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i
                continue
            if brk is None:
                if b['close'] > hi_wall[1]:
                    brk, brk_side = b, 'up'
                elif b['close'] < lo_wall[0]:
                    brk, brk_side = b, 'down'
                continue
            if brk_side == 'up':
                if b['high'] > brk['high']:
                    entry, sl = math.ceil(brk['high'] + 1), math.floor(brk['low'] - 1.5)
                    blocked_v = virgin_in_the_way(entry, sl, True)
                    if blocked_v:
                        blocked, swing = 'up', max(brk['high'], b['high'])
                        continue
                    target, twhy = nearest_target(entry, sl, True, [('R2', lv['r2']), ('Cam R3', lv['cr3']), ('R3', lv['r3'])])
                    why = (f"{head}; {brk['time']} candle closed over PDH/R1 {hi_wall[0]:,.0f}-{hi_wall[1]:,.0f} at {brk['close']:,.0f} and "
                           f"{b['time']} broke its high -> BUY over it; SL under the break candle's low; target {twhy}")
                    return {'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i
                if b['close'] < hi_wall[1]:
                    entry, sl = math.floor(b['low'] - 1), math.ceil(brk['high'] + 1.5)
                    target, twhy = nearest_target(entry, sl, False, [('CPR TC', lv['tc']), ('CPR BC', lv['bc']), ('PDL', lv['pdl']), ('S1', lv['s1'])])
                    why = (f"{head}; {brk['time']} candle closed over PDH/R1 {hi_wall[0]:,.0f}-{hi_wall[1]:,.0f} at {brk['close']:,.0f} but "
                           f"{b['time']} showed weakness — closed back under the box at {b['close']:,.0f} -> SELL under it; "
                           f"SL over the {brk['time']} high; target {twhy}")
                    return {'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i
                brk = b if b['high'] >= brk['high'] else brk         # still over the box, undecided
            else:
                if b['low'] < brk['low']:
                    entry, sl = math.floor(brk['low'] - 1), math.ceil(brk['high'] + 1.5)
                    blocked_v = virgin_in_the_way(entry, sl, False)
                    if blocked_v:
                        blocked, swing = 'down', min(brk['low'], b['low'])
                        continue
                    target, twhy = nearest_target(entry, sl, False, [('S2', lv['s2']), ('Cam S3', lv['cs3']), ('S3', lv['s3'])])
                    why = (f"{head}; {brk['time']} candle closed under PDL/S1 {lo_wall[0]:,.0f}-{lo_wall[1]:,.0f} at {brk['close']:,.0f} and "
                           f"{b['time']} broke its low -> SELL under it; SL over the break candle's high; target {twhy}")
                    return {'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i
                if b['close'] > lo_wall[0]:
                    entry, sl = math.ceil(b['high'] + 1), math.floor(brk['low'] - 1.5)
                    target, twhy = nearest_target(entry, sl, True, [('CPR BC', lv['bc']), ('CPR TC', lv['tc']), ('PDH', lv['pdh']), ('R1', lv['r1'])])
                    why = (f"{head}; {brk['time']} candle closed under PDL/S1 {lo_wall[0]:,.0f}-{lo_wall[1]:,.0f} at {brk['close']:,.0f} but "
                           f"{b['time']} showed strength — closed back over the box at {b['close']:,.0f} -> BUY over it; "
                           f"SL under the {brk['time']} low; target {twhy}")
                    return {'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i
                brk = b if b['low'] <= brk['low'] else brk
        if blocked:
            return None, (f"{head}; the {'downside' if blocked == 'down' else 'upside'} continuation was refused for the virgin CPR "
                          f"{blocked_v['bc']:,.0f}-{blocked_v['tc']:,.0f} in its way and price never rejected back "
                          f"{'over' if blocked == 'down' else 'under'} the box by {SETUP_UNTIL} — no trade"), None
        return None, f"{head}; neither box was closed through and decided by {SETUP_UNTIL} — no trade", None

    reached = False
    in_break, break_t, break_px = None, None, None       # the inside-CPR break awaiting its cross candle
    big_reject = None                                    # a CPR rejection too big to enter off, awaiting its small retest candle
    # The weekly CPR lying over the PDH/R1 box (under the PDL/S1 box) with
    # the 09:15 open inside that zone: the zone_rejection setup.
    zone_hi = bool(w and w['bc'] <= hi_wall[1] + tol and w['tc'] >= hi_wall[0] - tol
                   and min(hi_wall[0], w['bc']) - tol <= c1['open'] <= max(hi_wall[1], w['tc']) + tol)
    zone_lo = bool(w and w['bc'] <= lo_wall[1] + tol and w['tc'] >= lo_wall[0] - tol
                   and min(lo_wall[0], w['bc']) - tol <= c1['open'] <= max(lo_wall[1], w['tc']) + tol)
    # The gap-day base: small candles after 09:15 while the zone is still
    # out of reach. A strong candle either completes it (the trigger) or
    # ends it — the first strong candle is the entry or there is none.
    base_n = base_counter = 0
    base_ok = True
    until = LATE_SETUP_UNTIL if merged and side_open in ('above', 'below') else SETUP_UNTIL
    for i, b in enumerate(bars):
        if b['time'] > until:
            break
        rng = b['high'] - b['low']
        strong = rng > 0 and abs(b['close'] - b['open']) / rng >= STRONG_BODY
        # A candle closing at its own extreme after a long wick the other way
        # is a reversal candle even with a modest body (16 Feb 2026: 49%
        # body, closed on its high after dipping under PDL and S1).
        at_top = rng > 0 and (b['close'] - b['low']) / rng >= 0.85
        at_bottom = rng > 0 and (b['high'] - b['close']) / rng >= 0.85
        red = b['close'] < b['open'] and (strong or at_bottom)
        green = b['close'] > b['open'] and (strong or at_top)
        if side_open == 'gap-up':
            # Wait for price to come down to R1/PDH: a strong close back
            # above the zone is the reversal (BUY), a close under it the
            # breakdown (SELL). The 09:15 candle itself never triggers.
            if i == 0:
                continue
            if b['low'] > zone[1]:
                if not reached:
                    made = base_trade(i, b, strong, False)
                    if made:
                        return (*made, i)
                continue
            reached = True
            if green and b['close'] > zone[1]:
                # A BIG reversal candle with Cam R3 inside the box is not
                # entered over its high: it is the breakout, and the trade
                # is the retest to Cam R3 (10 Mar 2026).
                if rng > px * BIG_CANDLE_PCT / 100 and zone[0] <= lv['cr3'] <= zone[1]:
                    return cam_retest(i, b, True)
                entry, sl = b['high'] + buf, min(zone[0], b['low']) - buf
                made = build('BUY', entry, sl, next_level(entry, True, resistances),
                             f"gap-up; {b['time']} candle came to R1/PDH {zone[0]:,.0f}-{zone[1]:,.0f} (low {b['low']:,.0f}) and reversed up, closed {b['close']:,.0f}")
                if made:
                    return (*made, i)
            elif b['close'] < zone[0]:
                entry, sl = b['low'] - buf, zone[1] + buf
                made = build('SELL', entry, sl, ('CPR', band_hi),
                             f"gap-up; {b['time']} candle broke down through R1/PDH {zone[0]:,.0f}-{zone[1]:,.0f}, closed {b['close']:,.0f}")
                if made:
                    return (*made, i)
            continue
        if side_open == 'gap-down':
            if i == 0:
                continue
            if b['high'] < zone[0]:
                if not reached:
                    made = base_trade(i, b, strong, True)
                    if made:
                        return (*made, i)
                continue
            reached = True
            if red and b['close'] < zone[0]:
                if rng > px * BIG_CANDLE_PCT / 100 and zone[0] <= lv['cs3'] <= zone[1]:
                    return cam_retest(i, b, False)
                entry, sl = b['low'] - buf, max(zone[1], b['high']) + buf
                made = build('SELL', entry, sl, next_level(entry, False, supports),
                             f"gap-down; {b['time']} candle came up to S1/PDL {zone[0]:,.0f}-{zone[1]:,.0f} (high {b['high']:,.0f}) and reversed down, closed {b['close']:,.0f}")
                if made:
                    return (*made, i)
            elif b['close'] > zone[1]:
                entry, sl = b['high'] + buf, zone[0] - buf
                made = build('BUY', entry, sl, ('CPR', band_lo),
                             f"gap-down; {b['time']} candle broke up through S1/PDL {zone[0]:,.0f}-{zone[1]:,.0f}, closed {b['close']:,.0f}")
                if made:
                    return (*made, i)
            continue
        if side_open == 'inside':
            # 09:15 closed inside the CPR: the first strong close out of it
            # is the entry, stop at the 09:15 candle's other end, target the
            # first pivot pair that way (17 Feb 2026: 09:40 closed above TC
            # -> BUY 25,647, SL 25,591, target PDH 25,697).
            if i == 0:
                continue
            in_lo, in_hi = (lv['bc'], lv['tc']) if lv['bc'] <= c1['close'] <= lv['tc'] else (band_lo, band_hi)
            if rng1 > px * BIG_CANDLE_PCT / 100:
                # A big 09:15 candle: its far end is no stop, so the break is
                # not entered on the breakout candle. Any close out of the
                # CPR is the break; the next candle that comes back to the
                # line — the CROSS candle — is the entry, over its high with
                # the stop under its low (7 Apr 2026: 10:00 closed 22,914
                # over TC 22,902; 10:05 dipped to 22,894 -> BUY 22,930, SL
                # 22,892). A close back inside past TOUCH_PCT voids the break.
                if in_break == 'up':
                    if b['close'] < in_hi - tol:
                        in_break = None
                    elif b['low'] <= in_hi + tol:
                        entry, sl = math.ceil(b['high'] + 1), math.floor(b['low'] - 1.5)
                        boxed = no_room(entry, sl, True)
                        if boxed:
                            return None, f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f} on a {rng1:.0f}-pt candle; {break_t} closed above it, {b['time']} cross candle — {boxed}", None
                        level = next_level(entry, True, resistances[:2]) or next_level(entry, True, resistances)
                        target, twhy = level_or_rr(entry, sl, level, True)
                        why = (f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f} on a {rng1:.0f}-pt candle — no stop at its far end; "
                               f"{break_t} candle closed above the CPR at {break_px:,.0f}; {b['time']} cross candle came back to TC "
                               f"(low {b['low']:,.0f}) -> BUY over it; SL under its low; target {twhy}")
                        return ({'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i)
                elif in_break == 'down':
                    if b['close'] > in_lo + tol:
                        in_break = None
                    elif b['high'] >= in_lo - tol:
                        entry, sl = math.floor(b['low'] - 1), math.ceil(b['high'] + 1.5)
                        boxed = no_room(entry, sl, False)
                        if boxed:
                            return None, f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f} on a {rng1:.0f}-pt candle; {break_t} closed below it, {b['time']} cross candle — {boxed}", None
                        level = next_level(entry, False, supports[:2]) or next_level(entry, False, supports)
                        target, twhy = level_or_rr(entry, sl, level, False)
                        why = (f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f} on a {rng1:.0f}-pt candle — no stop at its far end; "
                               f"{break_t} candle closed below the CPR at {break_px:,.0f}; {b['time']} cross candle came back to BC "
                               f"(high {b['high']:,.0f}) -> SELL under it; SL over its high; target {twhy}")
                        return ({'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i)
                if in_break is None:
                    if b['close'] > in_hi:
                        in_break, break_t, break_px = 'up', b['time'], b['close']
                    elif b['close'] < in_lo:
                        in_break, break_t, break_px = 'down', b['time'], b['close']
                continue
            if strong and green and b['close'] > in_hi:
                entry, sl = math.ceil(b['high'] + 1), math.floor(c1['low'])
                boxed = no_room(entry, sl, True)
                if boxed:
                    return None, f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f}; {b['time']} candle closed above it — {boxed}", None
                made = build('BUY', entry, sl, next_level(entry, True, resistances[:2]) or next_level(entry, True, resistances),
                             f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f}; {b['time']} candle closed above it at {b['close']:,.0f} -> BUY over it; SL at the 09:15 low")
                if made:
                    return (*made, i)
            if strong and red and b['close'] < in_lo:
                entry, sl = math.floor(b['low'] - 1), math.ceil(c1['high'])
                boxed = no_room(entry, sl, False)
                if boxed:
                    return None, f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f}; {b['time']} candle closed below it — {boxed}", None
                made = build('SELL', entry, sl, next_level(entry, False, supports[:2]) or next_level(entry, False, supports),
                             f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f}; {b['time']} candle closed below it at {b['close']:,.0f} -> SELL under it; SL at the 09:15 high")
                if made:
                    return (*made, i)
            continue
        if side_open == 'between-up':
            if green and b['close'] > lv['tc']:
                entry, sl = b['high'] + buf, min(lv['bc'], b['low']) - buf
                made = build('BUY', entry, sl, next_level(entry, True, resistances),
                             f"above the weekly CPR, under the daily one; {b['time']} candle closed above the daily CPR {lv['bc']:,.0f}-{lv['tc']:,.0f} at {b['close']:,.0f}")
                if made:
                    return (*made, i)
            continue
        if side_open == 'between-down':
            if red and b['close'] < lv['bc']:
                entry, sl = b['low'] - buf, max(lv['tc'], b['high']) + buf
                made = build('SELL', entry, sl, next_level(entry, False, supports),
                             f"below the weekly CPR, above the daily one; {b['time']} candle closed below the daily CPR {lv['bc']:,.0f}-{lv['tc']:,.0f} at {b['close']:,.0f}")
                if made:
                    return (*made, i)
            continue
        if side_open == 'below':
            if zone_lo and strong and green and b['low'] <= lv['pdl'] and b['close'] > lv['pdl']:
                return zone_rejection(i, b, False)
            # SELL: rejection from the CPR. The candle has to reach the DAILY
            # CPR itself (its BC), not just the weekly band merged with it
            # (28 Jan 2026: a low into the weekly CPR, 79 pts short of the
            # daily TC, was no rejection), and close back under the band.
            if far_boxes:
                pass                                       # the CPR rejection is off on a far-box day
            elif merged:
                # Interlinked CPRs: a SMALL red candle up to the daily CPR
                # (BC within TOUCH_PCT) closing back under the whole band;
                # stop over its high, or over the VWAP just above it.
                if red and rng <= px * SIGNAL_MAX_PCT / 100 and b['high'] >= lv['bc'] - tol and b['close'] < band_lo:
                    entry = math.floor(b['low'] - 1)
                    vw = vwap_at(bars, i)
                    on_vwap = vw is not None and b['high'] < vw <= b['high'] + px * VWAP_STOP_PCT / 100
                    sl = math.ceil(vw) if on_vwap else math.ceil(b['high'] + 1.5)
                    level = next_level(entry, False, supports)
                    target, twhy = level_or_rr(entry, sl, level, False)
                    why = (f"interlinked CPRs {band_lo:,.0f}-{band_hi:,.0f}; {b['time']} small candle ({rng:.0f} pts) rejected from them "
                           f"(high {b['high']:,.0f}, closed {b['close']:,.0f} under both) -> SELL under it; "
                           f"SL {'over the VWAP ' + format(vw, ',.0f') + ' just above its high' if on_vwap else 'over its high'}; target {twhy}")
                    return ({'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i)
            elif big_reject is not None:
                # The retest after a big rejection: a SMALL candle back into
                # the CPR that does not close above it is the sell candle —
                # SELL under its low, SL over its high, target the nearer of
                # S1 / PDL (1 Sep 2026: 09:15's 55-pt rejection skipped;
                # 09:30 small candle to 24,077, closed 24,066 -> SELL 24,057).
                if (rng <= px * SIGNAL_MAX_PCT / 100 and b['high'] >= lv['bc'] and b['close'] <= lv['tc']
                        and (b['high'] - b['close']) / rng >= 0.5):          # a sell candle: closed in its lower half
                    entry, sl = math.floor(b['low'] - 1), math.ceil(b['high'] + 1.5)
                    target, twhy = nearest_level_target(entry, sl, False, [('S1', lv['s1']), ('PDL', lv['pdl'])])
                    why = (f"{big_reject['time']} candle rejected from the CPR {band_lo:,.0f}-{band_hi:,.0f} on a "
                           f"{big_reject['high'] - big_reject['low']:.0f}-pt candle — too big to enter under; {b['time']} small candle "
                           f"came back into the CPR (high {b['high']:,.0f}, closed {b['close']:,.0f}) -> SELL under it; SL over its high; target {twhy}")
                    return {'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i
            elif strong and red and b['high'] >= lv['bc'] and b['close'] < band_lo:   # a full-bodied rejection, not just a close near the low
                if rng > px * SIGNAL_MAX_PCT / 100:
                    big_reject = b                       # not entered off this candle: wait for the small retest
                    continue
                entry = b['low'] - buf
                sl = pivot_above(b['high']) + buf
                made = build('SELL', entry, sl, next_level(entry, False, supports),
                             f"{b['time']} candle rejected from the CPR (high {b['high']:,.0f} into {band_lo:,.0f}-{band_hi:,.0f}, closed {b['close']:,.0f})")
                if made:
                    return (*made, i)
            # BUY: rejection from PDL / S1 — entry over the candle, stop just
            # under the lowest level it rejected (not under its whole wick).
            # The candle has to REACH the level: a low that stops short of
            # it, even by a few points, rejected nothing (6 Mar 2026: 09:20
            # low 24,587 over S1 24,579 was not a rejection).
            # ... and it must not be a big candle: a stop under the level
            # behind a 68-pt candle is the candle's whole range (14 Jul
            # 2026: the 09:15 candle touched S1 and closed at the CPR — no
            # entry; the day's trade was the 11:30 rejection from the CPR).
            small = 0 < rng <= px * SIGNAL_MAX_PCT / 100
            touched = [(n, y) for n, y in (('PDL', lv['pdl']), ('S1', lv['s1']))
                       if green and small and b['low'] <= y and b['close'] > y]
            if touched:
                name, y = min(touched, key=lambda t: t[1])
                entry = math.ceil(b['high'] + 1)
                sl = math.floor(y - px * 0.02 / 100)
                target, twhy = level_or_rr(entry, sl, ('CPR', band_lo), True)
                why = (f"{b['time']} candle rejected from {' and '.join(n for n, _ in touched)} "
                       f"(low {b['low']:,.0f}, closed {b['close']:,.0f}) -> BUY over it; SL under {name} {y:,.0f}; target {twhy}")
                return ({'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i)
        else:
            if zone_hi and strong and red and b['high'] >= lv['pdh'] and b['close'] < lv['pdh']:
                return zone_rejection(i, b, True)
            if far_boxes:
                pass                                       # the CPR rejection is off on a far-box day
            elif merged:
                if green and rng <= px * SIGNAL_MAX_PCT / 100 and b['low'] <= lv['tc'] + tol and b['close'] > band_hi:
                    entry = math.ceil(b['high'] + 1)
                    vw = vwap_at(bars, i)
                    on_vwap = vw is not None and b['low'] - px * VWAP_STOP_PCT / 100 <= vw < b['low']
                    sl = math.floor(vw) if on_vwap else math.floor(b['low'] - 1.5)
                    level = next_level(entry, True, resistances)
                    target, twhy = level_or_rr(entry, sl, level, True)
                    why = (f"interlinked CPRs {band_lo:,.0f}-{band_hi:,.0f}; {b['time']} small candle ({rng:.0f} pts) rejected from them "
                           f"(low {b['low']:,.0f}, closed {b['close']:,.0f} over both) -> BUY over it; "
                           f"SL {'under the VWAP ' + format(vw, ',.0f') + ' just below its low' if on_vwap else 'under its low'}; target {twhy}")
                    return ({'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i)
            elif big_reject is not None:
                if (rng <= px * SIGNAL_MAX_PCT / 100 and b['low'] <= lv['tc'] and b['close'] >= lv['bc']
                        and (b['close'] - b['low']) / rng >= 0.5):           # a buy candle: closed in its upper half
                    entry, sl = math.ceil(b['high'] + 1), math.floor(b['low'] - 1.5)
                    target, twhy = nearest_level_target(entry, sl, True, [('R1', lv['r1']), ('PDH', lv['pdh'])])
                    why = (f"{big_reject['time']} candle rejected from the CPR {band_lo:,.0f}-{band_hi:,.0f} on a "
                           f"{big_reject['high'] - big_reject['low']:.0f}-pt candle — too big to enter over; {b['time']} small candle "
                           f"came back into the CPR (low {b['low']:,.0f}, closed {b['close']:,.0f}) -> BUY over it; SL under its low; target {twhy}")
                    return {'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i
            elif strong and green and b['low'] <= lv['tc'] and b['close'] > band_hi:
                if rng > px * SIGNAL_MAX_PCT / 100:
                    big_reject = b
                    continue
                entry = b['high'] + buf
                sl = pivot_below(b['low']) - buf
                made = build('BUY', entry, sl, next_level(entry, True, resistances),
                             f"{b['time']} candle rejected from the CPR (low {b['low']:,.0f} into {band_lo:,.0f}-{band_hi:,.0f}, closed {b['close']:,.0f})")
                if made:
                    return (*made, i)
            small = 0 < rng <= px * SIGNAL_MAX_PCT / 100
            touched = [(n, y) for n, y in (('PDH', lv['pdh']), ('R1', lv['r1']))
                       if red and small and b['high'] >= y and b['close'] < y]
            if touched:
                name, y = max(touched, key=lambda t: t[1])
                entry = math.floor(b['low'] - 1)
                sl = math.ceil(y + px * 0.02 / 100)
                target, twhy = level_or_rr(entry, sl, ('CPR', band_hi), False)
                why = (f"{b['time']} candle rejected from {' and '.join(n for n, _ in touched)} "
                       f"(high {b['high']:,.0f}, closed {b['close']:,.0f}) -> SELL under it; SL over {name} {y:,.0f}; target {twhy}")
                return ({'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i)
    if side_open == 'inside':
        in_lo, in_hi = (lv['bc'], lv['tc']) if lv['bc'] <= c1['close'] <= lv['tc'] else (band_lo, band_hi)
        return None, (f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f}: entry only on a strong close out of it — "
                      f"none by {SETUP_UNTIL} — no trade"), None
    if side_open == 'between-up':
        return None, (f"price above the weekly CPR ({w['bc']:,.0f}-{w['tc']:,.0f}) but below the daily CPR "
                      f"({lv['bc']:,.0f}-{lv['tc']:,.0f}): BUY only on a close above the daily CPR — "
                      f"never closed above it by {SETUP_UNTIL} — no trade"), None
    if side_open == 'between-down':
        return None, (f"price below the weekly CPR ({w['bc']:,.0f}-{w['tc']:,.0f}) but above the daily CPR "
                      f"({lv['bc']:,.0f}-{lv['tc']:,.0f}): SELL only on a close below the daily CPR — "
                      f"never closed below it by {SETUP_UNTIL} — no trade"), None
    if side_open == 'gap-up':
        return None, (f"gap-up open {c1['open']:,.0f}; R1/PDH {zone[0]:,.0f}-{zone[1]:,.0f} "
                      + ('reached but neither reversed nor broke by ' if reached else 'never reached by ') + SETUP_UNTIL
                      + ' — no trade'), None
    if side_open == 'gap-down':
        return None, (f"gap-down open {c1['open']:,.0f}; S1/PDL {zone[0]:,.0f}-{zone[1]:,.0f} "
                      + ('reached but neither reversed nor broke by ' if reached else 'never reached by ') + SETUP_UNTIL
                      + ' — no trade'), None
    if big_reject is not None:
        return None, (f"{big_reject['time']} candle rejected from the CPR on a {big_reject['high'] - big_reject['low']:.0f}-pt candle — "
                      f"too big to enter off, and no small retest candle back into the CPR by {SETUP_UNTIL} — no trade"), None
    if far_boxes:
        return None, (f"narrow CPR ({cpr_h:.0f} pts) with the boxes {box_gap:.0f} pts away on both sides — too thin to trade "
                      f"against, entries only on a break of a box or a rejection from one — none by {SETUP_UNTIL} — no trade"), None
    return None, f'no rejection candle by {SETUP_UNTIL}', None


def virgin_cpr_rejection(chart, bars, start=1):
    """(trade, why, setup-bar index) for the first strong rejection from
    the PDH/R1 (PDL/S1) box that yesterday's virgin CPR sits on, from bar
    `start` up to LATE_SETUP_UNTIL; None when there is none. The trade
    carries `extend`: the level the target moves to when the bar reaching
    the CPR's far line is a strong close through the CPR."""
    lv, y = chart['levels'], chart.get('prev_cpr')
    if not y or not y.get('virgin') or chart['width_pct'] >= NARROW_PCT:     # narrow CPR / small box only
        return None
    px = bars[0]['close']
    tol = px * TOUCH_PCT / 100
    max_rng = px * SIGNAL_MAX_PCT / 100
    hi = (min(lv['pdh'], lv['r1']), max(lv['pdh'], lv['r1']))
    lo = (min(lv['pdl'], lv['s1']), max(lv['pdl'], lv['s1']))
    on_top = y['tc'] >= hi[0] - tol and y['bc'] <= hi[1] + tol      # the virgin CPR overlaps the PDH/R1 box
    below = y['bc'] <= lo[1] + tol and y['tc'] >= lo[0] - tol
    if not (on_top or below):
        return None
    for i, b in enumerate(bars[start:], start=start):
        if b['time'] > LATE_SETUP_UNTIL:
            break
        rng = b['high'] - b['low']
        if rng <= 0 or rng > max_rng:                # a very large candle puts the stop too far for the CPR target
            continue
        strong = abs(b['close'] - b['open']) / rng >= STRONG_BODY
        at_bottom = (b['high'] - b['close']) / rng >= 0.85
        at_top = (b['close'] - b['low']) / rng >= 0.85
        if (on_top and b['close'] < b['open'] and (strong or at_bottom)
                and b['high'] >= hi[0] - tol and b['close'] < hi[0] and b['low'] > lv['tc']):
            entry, sl = math.floor(b['low'] - 1), math.ceil(b['high'] + 1)
            why = (f"virgin CPR {y['bc']:,.0f}-{y['tc']:,.0f} (yesterday's, untouched) on PDH/R1 {hi[0]:,.0f}-{hi[1]:,.0f}; "
                   f"{b['time']} candle rejected from it (high {b['high']:,.0f}, closed {b['close']:,.0f}) -> SELL under it; "
                   f"SL over its high; target the CPR (BC {lv['bc']:,.0f}), PDL {lv['pdl']:,.0f} if a strong red candle breaks the CPR")
            return ({'trade': 'SELL', 'entry': float(entry), 'target': float(round(lv['bc'])), 'sl': float(sl),
                     'extend': {'level': float(round(lv['pdl'])), 'name': 'PDL', 'through': float(lv['bc'])}}, why, i)
        if (below and b['close'] > b['open'] and (strong or at_top)
                and b['low'] <= lo[1] + tol and b['close'] > lo[1] and b['high'] < lv['bc']):
            entry, sl = math.ceil(b['high'] + 1), math.floor(b['low'] - 1)
            why = (f"virgin CPR {y['bc']:,.0f}-{y['tc']:,.0f} (yesterday's, untouched) under PDL/S1 {lo[0]:,.0f}-{lo[1]:,.0f}; "
                   f"{b['time']} candle rejected from it (low {b['low']:,.0f}, closed {b['close']:,.0f}) -> BUY over it; "
                   f"SL under its low; target the CPR (TC {lv['tc']:,.0f}), PDH {lv['pdh']:,.0f} if a strong green candle breaks the CPR")
            return ({'trade': 'BUY', 'entry': float(entry), 'target': float(round(lv['tc'])), 'sl': float(sl),
                     'extend': {'level': float(round(lv['pdh'])), 'name': 'PDH', 'through': float(lv['tc'])}}, why, i)
    return None


def simulate(bars, trade):
    """cpr_backtest_service.simulate_trade on a rule trade, from its setup
    bar. A trade with `extend` runs to its target first; if the bar that
    reaches it is a strong candle closing through the CPR (past `through`),
    the target is replaced by the extended level and the run goes on, with
    the same stop. The returned trade dict has the target actually used."""
    sim = svc.simulate_trade(bars, trade['trade'], trade['entry'], trade['target'], trade['sl'])
    ext = trade.get('extend')
    if not ext or sim['result'] != 'Target':
        return sim, trade
    k = next(i for i, b in enumerate(bars) if b['time'] == sim['exit_time'])
    b = bars[k]
    rng = b['high'] - b['low']
    is_buy = trade['trade'] == 'BUY'
    broke = (rng > 0 and abs(b['close'] - b['open']) / rng >= STRONG_BODY
             and ((b['close'] > ext['through']) if is_buy else (b['close'] < ext['through'])))
    if not broke:
        return sim, trade
    used = dict(trade, target=ext['level'])
    entry, sl, target = sim.get('fill') or trade['entry'], trade['sl'], ext['level']
    base = {'entry_time': sim['entry_time'], 'fill': entry}
    for x in bars[k + 1:]:
        hit_t = x['high'] >= target if is_buy else x['low'] <= target
        hit_s = x['low'] <= sl if is_buy else x['high'] >= sl
        if hit_t and hit_s:
            return dict(base, result='Both', pnl=None, exit_time=x['time']), used
        if hit_t:
            return dict(base, result='Target', pnl=round(target - entry if is_buy else entry - target, 2), exit_time=x['time']), used
        if hit_s:
            return dict(base, result='SL', pnl=round(sl - entry if is_buy else entry - sl, 2), exit_time=x['time']), used
    sq = next((x for x in bars if x['time'] >= svc.SQUARE_OFF), None)
    exit_px, exit_t = (sq['open'], sq['time']) if sq else (bars[-1]['close'], bars[-1]['time'])
    return dict(base, result='EOD', pnl=round(exit_px - entry if is_buy else entry - exit_px, 2), exit_time=exit_t), used


def cap_risk(trade, why):
    """The MAX_RISK stop cap: a stop further than MAX_RISK from the entry
    moves to half that distance, and the reason says so. A target that was
    a multiple of the risk (1:1, 1:2) is re-struck off the halved risk — a
    1:2 target 470 points away from a stop that is now 118 was never the
    trade (7 Apr 2026). A level target stays where the level is."""
    risk = abs(trade['entry'] - trade['sl'])
    if risk <= MAX_RISK:
        return trade, why
    is_buy = trade['trade'] == 'BUY'
    half = risk / 2
    sl = trade['entry'] - half if is_buy else trade['entry'] + half
    sl = float(math.floor(sl) if is_buy else math.ceil(sl))
    new_risk = abs(trade['entry'] - sl)
    out = dict(trade, sl=sl)
    note = f"SL halved to {sl:,.0f} ({risk:.0f}-pt stop is over {MAX_RISK:.0f})"
    reward = abs(trade['target'] - trade['entry'])
    for k in (TREND_RR, 1.0):
        if abs(reward - k * risk) <= 3.0:
            target = trade['entry'] + k * new_risk if is_buy else trade['entry'] - k * new_risk
            out['target'] = float(round(target))
            note += f", target 1:{k:g} re-struck to {out['target']:,.0f}"
            break
    return out, f"{why}; {note}"


def virgin_target(trade, why, chart):
    """A virgin CPR between the entry and the target is the nearer level:
    the target moves to its near edge (TC under a SELL, BC over a BUY),
    taking the nearest one to the entry, unless that edge is within
    NEAR_TARGET_X x the risk."""
    is_buy = trade['trade'] == 'BUY'
    entry, target = trade['entry'], trade['target']
    risk = abs(entry - trade['sl'])
    best = None
    for v in chart.get('virgin_cprs') or []:
        edge = v['bc'] if is_buy else v['tc']
        between = (entry < edge < target) if is_buy else (target < edge < entry)
        if not between or abs(edge - entry) < NEAR_TARGET_X * risk:
            continue
        if best is None or abs(edge - entry) < abs(best[1] - entry):
            best = (v, edge)
    if best is None:
        return trade, why
    v, edge = best
    return dict(trade, target=float(round(edge))), (
        f"{why}; target moved to the virgin CPR {v['bc']:,.0f}-{v['tc']:,.0f} ({v['date']}, untouched since) at {edge:,.0f}")


def one_zone(chart, bars):
    """(lo, hi) when the daily CPR, the weekly CPR and a box (PDH/R1 or
    PDL/S1) interlink into one continuous band and the 09:15 candle closed
    inside it; None otherwise."""
    lv, w = chart['levels'], chart.get('weekly')
    if not w:
        return None
    named = svc._reason_ladder(lv, w, chart['first_candle'], chart['day']['open'])
    if not svc.cprs_merged(named):
        return None
    c1 = bars[0]
    tol = c1['close'] * TOUCH_PCT / 100
    band_lo, band_hi = min(lv['bc'], w['bc']), max(lv['tc'], w['tc'])
    hi_box = (min(lv['pdh'], lv['r1']), max(lv['pdh'], lv['r1']))
    lo_box = (min(lv['pdl'], lv['s1']), max(lv['pdl'], lv['s1']))
    zone = None
    if w['bc'] <= hi_box[1] + tol and w['tc'] >= hi_box[0] - tol:
        zone = (band_lo, max(band_hi, hi_box[1]))
    elif w['bc'] <= lo_box[1] + tol and w['tc'] >= lo_box[0] - tol:
        zone = (min(band_lo, lo_box[0]), band_hi)
    return zone if zone and zone[0] <= c1['close'] <= zone[1] else None


def zone_in_the_way(trade, why, chart, bars):
    """With the CPRs and a box interlinked into one zone around the open,
    an entry INSIDE that zone has nothing to trade against — every line
    is part of the same band — and is refused, whatever the setup (4 Aug
    2025: BUY 24,671 inside 24,597-24,784; 6 Mar 2025: the PDH rejection
    SELL at 22,356 inside 22,212-22,466). An entry outside the zone — a
    break of the whole band — stands (3 Mar 2025: SELL 22,151 under the
    zone 22,176-22,450), and zone_breakout is the day's own setup."""
    if why.startswith('09:15 strong green candle opened') or why.startswith('09:15 strong red candle opened'):
        return trade, why                     # the 09:15 box rejection trades the zone's own edges: exempt
    zone = one_zone(chart, bars)
    if zone and zone[0] <= trade['entry'] <= zone[1]:
        lv, w = chart['levels'], chart['weekly']
        return None, (f"{why}; but the daily CPR {lv['bc']:,.0f}-{lv['tc']:,.0f}, the weekly CPR {w['bc']:,.0f}-{w['tc']:,.0f} and the "
                      f"box interlink into one zone {zone[0]:,.0f}-{zone[1]:,.0f} and the entry sits inside it — no level to trade against — "
                      f"only a break out of the zone is traded")
    return trade, why


def zone_breakout(chart, bars, zone):
    """The one-zone day's own trade: the first candle by RETEST_UNTIL that
    closes beyond the zone — over its top (BUY over that candle's high,
    SL under its low) or under its bottom (SELL under its low, SL over
    its high) — target the next level beyond, or 1:2 (6 Mar 2025: zone
    22,212-22,466; 13:05 closed 22,477 over R1 -> BUY 22,479, SL 22,456).
    (trade, why, index) or None."""
    lv = chart['levels']
    px = bars[0]['close']
    for i, b in enumerate(bars[1:], start=1):
        if b['time'] > RETEST_UNTIL:
            break
        rng = b['high'] - b['low']
        if rng <= 0:
            continue
        if b['close'] > zone[1]:
            entry, sl = math.ceil(b['high'] + 1), math.floor(b['low'] - 1.5)
            target, twhy = _next_or_rr(entry, sl, True, [('R2', lv['r2']), ('R3', lv['r3'])])
            why = (f"the CPRs and PDH/R1 interlink into one zone {zone[0]:,.0f}-{zone[1]:,.0f} around the open — only a break out of it "
                   f"is traded; {b['time']} candle closed over the zone at {b['close']:,.0f} -> BUY over it; SL under its low; target {twhy}")
            return {'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i
        if b['close'] < zone[0]:
            entry, sl = math.floor(b['low'] - 1), math.ceil(b['high'] + 1.5)
            target, twhy = _next_or_rr(entry, sl, False, [('S2', lv['s2']), ('S3', lv['s3'])])
            why = (f"the CPRs and the box interlink into one zone {zone[0]:,.0f}-{zone[1]:,.0f} around the open — only a break out of it "
                   f"is traded; {b['time']} candle closed under the zone at {b['close']:,.0f} -> SELL under it; SL over its high; target {twhy}")
            return {'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, i
    return None


def _level_or_rr(entry, sl, level, is_buy):
    """`level`, or 1:2 when it is nearer than NEAR_TARGET_X x the risk."""
    risk = abs(entry - sl)
    reward = (level[1] - entry) if is_buy else (entry - level[1])
    if reward < NEAR_TARGET_X * risk:
        target = entry + TREND_RR * risk if is_buy else entry - TREND_RR * risk
        return target, f"1:{TREND_RR:g} ({level[0]} {level[1]:,.0f} is only {reward:.0f} pts away, inside the {risk:.0f}-pt risk)"
    return level[1], f"{level[0]} {level[1]:,.0f}"


def _next_or_rr(entry, sl, is_buy, levels):
    """The nearest of `levels` beyond NEAR_TARGET_X x the risk and 1:2 —
    whichever is nearer."""
    risk = abs(entry - sl)
    cands = [(n, y) for n, y in levels if (y > entry if is_buy else y < entry) and abs(y - entry) >= NEAR_TARGET_X * risk]
    cands.append((f'1:{TREND_RR:g}', entry + TREND_RR * risk if is_buy else entry - TREND_RR * risk))
    n, y = min(cands, key=lambda t: abs(t[1] - entry))
    return y, (n if n.startswith('1:') else f'{n} {y:,.0f}')


def vwap_in_the_way(trade, why, bars, i):
    """The session VWAP between the setup candle and the entry — the
    candle sits on one side of it and the stop order on the other, so the
    trigger itself has to cross the VWAP — refuses the entry (20 May 2026:
    the 10:00 reversal candle's low 23,496 over the VWAP 23,487, the SELL
    at 23,484 under it). A VWAP the candle already straddles, or one
    clearly past the entry, is left alone. Returns (trade, why) or
    (None, why)."""
    vw = vwap_at(bars, i)
    if vw is None:
        return trade, why
    b = bars[i]
    entry = trade['entry']
    if trade['trade'] == 'SELL':
        blocked = entry - 3 <= vw <= b['low']
    else:
        blocked = b['high'] <= vw <= entry + 3
    if blocked:
        return None, (f"{why}; the session VWAP {vw:,.0f} sits between the {b['time']} candle and the {entry:,.0f} entry — "
                      f"the trigger would have to cross it — no trade")
    return trade, why


def reversal_after_stop(chart, bars, sim, first):
    """The second trade of a volatile day: the bar that stopped the first
    trade is a strong candle the other way and a virgin CPR lies ahead of
    it — the reversal is traded over that candle's high (under its low),
    SL at its other end, target the virgin CPR's near edge (20 Jan 2025:
    the 09:20-candle SELL stopped by the 09:55 strong green; BUY 23,237,
    SL 23,204, target 17 Jan's virgin CPR 23,318). (trade, why, index)
    or None."""
    if sim['result'] != 'SL' or not sim.get('exit_time') or sim['exit_time'] > LATE_SETUP_UNTIL:
        return None
    k = next((i for i, b in enumerate(bars) if b['time'] == sim['exit_time']), None)
    if k is None:
        return None
    b = bars[k]
    rng = b['high'] - b['low']
    if rng <= 0 or abs(b['close'] - b['open']) / rng < STRONG_BODY:
        return None
    is_buy = first['trade'] == 'SELL' and b['close'] > b['open']
    is_sell = first['trade'] == 'BUY' and b['close'] < b['open']
    if not (is_buy or is_sell):
        return None
    entry = math.ceil(b['high'] + 1) if is_buy else math.floor(b['low'] - 1)
    sl = math.floor(b['low'] - 1.5) if is_buy else math.ceil(b['high'] + 1.5)
    risk = abs(entry - sl)
    ahead = [v for v in chart.get('virgin_cprs') or []
             if ((entry + NEAR_TARGET_X * risk < v['bc'] <= entry + REVERSAL_MAX_RR * risk) if is_buy
                 else (entry - REVERSAL_MAX_RR * risk <= v['tc'] < entry - NEAR_TARGET_X * risk))]
    if not ahead:
        return None
    v = min(ahead, key=lambda v: abs((v['bc'] if is_buy else v['tc']) - entry))
    target = v['bc'] if is_buy else v['tc']
    why = (f"the first trade was stopped by the {b['time']} strong {'green' if is_buy else 'red'} candle — the reversal; "
           f"{'BUY over' if is_buy else 'SELL under'} it; SL {'under its low' if is_buy else 'over its high'}; "
           f"target the virgin CPR {v['bc']:,.0f}-{v['tc']:,.0f} ({v['date']}, untouched since) at {target:,.0f}")
    return {'trade': 'BUY' if is_buy else 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, k


def propose_all(chart, bars):
    """Every trade the rule takes in the session, replayed: a list of
    (trade, why, setup-bar index, sim). The first is `propose`'s — or,
    when that finds nothing, the virgin-CPR rejection. A first trade that
    is closed out (stopped, at target, or never filled) may be followed by one more: the
    virgin-CPR rejection after its exit (a first trade that hit its target
    too — 1 Sep 2026). Every trade goes through cap_risk and virgin_target.
    The trade dicts carry the target actually used; a session with no
    trade is one (None, why, None, None)."""
    p, why, i = propose(chart, bars)
    if not p:
        late = virgin_cpr_rejection(chart, bars)
        if late:
            p, why, i = late
    if p:
        p, why = zone_in_the_way(p, why, chart, bars)
    zone = one_zone(chart, bars)
    if not p and zone:
        # The one-zone day: nothing else stood, so the break out of the zone is the trade.
        brk = zone_breakout(chart, bars, zone)
        if brk:
            p, why, i = brk
    if not p:
        return [(None, why, None, None)]
    if p:
        p, why = cap_risk(p, why)
        p, why = virgin_target(p, why, chart)
        p, why = vwap_in_the_way(p, why, bars, i)
    if not p:
        # Refused for the VWAP: the day is still open to the late virgin-CPR rejection.
        late = virgin_cpr_rejection(chart, bars, i + 1)
        if late:
            p2, why2, i2 = late
            p2, why2 = zone_in_the_way(p2, why2, chart, bars)
            if p2:
                p2, why2 = cap_risk(p2, why2)
                p2, why2 = virgin_target(p2, why2, chart)
                p2, why2 = vwap_in_the_way(p2, why2, bars, i2)
            if p2:
                sim2, used2 = simulate(bars[i2:], p2)
                sim2['after'] = 'refused'
                return [(None, why, None, None), (used2, why2, i2, sim2)]
        return [(None, why, None, None)]
    sim, used = simulate(bars[i:], p)
    out = [(used, why, i, sim)]
    if sim['result'] in ('SL', 'Target', 'No fill'):
        after = sim['exit_time'] if sim['result'] in ('SL', 'Target') else bars[i]['time']
        start = next((k for k, b in enumerate(bars) if b['time'] > after), len(bars))
        late = virgin_cpr_rejection(chart, bars, start) or reversal_after_stop(chart, bars, sim, used)
        if late:
            p2, why2, i2 = late
            p2, why2 = cap_risk(p2, why2)
            p2, why2 = virgin_target(p2, why2, chart)
            p2, why2 = vwap_in_the_way(p2, why2, bars, i2)
            if not p2:
                return out
            sim2, used2 = simulate(bars[i2:], p2)
            sim2['after'] = sim['result']
            out.append((used2, why2, i2, sim2))
    return out
