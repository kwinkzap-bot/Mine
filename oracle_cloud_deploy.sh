#!/bin/bash

###############################################################################
# Oracle Cloud Always Free Tier - Trading Application Deployment Script
# This script automates the deployment of the trading application to Oracle Cloud
# 
# Prerequisites:
# - Oracle Cloud Always Free account
# - Ubuntu 22.04 VM instance (4 OCPU, 24GB RAM free tier)
# - SSH access to the instance
# - Domain name (optional, for HTTPS)
#
# Usage: bash oracle_cloud_deploy.sh [domain.com]
#        bash oracle_cloud_deploy.sh  # for IP-based access
###############################################################################

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
DOMAIN=${1:-""}
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
    supervisor \
    certbot \
    python3-certbot-nginx \
    ufw \
    htop \
    iotop

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

echo -e "${GREEN}✓ Application directory created: $APP_HOME${NC}"

###############################################################################
# Phase 3: Clone/Copy Application Code
###############################################################################
echo -e "${YELLOW}[Phase 3] Preparing application code...${NC}"

# If deploying from GitHub (uncomment and modify if needed)
# sudo -u $APP_USER git clone https://github.com/YOUR_REPO/trading-app.git $APP_HOME

# For now, create placeholder directory structure
sudo -u $APP_USER mkdir -p $APP_HOME/{logs,data,config}

echo -e "${GREEN}✓ Application directory structure created${NC}"
echo -e "${YELLOW}   NOTE: Copy your trading application code to $APP_HOME${NC}"

###############################################################################
# Phase 4: Setup Python Virtual Environment
###############################################################################
echo -e "${YELLOW}[Phase 4] Setting up Python virtual environment...${NC}"

sudo -u $APP_USER python${PYTHON_VERSION} -m venv $APP_HOME/venv

# Activate and upgrade pip
source $APP_HOME/venv/bin/activate
pip install --upgrade pip setuptools wheel

echo -e "${GREEN}✓ Virtual environment created at $APP_HOME/venv${NC}"

###############################################################################
# Phase 5: Install Application Dependencies
###############################################################################
echo -e "${YELLOW}[Phase 5] Installing application dependencies...${NC}"

# Create requirements.txt if it doesn't exist
if [ -f "$APP_HOME/requirements.txt" ]; then
    sudo -u $APP_USER $APP_HOME/venv/bin/pip install -r $APP_HOME/requirements.txt
    echo -e "${GREEN}✓ Dependencies installed from requirements.txt${NC}"
else
    echo -e "${YELLOW}⚠ requirements.txt not found at $APP_HOME/requirements.txt${NC}"
    echo -e "${YELLOW}   Please add your requirements.txt and run:${NC}"
    echo -e "${YELLOW}   source $APP_HOME/venv/bin/activate${NC}"
    echo -e "${YELLOW}   pip install -r $APP_HOME/requirements.txt${NC}"
fi

###############################################################################
# Phase 6: Create Systemd Service Files
###############################################################################
echo -e "${YELLOW}[Phase 6] Creating systemd service files...${NC}"

# Flask application service
sudo tee /etc/systemd/system/trading-app.service > /dev/null <<EOF
[Unit]
Description=Trading Application Flask Server
After=network.target

[Service]
User=$APP_USER
Group=www-data
WorkingDirectory=$APP_HOME
Environment="PATH=$APP_HOME/venv/bin"
ExecStart=$APP_HOME/venv/bin/gunicorn \\
    --workers 4 \\
    --worker-class sync \\
    --bind 127.0.0.1:$APP_PORT \\
    --timeout 120 \\
    --max-requests 1000 \\
    --max-requests-jitter 100 \\
    --access-logfile $APP_HOME/logs/access.log \\
    --error-logfile $APP_HOME/logs/error.log \\
    --log-level info \\
    wsgi:app

Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Background scheduler service
sudo tee /etc/systemd/system/trading-scheduler.service > /dev/null <<EOF
[Unit]
Description=Trading Application Background Scheduler
After=trading-app.service
Requires=trading-app.service

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_HOME
Environment="PATH=$APP_HOME/venv/bin"
ExecStart=$APP_HOME/venv/bin/python -c "from src.trading_app.app.scheduler import init_scheduler; init_scheduler()"

Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
echo -e "${GREEN}✓ Systemd service files created${NC}"

