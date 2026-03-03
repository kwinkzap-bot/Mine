# 🚀 Quick Reference - Oracle Cloud Deployment

## Instance Details
- **IP Address**: 140.245.204.188
- **SSH Key**: `/Users/kavinkumar/Downloads/ssh-key-2026-01-27.key`
- **OS**: Ubuntu 24.04.4 LTS
- **Python**: 3.12.3

## One-Liner Commands

### SSH In
```bash
ssh -i /Users/kavinkumar/Downloads/ssh-key-2026-01-27.key ubuntu@140.245.204.188
```

### View Real-Time Logs
```bash
sudo journalctl -u trading-app -f
```

### Restart App
```bash
sudo systemctl restart trading-app
```

### Check Status
```bash
sudo systemctl status trading-app --no-pager
```

### View All Running Services
```bash
sudo systemctl status trading-app trading-scheduler nginx --no-pager
```

## Service Status

| Service | Port | Status | Memory |
|---------|------|--------|--------|
| **Flask+Gunicorn** | 8000 | ✅ RUNNING | 122 MB |
| **Nginx** | 80/443 | ✅ RUNNING | 2.4 MB |
| **Scheduler** | — | 🔄 Integrated | Included |

## Important Paths

- **App Root**: `/home/trading/trading_app/`
- **Source Code**: `/home/trading/trading_app/src/`
- **Environment Files**: `/home/trading/trading_app/env/`
- **Python Executable**: `/home/trading/trading_app/venv/bin/python3`
- **WSGI Entry**: `/home/trading/trading_app/wsgi.py`
- **Nginx Config**: `/etc/nginx/sites-enabled/trading-app`
- **Service Files**: `/etc/systemd/system/trading-*.service`

## Critical Next Step ⚠️

**Update Oracle Cloud Network Security Group:**
1. Log into Oracle Cloud Console
2. Go to Compute → Instances
3. Find "mine-trading-app"
4. Click VCN
5. Add Ingress Rule (Port 80, 443, Source 0.0.0.0/0)

**After this**, app will be accessible at: `http://140.245.204.188`

## Restart Procedure

```bash
# Full restart sequence:
ssh -i /Users/kavinkumar/Downloads/ssh-key-2026-01-27.key ubuntu@140.245.204.188

# Then run these:
sudo systemctl restart trading-app
sudo systemctl restart nginx
sudo systemctl status trading-app --no-pager
```

## Emergency Troubleshooting

### App won't start?
```bash
sudo journalctl -u trading-app -n 50 --no-pager
```

### Check ports
```bash
sudo ss -tulpn | grep -E '8000|:80'
```

### Restart from scratch
```bash
sudo systemctl stop trading-app
sudo systemctl start trading-app
```

### View error logs
```bash
tail -100 /var/log/syslog | grep gunicorn
```

## Testing

### From instance (always works)
```bash
curl http://localhost:8000/
curl http://localhost/  # via Nginx
```

### From Mac (after NSG fix)
```bash
curl http://140.245.204.188/
```

## Cost: $0/month ✅

All 4 CPU cores, 24GB RAM, 10GB storage = **Forever Free**

---

## Git Repository
```bash
cd /Users/kavinkumar/Mine
git status
git log --oneline | head -5
```

Recent commits:
- `6134ce0` - Complete deployment summary
- `15d5592` - Deployment status report
- `15ee09d` - Add Python dependencies

---

*Last Updated: March 3, 2026 - ALL SYSTEMS OPERATIONAL ✅*
