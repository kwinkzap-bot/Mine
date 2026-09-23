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
import threading
import time
from typing import Optional, Dict, Tuple
from dotenv import load_dotenv
from trading_app.app.utils.logger import logger


def _parse_env_text(text: str) -> Dict[str, str]:
    """The .env body as a dict, inline comments stripped."""
    env_vars = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            key, value = line.split('=', 1)
            raw_val = value.strip()
            if ' #' in raw_val:
                raw_val = raw_val.split(' #')[0].rstrip()
            env_vars[key.strip()] = raw_val
    return env_vars


def _read_env_file(env_file: str):
    """Parse the file, or return None rather than hand back a torn read.

    The env file is rewritten in place by every broker login and by the
    toggles on the Algo tabs. A reader that catches it mid-write parses a
    *prefix* — and a prefix is indistinguishable from a complete file, so it
    used to land in the cache as the truth and silently turn later flags
    off. That is what skipped four Telegram calls with "no broker has
    BROKER_N_TG_ACTIVE=true" on 2026-09-21/22: the prefix reached
    BROKER_1_TYPE but stopped before BROKER_1_TG_ACTIVE, and the lie sat in
    the cache until an unrelated save cleared it.

    So the size is taken before and after, and the read is only trusted when
    it accounts for the whole file. A writer caught in the act is waited out;
    three failures return None, and the caller must not cache that.
    """
    for attempt in (1, 2, 3):
        try:
            size_before = os.path.getsize(env_file)
            with open(env_file, 'rb') as f:
                raw = f.read()
            size_after = os.path.getsize(env_file)
            if len(raw) == size_before == size_after:
                return _parse_env_text(raw.decode('utf-8', 'replace'))
            logger.warning(f"[UserEnv] {os.path.basename(env_file)} changed under the read "
                           f"({size_before} → {len(raw)} → {size_after}) — retry {attempt}")
        except OSError as e:
            logger.warning(f"[UserEnv] read of {env_file} failed: {e} — retry {attempt}")
        time.sleep(0.05)
    logger.error(f"[UserEnv] could not read {env_file} cleanly — NOT caching a partial file")
    return None


