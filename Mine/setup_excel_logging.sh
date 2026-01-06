#!/bin/bash
# Setup script to install debugging dependencies

echo "🔧 Installing Excel logging dependencies..."

# Check if virtual environment is activated
if [ -z "$VIRTUAL_ENV" ]; then
    echo "⚠️  Virtual environment not activated"
    echo "Run this from your virtual environment:"
    echo "source .venv/bin/activate"
    exit 1
fi

# Install openpyxl for Excel logging
echo "📦 Installing openpyxl..."
pip install openpyxl==3.1.2

# Install schedule if not already installed
echo "📦 Installing schedule..."
pip install schedule==1.2.0

# Verify installation
echo ""
echo "✅ Verifying installation..."
python -c "import openpyxl; print('✓ openpyxl version:', openpyxl.__version__)"
python -c "import schedule; print('✓ schedule version:', schedule.__version__)"

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Run the application: python run.py"
echo "2. Excel file will be created at: /Users/kavinkumar/Mine/Mine/signal_logs.xlsx"
echo "3. Open signal_logs.xlsx in Excel and refresh every 5 minutes to see updates"
echo ""
echo "For detailed debugging instructions, see: DEBUGGING_LIVE_SIGNALS.md"
