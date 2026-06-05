"""
Background scheduler for recurring tasks during market hours.
Handles market hours checking and scheduled API calls.
"""
from datetime import datetime, time
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
        
        # Schedule CPR filter to run every 5 minutes during market hours
        # Cron expression: Every 5 minutes on weekdays between 9:15 AM and 3:40 PM IST
        self.cpr_filter_job = self.scheduler.add_job(
            self._run_cpr_filter_task,
            CronTrigger(  # type: ignore
                day_of_week='mon-fri',  # Monday to Friday
                hour='9-15',             # 9 AM to 3 PM
                minute='*/5',            # Every 5 minutes
                second='0'
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
        
        self.scheduler.add_job(
            self._run_straddle_rollover,
            CronTrigger(
                day_of_week='tue',
                hour=15,
                minute=15,
                second=0,
                timezone='Asia/Kolkata',
            ),
            id='nifty_straddle_rollover',
            name='NIFTY Weekly Straddle Rollover',
            replace_existing=True,
            misfire_grace_time=120,
        )

        self.scheduler.start()
        jobs = {j.id: str(j.next_run_time) for j in self.scheduler.get_jobs()}
        logger.info(f"Market scheduler started — jobs registered: {list(jobs.keys())}")
        logger.info(f"[Straddle Rollover] next run: {jobs.get('nifty_straddle_rollover', 'NOT REGISTERED')}")
    
    def stop(self):
        """Stop the background scheduler."""
        if not self.scheduler or not self.scheduler.running:
            return
        self.scheduler.shutdown()
        logger.info("Market scheduler stopped")
    
    def _run_straddle_rollover(self):
        """Tuesday 3:15 PM: exit current week straddle and enter next week."""
        import os
        try:
            if not self.is_trading_day():
                return
            from trading_app.app.routes.api import _get_straddle_broker
            from trading_app.service.provider_logic import get_data_provider, get_kite
            username = os.getenv('MONITORING_USERNAME', 'Mine')
            broker_instance = _get_straddle_broker(username)
            if not broker_instance:
                logger.info("[Straddle Scheduler] No broker with STRADDLE_ACTIVE=true — skipping")
                return
            provider = get_data_provider(user=username)
            kite = get_kite(user=username, instance=broker_instance)
            if not provider or not kite:
                logger.warning("[Straddle Scheduler] Provider or Kite not available — skipping")
                return
            from trading_app.algo.nifty_weekly_straddle import NiftyWeeklyStraddle
            result = NiftyWeeklyStraddle(provider, kite, broker_instance, username).rollover_or_enter()
            logger.info(f"[Straddle Scheduler] Rollover result: {result.get('success')} — {result.get('error', '')}")
        except Exception as e:
            logger.error(f"[Straddle Scheduler] Error in rollover: {e}", exc_info=True)

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

    import atexit
    atexit.register(market_scheduler.stop)
    return market_scheduler
