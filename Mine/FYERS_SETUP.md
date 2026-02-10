# Fyers Configuration Guide

## Error: FYERS_APP_ID or FYERS_SECRET_KEY not configured

This error occurs when you try to login with Fyers but the credentials are not configured in your user environment file.

## Solution: Add Fyers Credentials

### Step 1: Get Your Fyers Credentials

1. Go to **Fyers Dashboard**: https://myapi.fyers.in/dashboard/
2. Log in with your Fyers account
3. Click **"Create APP"** or select existing app
4. You'll see:
   - **APP ID**: Format like `XXXXXXXXX-100` (e.g., `8IVVH42PV6-100`)
   - **APP SECRET**: A string of characters (e.g., `90QQ7IQKG3`)
5. Copy both values

### Step 2: Add to Your User Environment File

**File Location**: `Mine/env/YOUR_USERNAME.env`

Example: If your username is `Kavin`, edit `Mine/env/Kavin.env`

Find the section:
```dotenv
# ============================================
# FYERS TRADING CREDENTIALS
# ============================================

FYERS_APP_ID=
FYERS_SECRET_KEY=
```

Update it with your credentials:
```dotenv
# ============================================
# FYERS TRADING CREDENTIALS
# ============================================

FYERS_APP_ID=8IVVH42PV6-100
FYERS_SECRET_KEY=90QQ7IQKG3
```

**Important:**
- Replace `8IVVH42PV6-100` with your actual APP ID
- Replace `90QQ7IQKG3` with your actual APP SECRET
- Keep these values confidential
- Do not share these in version control

### Step 3: Restart the Application

After updating the credentials, restart your Flask application:

```bash
cd /Users/kavinkumar/Mine
python Mine/main.py
```

Or if using the VS Code task:
- Press `Ctrl+Shift+B` to run the build task

### Step 4: Test Fyers Login

1. Navigate to: http://127.0.0.1:5000
2. Go to **Settings** → **Brokers** → **Fyers**
3. Click **"Login with Fyers"**
4. You should be redirected to Fyers OAuth page
5. Authorize the application
6. You'll be redirected back with `FYERS_ACCESS_TOKEN` saved

## Troubleshooting

### Issue: Still getting "FYERS_APP_ID not configured" error

**Solution:**
1. Check you edited the correct file: `Mine/env/YOUR_USERNAME.env`
2. Verify the env file path by checking the app logs
3. Ensure values are not empty (no spaces after `=`)
4. Restart the application

### Issue: "Invalid FYERS_APP_ID or FYERS_SECRET_KEY"

**Solution:**
1. Double-check credentials from Fyers Dashboard
2. Ensure you copied the entire string (including hyphens for APP_ID)
3. If secret key shows as masked in Fyers dashboard, copy it again
4. Try creating a new APP in Fyers Dashboard

### Issue: Fyers login redirects to error page

**Solution:**
1. Verify `FYERS_REDIRECT_URI` matches your server URL
   - Local: `http://127.0.0.1:5000/auth/login/fyers/callback`
   - Production: Update in Fyers Dashboard
2. Check that redirect URI is set in Fyers Dashboard → App Settings
3. Verify port 5000 is accessible

## Verifying Configuration

Check if credentials are properly loaded by looking at the application logs:

```
[Fyers Login] Login request received
[Fyers Login] Redirecting to Fyers OAuth: https://api.fyers.in/authorize?...
```

If you see these messages, credentials are loaded correctly.

## Security Best Practices

1. **Never commit credentials** to version control
2. **Use separate APP credentials** for development vs. production
3. **Rotate APP secrets** periodically from Fyers Dashboard
4. **Store credentials securely** - treat like passwords
5. **Use environment-specific files** (e.g., Kavin.env for testing, Mine.env for production)

## Related Files

- **User Environment File**: `Mine/env/Kavin.env` (or your username)
- **Auth Configuration**: `src/trading_app/app/routes/auth.py` (line 749)
- **Fyers Service**: `src/trading_app/service/fyers_order_services.py`
- **API Configuration**: `src/trading_app/app/routes/api.py` (line 257)

## Fyers Resources

- **Official API Docs**: https://fyers.in/api-docs/
- **Dashboard**: https://myapi.fyers.in/dashboard/
- **Support**: https://support.fyers.in/

---

**Need help?** Check the app logs for detailed error messages or contact Fyers support.
