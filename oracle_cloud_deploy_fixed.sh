#!/bin/bash

###############################################################################
# Oracle Cloud Always Free Tier - Trading Application Deployment Script
# Simplified and Fixed Version
###############################################################################

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
APP_USER="trading"
APP_HOME="/home/$APP_USER/trading_app"
APP_PORT=8000
PYTHON_VERSION="3.11"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Oracle Cloud Trading App Deployment${NC}"
echo -e "${BLUE}========================================${NC}"

###############################################################################
# Phase 1: System Update and Dependencies
###############################################################################
echo -e "${YELLOW}[Phase 1] Updating system and installing dependencies...${NC}"

sudo apt-get update
sudo apt-get upgrade -y

# Install required packages
sudo apt-get install -y \
    python${PYTHON_VERSION} \
    python${PYTHON_VERSION}-venv \
    python${PYTHON_VERSION}-dev \
    python3-pip \
    nginx \
    git \
    curl \
    wget \
    build-essential \
    libssl-dev \
    libffi-dev \
    certbot \
    python3-certbot-nginx \
    ufw

echo -e "${GREEN}✓ System updated and dependencies installed${NC}"

###############################################################################
# Phase 2: Create Application User and Directory
###############################################################################
echo -e "${YELLOW}[Phase 2] Setting up application user and directories...${NC}"

# Create trading user if not exists
if ! id "$APP_USER" &>/dev/null; then
    sudo useradd -m -s /bin/bash $APP_USER
    echo -e "${GREEN}✓ Created user: $APP_USER${NC}"
else
    echo -e "${GREEN}✓ User already exists: $APP_USER${NC}"
fi

# Create app directory
sudo mkdir -p $APP_HOME
sudo chown -R $APP_USER:$APP_USER $APP_HOME

# Create subdirectories
sudo -u $APP_USER mkdir -p $APP_HOME/{logs,data,config}

echo -e "${GREEN}✓ Application directory created: $APP_HOME${NC}"

###############################################################################
# Phase 3: Setup Python Virtual Environment
###############################################################################
echo -e "${YELLOW}[Phase 3] Setting up Python virtual environment...${NC}"

sudo -u $APP_USER python${PYTHON_VERSION} -m venv $APP_HOME/venv

# Upgrade pip in virtual environment
sudo -u $APP_USER $APP_HOME/venv/bin/pip install --upgrade pip setuptools wheel

echo -e "${GREEN}✓ Virtual environment created at $APP_HOME/venv${NC}"

###############################################################################
# Phase 4: Install Gunicorn
###############################################################################
echo -e "${YELLOW}[Phase 4] Installing Gunicorn WSGI server...${NC}"

sudo -u $APP_USER $APP_HOME/venv/bin/pip install gunicorn

echo -e "${GREEN}✓ Gunicorn installed${NC}"

###############################################################################
# Phase 5: Create Systemd Service Files
###############################################################################
echo -e "${YELLOW}[Phase 5] Creating systemd service files...${NC}"

# Create Flask application service file
sudo bash -c "cat > /etc/systemd/system/trading-app.service << 'EOF'
[Unit]
Description=Trading Application Flask Server
After=network.target

[Service]
Type=notify
User=$APP_USER
Group=www-data
WorkingDirectory=$APP_HOME
Environment=\"PATH=$APP_HOME/venv/bin\"
ExecStart=$APP_HOME/venv/bin/gunicorn \\
    --workers 4 \\
    --worker-class sync \\
    --bind 127.0.0.1:$APP_PORT \\
    --timeout 120 \\
    --max-requests 1000 \\
    --access-logfile $APP_HOME/logs/access.log \\
    --error-logfile $APP_HOME/logs/error.log \\
    wsgi:app

Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF"

# Create background scheduler service file
sudo bash -c "cat > /etc/systemd/system/trading-scheduler.service << 'EOF'
[Unit]
Description=Trading Application Background Scheduler
After=trading-app.service
Requires=trading-app.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_HOME
Environment=\"PATH=$APP_HOME/venv/bin\"
ExecStart=$APP_HOME/venv/bin/python -m src.trading_app.app.scheduler

Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload

