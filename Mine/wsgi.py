#!/usr/bin/env python
"""
WSGI entry point for gunicorn deployment.
This file creates the Flask application instance for production servers.
"""
import os
import sys

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from trading_app.app import create_app

# Create the Flask application instance
app = create_app()

if __name__ == "__main__":
    app.run()
