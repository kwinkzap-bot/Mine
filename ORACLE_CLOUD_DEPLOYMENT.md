# 🚀 Oracle Cloud Deployment Guide

## Prerequisites

1. **Oracle Cloud Account** (Free Tier)
   - Sign up at: https://www.oracle.com/cloud/free/
   - No credit card charged for Always Free resources

2. **VM Instance Created**
   - Shape: VM.Standard.E2.1.Micro (Always Free)
   - OS: Ubuntu 22.04 LTS
   - Public IP assigned

## 📋 Step-by-Step Deployment

### Step 1: Create Oracle Cloud VM

1. **Sign in** to Oracle Cloud Console: https://cloud.oracle.com
2. Navigate to **Compute** → **Instances**
3. Click **Create Instance**
4. Configure:
   - **Name:** `trading-app-vm`
   - **Image:** Ubuntu 22.04 (Minimal or Server)
   - **Shape:** VM.Standard.E2.1.Micro (Always Free eligible)
   - **Network:** Use default VCN or create new
   - **SSH Keys:** Upload your public key or generate new
5. Click **Create**
6. Wait for instance to provision (~2 minutes)
7. **Note the Public IP address**

### Step 2: Configure Network Security

1. In the VM instance details, click on the **Subnet** name
2. Click on the **Default Security List**
3. Click **Add Ingress Rules**
4. Add rule for HTTP:
   - **Source CIDR:** `0.0.0.0/0`
   - **Destination Port Range:** `80`
   - **Description:** `HTTP`
5. Add rule for HTTPS (optional):
   - **Source CIDR:** `0.0.0.0/0`
   - **Destination Port Range:** `443`
   - **Description:** `HTTPS`

### Step 3: Connect to Your VM

```bash
# Replace with your actual IP and key path
ssh -i ~/.ssh/your-key.pem ubuntu@YOUR_VM_PUBLIC_IP
```

### Step 4: Upload Application Files

**Option A: Using SCP (from your local machine)**
```bash
# From your Mac terminal (not SSH session)
cd /Users/kavinkumar/Mine
tar -czf trading-app.tar.gz Mine/

# Upload to VM
scp -i ~/.ssh/your-key.pem trading-app.tar.gz ubuntu@YOUR_VM_PUBLIC_IP:~

# Upload deployment script
scp -i ~/.ssh/your-key.pem deploy_oracle.sh ubuntu@YOUR_VM_PUBLIC_IP:~
```

**Option B: Using Git**
```bash
# On the VM (SSH session)
cd ~
git clone <your-repo-url> trading-app
```

### Step 5: Extract and Run Deployment Script

```bash
# On the VM (SSH session)
cd ~

# If you used SCP:
tar -xzf trading-app.tar.gz
mkdir -p trading-app
mv Mine trading-app/

# Make deployment script executable
chmod +x deploy_oracle.sh

# Run deployment script
./deploy_oracle.sh
```

The script will:
- ✅ Install Python 3.13
- ✅ Install nginx
- ✅ Set up virtual environment
- ✅ Install dependencies
- ✅ Create systemd service
- ✅ Configure nginx reverse proxy
- ✅ Start the application

### Step 6: Configure Your Credentials

```bash
# Edit the .env file
nano /home/ubuntu/trading-app/Mine/.env
```

Update these values:
```env
API_KEY=your_zerodha_api_key
API_SECRET=your_zerodha_api_secret
ACCESS_TOKEN=your_zerodha_access_token
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
```

Press `Ctrl+X`, then `Y`, then `Enter` to save.

### Step 7: Restart the Application

```bash
sudo systemctl restart trading-app
```

### Step 8: Access Your Application

Open in browser:
```
http://YOUR_VM_PUBLIC_IP
```

---

## 🛠️ Useful Commands

### View Application Logs
```bash
# Real-time logs
sudo journalctl -u trading-app -f

# Last 100 lines
sudo journalctl -u trading-app -n 100
```

### Restart Application
```bash
sudo systemctl restart trading-app
```

