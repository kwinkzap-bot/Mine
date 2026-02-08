"""
User-specific environment variable management.
Loads environment variables from user-specific .env files.
Supports multi-user scenarios with per-user broker credentials.
"""
import os
from typing import Optional, Dict
from dotenv import load_dotenv
from trading_app.app.utils.logger import logger


class UserEnvManager:
    """Manages user-specific environment variables."""
    
    # Cache for loaded user envs to avoid repeated file reads
    _user_env_cache: Dict[str, Dict[str, str]] = {}
    
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
    def get_user_var(username: str, var_name: str, default: str = '') -> str:
        """Get a user-specific environment variable.
        
        Reads from user's .env file without polluting global environment.
        Uses caching to improve performance.
        
        Args:
            username: Username
            var_name: Variable name (e.g., 'API_KEY')
            default: Default value if not found
            
        Returns:
            Variable value or default
        """
        try:
            # Check cache first
            if username not in UserEnvManager._user_env_cache:
                UserEnvManager._user_env_cache[username] = {}
            
            cached = UserEnvManager._user_env_cache[username]
            if var_name in cached:
                return cached[var_name]
            
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
            
            return env_vars.get(var_name, default)
            
        except Exception as e:
            logger.error(f"Error getting user var {var_name} for {username}: {e}")
            return default
    
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
        
        Args:
            username: Username
            var_name: Variable name
            value: Variable value
            
        Returns:
            True if successful, False otherwise
        """
        try:
            env_file = UserEnvManager.get_user_env_file(username)
            
            # Read existing content
            lines = []
            found = False
            
            if os.path.exists(env_file):
                with open(env_file, 'r') as f:
                    lines = f.readlines()
            
            # Update or add variable
            updated_lines = []
            for line in lines:
                if line.startswith(f'{var_name}='):
                    updated_lines.append(f'{var_name}={value}\n')
                    found = True
                else:
                    updated_lines.append(line)
            
            if not found:
                updated_lines.append(f'{var_name}={value}\n')
            
            # Write back
            with open(env_file, 'w') as f:
                f.writelines(updated_lines)
            
            # Invalidate cache
            if username in UserEnvManager._user_env_cache:
                del UserEnvManager._user_env_cache[username]
            
            logger.info(f"✓ Saved {var_name} to {username}.env")
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
