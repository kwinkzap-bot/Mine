#!/bin/bash
# Quick Start Script for Trading Options Strategy Platform

echo "=========================================="
echo "Trading Options Strategy - Quick Start"
echo "=========================================="
echo ""

# Check Python version
echo "✓ Checking Python version..."
python --version

# Create virtual environment (optional)
if [ ! -d "venv" ]; then
    echo ""
    echo "ℹ Creating virtual environment..."
    python -m venv venv
    echo "✓ Virtual environment created"
fi

# Activate virtual environment
echo ""
echo "ℹ Activating virtual environment..."
if [ -d "venv/Scripts" ]; then
    # Windows
    source venv/Scripts/activate
else
    # Linux/Mac
    source venv/bin/activate
fi
echo "✓ Virtual environment activated"

# Install dependencies
echo ""
echo "ℹ Installing dependencies..."
pip install -r requirements.txt
echo "✓ Dependencies installed"

# Check .env file
echo ""
echo "ℹ Checking .env file..."
if [ ! -f ".env" ]; then
    echo "⚠ .env file not found. Creating template..."
    cat > .env << EOF
# Flask Configuration
FLASK_ENV=development
FLASK_HOST=127.0.0.1
FLASK_PORT=5000

# Zerodha API
API_KEY=your_api_key_here
ACCESS_TOKEN=your_access_token_here

# Security
SECRET_KEY=dev-key-change-in-production

# Logging
LOG_LEVEL=INFO
EOF
    echo "✓ .env template created. Please update with your credentials."
else
    echo "✓ .env file found"
fi

# Run syntax check
echo ""
echo "ℹ Running syntax validation..."
python -m py_compile app/__init__.py app/config.py app/extensions.py
if [ $? -eq 0 ]; then
    echo "✓ Syntax validation passed"
else
    echo "✗ Syntax validation failed"
    exit 1
fi

# Display next steps
echo ""
echo "=========================================="
echo "✓ Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Update .env with your Zerodha credentials"
echo "2. Run: python run.py"
echo "3. Visit: http://127.0.0.1:5000"
echo ""
echo "Documentation:"
echo "- STRUCTURE.md - New folder structure"
echo "- MIGRATION_GUIDE.md - Migration from old structure"
echo "- OPTIMIZATION_SUMMARY.md - What was optimized"
echo ""
