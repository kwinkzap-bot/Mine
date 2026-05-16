import datetime
import logging
from py_vollib.black_scholes import black_scholes
from py_vollib.black_scholes.implied_volatility import implied_volatility
from py_vollib.black_scholes.greeks.analytical import delta, gamma, theta, vega

logger = logging.getLogger(__name__)

class GreeksCalculator:
    """
    Real-time Options Greeks and Implied Volatility Calculator using Black-Scholes-Merton model.
    """
    
    @staticmethod
    def calculate_time_to_expiry(expiry_date: str) -> float:
        """
        Calculate time to expiry in years.
        Supports expiry_date formats: 'YYYY-MM-DD' or datetime objects.
        """
        try:
            now = datetime.datetime.now()
            if isinstance(expiry_date, str):
                # Assuming format like "2024-05-30"
                expiry = datetime.datetime.strptime(expiry_date, "%Y-%m-%d")
            elif isinstance(expiry_date, datetime.datetime):
                expiry = expiry_date
            elif isinstance(expiry_date, datetime.date):
                expiry = datetime.datetime.combine(expiry_date, datetime.time(15, 30))
            else:
                return 0.0

            # Expiry is usually at 15:30 IST on the expiry day
            expiry = expiry.replace(hour=15, minute=30, second=0, microsecond=0)
            
            # If current time is past expiry, option has expired.
            if now >= expiry:
                return 0.001 # Minimal time to prevent division by zero
            
            td = expiry - now
            days_to_expiry = td.total_seconds() / (24 * 3600)
            
            # Set a minimum of 0.001 years (approx 0.36 days) to prevent IV and Theta explosions
            time_in_years = max(days_to_expiry / 365.0, 0.001)
            return time_in_years
        except Exception as e:
            logger.error(f"Error calculating time to expiry: {e}")
            return 0.001

    @staticmethod
    def calculate_theoretical_price(option_type: str, underlying_price: float, strike_price: float, expiry_date: str, iv: float, risk_free_rate: float = 0.07) -> float:
        """
        Calculates theoretical option price using Black-Scholes.
        :param iv: Implied Volatility as a decimal (e.g., 0.20 for 20%)
        """
        try:
            if underlying_price <= 0 or strike_price <= 0 or iv <= 0:
                return 0.0
            
            flag = 'c' if option_type.upper() == 'CE' else 'p'
            t = GreeksCalculator.calculate_time_to_expiry(expiry_date)
            
            # flag, S, K, t, r, sigma
            price = black_scholes(flag, underlying_price, strike_price, t, risk_free_rate, iv)
            return round(price, 2)
        except Exception as e:
            logger.error(f"Error calculating theoretical price: {e}")
            return 0.0

    @staticmethod
    def calculate_greeks(option_type: str, ltp: float, underlying_price: float, strike_price: float, expiry_date: str, risk_free_rate: float = 0.07):
        """
        Calculates IV and all major Greeks.
        
        :param option_type: 'CE' or 'PE'
        :param ltp: Last traded price of the option
        :param underlying_price: Spot or Futures price of the underlying
        :param strike_price: Strike price of the option
        :param expiry_date: Expiry date string (e.g., '2024-05-30')
        :param risk_free_rate: Annualized risk-free rate (Default: 7% for India)
        :return: Dict containing IV, Delta, Theta, Gamma, Vega
        """
        # Default empty return
        result = {
            "IV": 0.0,
            "Delta": 0.0,
            "Theta": 0.0,
            "Gamma": 0.0,
            "Vega": 0.0
        }

        # Basic validations
        if ltp <= 0 or underlying_price <= 0 or strike_price <= 0:
            return result

        flag = 'c' if option_type.upper() == 'CE' else 'p'
        t = GreeksCalculator.calculate_time_to_expiry(expiry_date)
        
        try:
            # 1. Calculate Implied Volatility
            # price, S, K, t, r, flag
            iv = implied_volatility(ltp, underlying_price, strike_price, t, risk_free_rate, flag)
            
            # If IV calculation fails or returns unreasonable value
            if iv is None or iv <= 0:
                return result

            # Cap IV at 500% to prevent ridiculous numbers on expiry day
            iv = min(iv, 5.0)

            # 2. Calculate Greeks using the derived IV
            # flag, S, K, t, r, sigma
            d = delta(flag, underlying_price, strike_price, t, risk_free_rate, iv)
            g = gamma(flag, underlying_price, strike_price, t, risk_free_rate, iv)
            
            # py_vollib theta already returns daily theta (change per 1 day)
            th = theta(flag, underlying_price, strike_price, t, risk_free_rate, iv)
            
            # Cap Theta to prevent extreme values (e.g., losing more than the option's value in a day)
            # If LTP is 50, you can't lose 200 in a day. Theta should at most be -ltp.
            th = max(th, -ltp)
            
            # py_vollib vega already returns the change for a 1% change in IV
            v = vega(flag, underlying_price, strike_price, t, risk_free_rate, iv)

            result = {
                "IV": round(iv * 100, 2), # Convert to percentage
                "Delta": round(d, 4),
                "Theta": round(th, 2), # Rounded to 2 decimal places for UI
                "Gamma": round(g, 6),
                "Vega": round(v, 4)
            }
        except Exception as e:
            # Volatility calculation might fail for deep ITM/OTM options if LTP is anomalous
            logger.debug(f"Greeks calculation failed for {option_type} {strike_price}: {e}")
            pass

        return result
