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
        BACKTEST_RESULTS: '/api/backtest-results',

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



        // Delta-RSI Filter elements
        DRSI_BULLISH_RESULTS: 'drsiBullishResults',
        DRSI_BULLISH_BODY: 'drsiBullishBody',
        DRSI_BULLISH_COUNT: 'drsiBullishCount',
        DRSI_BULLISH_TABLE: 'drsiBullishTable',
        DRSI_BEARISH_RESULTS: 'drsiBearishResults',
        DRSI_BEARISH_BODY: 'drsiBearishBody',
        DRSI_BEARISH_COUNT: 'drsiBearishCount',
        DRSI_BEARISH_TABLE: 'drsiBearishTable',
        CAMARILLA_CPR_REVERSAL_BULLISH_RESULTS: 'camarillaCprReversalBullishResults',
        CAMARILLA_CPR_REVERSAL_BULLISH_BODY: 'camarillaCprReversalBullishBody',
        CAMARILLA_CPR_REVERSAL_BULLISH_COUNT: 'camarillaCprReversalBullishCount',
        CAMARILLA_CPR_REVERSAL_BULLISH_TABLE: 'camarillaCprReversalBullishTable',
        CAMARILLA_CPR_REVERSAL_BEARISH_RESULTS: 'camarillaCprReversalBearishResults',
        CAMARILLA_CPR_REVERSAL_BEARISH_BODY: 'camarillaCprReversalBearishBody',
        CAMARILLA_CPR_REVERSAL_BEARISH_COUNT: 'camarillaCprReversalBearishCount',
        CAMARILLA_CPR_REVERSAL_BEARISH_TABLE: 'camarillaCprReversalBearishTable',
        DRSI_REVERSAL_BULLISH_RESULTS: 'drsiReversalBullishResults',
        DRSI_REVERSAL_BULLISH_BODY: 'drsiReversalBullishBody',
        DRSI_REVERSAL_BULLISH_COUNT: 'drsiReversalBullishCount',
        DRSI_REVERSAL_BULLISH_TABLE: 'drsiReversalBullishTable',
        DRSI_REVERSAL_BEARISH_RESULTS: 'drsiReversalBearishResults',
        DRSI_REVERSAL_BEARISH_BODY: 'drsiReversalBearishBody',
        DRSI_REVERSAL_BEARISH_COUNT: 'drsiReversalBearishCount',
        DRSI_REVERSAL_BEARISH_TABLE: 'drsiReversalBearishTable',

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
        OPTIONS_CHART: '/options-chart',
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
    get: function (path, defaultValue = null) {
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