###############################################################################
# Phase 7: Configure Nginx as Reverse Proxy
###############################################################################
echo -e "${YELLOW}[Phase 7] Configuring Nginx as reverse proxy...${NC}"

# Backup original config
sudo cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.bak

# Create trading app nginx config
sudo tee /etc/nginx/sites-available/trading-app > /dev/null <<'EOF'
upstream trading_app {
    server 127.0.0.1:8000;
}

# Redirect HTTP to HTTPS (if domain provided)
server {
    listen 80;
    listen [::]:80;
    server_name _;
    
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    
    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS server block
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name _;

    # SSL configuration (update paths if using custom certificate)
    ssl_certificate /etc/letsencrypt/live/YOUR_DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Logging
    access_log /var/log/nginx/trading_app_access.log;
    error_log /var/log/nginx/trading_app_error.log;

    # Client upload size
    client_max_body_size 10M;

    # Gzip compression
    gzip on;
    gzip_types text/plain text/css text/xml text/javascript 
               application/x-javascript application/xml+rss;
    gzip_vary on;
    gzip_comp_level 6;

    location / {
        proxy_pass http://trading_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
        
        # Timeouts for long-running requests
        proxy_connect_timeout 60s;
        proxy_send_timeout 120s;
        proxy_read_timeout 120s;
    }

    # WebSocket support
    location /ws {
        proxy_pass http://trading_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Static files
    location /static/ {
        alias /var/www/trading_app/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/trading-app /etc/nginx/sites-enabled/trading-app

# Remove default site
sudo rm -f /etc/nginx/sites-enabled/default

# Test nginx config
if sudo nginx -t; then
    sudo systemctl restart nginx
    echo -e "${GREEN}✓ Nginx configured and restarted${NC}"
else
    echo -e "${RED}✗ Nginx configuration error${NC}"
    exit 1
fi

###############################################################################
# Phase 8: Configure Firewall (UFW)
###############################################################################
echo -e "${YELLOW}[Phase 8] Configuring firewall...${NC}"

sudo ufw --force enable
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https

echo -e "${GREEN}✓ Firewall configured${NC}"

###############################################################################
# Phase 9: Install Gunicorn
###############################################################################
echo -e "${YELLOW}[Phase 9] Installing Gunicorn WSGI server...${NC}"

source $APP_HOME/venv/bin/activate
pip install gunicorn[gevent]

deactivate
echo -e "${GREEN}✓ Gunicorn installed${NC}"

###############################################################################
# Phase 10: Create Monitoring Script
###############################################################################
echo -e "${YELLOW}[Phase 10] Creating monitoring script...${NC}"

sudo tee /usr/local/bin/check-trading-health.sh > /dev/null <<'EOF'
#!/bin/bash

HEALTH_LOG="/var/log/trading_health.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] Health Check" >> $HEALTH_LOG

# Check if Flask app is running
if systemctl is-active --quiet trading-app; then
    echo "✓ Trading app is running" >> $HEALTH_LOG
else
    echo "✗ Trading app is NOT running - Restarting..." >> $HEALTH_LOG
    sudo systemctl restart trading-app
fi

# Check Nginx
if systemctl is-active --quiet nginx; then
    echo "✓ Nginx is running" >> $HEALTH_LOG
else
    echo "✗ Nginx is NOT running - Restarting..." >> $HEALTH_LOG
    sudo systemctl restart nginx
fi

# Check disk space
DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 85 ]; then
    echo "⚠ Disk usage is ${DISK_USAGE}% - Consider cleanup" >> $HEALTH_LOG
else
    echo "✓ Disk usage: ${DISK_USAGE}%" >> $HEALTH_LOG
fi

echo "" >> $HEALTH_LOG
EOF

sudo chmod +x /usr/local/bin/check-trading-health.sh

# Add to crontab
(sudo crontab -l 2>/dev/null || echo "") | grep -q "check-trading-health" || \
    (sudo crontab -l 2>/dev/null || echo ""; echo "*/5 * * * * /usr/local/bin/check-trading-health.sh") | sudo crontab -

echo -e "${GREEN}✓ Monitoring script created and scheduled${NC}"

###############################################################################
# Phase 11: Setup Log Rotation
###############################################################################
echo -e "${YELLOW}[Phase 11] Configuring log rotation...${NC}"

sudo tee /etc/logrotate.d/trading-app > /dev/null <<EOF
$APP_HOME/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 $APP_USER www-data
    sharedscripts
    postrotate
        systemctl reload trading-app > /dev/null 2>&1 || true
    endscript
}
EOF

echo -e "${GREEN}✓ Log rotation configured${NC}"

###############################################################################
# Phase 12: Final Instructions
###############################################################################
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}✓ Deployment Setup Complete!${NC}"
echo -e "${BLUE}========================================${NC}"

