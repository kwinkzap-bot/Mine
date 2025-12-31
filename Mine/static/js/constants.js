/**
 * constants.js
 * Centralized constants file for static content used across all JavaScript files
 * Reusable across all other JS files in the application
 */

window.CONSTANTS = {
    // API Endpoints
    API_ENDPOINTS: {
        // CPR Filter endpoints
        CPR_FILTER: '/api/cpr-filter',
        CPR_FILTER_ABOVE: '/api/cpr-filter/above',
        CPR_FILTER_BELOW: '/api/cpr-filter/below',
        
        // Options Chart endpoints
        OPTIONS_INIT: '/api/options-init',
        UNDERLYING_PRICE: '/api/underlying-price',
        OPTIONS_STRIKES: '/api/options-strikes',
        OPTIONS_DEFAULT_STRIKES: '/api/options-default-strikes',
        OPTIONS_CHART_DATA: '/api/options-chart-data',
        OPTIONS_PDH_PDL: '/api/options-pdh-pdl',
        
        // Strategy endpoints
        STRATEGY_DATA: '/api/strategy-data',
        BACKTEST_RESULTS: '/api/backtest-results',
        
        // Historical data endpoints
        HISTORICAL_DATA: '/api/historical-data',
        
        // Authentication endpoints
        LOGIN: '/auth/login',
        LOGOUT: '/auth/logout',
        
        // Multi CPR endpoint
        MULTI_CPR_LIVE: '/api/multi-cpr-live'
    },

    // Timeout and Interval Settings (in milliseconds)
    TIMEOUTS: {
        NOTIFICATION_DURATION: 5000,      // 5 seconds - How long notifications display
        CPR_REFRESH_INTERVAL: 300000,     // 5 minutes - CPR data refresh rate
        COUNTDOWN_TICK: 1000,             // 1 second - Countdown timer tick
        API_TIMEOUT: 30000,               // 30 seconds - API request timeout
        SESSION_CHECK_INTERVAL: 60000     // 1 minute - Session validity check
    },

    // DOM Element IDs
    DOM_IDS: {
        // Status and Containers
        STATUS_BAR: 'status-bar',
        NOTIFICATION_CONTAINER: 'notification-container',
        API_LOADER: 'api-loader',
        
        // CPR Filter elements
        ABOVE_RESULTS: 'aboveResults',
        ABOVE_BODY: 'aboveBody',
        ABOVE_COUNT: 'aboveCount',
        ABOVE_TABLE: 'aboveTable',
        BELOW_RESULTS: 'belowResults',
        BELOW_BODY: 'belowBody',
        BELOW_COUNT: 'belowCount',
        BELOW_TABLE: 'belowTable',
        CROSS_ABOVE_RESULTS: 'crossAboveResults',
        CROSS_ABOVE_BODY: 'crossAboveBody',
        CROSS_ABOVE_COUNT: 'crossAboveCount',
        CROSS_ABOVE_TABLE: 'crossAboveTable',
        CROSS_BELOW_RESULTS: 'crossBelowResults',
        CROSS_BELOW_BODY: 'crossBelowBody',
        CROSS_BELOW_COUNT: 'crossBelowCount',
        CROSS_BELOW_TABLE: 'crossBelowTable',
        
        // Strategy elements
        STRATEGY_FORM: 'strategyForm',
        STRATEGY_RESULTS: 'strategyResults',
        
        // Options Chart elements
        OPTIONS_CHART: 'optionsChart',
        UNDERLYING_INPUT: 'underlyingInput',
        STRIKE_INPUT: 'strikeInput'
    },

    // CSS Classes
    CSS_CLASSES: {
        HIDDEN: 'hidden',
        NOTIFICATION: 'notification',
        NOTIFICATION_SUCCESS: 'success',
        NOTIFICATION_ERROR: 'error',
        NOTIFICATION_INFO: 'info',
        NOTIFICATION_WARNING: 'warning',
        ACTIVE: 'active',
        LOADING: 'loading',
        DISABLED: 'disabled',
        TABLE_ROW_HIGHLIGHT: 'highlight',
        TIMEFRAME_BTN: 'timeframe-btn'
    },

    // Chart Configuration
    CHART_CONFIG: {
        CE_COLOR: '#00c853',  // Green for Call Options
        PE_COLOR: '#2962ff'   // Blue for Put Options
    },

    // HTTP Status Codes
    HTTP_STATUS: {
        OK: 200,
        CREATED: 201,
        BAD_REQUEST: 400,
        UNAUTHORIZED: 401,
        FORBIDDEN: 403,
        NOT_FOUND: 404,
        INTERNAL_SERVER_ERROR: 500,
        SERVICE_UNAVAILABLE: 503
    },

    // Notification Types
    NOTIFICATION_TYPES: {
        SUCCESS: 'success',
        ERROR: 'error',
        INFO: 'info',
        WARNING: 'warning'
    },

    // HTTP Methods
    HTTP_METHODS: {
        GET: 'GET',
        POST: 'POST',
        PUT: 'PUT',
        DELETE: 'DELETE',
        PATCH: 'PATCH'
    },

    // Sort Directions
    SORT_DIRECTION: {
        ASC: 'asc',
        DESC: 'desc'
    },

    // Data Formats
    DATE_FORMAT: 'YYYY-MM-DD',
    TIME_FORMAT: 'HH:mm:ss',
    DATETIME_FORMAT: 'YYYY-MM-DD HH:mm:ss',

    // Pagination
    PAGINATION: {
        DEFAULT_PAGE_SIZE: 10,
        DEFAULT_PAGE: 1,
        MAX_PAGE_SIZE: 100
    },

    // Error Messages
    ERROR_MESSAGES: {
        SESSION_EXPIRED: 'Your session has expired or you are not authorized. Please login again.',
        AUTHENTICATION_ERROR: 'Authentication error. Redirecting to login...',
        NETWORK_ERROR: 'Network error. Please check your connection.',
        SERVER_ERROR: 'Server error. Please try again later.',
        INVALID_INPUT: 'Invalid input provided.',
        REQUIRED_FIELD: 'This field is required.',
        UNEXPECTED_ERROR: 'An unexpected error occurred.'
    },

    // Success Messages
    SUCCESS_MESSAGES: {
        OPERATION_SUCCESS: 'Operation completed successfully.',
        DATA_LOADED: 'Data loaded successfully.',
        DATA_SAVED: 'Data saved successfully.',
        DATA_DELETED: 'Data deleted successfully.'
    },

    // URL Paths
    PAGES: {
        LOGIN: '/auth/login',
        DASHBOARD: '/',
        CPR_FILTER: '/cpr-filter',
        STRATEGY: '/strategy',
        OPTIONS_CHART: '/options-chart',
        HISTORICAL: '/historical',
        BACKTEST: '/backtest'
    },

    // Debug Mode
    DEBUG: false,

    /**
     * Utility method to safely get nested constant values
     * @param {string} path - Dot-separated path (e.g., 'API_ENDPOINTS.CPR_FILTER')
     * @param {*} defaultValue - Default value if path not found
     * @returns {*} - The value at the path or defaultValue
     */
    get: function(path, defaultValue = null) {
        const keys = path.split('.');
        let result = this;
        for (let key of keys) {
            if (result[key] !== undefined) {
                result = result[key];
            } else {
                return defaultValue;
            }
        }
        return result;
    }
};
