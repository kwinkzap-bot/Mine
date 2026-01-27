#!/bin/bash
# Oracle Cloud VM Deployment Script for Trading Application
# Run this script on your Oracle Cloud Ubuntu VM

set -e

echo "🚀 Starting Trading App Deployment on Oracle Cloud..."

# Update system
echo "📦 Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install Python 3.13 and dependencies
echo "🐍 Installing Python 3.13..."
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.13 python3.13-venv python3.13-dev python3-pip

# Install nginx for reverse proxy
echo "🌐 Installing nginx..."
sudo apt install -y nginx

# Install git (if not present)
sudo apt install -y git

# Create application directory
APP_DIR="/home/ubuntu/trading-app"
echo "📁 Creating application directory at $APP_DIR..."
sudo mkdir -p $APP_DIR
sudo chown ubuntu:ubuntu $APP_DIR

# Clone or copy application
echo "📥 Setting up application files..."
cd $APP_DIR

# If running from local, files should already be here
# Otherwise, you can git clone:
# git clone <your-repo-url> .

# Create virtual environment
echo "🔧 Creating Python virtual environment..."
python3.13 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
echo "📚 Installing Python dependencies..."
cd Mine
pip install -r requirements.txt

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file template..."
    cat > .env << 'EOF'
# Zerodha Kite API Credentials
API_KEY=your_api_key_here
API_SECRET=your_api_secret_here
ACCESS_TOKEN=your_access_token_here

# Flask Configuration
FLASK_ENV=production
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
SECRET_KEY=your_secret_key_here

# Other credentials...
EOF
    echo "⚠️  Please edit .env file with your actual credentials"
fi

# Create systemd service file
echo "🔧 Creating systemd service..."
sudo tee /etc/systemd/system/trading-app.service > /dev/null << EOF
[Unit]
Description=Trading Application
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$APP_DIR/Mine
Environment="PATH=$APP_DIR/venv/bin"
ExecStart=$APP_DIR/venv/bin/gunicorn wsgi:app --bind 127.0.0.1:5000 --workers 2 --timeout 120
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Create nginx configuration
echo "🌐 Configuring nginx..."
sudo tee /etc/nginx/sites-available/trading-app > /dev/null << 'EOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 20M;
    
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Static files
    location /static {
        alias $APP_DIR/Mine/static;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
EOF

# Enable nginx site
sudo ln -sf /etc/nginx/sites-available/trading-app /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Test nginx configuration
sudo nginx -t

# Configure firewall (Oracle Cloud uses iptables)
echo "🔥 Configuring firewall..."
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

# Reload systemd and start services
echo "🎬 Starting services..."
sudo systemctl daemon-reload
sudo systemctl enable trading-app
sudo systemctl start trading-app
sudo systemctl restart nginx

# Check service status
echo ""
echo "✅ Deployment complete!"
echo ""
echo "📊 Service Status:"
sudo systemctl status trading-app --no-pager

echo ""
echo "🌐 Your app should be accessible at:"
echo "http://$(curl -s ifconfig.me)"
echo ""
echo "📝 Useful commands:"
echo "  - View logs: sudo journalctl -u trading-app -f"
echo "  - Restart app: sudo systemctl restart trading-app"
echo "  - Check status: sudo systemctl status trading-app"
echo "  - Edit .env: nano $APP_DIR/Mine/.env"
echo ""
echo "⚠️  Don't forget to:"
echo "  1. Edit $APP_DIR/Mine/.env with your API credentials"
echo "  2. Open port 80 in Oracle Cloud Network Security List"
echo "  3. Restart the app after updating .env"
