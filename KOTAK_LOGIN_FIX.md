# Kotak Neo Login - Issues Fixed & Setup Guide

## 🔧 Issues Identified & Fixed

### 1. **Mobile Number Format Issue**
**Problem**: Code was adding "+91" even if already present, causing "+91+91XXXXXXXXXX"
**Fix**: Now strips "+91" or "91" prefix before processing
```python
# BEFORE: Would create "+91+919876543210"
# AFTER: Correctly extracts last 10 digits
if mobile_str.startswith(('+91', '91')):
    mobile_str = mobile_str[-10:] if len(mobile_str) >= 10 else mobile_str
```

### 2. **Insufficient Validation**
**Problem**: Missing validation of mobile number length (10 digits)
**Fix**: Added comprehensive validation for all fields:
- Mobile: Must be exactly 10 digits
- MPIN: Must be exactly 6 digits
- OTP: Must be exactly 6 digits
- UCC: Must be 3-10 characters

### 3. **Poor Error Messages**
**Problem**: Generic error messages like "Login failed" without context
**Fix**: Now provides specific error messages:
- "Step 1: OTP must be 6 digits (got: 4 characters). OTP expires after 30 seconds."
- "Step 2: TRADING_TOKEN missing from response"
- Includes full API responses for debugging

### 4. **Missing Token Validation**
**Problem**: Code didn't verify that tokens were actually received
**Fix**: Now explicitly checks each token after API response:
```python
if not self.view_token or not self.view_sid:
    self.last_error = "Step 1: Token/SID missing from response"
    return False
```

### 5. **OTP Expiration Not Handled**
**Problem**: User had to enter OTP, but code validates it after delays
**Fix**: 
- Clear message: "OTP expires after 30 seconds"
- Suggestion to use fresh OTP from authenticator

### 6. **UCC Validation**
**Problem**: Accepted any value for UCC (should be 3-10 chars)
**Fix**: Added validation and converts to uppercase (standard format)

### 7. **Missing Connectivity Check**
**Problem**: No way to know if Kotak API is reachable
**Fix**: Added `diagnose_authentication()` method that tests connectivity

---

## ✅ Authentication Flow (Corrected)

### Step 1: TOTP Login
```
Request: https://mis.kotaksecurities.com/login/1.0/tradeApiLogin
Headers:
  - Authorization: <API_KEY>
  - neo-fin-key: neotradeapi
  - Content-Type: application/json

Body:
  {
    "mobileNumber": "+919876543210",  // Full number with +91
    "ucc": "XV5PK",                   // 5 char Unique Client Code (uppercase)
    "totp": "123456"                  // 6-digit OTP from authenticator
  }

Response (success):
  {
    "success": true,
    "data": {
      "token": "VIEW_TOKEN_VALUE",
      "sid": "VIEW_SID_VALUE"
    }
  }
```

### Step 2: MPIN Validation
```
Request: https://mis.kotaksecurities.com/login/1.0/tradeApiValidate
Headers:
  - Authorization: <API_KEY>
  - neo-fin-key: neotradeapi
  - sid: <VIEW_SID>              // From Step 1
  - Auth: <VIEW_TOKEN>            // From Step 1
  - Content-Type: application/json

Body:
  {
    "mpin": "654321"  // 6-digit MPIN from trading PIN
  }

Response (success):
  {
    "success": true,
    "data": {
      "token": "TRADING_TOKEN_VALUE",
      "sid": "TRADING_SID_VALUE",
      "baseUrl": "https://..."
    }
  }
```

---

## 🔍 How to Diagnose Issues

### Method 1: Use Built-in Diagnostic
```python
from trading_app.service.kotak_order_services import KotakOrderService

kotak = KotakOrderService()
diagnostics = kotak.diagnose_authentication()
print(diagnostics)
# Shows:
# - Which credentials are missing
# - Format validation errors
# - API connectivity status
```

### Method 2: Check Logs
Logs now show detailed step-by-step output:
```
[authenticate] Step 1: TOTP Login...
[authenticate] Login status code: 200
[authenticate] ✓ Step 1 successful
[authenticate] Step 2: MPIN Validate...
[authenticate] ✓ Step 2 successful!
[authenticate] ✅ Authentication SUCCESSFUL!
```

### Common Error Messages & Solutions:

