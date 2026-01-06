#!/usr/bin/env python
"""
Main entry point for the trading application.
This is the recommended way to run the application.
"""
import os
import sys

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

if __name__ == '__main__':
    # Import and run the app
    from scripts.run import main
    main()
