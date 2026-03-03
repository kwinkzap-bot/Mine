#!/bin/bash

###############################################################################
# Oracle Cloud Trading Application - Post-Deployment Setup Guide
# This document provides step-by-step instructions for deploying the
# trading application to Oracle Cloud Always Free Tier
###############################################################################

# ============================================================================
# STEP 1: Create Oracle Cloud Account and Instance
# ============================================================================

# 1.1 Create account at: https://www.oracle.com/cloud/free/
#     - Always Free: No credit card required for free tier
#     - Free 30 days with $300 credit if you provide payment method
#     - Always Free resources have no time limit

# 1.2 Create a Compute Instance
#     - Click: Compute → Instances → Create Instance
#     - Name: trading-app (or your preferred name)
#     - Image: Ubuntu 22.04 LTS (always free eligible)
#     - Shape: Ampere (ARM-based) - free tier eligible
#       - 4 OCPUs (CPU cores)
#       - 24GB RAM
#       - These are always free!
#     - VCN (Virtual Cloud Network): Default or create new
#     - Public IP: Assign (or use private IP with VPN)
#     - Storage: 50-100 GB (recommended)
#     - Download SSH key pair (.key file)

# 1.3 Configure Security Group
#     - Ingress Rules:
#       • SSH (port 22): Your IP only
#       • HTTP (port 80): 0.0.0.0/0 (anywhere)
#       • HTTPS (port 443): 0.0.0.0/0 (anywhere)
#     - Egress Rules: Allow all (default)

# ============================================================================
# STEP 2: SSH into Instance
# ============================================================================

# Set correct permissions on your SSH key
chmod 600 /path/to/your/ssh/key.key

# SSH into the instance
# Replace PUBLIC_IP with your instance's public IP address
ssh -i /path/to/your/ssh/key.key ubuntu@PUBLIC_IP

# ============================================================================
# STEP 3: Run Deployment Script
# ============================================================================

# Download the deployment script to the instance
# Option A: Copy from your local machine
scp -i /path/to/your/ssh/key.key oracle_cloud_deploy.sh ubuntu@PUBLIC_IP:/tmp/

# Option B: Or download directly in the instance
cd /tmp
wget https://path-to-your-repo/oracle_cloud_deploy.sh
chmod +x /tmp/oracle_cloud_deploy.sh

# Run the deployment script
# This will take 5-10 minutes
sudo bash /tmp/oracle_cloud_deploy.sh

# ============================================================================
# STEP 4: Prepare Your Application
# ============================================================================

# Create environment file with your credentials
cat > ~/.env <<'EOF'
# Kite Broker
KITE_API_KEY=your_kite_api_key_here
KITE_ACCESS_TOKEN=your_kite_access_token

# Fyers Broker
FYERS_API_ID=your_fyers_api_id
FYERS_API_TOKEN=your_fyers_token

# Kotak Broker
KOTAK_API_KEY=your_kotak_key
KOTAK_API_SECRET=your_kotak_secret

# Dhan Broker
DHAN_API_KEY=your_dhan_key
DHAN_ACCESS_TOKEN=your_dhan_token

# Flask Configuration
FLASK_ENV=production
FLASK_DEBUG=0
SECRET_KEY=generate_a_secure_key_here

# Database (optional)
DB_URL=sqlite:///trading_app.db

# WhatsApp Integration (optional)
WHATSAPP_API_KEY=your_key_here
EOF

# Set restrictive permissions
chmod 600 ~/.env

# Copy application code
# From your local machine:
scp -r -i /path/to/key.key /path/to/your/trading_app ubuntu@PUBLIC_IP:/home/trading/trading_app/src/

# Copy requirements.txt
scp -i /path/to/key.key requirements.txt ubuntu@PUBLIC_IP:/home/trading/trading_app/

# ============================================================================
# STEP 5: Install Dependencies (in the instance)
# ============================================================================

# Activate virtual environment
source /home/trading/trading_app/venv/bin/activate

# Install Python dependencies
cd /home/trading/trading_app
pip install -r requirements.txt

# Deactivate (optional)
deactivate

# ============================================================================
# STEP 6: Set Up SSL Certificate (Optional but Recommended)
# ============================================================================

# If you have a domain name (e.g., trading.example.com):

# Install Certbot and setup Let's Encrypt
sudo apt-get install -y certbot python3-certbot-nginx