| Error | Cause | Solution |
|-------|-------|----------|
| "OTP must be 6 digits (got: 3 characters)" | Invalid OTP | Get fresh 6-digit code from authenticator app (expires every 30 sec) |
| "MPIN must be 6 digits (got: 5 characters)" | Wrong MPIN length | Check .env file has exactly 6 digits |
| "Mobile must be 10 digits" | Wrong phone number | Remove +91 from .env, just use 10 digits |
| "UCC must be 3-10 characters" | Invalid UCC | Get correct Unique Client Code from Kotak account |
| "Step 1: Token/SID missing from response" | API issue | Try again with valid credentials |
| "Step 2: TRADING_TOKEN missing from response" | MPIN failed | Verify MPIN is correct, try again |
| "Step 1: Request timeout" | Network issue | Check internet connection |
| "Cannot reach Kotak API" | Firewall/Network blocked | Check firewall or try from different network |

---

## 📝 .env Setup (Correct Format)

```env
# Kotak Neo Credentials
KOTAK_ACCESS_TOKEN=your_api_key_from_kotak_dashboard
KOTAK_MOBILE_NUMBER=9876543210          # 10 digits, NO +91
KOTAK_UCC=XV5PK                         # Unique Client Code (5 chars, case-insensitive)
KOTAK_MPIN=654321                       # 6-digit trading PIN
KOTAK_TOTP_SECRET=123456                # 6-digit OTP (fresh from authenticator)

# Auto-saved after successful login:
KOTAK_TRADING_TOKEN=...
KOTAK_TRADING_SID=...
KOTAK_BASE_URL=...
```

**Important:**
- ❌ Don't use: `KOTAK_MOBILE_NUMBER=+919876543210`
- ✅ Use: `KOTAK_MOBILE_NUMBER=9876543210`
- ❌ OTP expires in 30 seconds - get fresh one from authenticator each time
- ✅ MPIN doesn't expire - use same one each time

---

## 🚀 Testing Authentication

### From Python Console:
```python
from trading_app.service.kotak_order_services import KotakOrderService

# Method 1: With credentials in .env
kotak = KotakOrderService()
success = kotak.authenticate()
if success:
    print("✅ Authenticated!")
    print(f"Base URL: {kotak.base_url}")
else:
    print(f"❌ Failed: {kotak.last_error}")

# Method 2: With inline credentials
kotak = KotakOrderService(
    access_token="your_api_key",
    mobile_number="9876543210",
    ucc="XV5PK",
    mpin="654321",
    totp_secret="123456"
)
success = kotak.authenticate()
```

### From Web Interface:
```
POST http://localhost:5000/authenticate_kotak
{
  "access_token": "your_api_key",
  "mobile": "9876543210",
  "ucc": "XV5PK",
  "mpin": "654321",
  "totp_code": "123456"
}
```

---

## 🔐 Security Notes

1. **Never log OTP**: The code masks OTP in logs as "123456" → "12****"
2. **Never log MPIN**: The code masks MPIN in logs as "654321" → "******"
3. **OTP is one-time**: Each OTP can only be used once, valid for ~30 seconds
4. **MPIN is persistent**: Same MPIN used every login
5. **Tokens are session-based**: Tokens saved to .env can be reused until expired

---

## 📚 Getting Kotak Credentials

### 1. Access Token (API Key)
- Go to: Kotak Neo Dashboard → Settings → API Keys
- Copy the "Trade API Key"
- Paste in .env as `KOTAK_ACCESS_TOKEN`

### 2. Mobile Number
- Use your registered mobile number with Kotak
- Remove any +91 prefix
- Should be exactly 10 digits

### 3. UCC (Unique Client Code)
- Found in: Kotak Neo Dashboard → Account → Client Details
- Usually 5 characters like "XV5PK"

### 4. MPIN (Trading PIN)
- Set during Kotak account setup
- 6-digit number (like your demat PIN)
- Different from login password

### 5. TOTP (One-Time Password)
- Available from: Google Authenticator / Authy app
- New code every 30 seconds
- Get fresh code each time you authenticate

---

## 🐛 Debug Mode

Enable detailed logging:
```python
import logging
logging.basicConfig(level=logging.DEBUG)

kotak = KotakOrderService(...)
kotak.authenticate()
# Now shows all request/response details
```

---

## ✨ Changes Summary

| Component | Before | After |
|-----------|--------|-------|
| Mobile format handling | Buggy (double +91) | ✅ Fixed |
| Validation | Minimal | ✅ Comprehensive |
| Error messages | Generic | ✅ Specific with solutions |
| Token verification | Missing | ✅ Explicit checks |
| Diagnostics | None | ✅ Built-in diagnostic method |
| Exception handling | Basic | ✅ Detailed with traces |
| Logging | Minimal | ✅ Step-by-step traces |
| Timeout handling | Generic | ✅ Custom timeout messages |
| Connection errors | Generic | ✅ Specific with suggestions |

