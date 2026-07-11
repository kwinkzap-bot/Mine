"""
Background scheduler for recurring tasks during market hours.
Handles market hours checking and scheduled API calls.
"""
from datetime import datetime, time, timedelta
from typing import Optional, Any
from trading_app.app.utils.logger import logger

# Optional: APScheduler for background scheduling
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    APSCHEDULER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    APSCHEDULER_AVAILABLE = False
    BackgroundScheduler = None  # type: ignore
    CronTrigger = None  # type: ignore
    logger.warning("APScheduler not installed — background scheduler disabled")
except Exception as e:
    APSCHEDULER_AVAILABLE = False
    BackgroundScheduler = None  # type: ignore
    CronTrigger = None  # type: ignore
    logger.error(f"APScheduler failed to initialize (unexpected error) — scheduler disabled: {e}", exc_info=True)


class MarketScheduler:
    """Manages background scheduled tasks during market hours."""
    
    # Market hours: 9:15 AM to 3:40 PM IST (Monday to Friday)
    MARKET_OPEN = time(9, 15)
    MARKET_CLOSE = time(15, 40)
    
    def __init__(self):
        """Initialize the scheduler."""
        self.scheduler: Optional[Any] = None
        self.cpr_filter_job: Optional[Any] = None
        self.oi_persistence_job: Optional[Any] = None
        self.historic_oi_job: Optional[Any] = None
        
        if not APSCHEDULER_AVAILABLE:
            return
        
        self.scheduler = BackgroundScheduler(daemon=True)  # type: ignore
    
    def is_market_hours(self) -> bool:
        """Check if current time is within market hours."""
        now = datetime.now().time()
        # Market is open from 9:15 AM to 3:40 PM
        return self.MARKET_OPEN <= now <= self.MARKET_CLOSE
    
    def is_trading_day(self) -> bool:
        """Check if today is a trading day (Monday-Friday)."""
        today = datetime.now().weekday()
        return today < 5  # 0-4 are Monday-Friday
    
    def start(self):
        """Start the background scheduler."""
        if not APSCHEDULER_AVAILABLE:
            logger.warning("APScheduler not available - backend scheduler disabled")
            logger.info("Use frontend scheduler (JavaScript) for recurring CPR filter calls")
            return
        
        if not self.scheduler or self.scheduler.running:
            logger.info("Scheduler is already running")
            return

        assert CronTrigger is not None  # APScheduler is available (checked above)

        # Schedule CPR filter to run every 5 minutes during market hours
        # Cron expression: Every 5 minutes on weekdays between 9:15 AM and 3:40 PM IST
        self.cpr_filter_job = self.scheduler.add_job(
            self._run_cpr_filter_task,
            CronTrigger(
                day_of_week='mon-fri',
                hour='9-15',
                minute='*/5',
                second='0',
            ),
            id='cpr_filter_recurring',
            name='CPR Filter Recurring Task',
            replace_existing=True,
            misfire_grace_time=60  # Allow 60s grace period if task is late
        )
        
        # Schedule OI Persistence - Every 1 minute
        self.oi_persistence_job = self.scheduler.add_job(
            self._run_oi_persistence_task,
            CronTrigger(
                day_of_week='mon-fri',
                hour='9-15',
                minute='*',  # Run every minute
                second='30'  # Offset by 30s
            ),
            id='oi_persistence',
            name='Open Interest Persistence',
            replace_existing=True,
            misfire_grace_time=60
        )
        
        self.historic_oi_job = self.scheduler.add_job(
            self._run_historic_oi_record_task,
            CronTrigger(
                day_of_week='mon-fri',
                hour=20,
                minute=0,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='historic_oi_record',
            name='Historic OI Daily Record',
            replace_existing=True,
            misfire_grace_time=300,
        )

        self.scheduler.add_job(
            self._run_fii_sector_task,
            CronTrigger(
                day_of_week='mon-fri',
                hour=16,
                minute=0,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='fii_sector_snapshot',
            name='FII Sector Limit Snapshot',
            replace_existing=True,
            misfire_grace_time=300,
        )

        # End of market hours (close is 3:40 PM): refresh the EMA Narrow
        # scanner's local candle store for every equity stock. Staggered 5
        # minutes after the FII sector task so both don't compete for the
        # same rate-limited broker API at once. First run can take a while
        # (full history download); every run after that is a same-day tail
        # refresh (seconds), so the scanner runs from local files all day.
        self.scheduler.add_job(
            self._run_ema_narrow_prewarm_task,
            CronTrigger(
                day_of_week='mon-fri',
                hour=16,
                minute=5,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='ema_narrow_prewarm',
            name='EMA Narrow Candle Store Prewarm',
            replace_existing=True,
            misfire_grace_time=3600,
        )

        self.scheduler.add_job(
            self._start_rtp_monitoring,
            CronTrigger(
                day_of_week='mon-fri',
                hour=9,
                minute=15,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='rtp_algo_start',
            name='RTP 1m Railway Track Algo Start',
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Watchdog: restart the thread if it crashes mid-day
        self.scheduler.add_job(
            self._watchdog_rtp,
            CronTrigger(
                day_of_week='mon-fri',
                hour='9-15',
                minute='*/5',
                second=30,
                timezone='Asia/Kolkata',
            ),
            id='rtp_algo_watchdog',
            name='RTP 1m Algo Watchdog',
            replace_existing=True,
            misfire_grace_time=60,
        )

        # RTP 30s (same logic, 30-second candles): start at 9:15 AM weekdays
        self.scheduler.add_job(
            self._start_rtp30s_monitoring,
            CronTrigger(
                day_of_week='mon-fri',
                hour=9,
                minute=15,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='rtp30s_algo_start',
            name='RTP 30s Railway Track Algo Start',
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Watchdog: restart the RTP 30s thread if it crashes mid-day
        self.scheduler.add_job(
            self._watchdog_rtp30s,
            CronTrigger(
                day_of_week='mon-fri',
                hour='9-15',
                minute='*/5',
                second=30,
                timezone='Asia/Kolkata',
            ),
            id='rtp30s_algo_watchdog',
            name='RTP 30s Algo Watchdog',
            replace_existing=True,
            misfire_grace_time=60,
        )

        # RTP 2m (same logic, 2-minute candles): start at 9:15 AM weekdays
        self.scheduler.add_job(
            self._start_rtp2m_monitoring,
            CronTrigger(
                day_of_week='mon-fri',
                hour=9,
                minute=15,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='rtp2m_algo_start',
            name='RTP 2m Railway Track Algo Start',
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Watchdog: restart the RTP 2m thread if it crashes mid-day
        self.scheduler.add_job(
            self._watchdog_rtp2m,
            CronTrigger(
                day_of_week='mon-fri',
                hour='9-15',
                minute='*/5',
                second=30,
                timezone='Asia/Kolkata',
            ),
            id='rtp2m_algo_watchdog',
            name='RTP 2m Algo Watchdog',
            replace_existing=True,
            misfire_grace_time=60,
        )

        # RTP 3m (same logic, 3-minute candles): start at 9:15 AM weekdays
        self.scheduler.add_job(
            self._start_rtp3m_monitoring,
            CronTrigger(
                day_of_week='mon-fri',
                hour=9,
                minute=15,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='rtp3m_algo_start',
            name='RTP 3m Railway Track Algo Start',
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Watchdog: restart the RTP 3m thread if it crashes mid-day
        self.scheduler.add_job(
            self._watchdog_rtp3m,
            CronTrigger(
                day_of_week='mon-fri',
                hour='9-15',
                minute='*/5',
                second=30,
                timezone='Asia/Kolkata',
            ),
            id='rtp3m_algo_watchdog',
            name='RTP 3m Algo Watchdog',
            replace_existing=True,
            misfire_grace_time=60,
        )

        # RTP 5m (same logic, 5-minute candles): start at 9:15 AM weekdays
        self.scheduler.add_job(
            self._start_rtp5m_monitoring,
            CronTrigger(
                day_of_week='mon-fri',
                hour=9,
                minute=15,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='rtp5m_algo_start',
            name='RTP 5m Railway Track Algo Start',
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Watchdog: restart the RTP 5m thread if it crashes mid-day
        self.scheduler.add_job(
            self._watchdog_rtp5m,
            CronTrigger(
                day_of_week='mon-fri',
                hour='9-15',
                minute='*/5',
                second=30,
                timezone='Asia/Kolkata',
            ),
            id='rtp5m_algo_watchdog',
            name='RTP 5m Algo Watchdog',
            replace_existing=True,
            misfire_grace_time=60,
        )

        # 2nd 30-Sec Candle algo: start at 9:15 AM weekdays
        self.scheduler.add_job(
            self._start_sc_monitoring,
            CronTrigger(
                day_of_week='mon-fri',
                hour=9,
                minute=15,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='sc_algo_start',
            name='2nd 30-Sec Candle Algo Start',
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Watchdog: restart the 2nd-candle thread if it crashes mid-day
        self.scheduler.add_job(
            self._watchdog_sc,
            CronTrigger(
                day_of_week='mon-fri',
                hour='9-15',
                minute='*/5',
                second=30,
                timezone='Asia/Kolkata',
            ),
            id='sc_algo_watchdog',
            name='2nd Candle Algo Watchdog',
            replace_existing=True,
            misfire_grace_time=60,
        )

        # Intrinsic ATM Range Breakout algo (paper trade): start at 9:15 AM weekdays
        self.scheduler.add_job(
            self._start_intrinsic_range_monitoring,
            CronTrigger(
                day_of_week='mon-fri',
                hour=9,
                minute=15,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='intrinsic_range_algo_start',
            name='Intrinsic Range Algo Start',
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Watchdog: restart the Intrinsic Range thread if it crashes mid-day
        self.scheduler.add_job(
            self._watchdog_intrinsic_range,
            CronTrigger(
                day_of_week='mon-fri',
                hour='9-15',
                minute='*/5',
                second=30,
                timezone='Asia/Kolkata',
            ),
            id='intrinsic_range_algo_watchdog',
            name='Intrinsic Range Algo Watchdog',
            replace_existing=True,
            misfire_grace_time=60,
        )

        self.scheduler.start()
        jobs = {j.id: str(j.next_run_time) for j in self.scheduler.get_jobs()}
        logger.info(f"Market scheduler started — jobs registered: {list(jobs.keys())}")

    def stop(self):
        """Stop the background scheduler."""
        if not self.scheduler or not self.scheduler.running:
            return
        self.scheduler.shutdown()
        logger.info("Market scheduler stopped")

    def _run_cpr_filter_task(self):
        """Execute CPR filter task (called by scheduler)."""
        try:
            if not self.is_market_hours():
                logger.debug("Outside market hours, skipping CPR filter task")
                return
            
            if not self.is_trading_day():
                logger.debug("Not a trading day, skipping CPR filter task")
                return
            
            logger.info("Executing scheduled CPR filter task...")
            
            # Background scheduler tasks run without Flask request context
            # This is a limitation - background tasks cannot access user sessions
            logger.warning("Background scheduler: CPR filter task requires active user session")
            logger.info("Note: Implement persistent authentication (API key) for background tasks")
            
        except Exception as e:
            logger.error(f"Unexpected error in CPR filter background task: {e}", exc_info=True)

    def _run_oi_persistence_task(self):
        """Fetch and store Open Interest data."""
        import os
        try:
            # Strict checks for production - Run only during market hours on trading days
            # Unless FORCE_OI_TASK env var is set (for testing)
            if (not self.is_market_hours() or not self.is_trading_day()) and not os.getenv('FORCE_OI_TASK'):
                return

            logger.info("Starting OI Persistence Task...")

            # Need to get data provider without session.
            # Background thread has no Flask session, so we pass user explicitly.
            # The DATA_PROVIDER env flag determines whether to use Kite or Fyers.
            from trading_app.app.routes.api import get_data_provider
            provider = get_data_provider(user='Mine')
            
            if not provider:
                logger.warning("OI Persistence: Could not get data provider. Check DATA_PROVIDER flag and credentials in env.")
                return
                
            from trading_app.service.open_interest_service import OpenInterestService
            oi_service = OpenInterestService(provider)
            
            symbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
            for symbol in symbols:
                try:
                    logger.info(f"OI Persistence: Fetching data for {symbol}...")
                    # CURRENT (nearest) expiry, even on expiry day: these rows feed
                    # the dashboard PCR/Vega tabs and the live /open-interest chain,
                    # which must all track the actively traded (expiring) contract.
                    # Only the EOD historic recorder (dashboard/oi_historic_data.py)
                    # rolls to the next expiry (use_next_expiry=True).
                    data = oi_service.get_open_interest_data(symbol)
                    
                    if data.get('success'):
                        oi_service.save_oi_snapshot(symbol, data)
                        logger.info(f"✅ OI Persistence: Saved snapshot for {symbol}")
                    else:
                        logger.warning(f"⚠️ OI Persistence: Failed to fetch {symbol}: {data.get('error')}")
                        
                except Exception as inner_e:
                    logger.error(f"❌ OI Persistence: Error processing {symbol}: {inner_e}", exc_info=True)
                    
        except Exception as e:
            logger.error(f"Unexpected error in OI persistence task: {e}", exc_info=True)


    def _run_fii_sector_task(self):
        """4:00 PM IST: scrape NSE FPI sector limits and save to SQLite."""
        try:
            if not self.is_trading_day():
                return
            logger.info("[FIISector Scheduler] Fetching sector FPI limits...")
            from trading_app.service.fii_sector_service import FIISectorService
            svc = FIISectorService()
            rows = svc.get_sector_fpi_data()
            if rows:
                svc.save_snapshot(rows)
                logger.info(f"[FIISector Scheduler] Saved {len(rows)} sector rows")
            else:
                logger.warning("[FIISector Scheduler] No data returned — skipping save")
        except Exception as e:
            logger.error(f"[FIISector Scheduler] Error: {e}", exc_info=True)

    def _run_ema_narrow_prewarm_task(self):
        """~4:05 PM IST (after market close): refresh the local daily-candle
        store for every NSE equity stock so the EMA Narrow scanner's next-day
        scans run from local files instead of the live broker API."""
        try:
            if not self.is_trading_day():
                return
            logger.info("[EMA Narrow Prewarm] Starting end-of-day candle store refresh...")
            from trading_app.service.provider_logic import get_data_provider
            provider = get_data_provider(user=self._rtp_username())
            if not provider:
                logger.warning("[EMA Narrow Prewarm] No data provider available — skipping")
                return
            from trading_app.filters.ema_narrow_prewarm import prime_equity_store

            def _log_progress(done, total):
                logger.info(f"[EMA Narrow Prewarm] {done}/{total} stocks refreshed...")

            stats = prime_equity_store(provider, progress_cb=_log_progress)
            logger.info(
                f"[EMA Narrow Prewarm] Done — {stats['done']}/{stats['total']} stocks "
                f"({stats['failed']} failed) in {stats['elapsed_sec']:.0f}s"
            )
        except Exception as e:
            logger.error(f"[EMA Narrow Prewarm] Unexpected error: {e}", exc_info=True)

    # ── RTP algo management ───────────────────────────────────────────────────

    def _rtp_username(self) -> str:
        import os
        return os.getenv('MONITORING_USERNAME', 'Mine')

    def _rtp_active(self) -> bool:
        from trading_app.app.utils.user_env import UserEnvManager
        val = UserEnvManager.get_user_var(self._rtp_username(), 'EMA_RTP_1M_ACTIVE', 'false')
        return val.strip().lower() == 'true'

    def _ensure_rtp_running(self, source: str = '', variant: str = '1m') -> None:
        """Start an RTP monitoring thread (per timeframe variant) if not already running.
        Always starts during market hours regardless of the variant's active flag —
        the kill-switch lives inside the loop and gates signal detection only.
        Gating thread startup on the flag would leave the algo dormant until the
        next 5-min watchdog tick after the user enables it.
        Guards against duplicate starts via the module-level instance registry.
        """
        tag = f"RTP{'' if variant == '1m' else variant} {source}"
        try:
            if not self.is_trading_day():
                return
            now = datetime.now()
            h, m = now.hour, now.minute
            # Window: 9:15 AM – 3:27 PM IST. The monitor loop exits at 3:28 PM
            # (m >= 28); the watchdog must close before that so it doesn't restart
            # a thread that just exited for EOD.
            in_window = (h > 9 or (h == 9 and m >= 15)) and (h < 15 or (h == 15 and m <= 27))
            if not in_window:
                return
            from trading_app.algo.rtp_railway_track.rtp_algo import RTPAlgo, get_instance
            username = self._rtp_username()
            existing = get_instance(username, variant)
            if existing and existing.is_running():
                return  # Already alive
            if existing:
                logger.warning(f"[{tag}] Monitoring thread dead — restarting")
            else:
                logger.info(f"[{tag}] Starting monitoring thread for user={username}")
            RTPAlgo(username=username, variant=variant).start()
        except Exception as e:
            logger.error(f"[{tag}] _ensure_rtp_running failed: {e}", exc_info=True)

    def _start_rtp_monitoring(self) -> None:
        """9:15 AM weekdays: start RTP 1m Railway Track algo monitoring thread."""
        self._ensure_rtp_running(source='Scheduler', variant='1m')

    def _watchdog_rtp(self) -> None:
        """Every 5 minutes during market hours: restart RTP 1m thread if it crashed."""
        self._ensure_rtp_running(source='Watchdog', variant='1m')

    def _start_rtp30s_monitoring(self) -> None:
        """9:15 AM weekdays: start RTP 30s Railway Track algo monitoring thread."""
        self._ensure_rtp_running(source='Scheduler', variant='30s')

    def _watchdog_rtp30s(self) -> None:
        """Every 5 minutes during market hours: restart RTP 30s thread if it crashed."""
        self._ensure_rtp_running(source='Watchdog', variant='30s')

    def _start_rtp2m_monitoring(self) -> None:
        """9:15 AM weekdays: start RTP 2m Railway Track algo monitoring thread."""
        self._ensure_rtp_running(source='Scheduler', variant='2m')

    def _watchdog_rtp2m(self) -> None:
        """Every 5 minutes during market hours: restart RTP 2m thread if it crashed."""
        self._ensure_rtp_running(source='Watchdog', variant='2m')

    def _start_rtp3m_monitoring(self) -> None:
        """9:15 AM weekdays: start RTP 3m Railway Track algo monitoring thread."""
        self._ensure_rtp_running(source='Scheduler', variant='3m')

    def _watchdog_rtp3m(self) -> None:
        """Every 5 minutes during market hours: restart RTP 3m thread if it crashed."""
        self._ensure_rtp_running(source='Watchdog', variant='3m')

    def _start_rtp5m_monitoring(self) -> None:
        """9:15 AM weekdays: start RTP 5m Railway Track algo monitoring thread."""
        self._ensure_rtp_running(source='Scheduler', variant='5m')

    def _watchdog_rtp5m(self) -> None:
        """Every 5 minutes during market hours: restart RTP 5m thread if it crashed."""
        self._ensure_rtp_running(source='Watchdog', variant='5m')

    # ── 2nd 30-Sec Candle algo management ─────────────────────────────────────

    def _ensure_sc_running(self, source: str = '') -> None:
        """Start the 2nd-candle monitoring thread if it is not already running.
        Mirrors _ensure_rtp_running: starts during market hours regardless of
        SC_ALGO_ACTIVE — the kill-switch lives inside the loop and gates entries only.
        """
        try:
            if not self.is_trading_day():
                return
            now = datetime.now()
            h, m = now.hour, now.minute
            in_window = (h > 9 or (h == 9 and m >= 15)) and (h < 15 or (h == 15 and m <= 27))
            if not in_window:
                return
            from trading_app.algo.second_candle.second_candle_algo import SecondCandleAlgo, get_instance
            username = self._rtp_username()
            existing = get_instance(username)
            if existing and existing.is_running():
                return  # Already alive
            if existing:
                logger.warning(f"[SC {source}] Monitoring thread dead — restarting")
            else:
                logger.info(f"[SC {source}] Starting monitoring thread for user={username}")
            SecondCandleAlgo(username=username).start()
        except Exception as e:
            logger.error(f"[SC {source}] _ensure_sc_running failed: {e}", exc_info=True)

    def _start_sc_monitoring(self) -> None:
        """9:15 AM weekdays: start 2nd 30-Sec Candle algo monitoring thread."""
        self._ensure_sc_running(source='Scheduler')

    def _watchdog_sc(self) -> None:
        """Every 5 minutes during market hours: restart 2nd-candle thread if it crashed."""
        self._ensure_sc_running(source='Watchdog')

    # ── Intrinsic ATM Range Breakout algo management (paper trade) ────────────

    def _ensure_intrinsic_range_running(self, source: str = '') -> None:
        """Start the Intrinsic Range monitoring thread if it is not already running.
        Mirrors _ensure_sc_running: starts during market hours regardless of
        EMA_INTRINSIC_RANGE_ACTIVE — the kill-switch lives inside the loop and
        gates paper entries only. All executions here are simulated (paper trade);
        no broker orders are placed.
        """
        try:
            if not self.is_trading_day():
                return
            now = datetime.now()
            h, m = now.hour, now.minute
            in_window = (h > 9 or (h == 9 and m >= 15)) and (h < 15 or (h == 15 and m <= 27))
            if not in_window:
                return
            from trading_app.algo.intrinsic_range.intrinsic_range_algo import IntrinsicRangeAlgo, get_instance
            username = self._rtp_username()
            existing = get_instance(username)
            if existing and existing.is_running():
                return  # Already alive
            if existing:
                logger.warning(f"[IntrinsicRange {source}] Monitoring thread dead — restarting")
            else:
                logger.info(f"[IntrinsicRange {source}] Starting monitoring thread for user={username}")
            IntrinsicRangeAlgo(username=username).start()
        except Exception as e:
            logger.error(f"[IntrinsicRange {source}] _ensure_intrinsic_range_running failed: {e}", exc_info=True)

    def _start_intrinsic_range_monitoring(self) -> None:
        """9:15 AM weekdays: start Intrinsic Range algo monitoring thread."""
        self._ensure_intrinsic_range_running(source='Scheduler')

    def _watchdog_intrinsic_range(self) -> None:
        """Every 5 minutes during market hours: restart Intrinsic Range thread if it crashed."""
        self._ensure_intrinsic_range_running(source='Watchdog')

    def _run_historic_oi_record_task(self):
        """8:00 PM IST: fetch and persist daily OI snapshot for all symbols."""
        try:
            if not self.is_trading_day():
                return
            logger.info("[HistoricOI Scheduler] Recording daily OI snapshot...")
            from trading_app.service.provider_logic import get_data_provider
            # Provider is optional at 8 PM: the EOD record is built from the
            # official NSE bhavcopy. A live broker session is only used as an
            # intraday fallback, so a None/expired provider must not skip the job.
            try:
                provider = get_data_provider(user='Mine')
            except Exception as e:
                logger.warning(f"[HistoricOI Scheduler] Provider unavailable ({e}) — using bhavcopy only")
                provider = None
            if not provider:
                logger.info("[HistoricOI Scheduler] No live broker session — recording from bhavcopy only")
            from trading_app.dashboard.oi_historic_data import fetch_and_store_all
            results = fetch_and_store_all(provider=provider)
            for r in results:
                if r.get('success'):
                    logger.info(f"[HistoricOI Scheduler] ✅ {r['symbol']} recorded")
                else:
                    logger.warning(f"[HistoricOI Scheduler] ⚠️ {r['symbol']}: {r.get('error')}")
        except Exception as e:
            logger.error(f"[HistoricOI Scheduler] Unexpected error: {e}", exc_info=True)

    def _run_historic_oi_catchup(self):
        """Startup recovery: backfill any recent trading day whose 8 PM OI record
        was missed because the process wasn't alive at 20:00 IST.

        The historic_oi_record cron only fires when this process happens to be
        running at that exact minute — a laptop asleep, a restart, or a machine
        that's only up during market hours silently drops that evening's record
        with no retry (this is what left gaps like May 28 / June 26). On every
        startup we look back over the last week and rebuild any missing weekday
        from the official NSE bhavcopy. refresh_record is self-limiting: on
        holidays / dates with no published bhavcopy it returns 'No data
        available' and no row is created, so this never invents fake rows.
        """
        try:
            try:
                from zoneinfo import ZoneInfo
                now_ist = datetime.now(ZoneInfo('Asia/Kolkata'))
            except Exception:
                now_ist = datetime.now()

            from trading_app.dashboard.oi_historic_data import (
                get_all_records, refresh_record,
            )
            recorded = {
                r.get('date') for r in get_all_records()
                if r.get('symbol') == 'NIFTY'
            }

            # Provider is a best-effort intraday fallback; the backfill is built
            # from the bhavcopy, so a None/expired broker session is fine.
            try:
                from trading_app.service.provider_logic import get_data_provider
                provider = get_data_provider(user='Mine')
            except Exception:
                provider = None

            today = now_ist.date()
            backfilled = 0
            for back in range(1, 8):
                d = today - timedelta(days=back)
                if d.weekday() >= 5:            # skip Sat/Sun
                    continue
                ds = d.isoformat()
                if ds in recorded:
                    continue
                res = refresh_record(ds, 'NIFTY', provider=provider)
                if res.get('success'):
                    backfilled += 1
                    logger.info(f"[HistoricOI Catchup] ✅ backfilled missed day {ds}")
                else:
                    logger.info(
                        f"[HistoricOI Catchup] {ds}: {res.get('error')} "
                        "(holiday / no bhavcopy — skipped)"
                    )

            # Today: if the 8 PM window has already passed and today's record is
            # still missing, run the full EOD task now (it also patches FII flow).
            tds = today.isoformat()
            if now_ist.weekday() < 5 and now_ist.hour >= 20 and tds not in recorded:
                logger.info(f"[HistoricOI Catchup] Today {tds} missing past 8 PM — recording now")
                self._run_historic_oi_record_task()

            if backfilled:
                logger.info(f"[HistoricOI Catchup] Backfilled {backfilled} missed trading day(s)")
        except Exception as e:
            logger.error(f"[HistoricOI Catchup] Unexpected error: {e}", exc_info=True)


# Global scheduler instance
market_scheduler = MarketScheduler()


def init_scheduler(app):
    """Initialize scheduler with Flask app."""
    if not APSCHEDULER_AVAILABLE:
        logger.info("APScheduler not installed - backend scheduler disabled")
        return market_scheduler

    logger.info("Initializing market scheduler...")
    with app.app_context():
        market_scheduler.start()
        # Startup recovery: if the server restarted during market hours the 9:15 AM
        # cron already passed and the algo thread was never launched. Start it now.
        market_scheduler._ensure_rtp_running(source='Startup', variant='1m')
        market_scheduler._ensure_rtp_running(source='Startup', variant='30s')
        market_scheduler._ensure_rtp_running(source='Startup', variant='3m')
        market_scheduler._ensure_rtp_running(source='Startup', variant='5m')
        market_scheduler._ensure_sc_running(source='Startup')
        market_scheduler._ensure_intrinsic_range_running(source='Startup')
        # Historic OI self-heal: backfill any recent trading day whose 8 PM
        # record was missed while this process was down. Runs off-thread so a
        # slow NSE bhavcopy fetch never blocks app startup.
        try:
            import threading
            threading.Thread(
                target=market_scheduler._run_historic_oi_catchup,
                name='historic-oi-catchup',
                daemon=True,
            ).start()
        except Exception as e:
            logger.error(f"[HistoricOI Catchup] failed to launch startup thread: {e}")

    import atexit
    atexit.register(market_scheduler.stop)
    return market_scheduler
