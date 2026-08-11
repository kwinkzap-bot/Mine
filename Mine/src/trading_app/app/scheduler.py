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

        # OI Crossover scan — every 3 minutes through the session. One Fyers
        # optionchain call per symbol across ~214 names, paced at 2.5 req/s to
        # stay under the endpoint's per-minute quota, takes ~102s; a 1-minute
        # cadence could not fit the sweep at all. The job itself is a no-op
        # outside 9:15-15:40 — the cron's 9-15 hour range fires either side of
        # the session and OICrossoverService.scan() turns those into skips.
        # Offset to :45 so it never lands on the OI persistence job's :30.
        self.oi_crossover_job = self.scheduler.add_job(
            self._run_oi_crossover_task,
            CronTrigger(
                day_of_week='mon-fri',
                hour='9-15',
                minute='*/3',
                second='45',
                timezone='Asia/Kolkata',
            ),
            id='oi_crossover_scan',
            name='OI Crossover Scan',
            replace_existing=True,
            misfire_grace_time=120,
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

        # Expiry High/Low Breakout scan — every hour on the hour during market
        # hours. The 60-minute candle grid starts at 9:15, so its first three
        # candles close at 10:15/11:15/12:15; minute=16 gives the broker a
        # short buffer to finalize the candle before we fetch it. Each run
        # persists its own notification row (see notification_service.py) so
        # every hour's scan results are kept distinct, not overwritten.
        self.scheduler.add_job(
            self._run_expiry_hl_notification_task,
            CronTrigger(
                day_of_week='mon-fri',
                hour='10-15',
                minute=16,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='expiry_hl_breakout_notification',
            name='Expiry High/Low Breakout Hourly Notification',
            replace_existing=True,
            misfire_grace_time=300,
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

        # 30-Min Opening Fakeout algo: start at 8:30 AM weekdays — 45 minutes
        # before the open, so the thread is already up and its data provider
        # proven working by the time the first candle prints, rather than
        # finding out at 9:15 that something needs a manual fix.
        self.scheduler.add_job(
            self._start_tmf_monitoring,
            CronTrigger(
                day_of_week='mon-fri',
                hour=8,
                minute=30,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='tmf_algo_start',
            name='30-Min Opening Fakeout Algo Start',
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Watchdog: restart the TMF thread if it crashes mid-day. Starts at
        # 8 so the pre-open stretch is covered too — that is exactly when a
        # not-yet-refreshed broker token can abort the thread on its first
        # try, and a 5-minute retry is what turns that into a non-event.
        self.scheduler.add_job(
            self._watchdog_tmf,
            CronTrigger(
                day_of_week='mon-fri',
                hour='8-15',
                minute='*/5',
                second=30,
                timezone='Asia/Kolkata',
            ),
            id='tmf_algo_watchdog',
            name='30-Min Opening Fakeout Algo Watchdog',
            replace_existing=True,
            misfire_grace_time=60,
        )

        # EMA Confluence Breakout algo (paper trade, futures): start at 8:30 AM
        # weekdays, same pre-open head start as the 30-Min Fakeout algo.
        self.scheduler.add_job(
            self._start_ema_confluence_monitoring,
            CronTrigger(
                day_of_week='mon-fri',
                hour=8,
                minute=30,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='ema_confluence_algo_start',
            name='EMA Confluence Breakout Algo Start',
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Watchdog: restart the EMA Confluence thread if it crashes mid-day
        # (from 8, covering the pre-open stretch — see the TMF watchdog).
        self.scheduler.add_job(
            self._watchdog_ema_confluence,
            CronTrigger(
                day_of_week='mon-fri',
                hour='8-15',
                minute='*/5',
                second=30,
                timezone='Asia/Kolkata',
            ),
            id='ema_confluence_algo_watchdog',
            name='EMA Confluence Breakout Algo Watchdog',
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
                    # CURRENT (nearest) expiry, even on expiry day: these oi_history
                    # rows feed the dashboard PCR tab and the live /open-interest
                    # chain, which must track the actively traded (expiring)
                    # contract. The next expiry is captured separately below into
                    # oi_expiry_snapshots — it never touches oi_history.
                    data = oi_service.get_open_interest_data(symbol)

                    if data.get('success'):
                        oi_service.save_oi_snapshot(symbol, data)
                        logger.info(f"✅ OI Persistence: Saved snapshot for {symbol}")
                    else:
                        logger.warning(f"⚠️ OI Persistence: Failed to fetch {symbol}: {data.get('error')}")

                    # Second chain: the expiry after the one above, stored in
                    # oi_expiry_snapshots. On expiry day the nearest chain is
                    # 0-DTE and its Vega series is not comparable to the
                    # reference, so the dashboard needs a live series for the
                    # following weekly too. Failures here must not cost us the
                    # primary snapshot, hence the separate try block.
                    try:
                        next_data = oi_service.get_open_interest_data(symbol, expiry_offset=1)
                        if next_data.get('success'):
                            saved_expiry = oi_service.save_expiry_snapshot(
                                symbol, next_data,
                                skip_expiry=oi_service.chain_expiry(data) if data.get('success') else None)
                            if saved_expiry:
                                logger.info(f"✅ OI Persistence: Saved {symbol} next-expiry "
                                            f"snapshot ({saved_expiry})")
                        else:
                            logger.warning(f"⚠️ OI Persistence: next-expiry fetch failed for "
                                           f"{symbol}: {next_data.get('error')}")
                    except Exception as next_e:
                        logger.error(f"❌ OI Persistence: next-expiry error for {symbol}: {next_e}")

                except Exception as inner_e:
                    logger.error(f"❌ OI Persistence: Error processing {symbol}: {inner_e}", exc_info=True)
                    
        except Exception as e:
            logger.error(f"Unexpected error in OI persistence task: {e}", exc_info=True)


    def _run_oi_crossover_task(self):
        """Every 3 min in-session: sweep the F&O universe for CE/PE OI-change
        crossovers. Skipped outside market hours — the change lines are
        measured against the 9:15 open and simply don't move when the market
        is shut, so an out-of-hours scan would only re-log the close.

        The session gate itself lives in OICrossoverService.scan() — every
        caller needs it, not just this job — so this only skips the obvious
        non-days early to save building a provider for nothing."""
        try:
            if not self.is_trading_day():
                return

            from trading_app.app.routes.api import get_data_provider
            provider = get_data_provider(user='Mine')
            if not provider:
                logger.warning("[OIX Scheduler] No data provider — skipping scan")
                return

            from trading_app.service.oi_crossover_service import OICrossoverService
            svc = OICrossoverService(provider)

            # Trim old series points once per session rather than on every
            # scan: it's a whole-table delete and the row count only matters
            # day-over-day.
            today = datetime.now().strftime('%Y-%m-%d')
            if getattr(self, '_oix_purged_on', None) != today:
                self._oix_purged_on = today
                removed = svc.purge()
                if removed:
                    logger.info(f"[OIX Scheduler] Purged {removed} old series rows")

            result = svc.scan()
            # The cron fires either side of the session (it is a 9-15 hour
            # range), so a skip is the expected outcome several times a day
            # and is not worth a warning. Only real failures are.
            if not result.get('success') and not result.get('skipped'):
                logger.warning(f"[OIX Scheduler] Scan failed: {result.get('error')}")
        except Exception as e:
            logger.error(f"[OIX Scheduler] Error: {e}", exc_info=True)

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
            # Mark the day done so the startup catch-up doesn't repeat this run.
            self._set_prewarm_marker_date(datetime.now().date().isoformat())
        except Exception as e:
            logger.error(f"[EMA Narrow Prewarm] Unexpected error: {e}", exc_info=True)

    def _run_expiry_hl_notification_task(self):
        """Runs every hour during market hours: scans all F&O stocks for an
        Expiry High/Low breakout on the 60-minute candle — same rule as
        the Monthly Expiry Breakout filter (touch-then-close-beyond the
        expiry level, close beyond every EMA 20/50/100/200, and touching
        at least one of them) — and stores the result as a notification
        (see notification_service.py) so the frontend bell shows a fresh
        entry every hour, even when no stock breaks out."""
        try:
            if not self.is_trading_day():
                return
            logger.info("[Expiry H/L Notify] Running hourly Expiry H/L breakout scan...")

            from trading_app.service.provider_logic import get_data_provider
            provider = get_data_provider(user=self._rtp_username())
            if not provider:
                logger.warning("[Expiry H/L Notify] No data provider available — skipping")
                return

            from trading_app.filters.cpr_filter import CPRFilterService
            from trading_app.filters.expiry_hl_scanner import filter_expiry_hl_breakout
            from trading_app.service.notification_service import create_notification

            cpr_service = CPRFilterService(kite_instance=provider)
            results = filter_expiry_hl_breakout(cpr_service, timeframe='60minute')
            buy_signals = results.get('buy', [])
            sell_signals = results.get('sell', [])

            now = datetime.now()
            time_label = now.strftime('%H:%M')
            total = len(buy_signals) + len(sell_signals)
            summary = (
                f"{len(buy_signals)} BUY, {len(sell_signals)} SELL"
                if total else "No breakouts this hour"
            )
            create_notification(
                category='expiry_hl_breakout',
                title=f"Expiry H/L Breakout — {time_label}",
                summary=summary,
                data={
                    'timeframe': '60minute',
                    'date': now.strftime('%Y-%m-%d'),
                    'time': time_label,
                    'buy': buy_signals,
                    'sell': sell_signals,
                },
            )
            logger.info(f"[Expiry H/L Notify] Saved notification — {summary}")

            self._send_expiry_hl_telegram(buy_signals, sell_signals, time_label)
        except Exception as e:
            logger.error(f"[Expiry H/L Notify] Unexpected error: {e}", exc_info=True)

    # Long lists would blow past Telegram's 4096-char cap and get silently
    # truncated mid-symbol, so each side is capped and the rest counted.
    _EXPIRY_HL_TG_MAX_PER_SIDE = 20

    def _send_expiry_hl_telegram(self, buy_signals: list, sell_signals: list, time_label: str) -> None:
        """Fire-and-forget Telegram alert for the hourly Expiry H/L breakout
        scan. Unlike the in-app bell — which logs every hour so you can see the
        scan ran — this only fires when at least one stock actually broke out;
        an hourly 'no breakouts' ping would be pure noise.

        Credentials come from the user's env file; with either unset, or with
        EXPIRY_HL_TELEGRAM=false, this is silently a no-op and the bell keeps
        working on an install that has never set Telegram up."""
        if not (buy_signals or sell_signals):
            return
        try:
            from trading_app.app.utils.user_env import UserEnvManager
            user = self._rtp_username()

            def _uvar(key: str, default: str = '') -> str:
                return (UserEnvManager.get_user_var(user, key, default) or '').strip()

            if _uvar('EXPIRY_HL_TELEGRAM', 'true').lower() == 'false':
                return
            token   = _uvar('TELEGRAM_BOT_TOKEN')
            chat_id = _uvar('TELEGRAM_CHAT_ID')
            if not (token and chat_id):
                return

            def _lines(signals: list, label: str, emoji: str) -> list:
                if not signals:
                    return []
                out = [f"{emoji} {label} ({len(signals)})"]
                for s in signals[:self._EXPIRY_HL_TG_MAX_PER_SIDE]:
                    level = s.get('expiry_high') if label == 'BUY' else s.get('expiry_low')
                    out.append(f"  {s.get('symbol')}  ₹{s.get('current_price')}  "
                               f"(exp {'H' if label == 'BUY' else 'L'} ₹{level})")
                extra = len(signals) - self._EXPIRY_HL_TG_MAX_PER_SIDE
                if extra > 0:
                    out.append(f"  …+{extra} more")
                return out

            message = '\n'.join(
                [f"📊 Expiry H/L Breakout — {time_label} (1H)"]
                + _lines(buy_signals, 'BUY', '🟢')
                + _lines(sell_signals, 'SELL', '🔴')
            )

            def _send() -> None:
                try:
                    from trading_app.service.telegram_service import TelegramService
                    result = TelegramService(token=token, chat_id=chat_id).send_text(message)
                    if result.get('success'):
                        logger.info("[Expiry H/L Notify] Telegram alert sent")
                    else:
                        logger.error(f"[Expiry H/L Notify] Telegram alert failed: {result.get('error')}")
                except Exception as e:
                    logger.error(f"[Expiry H/L Notify] Telegram alert failed: {e}")

            # Off-thread: send_text allows a 10s HTTP timeout, and the scan job
            # shouldn't sit on the scheduler's worker waiting for Telegram.
            import threading
            threading.Thread(target=_send, daemon=True, name='ExpiryHLNotify').start()
        except Exception as e:
            logger.error(f"[Expiry H/L Notify] Telegram alert setup failed: {e}")

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

    # ── 30-Min Opening Fakeout algo management ─────────────────────────────────

    def _algo_enabled(self, var_name: str) -> bool:
        """Persisted Start/Stop intent from the algo page's buttons.

        Defaults to true: the algo runs every trading day on its own. Clicking
        Stop writes false to the user's .env, which keeps the scheduler and the
        5-min watchdog from restarting the thread — across days and app
        restarts — until Start writes true again.
        """
        from trading_app.app.utils.user_env import UserEnvManager
        val = UserEnvManager.get_user_var(self._rtp_username(), var_name, 'true')
        return (val or 'true').strip().lower() != 'false'

    def _ensure_tmf_running(self, source: str = '') -> None:
        """Start the 30-Min Opening Fakeout monitoring thread if it is not
        already running. Mirrors _ensure_sc_running: starts during market
        hours regardless of TMF_ALGO_ACTIVE — the kill-switch lives inside
        the loop and gates order placement only (the thread always scans
        and logs signals). TMF_ALGO_ENABLED is separate: it is the user's
        Stop click, and it does keep the thread from starting.
        """
        try:
            if not self.is_trading_day():
                return
            if not self._algo_enabled('TMF_ALGO_ENABLED'):
                return  # User clicked Stop — stay stopped until they click Start
            now = datetime.now()
            h, m = now.hour, now.minute
            # Opens at 8:30, 45 minutes before the market. The thread idles
            # until 10:45 (it cannot scan before candles 1-3 have closed), so
            # the head start costs nothing and buys time to notice a dead
            # provider or an unrefreshed broker token before it matters.
            in_window = (h > 8 or (h == 8 and m >= 30)) and (h < 15 or (h == 15 and m <= 27))
            if not in_window:
                return
            from trading_app.algo.thirty_min_fakeout.tmf_algo import TMFAlgo, get_instance
            username = self._rtp_username()
            existing = get_instance(username)
            if existing and existing.is_running():
                return  # Already alive
            if existing:
                logger.warning(f"[TMF {source}] Monitoring thread dead — restarting")
            else:
                logger.info(f"[TMF {source}] Starting monitoring thread for user={username}")
            TMFAlgo(username=username).start()
        except Exception as e:
            logger.error(f"[TMF {source}] _ensure_tmf_running failed: {e}", exc_info=True)

    def _start_tmf_monitoring(self) -> None:
        """9:15 AM weekdays: start 30-Min Opening Fakeout algo monitoring thread."""
        self._ensure_tmf_running(source='Scheduler')

    def _watchdog_tmf(self) -> None:
        """Every 5 minutes during market hours: restart TMF thread if it crashed."""
        self._ensure_tmf_running(source='Watchdog')

    # ── EMA Confluence Breakout algo management (paper trade, futures) ────────

    def _ensure_ema_confluence_running(self, source: str = '') -> None:
        """Start the EMA Confluence Breakout monitoring thread if it is not
        already running. Mirrors _ensure_tmf_running: starts
        during market hours regardless of EMA_CONFLUENCE_ACTIVE — the
        kill-switch lives inside the loop and gates paper entries only. All
        executions here are simulated (paper trade); no broker orders are
        placed. EMA_CONFLUENCE_ENABLED is separate: it is the user's Stop
        click, and it does keep the thread from starting.
        """
        try:
            if not self.is_trading_day():
                return
            if not self._algo_enabled('EMA_CONFLUENCE_ENABLED'):
                return  # User clicked Stop — stay stopped until they click Start
            now = datetime.now()
            h, m = now.hour, now.minute
            # 8:30, same pre-open head start as TMF (see _ensure_tmf_running).
            in_window = (h > 8 or (h == 8 and m >= 30)) and (h < 15 or (h == 15 and m <= 27))
            if not in_window:
                return
            from trading_app.algo.ema_confluence.ema_confluence_algo import EmaConfluenceAlgo, get_instance
            username = self._rtp_username()
            existing = get_instance(username)
            if existing and existing.is_running():
                return  # Already alive
            if existing:
                logger.warning(f"[EmaConfluence {source}] Monitoring thread dead — restarting")
            else:
                logger.info(f"[EmaConfluence {source}] Starting monitoring thread for user={username}")
            EmaConfluenceAlgo(username=username).start()
        except Exception as e:
            logger.error(f"[EmaConfluence {source}] _ensure_ema_confluence_running failed: {e}", exc_info=True)

    def _start_ema_confluence_monitoring(self) -> None:
        """9:15 AM weekdays: start EMA Confluence Breakout algo monitoring thread."""
        self._ensure_ema_confluence_running(source='Scheduler')

    def _watchdog_ema_confluence(self) -> None:
        """Every 5 minutes during market hours: restart EMA Confluence thread if it crashed."""
        self._ensure_ema_confluence_running(source='Watchdog')

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

    def _prewarm_marker_path(self) -> str:
        """Marker file recording the last date the EMA Narrow prewarm completed.
        Lives beside the candle store it describes."""
        import os
        from trading_app.filters.candle_store import DATA_DIR
        return os.path.join(DATA_DIR, '.prewarm_date')

    def _prewarm_marker_date(self) -> Optional[str]:
        try:
            with open(self._prewarm_marker_path()) as f:
                return f.read().strip() or None
        except OSError:
            return None  # never run, or store dir not created yet

    def _set_prewarm_marker_date(self, day: str) -> None:
        try:
            import os
            path = self._prewarm_marker_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(day)
        except OSError as e:
            # Non-fatal: worst case the prewarm runs again on the next restart.
            logger.warning(f"[EOD Catchup] Could not write prewarm marker: {e}")

    def _run_eod_catchup(self):
        """Startup recovery for the two post-close jobs.

        fii_sector_snapshot (4:00 PM) and ema_narrow_prewarm (4:05 PM) only fire
        if this process happens to be alive at that exact minute. Start the app
        at 4:12 PM — or leave it down all day — and the day is silently lost with
        no retry (this is what left gaps like Jul 31 / Aug 4). On every startup,
        run whichever of the two is already due but hasn't happened today.

        Runs the two sequentially for the same reason their crons are staggered
        5 minutes apart: both hit rate-limited external APIs.
        """
        try:
            from zoneinfo import ZoneInfo
            now_ist = datetime.now(ZoneInfo('Asia/Kolkata'))
        except Exception:
            now_ist = datetime.now()

        if now_ist.weekday() >= 5:
            return

        today = now_ist.date().isoformat()

        # 4:00 PM FII sector snapshot — skip if today's rows already landed.
        try:
            if now_ist.time() >= time(16, 0):
                from trading_app.service.fii_sector_service import FIISectorService
                if today in set(FIISectorService.get_periods()):
                    logger.info(f"[EOD Catchup] FII sector already recorded for {today}")
                else:
                    logger.info(f"[EOD Catchup] FII sector missing for {today} — running now")
                    self._run_fii_sector_task()
        except Exception as e:
            logger.error(f"[EOD Catchup] FII sector catch-up failed: {e}", exc_info=True)

        # 4:05 PM EMA Narrow prewarm — only once per day. A same-day re-run is
        # usually a seconds-long tail refresh, but when the store is stale (the
        # process was down for a day or more) it is a full 2300-stock download
        # that takes ~10 minutes and gets throttled 429 by the broker. launchd
        # restarts this process on crash, so without the marker an evening
        # restart loop would stack overlapping downloads onto that same
        # rate-limited API.
        try:
            if now_ist.time() >= time(16, 5):
                if self._prewarm_marker_date() == today:
                    logger.info(f"[EOD Catchup] EMA Narrow prewarm already ran for {today}")
                else:
                    logger.info("[EOD Catchup] Running EMA Narrow prewarm refresh")
                    self._run_ema_narrow_prewarm_task()  # writes the marker on success
        except Exception as e:
            logger.error(f"[EOD Catchup] EMA Narrow prewarm catch-up failed: {e}", exc_info=True)

    def _run_startup_catchup(self):
        """All startup self-heal, on one background thread so a slow NSE fetch
        never blocks app startup and the catch-ups don't compete for the same
        rate-limited APIs at once. Each step is isolated so one failure doesn't
        skip the rest."""
        try:
            self._run_historic_oi_catchup()
        except Exception as e:
            logger.error(f"[Startup Catchup] Historic OI step failed: {e}", exc_info=True)
        try:
            self._run_eod_catchup()
        except Exception as e:
            logger.error(f"[Startup Catchup] EOD step failed: {e}", exc_info=True)


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
        # Every variant that has an rtp*_algo_start job must be listed here —
        # 2m was missed when it was added, leaving it dormant on restarts.
        for variant in ('1m', '30s', '2m', '3m', '5m'):
            market_scheduler._ensure_rtp_running(source='Startup', variant=variant)
        market_scheduler._ensure_sc_running(source='Startup')
        market_scheduler._ensure_tmf_running(source='Startup')
        market_scheduler._ensure_ema_confluence_running(source='Startup')
        # Self-heal for the jobs a late start would have missed: the 8 PM
        # historic OI record, plus the 4:00 PM FII sector snapshot and 4:05 PM
        # EMA Narrow prewarm. Runs off-thread so a slow NSE fetch never blocks
        # app startup.
        try:
            import threading
            threading.Thread(
                target=market_scheduler._run_startup_catchup,
                name='startup-catchup',
                daemon=True,
            ).start()
        except Exception as e:
            logger.error(f"[Startup Catchup] failed to launch startup thread: {e}")

    import atexit
    atexit.register(market_scheduler.stop)
    return market_scheduler