### Check Service Status
```bash
sudo systemctl status trading-app
```

### Update Application Code
```bash
cd /home/ubuntu/trading-app/Mine
git pull  # If using Git

# Or upload new files via SCP
# Then restart:
sudo systemctl restart trading-app
```

### Check nginx Status
```bash
sudo systemctl status nginx
sudo nginx -t  # Test configuration
```

### View nginx Logs
```bash
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

---

## 🔒 Security Best Practices

### 1. Set Up Firewall on VM
```bash
# Enable UFW firewall
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

### 2. Change Default SSH Port (Optional)
```bash
sudo nano /etc/ssh/sshd_config
# Change: Port 22 → Port 2222
sudo systemctl restart sshd

# Update Oracle Cloud Security List to allow new port
```

### 3. Set Up SSL/HTTPS with Let's Encrypt (Optional)
```bash
# Install certbot
sudo apt install -y certbot python3-certbot-nginx

# Get SSL certificate (requires domain name)
sudo certbot --nginx -d yourdomain.com
```

### 4. Keep System Updated
```bash
# Run weekly
sudo apt update && sudo apt upgrade -y
```

---

## 📊 Performance Optimization

### Increase Gunicorn Workers
```bash
sudo nano /etc/systemd/system/trading-app.service
# Change: --workers 2 → --workers 4
sudo systemctl daemon-reload
sudo systemctl restart trading-app
```

### Enable Gzip Compression in nginx
```bash
sudo nano /etc/nginx/nginx.conf
# Add in http block:
gzip on;
gzip_types text/plain text/css application/json application/javascript;
gzip_min_length 1000;

sudo systemctl restart nginx
```

---

## 🐛 Troubleshooting

### Application Not Starting
```bash
# Check logs for errors
sudo journalctl -u trading-app -n 50

# Common issues:
# 1. Missing .env file
# 2. Wrong file permissions
# 3. Python package errors
```

### Port 80 Not Accessible
```bash
# 1. Check Oracle Cloud Security List (most common)
# 2. Check VM firewall
sudo iptables -L -n -v

# 3. Check nginx is running
sudo systemctl status nginx
```

### Can't Connect via SSH
```bash
# Check Oracle Cloud Security List has port 22 open
# Verify correct SSH key is being used
# Check VM firewall allows SSH
```

---

## 💰 Cost Estimate

**Oracle Cloud Always Free Tier:**
- ✅ 2 AMD VMs (VM.Standard.E2.1.Micro)
- ✅ 1GB RAM per VM
- ✅ 0.5 OCPU per VM
- ✅ 200GB block storage
- ✅ 10TB outbound data transfer/month

**Your Usage:**
- 1 VM running 24/7: **$0/month** (Forever Free)
- nginx + gunicorn: Minimal resource usage
- **Total Cost: $0 forever** ✅

---

## 🔄 Auto-Restart on Failure

The systemd service is already configured to auto-restart:
```ini
Restart=always
RestartSec=10
```

Your app will automatically restart if it crashes!

---

## 📈 Monitoring (Optional)

### Set Up Basic Monitoring
```bash
# Install monitoring tools
sudo apt install -y htop iotop

# Check resource usage
htop

# Monitor network
sudo iotop
```

### Set Up Email Alerts (Optional)
```bash
# Install mailutils
sudo apt install -y mailutils

# Configure systemd to email on failure
sudo systemctl edit trading-app
# Add:
[Service]
OnFailure=status-email@%n.service
```

---

## 🎉 You're Done!

Your trading application is now:
- ✅ Running 24/7 on Oracle Cloud
- ✅ Accessible via HTTP
- ✅ Auto-restarts on failure
- ✅ Using nginx reverse proxy
- ✅ Optimized for production
- ✅ **Completely FREE forever!**

**Next Steps:**
1. Set up a domain name (optional)
2. Enable HTTPS with Let's Encrypt
3. Set up automated backups
4. Configure monitoring alerts

Need help? Check the logs or open an issue!