echo -e "${GREEN}✓ Systemd service files created${NC}"

###############################################################################
# Phase 6: Configure Nginx as Reverse Proxy
###############################################################################
echo -e "${YELLOW}[Phase 6] Configuring Nginx as reverse proxy...${NC}"

# Create Nginx configuration
sudo bash -c "cat > /etc/nginx/sites-available/trading-app << 'EOF'
upstream trading_app {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    listen [::]:80;
    server_name _;
    
    client_max_body_size 10M;
    
    # Gzip compression
    gzip on;
    gzip_types text/plain text/css text/javascript application/json;
    
    location / {
        proxy_pass http://trading_app;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 120s;
        proxy_read_timeout 120s;
    }

    location /ws {
        proxy_pass http://trading_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection \"upgrade\";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_buffering off;
    }

    location /static/ {
        alias $APP_HOME/static/;
        expires 30d;
        add_header Cache-Control \"public, immutable\";
    }
}
EOF"

# Enable the site
sudo ln -sf /etc/nginx/sites-available/trading-app /etc/nginx/sites-enabled/trading-app

# Remove default site
sudo rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
if sudo nginx -t; then
    sudo systemctl restart nginx
    echo -e "${GREEN}✓ Nginx configured and restarted${NC}"
else
    echo -e "${RED}✗ Nginx configuration error - please check manually${NC}"
fi

###############################################################################
# Phase 7: Configure Firewall (UFW)
###############################################################################
echo -e "${YELLOW}[Phase 7] Configuring firewall...${NC}"

sudo ufw --force enable
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https

echo -e "${GREEN}✓ Firewall configured${NC}"

###############################################################################
# Phase 8: Create Log Rotation
###############################################################################
echo -e "${YELLOW}[Phase 8] Setting up log rotation...${NC}"

sudo bash -c "cat > /etc/logrotate.d/trading-app << 'EOF'
$APP_HOME/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 $APP_USER www-data
    sharedscripts
}
EOF"

echo -e "${GREEN}✓ Log rotation configured${NC}"

###############################################################################
# Final Summary
###############################################################################
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}✓ Deployment Setup Complete!${NC}"
echo -e "${BLUE}========================================${NC}"

echo ""
echo -e "${YELLOW}NEXT STEPS:${NC}"
echo ""
echo "1. Copy your application code to the instance:"
echo "   scp -r /path/to/your/trading_app/src ubuntu@YOUR_IP:/home/trading/trading_app/"
echo ""
echo "2. Copy requirements.txt:"
echo "   scp /path/to/your/requirements.txt ubuntu@YOUR_IP:/home/trading/trading_app/"
echo ""
echo "3. SSH into the instance:"
echo "   ssh -i your-key.key ubuntu@YOUR_IP"
echo ""
echo "4. Install Python dependencies:"
echo "   source /home/trading/trading_app/venv/bin/activate"
echo "   pip install -r /home/trading/trading_app/requirements.txt"
echo ""
echo "5. Create .env file with credentials:"
echo "   nano /home/trading/trading_app/.env"
echo ""
echo "6. Start the services:"
echo "   sudo systemctl start trading-app"
echo "   sudo systemctl start trading-scheduler"
echo "   sudo systemctl enable trading-app"
echo "   sudo systemctl enable trading-scheduler"
echo ""
echo "7. Check status:"
echo "   sudo systemctl status trading-app"
echo "   curl http://127.0.0.1:8000/"
echo ""
echo -e "${YELLOW}USEFUL COMMANDS:${NC}"
echo ""
echo "  View logs:        sudo journalctl -u trading-app -f"
echo "  Restart service:  sudo systemctl restart trading-app"
echo "  Stop service:     sudo systemctl stop trading-app"
echo "  Check Nginx:      sudo systemctl status nginx"
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Happy Trading on Oracle Cloud! 📈${NC}"
echo -e "${BLUE}========================================${NC}"