# Get certificate for your domain
sudo certbot certonly --nginx -d your-domain.com

# This will create certificates at:
# /etc/letsencrypt/live/your-domain.com/fullchain.pem
# /etc/letsencrypt/live/your-domain.com/privkey.pem

# Update Nginx configuration
sudo nano /etc/nginx/sites-available/trading-app
# Find these lines and update:
#   ssl_certificate /etc/letsencrypt/live/YOUR_DOMAIN/fullchain.pem;
#   ssl_certificate_key /etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem;

# Test and reload
sudo nginx -t
sudo systemctl reload nginx

# Setup auto-renewal (runs at 3 AM daily)
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer

# ============================================================================
# STEP 7: Start Services
# ============================================================================

# Enable services to start on boot
sudo systemctl enable trading-app
sudo systemctl enable trading-scheduler
sudo systemctl enable nginx

# Start the services
sudo systemctl start trading-app
sudo systemctl start trading-scheduler

# Wait a few seconds, then check status
sleep 5
sudo systemctl status trading-app
sudo systemctl status trading-scheduler

# ============================================================================
# STEP 8: Verify Deployment
# ============================================================================

# Check if Flask app is running
curl http://127.0.0.1:8000/
# Should return your Flask app's home page

# Check health endpoint
curl http://127.0.0.1:8000/health
# Should return JSON with status

# Check via Nginx (from outside)
curl http://PUBLIC_IP/
curl https://your-domain.com/  # If using domain

# View logs
sudo journalctl -u trading-app -n 50
sudo journalctl -u trading-scheduler -n 50

# ============================================================================
# STEP 9: Configure Firewall (if not already done)
# ============================================================================

# Check firewall status
sudo ufw status

# If disabled, enable and configure:
sudo ufw enable
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https

# Verify
sudo ufw status numbered

# ============================================================================
# STEP 10: Setup Monitoring and Backups
# ============================================================================

# Check system resources
df -h /  # Disk usage
free -h  # Memory usage
top      # Real-time monitoring (press q to quit)

# Monitor application logs
sudo journalctl -u trading-app -f  # Follow Flask logs
sudo journalctl -u trading-scheduler -f  # Follow scheduler logs

# Tail error logs
tail -f /home/trading/trading_app/logs/error.log

# Create backup script
cat > /home/trading/backup.sh <<'EOF'
#!/bin/bash
BACKUP_DIR="/home/trading/backups"
mkdir -p $BACKUP_DIR
BACKUP_FILE="$BACKUP_DIR/backup-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf $BACKUP_FILE /home/trading/trading_app/
echo "Backup created: $BACKUP_FILE"
# Keep only last 7 backups
cd $BACKUP_DIR && ls -t | tail -n +8 | xargs rm -f
EOF

chmod +x /home/trading/backup.sh

# Schedule daily backups at 2 AM
# sudo crontab -e
# Add: 0 2 * * * /home/trading/backup.sh

# ============================================================================
# USEFUL COMMANDS FOR MANAGEMENT
# ============================================================================

# View application status
sudo systemctl status trading-app
sudo systemctl status trading-scheduler
sudo systemctl status nginx

# Restart services
sudo systemctl restart trading-app
sudo systemctl restart trading-scheduler
sudo systemctl restart nginx

# Stop services
sudo systemctl stop trading-app
sudo systemctl stop trading-scheduler

# View logs
sudo journalctl -u trading-app -n 100  # Last 100 lines
sudo journalctl -u trading-app -f      # Follow live logs
sudo journalctl -u trading-app --since "2 hours ago"  # Last 2 hours

# View Nginx logs
sudo tail -f /var/log/nginx/trading_app_access.log
sudo tail -f /var/log/nginx/trading_app_error.log

# Test Nginx configuration
sudo nginx -t

# Reload Nginx (without downtime)
sudo systemctl reload nginx

# SSH into trading user (for debugging)
sudo -u trading bash

# View application code
ls -la /home/trading/trading_app/src/

# View environment variables (as trading user)
sudo -u trading cat /home/trading/trading_app/.env

# Check process listening on port 8000
sudo netstat -tulpn | grep :8000
# or
sudo ss -tulpn | grep :8000

# ============================================================================
# TROUBLESHOOTING
# ============================================================================

