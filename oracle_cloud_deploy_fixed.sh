bash -c "$(cat << 'DEPLOY_SCRIPT'
#!/bin/bash
set -e
APP_USER="trading"
APP_HOME="/home/$APP_USER/trading_app"

echo "=== Oracle Cloud Trading App Setup ==="
echo "[1] Updating system..."
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip nginx git curl wget build-essential libssl-dev libffi-dev certbot python3-certbot-nginx ufw

echo "[2] Creating user and directories..."
sudo useradd -m -s /bin/bash trading 2>/dev/null || true
sudo mkdir -p $APP_HOME/{logs,data,config}
sudo chown -R $APP_USER:$APP_USER $APP_HOME

echo "[3] Setting up Python venv..."
sudo -u trading python3.11 -m venv $APP_HOME/venv
sudo -u trading $APP_HOME/venv/bin/pip install --upgrade pip setuptools wheel gunicorn

echo "[4] Creating systemd service..."
sudo tee /etc/systemd/system/trading-app.service > /dev/null << 'EOF'
[Unit]
Description=Trading Application
After=network.target

[Service]
User=trading
Group=www-data
WorkingDirectory=/home/trading/trading_app
Environment="PATH=/home/trading/trading_app/venv/bin"
ExecStart=/home/trading/trading_app/venv/bin/gunicorn --workers 4 --bind 127.0.0.1:8000 --timeout 120 wsgi:app
Restart=always
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload

echo "[5] Configuring Nginx..."
sudo tee /etc/nginx/sites-available/trading-app > /dev/null << 'EOF'
upstream trading_app {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name _;
    client_max_body_size 10M;
    
    location / {
        proxy_pass http://trading_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_connect_timeout 60s;
        proxy_send_timeout 120s;
        proxy_read_timeout 120s;
    }
    
    location /static/ {
        alias /home/trading/trading_app/static/;
        expires 30d;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/trading-app /etc/nginx/sites-enabled/trading-app
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

echo "[6] Setting up firewall..."
sudo ufw --force enable
sudo ufw default deny incoming
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "NEXT:"
echo "1. Copy app code: scp -r src ubuntu@YOUR_IP:/home/trading/trading_app/"
echo "2. Copy requirements.txt: scp requirements.txt ubuntu@YOUR_IP:/home/trading/trading_app/"
echo "3. Install deps: source /home/trading/trading_app/venv/bin/activate && pip install -r /home/trading/trading_app/requirements.txt"
echo "4. Create .env: nano /home/trading/trading_app/.env"
echo "5. Start: sudo systemctl start trading-app && sudo systemctl enable trading-app"
echo "6. Check: curl http://localhost"
DEPLOY_SCRIPT
)"