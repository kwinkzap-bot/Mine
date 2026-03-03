cat > /tmp/deploy.sh << 'EOF'
#!/bin/bash
set -e
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
APP_USER="trading"
APP_HOME="/home/$APP_USER/trading_app"
APP_PORT=8000
PYTHON_VERSION="3.11"

echo -e "${BLUE}Oracle Cloud Trading App Deployment${NC}"
echo -e "${YELLOW}[1] Updating system...${NC}"
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python${PYTHON_VERSION}-dev python3-pip nginx git curl wget build-essential libssl-dev libffi-dev certbot python3-certbot-nginx ufw
echo -e "${GREEN}✓ System updated${NC}"

echo -e "${YELLOW}[2] Creating user and directories...${NC}"
if ! id "$APP_USER" &>/dev/null; then
    sudo useradd -m -s /bin/bash $APP_USER
fi
sudo mkdir -p $APP_HOME/{logs,data,config}
sudo chown -R $APP_USER:$APP_USER $APP_HOME
echo -e "${GREEN}✓ Directories created${NC}"

echo -e "${YELLOW}[3] Setting up Python venv...${NC}"
sudo -u $APP_USER python${PYTHON_VERSION} -m venv $APP_HOME/venv
sudo -u $APP_USER $APP_HOME/venv/bin/pip install --upgrade pip setuptools wheel gunicorn
echo -e "${GREEN}✓ Python venv ready${NC}"

echo -e "${YELLOW}[4] Creating systemd service...${NC}"
sudo tee /etc/systemd/system/trading-app.service > /dev/null << 'SVCEOF'
[Unit]
Description=Trading Application Flask Server
After=network.target

[Service]
User=trading
Group=www-data
WorkingDirectory=/home/trading/trading_app
Environment="PATH=/home/trading/trading_app/venv/bin"
ExecStart=/home/trading/trading_app/venv/bin/gunicorn --workers 4 --bind 127.0.0.1:8000 --timeout 120 wsgi:app
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF
sudo systemctl daemon-reload
echo -e "${GREEN}✓ Service created${NC}"

echo -e "${YELLOW}[5] Configuring Nginx...${NC}"
sudo tee /etc/nginx/sites-available/trading-app > /dev/null << 'NGXEOF'
upstream trading_app {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name _;
    client_max_body_size 10M;
    gzip on;
    gzip_types text/plain text/css text/javascript application/json;
    
    location / {
        proxy_pass http://trading_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_connect_timeout 60s;
        proxy_send_timeout 120s;
        proxy_read_timeout 120s;
    }
    
    location /ws {
        proxy_pass http://trading_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
        proxy_buffering off;
    }
    
    location /static/ {
        alias /home/trading/trading_app/static/;
        expires 30d;
    }
}
NGXEOF
sudo ln -sf /etc/nginx/sites-available/trading-app /etc/nginx/sites-enabled/trading-app
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx
echo -e "${GREEN}✓ Nginx configured${NC}"

echo -e "${YELLOW}[6] Setting up firewall...${NC}"
sudo ufw --force enable
sudo ufw default deny incoming
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https
echo -e "${GREEN}✓ Firewall ready${NC}"

echo -e "${BLUE}======================================${NC}"
echo -e "${GREEN}✓ Deployment Complete!${NC}"
echo -e "${BLUE}======================================${NC}"
echo ""
echo "NEXT STEPS:"
echo "1. Copy your code: scp -r trading_app/src ubuntu@YOUR_IP:/home/trading/trading_app/"
echo "2. Copy requirements.txt: scp requirements.txt ubuntu@YOUR_IP:/home/trading/trading_app/"
echo "3. SSH in and install deps: pip install -r /home/trading/trading_app/requirements.txt"
echo "4. Create .env file: nano /home/trading/trading_app/.env"
echo "5. Start: sudo systemctl start trading-app && sudo systemctl enable trading-app"
echo "6. Check: curl http://localhost:8000"
echo ""
EOF
chmod +x /tmp/deploy.sh
sudo bash /tmp/deploy.sh