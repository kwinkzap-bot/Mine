"""
Backward compatibility entry point.
This file exists for compatibility with the old project structure.
The actual implementation is in main.py - please use that instead.

To run the application:
    python main.py      # Recommended (primary entry point)
    python run.py       # Legacy/backward compatibility
"""
import os
import sys

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

if __name__ == '__main__':
    # Import and run from main
    from main import main
    main()
