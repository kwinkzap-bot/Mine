/**
 * cpr_filter_scheduler.js
 * Manages recurring CPR filter API calls during market hours (9:15 AM - 3:40 PM IST)
 */

const CPRFilterScheduler = (function() {
    // Market hours configuration
    const MARKET_OPEN_HOUR = 9;
    const MARKET_OPEN_MINUTE = 15;
    const MARKET_CLOSE_HOUR = 15;
    const MARKET_CLOSE_MINUTE = 40;
    
    // Recurring interval (in minutes)
    const INTERVAL_MINUTES = 5;
    
    let intervalId = null;
    let isRunning = false;
    
    /**
     * Check if current time is within market hours
     */
    function isMarketOpen() {
        const now = new Date();
        const currentHour = now.getHours();
        const currentMinute = now.getMinutes();
        const currentDay = now.getDay();
        
        // Check if it's a trading day (Monday-Friday, 1-5)
        const isWeekday = currentDay >= 1 && currentDay <= 5;
        
        // Check if it's within market hours
        const openTime = MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MINUTE;
        const closeTime = MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MINUTE;
        const currentTime = currentHour * 60 + currentMinute;
        
        return isWeekday && (currentTime >= openTime && currentTime <= closeTime);
    }
    
    /**
     * Execute CPR filter API call
     */
    async function executeCPRFilter() {
        try {
            if (!isMarketOpen()) {
                console.log('[CPR Scheduler] Outside market hours, skipping call');
                return;
            }
            
            console.log('[CPR Scheduler] Executing CPR filter at', new Date().toLocaleTimeString());
            
            const response = await fetch('/api/cpr-filter', {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                }
            });
            
            if (!response.ok) {
                if (response.status === 401) {
                    console.warn('[CPR Scheduler] Authentication required - user needs to login');
                    stop();
                    return;
                }
                throw new Error(`API error: ${response.status}`);
            }
            
            const data = await response.json();
            
            if (data.success) {
                console.log(`[CPR Scheduler] ✓ CPR filter completed: ${data.data?.length || 0} stocks`);
                
                // Trigger custom event for other components to react to results
                const event = new CustomEvent('cprFilterUpdated', {
                    detail: { results: data.data, timestamp: new Date() }
                });
                document.dispatchEvent(event);
            } else {
                console.warn('[CPR Scheduler] API returned error:', data.error);
            }
        } catch (error) {
            console.error('[CPR Scheduler] Error executing CPR filter:', error);
        }
    }
    
    /**
     * Start recurring CPR filter calls
     */
    function start() {
        if (isRunning) {
            console.log('[CPR Scheduler] Already running');
            return;
        }
        
        if (!isMarketOpen()) {
            console.log('[CPR Scheduler] Market is closed, will start when market opens');
        }
        
        // Clear any existing interval
        if (intervalId) {
            clearInterval(intervalId);
        }
        
        isRunning = true;
        
        // Execute immediately if market is open
        if (isMarketOpen()) {
            executeCPRFilter();
        }
        
        // Set recurring interval
        intervalId = setInterval(() => {
            if (isMarketOpen()) {
                executeCPRFilter();
            }
        }, INTERVAL_MINUTES * 60 * 1000);  // Convert minutes to milliseconds
        
        console.log(`[CPR Scheduler] Started - CPR filter will execute every ${INTERVAL_MINUTES} minutes during market hours`);
    }
    
    /**
     * Stop recurring CPR filter calls
     */
    function stop() {
        if (intervalId) {
            clearInterval(intervalId);
            intervalId = null;
        }
        isRunning = false;
        console.log('[CPR Scheduler] Stopped');
    }
    
    /**
     * Get scheduler status
     */
    function getStatus() {
        return {
            isRunning: isRunning,
            isMarketOpen: isMarketOpen(),
            nextExecutionTime: calculateNextExecutionTime()
        };
    }
    
    /**
     * Calculate next execution time
     */
    function calculateNextExecutionTime() {
        if (!isMarketOpen()) {
            return 'Market closed - next execution at 9:15 AM IST';
        }
        
        const now = new Date();
        const nextExecution = new Date(now.getTime() + INTERVAL_MINUTES * 60 * 1000);
        return nextExecution.toLocaleTimeString();
    }
    
    // Public API
    return {
        start: start,
        stop: stop,
        getStatus: getStatus,
        executeNow: executeCPRFilter,
        isMarketOpen: isMarketOpen
    };
})();

// Auto-start scheduler when page loads if user is authenticated
document.addEventListener('DOMContentLoaded', function() {
    // Check if user is authenticated by attempting to call a protected endpoint
    fetch('/api/health')
        .then(response => {
            if (response.ok) {
                console.log('[CPR Scheduler] Authentication verified, starting scheduler');
                CPRFilterScheduler.start();
            }
        })
        .catch(error => {
            console.log('[CPR Scheduler] Not authenticated, scheduler not started');
        });
});

// Clean up on page unload
window.addEventListener('beforeunload', function() {
    CPRFilterScheduler.stop();
});
