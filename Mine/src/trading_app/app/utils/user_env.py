"""
User-specific environment variable management.
Loads environment variables from user-specific .env files.
Supports multi-user scenarios with per-user broker credentials.

Broker Configuration Format (BROKER_{N}_{FIELD}):
    BROKER_1_TYPE=zerodha
    BROKER_1_NAME=My Zerodha
    BROKER_1_API_KEY=xxx
    BROKER_1_API_SECRET=xxx
    
    BROKER_2_TYPE=kotak
    BROKER_2_NAME=Kotak Neo
    BROKER_2_CONSUMER_KEY=xxx
    ...
"""
import os
from typing import Optional, Dict, Tuple
from dotenv import load_dotenv
from trading_app.app.utils.logger import logger


class UserEnvManager:
    """Manages user-specific environment variables."""
    
    # Cache for loaded user envs to avoid repeated file reads
    _user_env_cache: Dict[str, Dict[str, str]] = {}
    
    # Mapping from legacy variable names to (broker_type, field_name)
    # This allows backward compatibility while using new format
    LEGACY_TO_NEW_MAP = {
        # Zerodha
        'API_KEY': ('zerodha', 'API_KEY'),
        'API_SECRET': ('zerodha', 'API_SECRET'),
        'ACCESS_TOKEN': ('zerodha', 'ACCESS_TOKEN'),
        'REQUEST_TOKEN': ('zerodha', 'REQUEST_TOKEN'),
        # Kotak
        'KOTAK_CONSUMER_KEY': ('kotak', 'CONSUMER_KEY'),
        'KOTAK_UCC': ('kotak', 'UCC'),
        'KOTAK_MOBILE_NUMBER': ('kotak', 'MOBILE_NUMBER'),
        'KOTAK_MPIN': ('kotak', 'MPIN'),
        'KOTAK_TOTP_SECRET': ('kotak', 'TOTP_SECRET'),
        'KOTAK_TRADING_TOKEN': ('kotak', 'TRADING_TOKEN'),
        'KOTAK_TRADING_SID': ('kotak', 'TRADING_SID'),
        'KOTAK_BASE_URL': ('kotak', 'BASE_URL'),
        # Dhan
        'DHAN_ACCESS_TOKEN': ('dhan', 'ACCESS_TOKEN'),
        'DHAN_CLIENT_ID': ('dhan', 'CLIENT_ID'),
        # Fyers
        'FYERS_APP_ID': ('fyers', 'APP_ID'),
        'FYERS_SECRET_KEY': ('fyers', 'SECRET_KEY'),
        'FYERS_ACCESS_TOKEN': ('fyers', 'ACCESS_TOKEN'),
        'FYERS_REDIRECT_URI': ('fyers', 'REDIRECT_URI'),
    }
    
    @staticmethod
    def get_user_env_file(username: str) -> str:
        """Get the path to user-specific .env file.
        
        Args:
            username: Username (e.g., 'Mine', 'Kavin')
            
        Returns:
            Path to user's .env file (e.g., '/path/to/project/env/Mine.env')
        """
        # user_env.py is at: /workspace/Mine/Mine/src/trading_app/app/utils/user_env.py
        # dirname(__file__) = /workspace/Mine/Mine/src/trading_app/app/utils
        # Go up 4 more levels to reach /workspace/Mine/Mine (project root)
        # Then into env folder for USERNAME.env
        current_file = os.path.abspath(__file__)
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file)))))
        return os.path.join(base_dir, 'env', f'{username}.env')
    
    @staticmethod
    def load_user_env(username: str) -> bool:
        """Load user-specific environment variables into the current process.
        
        This loads variables from the user's .env file (e.g., Kavin.env)
        and makes them available via os.getenv().
        
        Args:
            username: Username whose .env file to load
            
        Returns:
            True if successful, False otherwise
        """
        try:
            env_file = UserEnvManager.get_user_env_file(username)
            
            if not os.path.exists(env_file):
                logger.warning(f"User .env file not found: {env_file}")
                return False
            
            # Load the user-specific .env file
            load_dotenv(env_file, override=True)
            logger.info(f"✓ Loaded environment from {username}.env")
            
            # Clear cache for this user
            UserEnvManager._user_env_cache[username] = {}
            
            return True
            
        except Exception as e:
            logger.error(f"Error loading user env for {username}: {e}")
            return False
    
    @staticmethod
    def _find_broker_instance(env_vars: Dict[str, str], broker_type: str) -> Optional[int]:
        """Find the instance number for a broker type.
        
        Searches for BROKER_{N}_TYPE matching the given broker_type.
        
        Args:
            env_vars: Dictionary of environment variables
            broker_type: Broker type (zerodha, kotak, dhan, fyers)
            
        Returns:
            Instance number if found, None otherwise
        """
        for i in range(1, 21):  # Support up to 20 broker instances
            type_key = f'BROKER_{i}_TYPE'
            if env_vars.get(type_key, '').strip().lower() == broker_type.lower():
                return i
        return None
    
    @staticmethod
    def get_user_var(username: str, var_name: str, default: str = '') -> str:
        """Get a user-specific environment variable.
        
        Reads from user's .env file without polluting global environment.
        Uses caching to improve performance.
        
        Supports legacy variable names (e.g., KOTAK_CONSUMER_KEY) by
        automatically translating them to new BROKER_{N}_{FIELD} format.
        
        Args:
            username: Username
            var_name: Variable name (e.g., 'API_KEY', 'KOTAK_CONSUMER_KEY', 'BROKER_2_UCC')
            default: Default value if not found
            
        Returns:
            Variable value or default
        """
        try:
            # Check cache first
            if username not in UserEnvManager._user_env_cache:
                UserEnvManager._user_env_cache[username] = {}
            
            cached = UserEnvManager._user_env_cache[username]
            
            # If cache has data, check for the variable
            if cached:
                # Direct lookup first (for BROKER_{N}_{FIELD} format or cached legacy)
                if var_name in cached:
                    return cached[var_name]
                
                # Check if this is a legacy variable name that needs translation
                if var_name in UserEnvManager.LEGACY_TO_NEW_MAP:
                    broker_type, field_name = UserEnvManager.LEGACY_TO_NEW_MAP[var_name]
                    instance_num = UserEnvManager._find_broker_instance(cached, broker_type)
                    if instance_num:
                        new_key = f'BROKER_{instance_num}_{field_name}'
                        if new_key in cached:
                            return cached[new_key]
                
                return default
            
            # Load from file
            env_file = UserEnvManager.get_user_env_file(username)
            
            if not os.path.exists(env_file):
                return default
            
            # Parse .env file
            env_vars = {}
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, value = line.split('=', 1)
                        env_vars[key.strip()] = value.strip()
            
            # Cache all vars for this user
            UserEnvManager._user_env_cache[username] = env_vars
            
            # Direct lookup first
            if var_name in env_vars:
                return env_vars[var_name]
            
            # Check if this is a legacy variable name that needs translation
            if var_name in UserEnvManager.LEGACY_TO_NEW_MAP:
                broker_type, field_name = UserEnvManager.LEGACY_TO_NEW_MAP[var_name]
                instance_num = UserEnvManager._find_broker_instance(env_vars, broker_type)
                if instance_num:
                    new_key = f'BROKER_{instance_num}_{field_name}'
                    if new_key in env_vars:
                        return env_vars[new_key]
            
            return default
            
        except Exception as e:
            logger.error(f"Error getting user var {var_name} for {username}: {e}")
            return default
    
    @staticmethod
    def get_broker_credentials(username: str, broker_type: str) -> Dict[str, str]:
        """Get all credentials for a specific broker type.
        
        Finds the broker instance of the given type and returns all its credentials.
        
        Args:
            username: Username
            broker_type: Broker type (zerodha, kotak, dhan, fyers)
            
        Returns:
            Dictionary of credential_name: value pairs
        """
        try:
            # Ensure cache is populated
            UserEnvManager.get_user_var(username, 'BROKER_1_TYPE')
            
            cached = UserEnvManager._user_env_cache.get(username, {})
            instance_num = UserEnvManager._find_broker_instance(cached, broker_type)
            
            if not instance_num:
                return {}
            
            # Collect all BROKER_{instance_num}_{field} entries
            prefix = f'BROKER_{instance_num}_'
            credentials = {}
            for key, value in cached.items():
                if key.startswith(prefix):
                    field_name = key[len(prefix):]
                    credentials[field_name] = value
            
            return credentials
            
        except Exception as e:
            logger.error(f"Error getting broker credentials for {broker_type}: {e}")
            return {}
    
    @staticmethod
    def get_user_vars(username: str, var_names: list) -> Dict[str, str]:
        """Get multiple user-specific environment variables.
        
        Args:
            username: Username
            var_names: List of variable names
            
        Returns:
            Dictionary of var_name: value pairs
        """
        return {
            name: UserEnvManager.get_user_var(username, name)
            for name in var_names
        }
    
    @staticmethod
    def save_user_var(username: str, var_name: str, value: str) -> bool:
        """Save a variable to user's .env file.
        
        Supports legacy variable names by translating them to new BROKER_{N}_{FIELD} format.
        
        Args:
            username: Username
            var_name: Variable name (legacy or new format)
            value: Variable value
            
        Returns:
            True if successful, False otherwise
        """
        try:
            env_file = UserEnvManager.get_user_env_file(username)
            
            # Translate legacy variable name to new format if needed
            actual_var_name = var_name
            if var_name in UserEnvManager.LEGACY_TO_NEW_MAP:
                broker_type, field_name = UserEnvManager.LEGACY_TO_NEW_MAP[var_name]
                # Ensure cache is populated
                UserEnvManager.get_user_var(username, 'BROKER_1_TYPE')
                cached = UserEnvManager._user_env_cache.get(username, {})
                instance_num = UserEnvManager._find_broker_instance(cached, broker_type)
                if instance_num:
                    actual_var_name = f'BROKER_{instance_num}_{field_name}'
                    logger.debug(f"Translated {var_name} -> {actual_var_name}")
            
            # Read existing content
            lines = []
            found = False
            
            if os.path.exists(env_file):
                with open(env_file, 'r') as f:
                    lines = f.readlines()
            
            # Update or add variable
            updated_lines = []
            for line in lines:
                if line.startswith(f'{actual_var_name}='):
                    updated_lines.append(f'{actual_var_name}={value}\n')
                    found = True
                else:
                    updated_lines.append(line)
            
            if not found:
                updated_lines.append(f'{actual_var_name}={value}\n')
            
            # Write back
            with open(env_file, 'w') as f:
                f.writelines(updated_lines)
            
            # Invalidate cache
            if username in UserEnvManager._user_env_cache:
                del UserEnvManager._user_env_cache[username]
            
            logger.info(f"✓ Saved {actual_var_name} to {username}.env")
            return True
            
        except Exception as e:
            logger.error(f"Error saving user var {var_name} for {username}: {e}")
            return False
    
    @staticmethod
    def save_user_vars(username: str, vars_dict: Dict[str, str]) -> bool:
        """Save multiple variables to user's .env file.
        
        Args:
            username: Username
            vars_dict: Dictionary of var_name: value pairs
            
        Returns:
            True if successful, False otherwise
        """
        try:
            env_file = UserEnvManager.get_user_env_file(username)
            
            # Read existing content
            lines = []
            found_vars = set()
            
            if os.path.exists(env_file):
                with open(env_file, 'r') as f:
                    lines = f.readlines()
            
            # Update existing or mark for addition
            updated_lines = []
            for line in lines:
                matched = False
                for var_name in vars_dict.keys():
                    if line.startswith(f'{var_name}='):
                        updated_lines.append(f'{var_name}={vars_dict[var_name]}\n')
                        found_vars.add(var_name)
                        matched = True
                        break
                if not matched:
                    updated_lines.append(line)
            
            # Add missing variables
            for var_name, value in vars_dict.items():
                if var_name not in found_vars:
                    updated_lines.append(f'{var_name}={value}\n')
            
            # Write back
            with open(env_file, 'w') as f:
                f.writelines(updated_lines)
            
            # Invalidate cache
            if username in UserEnvManager._user_env_cache:
                del UserEnvManager._user_env_cache[username]
            
            logger.info(f"✓ Saved {len(vars_dict)} variables to {username}.env")
            return True
            
        except Exception as e:
            logger.error(f"Error saving user vars for {username}: {e}")
            return False
    
    @staticmethod
    def get_all_user_vars(username: str) -> Dict[str, str]:
        """Get all environment variables for a user.
        
        Args:
            username: Username
            
        Returns:
            Dictionary of all variables in user's .env file
        """
        try:
            env_file = UserEnvManager.get_user_env_file(username)
            
            if not os.path.exists(env_file):
                return {}
            
            # Check cache first
            if username in UserEnvManager._user_env_cache:
                if UserEnvManager._user_env_cache[username]:
                    return UserEnvManager._user_env_cache[username]
            else:
                UserEnvManager._user_env_cache[username] = {}
            
            # Parse .env file
            env_vars = {}
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, value = line.split('=', 1)
                        env_vars[key.strip()] = value.strip()
            
            # Cache it
            UserEnvManager._user_env_cache[username] = env_vars
            return env_vars
            
        except Exception as e:
            logger.error(f"Error getting all user vars for {username}: {e}")
            return {}
    
    @staticmethod
    def clear_cache(username: Optional[str] = None) -> None:
        """Clear the environment variable cache.
        
        Args:
            username: Specific user to clear, or None to clear all
        """
        if username:
            if username in UserEnvManager._user_env_cache:
                del UserEnvManager._user_env_cache[username]
                logger.info(f"Cleared cache for {username}")
        else:
            UserEnvManager._user_env_cache.clear()
            logger.info("Cleared all cache")