echo ""
echo -e "${YELLOW}IMPORTANT NEXT STEPS:${NC}"
echo ""
echo "1. ${YELLOW}Copy your application code:${NC}"
echo "   scp -r /path/to/your/trading_app ubuntu@your-instance-ip:$APP_HOME/src"
echo "   scp /path/to/your/requirements.txt ubuntu@your-instance-ip:$APP_HOME/"
echo ""
echo "2. ${YELLOW}Install Python dependencies:${NC}"
echo "   source $APP_HOME/venv/bin/activate"
echo "   pip install -r $APP_HOME/requirements.txt"
echo ""
echo "3. ${YELLOW}Setup SSL certificate (if using domain):${NC}"
echo "   sudo certbot certonly --nginx -d your-domain.com"
echo "   Update /etc/nginx/sites-available/trading-app with your domain"
echo "   sudo systemctl reload nginx"
echo ""
echo "4. ${YELLOW}Configure environment variables:${NC}"
echo "   Create $APP_HOME/.env with required broker credentials"
echo "   Set appropriate permissions: chmod 600 .env"
echo ""
echo "5. ${YELLOW}Start the application:${NC}"
echo "   sudo systemctl start trading-app"
echo "   sudo systemctl start trading-scheduler"
echo "   sudo systemctl enable trading-app"
echo "   sudo systemctl enable trading-scheduler"
echo ""
echo "6. ${YELLOW}Verify it's running:${NC}"
echo "   sudo systemctl status trading-app"
echo "   curl http://127.0.0.1:$APP_PORT"
echo ""
echo -e "${YELLOW}USEFUL COMMANDS:${NC}"
echo ""
echo "  View logs:        sudo journalctl -u trading-app -f"
echo "  Restart app:      sudo systemctl restart trading-app"
echo "  Check health:     sudo /usr/local/bin/check-trading-health.sh"
echo "  Monitor disk:     df -h / && du -sh $APP_HOME"
echo "  Backup data:      tar -czf backup-\$(date +%Y%m%d).tar.gz $APP_HOME/data"
echo ""
echo -e "${YELLOW}ORACLE CLOUD SETTINGS:${NC}"
echo "  - Instance: VM.Standard.E2.1.Micro (eligible for always-free)"
echo "  - vCPUs: 4 (using 1.5 vCPUs in free tier)"
echo "  - RAM: 24GB (using 6GB in free tier, upgrade available)"
echo "  - Storage: 10GB (additional storage available at low cost)"
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Happy Trading! 📈${NC}"
echo -e "${BLUE}========================================${NC}"