class UserEnvManager:
    """Manages user-specific environment variables."""
    
    # Cache for loaded user envs to avoid repeated file reads. Written from
    # request threads and from every algo/listener thread, so it is only ever
    # replaced wholesale under this lock — never mutated in place.
    _user_env_cache: Dict[str, Dict[str, str]] = {}
    _cache_lock = threading.RLock()
    
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
        # ICICI Direct (Breeze)
        'ICICI_API_KEY': ('icici', 'API_KEY'),
        'ICICI_SECRET_KEY': ('icici', 'SECRET_KEY'),
        'ICICI_SESSION_TOKEN': ('icici', 'SESSION_TOKEN'),
    }
    
    @staticmethod
    def _atomic_write_lines(env_file: str, lines) -> None:
        """Replace the env file in one step (temp file, fsync, os.replace).

        The file holds the broker credentials and every per-account flag, and
        it is read without a lock by the algo and listener threads. Truncating
        it in place gives those readers a prefix; replacing it means a reader
        sees either the old file or the new one.
        """
        tmp = f'{env_file}.{os.getpid()}.tmp'
        try:
            with open(tmp, 'w') as f:
                f.writelines(lines)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, env_file)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

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
        
        Also sets legacy variable names (FYERS_SECRET_KEY, KOTAK_CONSUMER_KEY, etc.)
        in os.environ for backward compatibility with services that use os.getenv().
        
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
            
            # Parse file to get all vars
            env_vars = _read_env_file(env_file) or {}
            
            # Cache all vars
            UserEnvManager._user_env_cache[username] = env_vars
            
            # Set legacy variable names in os.environ for backward compatibility
            # Services like FyersOrderService use os.getenv() directly
            NEW_TO_LEGACY_MAP = {
                ('zerodha', 'API_KEY'): 'API_KEY',
                ('zerodha', 'API_SECRET'): 'API_SECRET',
                ('zerodha', 'ACCESS_TOKEN'): 'ACCESS_TOKEN',
                ('zerodha', 'REQUEST_TOKEN'): 'REQUEST_TOKEN',
                ('kotak', 'CONSUMER_KEY'): 'KOTAK_CONSUMER_KEY',
                ('kotak', 'UCC'): 'KOTAK_UCC',
                ('kotak', 'MOBILE_NUMBER'): 'KOTAK_MOBILE_NUMBER',
                ('kotak', 'MPIN'): 'KOTAK_MPIN',
                ('kotak', 'TOTP_SECRET'): 'KOTAK_TOTP_SECRET',
                ('kotak', 'TRADING_TOKEN'): 'KOTAK_TRADING_TOKEN',
                ('kotak', 'TRADING_SID'): 'KOTAK_TRADING_SID',
                ('kotak', 'BASE_URL'): 'KOTAK_BASE_URL',
                ('dhan', 'ACCESS_TOKEN'): 'DHAN_ACCESS_TOKEN',
                ('dhan', 'CLIENT_ID'): 'DHAN_CLIENT_ID',
                ('fyers', 'APP_ID'): 'FYERS_APP_ID',
                ('fyers', 'SECRET_KEY'): 'FYERS_SECRET_KEY',
                ('fyers', 'ACCESS_TOKEN'): 'FYERS_ACCESS_TOKEN',
                ('fyers', 'REDIRECT_URI'): 'FYERS_REDIRECT_URI',
                ('icici', 'API_KEY'): 'ICICI_API_KEY',
                ('icici', 'SECRET_KEY'): 'ICICI_SECRET_KEY',
                ('icici', 'SESSION_TOKEN'): 'ICICI_SESSION_TOKEN',
            }
            
            # Find all broker instances and set legacy names
            for i in range(1, 21):
                broker_type_key = f'BROKER_{i}_TYPE'
                broker_type = env_vars.get(broker_type_key, '').strip().lower()
                if not broker_type:
                    continue
                
                # For each field of this broker, set legacy name in os.environ
                for (btype, field), legacy_name in NEW_TO_LEGACY_MAP.items():
                    if btype == broker_type:
                        new_key = f'BROKER_{i}_{field}'
                        if new_key in env_vars:
                            os.environ[legacy_name] = env_vars[new_key]
                            logger.debug(f"Set legacy env: {legacy_name} from {new_key}")
            
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
            # If username is None or empty, return default
            if not username:
                logger.warning(f"get_user_var called with empty username for {var_name}")
                return default
            
            # Check cache first
            with UserEnvManager._cache_lock:
                cached = UserEnvManager._user_env_cache.get(username) or {}
            
            # If cache has data (check for non-empty dict), check for the variable
            if cached:  # Non-empty dict
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
            
            # Parse .env file. A read the writer was in the middle of is a
            # prefix, not an answer: it is never cached, because a cached
            # prefix reads as "that flag is not set" for the life of the
            # process.
            env_vars = _read_env_file(env_file)
            if env_vars is None:
                return default
            
            # Cache all vars for this user
            with UserEnvManager._cache_lock:
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
            
            # Write back, atomically: a reader that catches a truncated
            # env file caches the prefix and silently loses every flag
            # below the cut (see _read_env_file).
            UserEnvManager._atomic_write_lines(env_file, updated_lines)
            
            # Invalidate cache
            UserEnvManager.clear_cache(username)
            
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
            logger.info(f"save_user_vars: username={username}, env_file={env_file}")
            logger.info(f"save_user_vars: vars_dict keys={list(vars_dict.keys())}")
            
            # Read existing content
            lines = []
            found_vars = set()
            
            if os.path.exists(env_file):
                with open(env_file, 'r') as f:
                    lines = f.readlines()
                logger.info(f"save_user_vars: Read {len(lines)} lines from {env_file}")
            else:
                logger.warning(f"save_user_vars: File does not exist: {env_file}")
            
            # Update existing or mark for addition
            updated_lines = []
            for line in lines:
                matched = False
                for var_name in vars_dict.keys():
                    if line.startswith(f'{var_name}='):
                        updated_lines.append(f'{var_name}={vars_dict[var_name]}\n')
                        found_vars.add(var_name)
                        matched = True
                        logger.info(f"save_user_vars: Found and updated {var_name}")
                        break
                if not matched:
                    updated_lines.append(line)
            
            # Add missing variables
            for var_name, value in vars_dict.items():
                if var_name not in found_vars:
                    updated_lines.append(f'{var_name}={value}\n')
                    logger.info(f"save_user_vars: Added new var {var_name}")
            
            # Write back, atomically — see save_user_var.
            UserEnvManager._atomic_write_lines(env_file, updated_lines)
            logger.info(f"save_user_vars: Wrote {len(updated_lines)} lines to {env_file}")
            
            # Invalidate cache
            UserEnvManager.clear_cache(username)
            
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
            with UserEnvManager._cache_lock:
                cached = UserEnvManager._user_env_cache.get(username)
            if cached:
                return cached
            
            # Parse .env file — never caching a torn read (see _read_env_file)
            env_vars = _read_env_file(env_file)
            if env_vars is None:
                return {}
            
            # Cache it
            with UserEnvManager._cache_lock:
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
        with UserEnvManager._cache_lock:
            if username:
                if UserEnvManager._user_env_cache.pop(username, None) is not None:
                    logger.info(f"Cleared cache for {username}")
            else:
                UserEnvManager._user_env_cache.clear()
                logger.info("Cleared all cache")
