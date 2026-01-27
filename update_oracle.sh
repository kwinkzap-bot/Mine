#!/bin/bash
# Quick Update Script for Oracle Cloud Deployment
# Run this after making code changes

set -e

APP_DIR="/home/ubuntu/trading-app/Mine"

echo "🔄 Updating Trading App..."

# Navigate to app directory
cd $APP_DIR

# Pull latest changes (if using Git)
if [ -d .git ]; then
    echo "📥 Pulling latest changes from Git..."
    git pull
fi

# Activate virtual environment
source ../venv/bin/activate

# Update dependencies if requirements.txt changed
echo "📚 Checking for dependency updates..."
pip install -r requirements.txt --upgrade

# Restart the application
echo "🔄 Restarting application..."
sudo systemctl restart trading-app

# Wait a moment for restart
sleep 2

# Check status
echo ""
echo "✅ Update complete!"
echo ""
echo "📊 Service Status:"
sudo systemctl status trading-app --no-pager | head -n 10

echo ""
echo "📝 View logs with: sudo journalctl -u trading-app -f"