# Application won't start?
# 1. Check logs: sudo journalctl -u trading-app -n 50
# 2. Verify dependencies: source venv/bin/activate && pip list
# 3. Check Python syntax: python -m py_compile src/trading_app/app/*.py
# 4. Test Flask directly: python wsgi.py

# Nginx not working?
# 1. Check config: sudo nginx -t
# 2. Check status: sudo systemctl status nginx
# 3. Check logs: sudo tail -f /var/log/nginx/error.log
# 4. Verify proxy: curl -H "Host: your-domain.com" http://127.0.0.1

# High CPU/Memory usage?
# 1. Identify process: ps aux | grep python
# 2. Monitor: top -u trading
# 3. Check database: du -sh /home/trading/trading_app/data/
# 4. Review logs for errors

# Disk space running out?
# 1. Check usage: df -h /
# 2. Find large files: find /home/trading -type f -size +100M
# 3. Clean logs: sudo journalctl --vacuum=100M
# 4. Remove old backups: ls -lt /home/trading/backups/ | tail -n +8 | xargs rm

# ============================================================================
# PERFORMANCE OPTIMIZATION
# ============================================================================

# 1. Increase gunicorn workers based on CPU cores
#    Edit: /etc/systemd/system/trading-app.service
#    --workers 8  # For 4-core CPU, use 2x cores
#    Then: sudo systemctl daemon-reload && sudo systemctl restart trading-app

# 2. Enable gzip compression in Nginx (already done in config)

# 3. Optimize database
#    - Use SQLite3 for single-instance (already configured)
#    - Consider PostgreSQL for multi-instance setup
#    - Add database indexes for frequent queries

# 4. Use Redis for caching (optional)
#    sudo apt-get install redis-server
#    Update Flask config to use Redis

# 5. Monitor and tune memory
#    Increase worker timeouts if requests take >120 seconds:
#    --timeout 180  # Edit in trading-app.service

# ============================================================================
# COST MONITORING (Oracle Cloud Free Tier)
# ============================================================================

# Always Free Resources:
# - 2 VMs with 1 OCPU, 1GB RAM each (or 1 with 4 OCPU, 24GB)
# - 10 GB of Block Storage
# - 10 GB of Object Storage
# - 10 GB of Archive Storage
# - 21 GB of Autonomous Database

# Monthly Cost: $0 (unlimited duration)
# No credit card required to stay free

# Track usage in Oracle Cloud Console:
# - Billing → Cost Analysis
# - Governance → Usage Limits
# - Set spending alerts to notify if costs exceed free tier

# ============================================================================
# NEXT STEPS
# ============================================================================

# 1. Verify everything is working:
#    - Access your app via IP or domain
#    - Test placing an order
#    - Check real-time data streaming
#    - Monitor logs for errors

# 2. Setup monitoring:
#    - Use CloudWatch or DataDog for alerts
#    - Monitor disk space, CPU, memory, errors
#    - Set up notification when services go down

# 3. Plan backups:
#    - Daily backups of application data
#    - Weekly backups of database
#    - Store backups in Oracle Cloud Object Storage (free tier)

# 4. Document your setup:
#    - Save SSH keys securely
#    - Document all credentials in password manager
#    - Create runbooks for common operations
#    - Keep Oracle Cloud account credentials safe

# ============================================================================
# ESTIMATED DEPLOYMENT TIME
# ============================================================================

# 1. Create Oracle Cloud account: 5 minutes
# 2. Create and configure instance: 10 minutes
# 3. Run deployment script: 10 minutes
# 4. Copy application code: 5 minutes
# 5. Install dependencies: 10 minutes
# 6. Setup SSL (optional): 5 minutes
# 7. Start services: 2 minutes
# 8. Verify deployment: 5 minutes
#
# TOTAL: 25-35 minutes for complete production deployment

# ============================================================================
# SUPPORT & DOCUMENTATION
# ============================================================================

# Oracle Cloud Free Tier: https://www.oracle.com/cloud/free/
# Ubuntu on Oracle Cloud: https://www.oracle.com/ubuntu/
# Flask Documentation: https://flask.palletsprojects.com
# Gunicorn Documentation: https://gunicorn.org/
# Nginx Documentation: https://nginx.org/en/docs/
# APScheduler Documentation: https://apscheduler.readthedocs.io/

###############################################################################
# Happy Deploying! Your trading app is now running on Oracle Cloud Free Tier
# 24/7 with 4 CPUs, 24GB RAM, and no monthly cost. 📈
###############################################################################
