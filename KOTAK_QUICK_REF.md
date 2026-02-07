# ⚡ Kotak Neo Login - Quick Reference Card

## 🚀 Quick Start (30 seconds)

```bash
# 1. Check credentials
python test_kotak_auth.py

# 2. Update .env if needed (see below)

# 3. Try again
python test_kotak_auth.py

# 4. Success! ✅
```

---

## 📋 .env Checklist

```env
✓ KOTAK_MOBILE_NUMBER=9876543210       # 10 digits, NO +91
✓ KOTAK_UCC=XV5PK                      # 5 chars, uppercase
✓ KOTAK_MPIN=654321                    # 6 digits
✓ KOTAK_TOTP_SECRET=123456             # Fresh OTP (30 sec)
✓ KOTAK_ACCESS_TOKEN=your_api_key      # From Kotak Dashboard
```

**Critical**: OTP expires in 30 seconds. Get fresh one from authenticator app!

---

## ❌ Common Errors → ✅ Solutions

| Error | Fix |
|-------|-----|
| `OTP must be 6 digits (got: 3)` | Get fresh 6-digit OTP NOW |
| `MPIN must be 6 digits (got: 5)` | Check .env, exactly 6 digits |
| `Mobile must be 10 digits` | Remove +91, use: 9876543210 |
| `UCC must be 3-10 characters` | Get from Kotak account details |
| `Token missing from response` | Try again, use valid credentials |
| `Cannot reach Kotak API` | Check internet/firewall |
| `Request timeout` | Try again after 30 seconds |

---

## 🔧 Debugging

```python
from trading_app.service.kotak_order_services import KotakOrderService

# Test 1: Check credentials
kotak = KotakOrderService()
kotak.diagnose_authentication()

# Test 2: Try to authenticate
success = kotak.authenticate()
if not success:
    print(kotak.last_error)  # Shows what went wrong
```

---

## 📚 Full Guides

- **Setup Guide**: [KOTAK_LOGIN_FIX.md](KOTAK_LOGIN_FIX.md)
- **Detailed Summary**: [KOTAK_FIX_SUMMARY.md](KOTAK_FIX_SUMMARY.md)
- **Diagnostic Tool**: `python test_kotak_auth.py`

---

## ✨ What Was Fixed

- ✅ Mobile number format bug (+91 added twice)
- ✅ Weak input validation
- ✅ Vague error messages  
- ✅ Missing token verification
- ✅ No OTP expiration warning
- ✅ No diagnostic tools

---

## 🎯 Success = All True

```python
kotak = KotakOrderService()
kotak.authenticate()  # → True

# These should be filled:
kotak.trading_token   # → "eyJ0eXAi..."
kotak.trading_sid     # → "2024020112345"
kotak.base_url        # → "https://api..."
```

---

## 🆘 Still Not Working?

1. Run: `python test_kotak_auth.py`
2. Read error message carefully
3. Check [KOTAK_LOGIN_FIX.md](KOTAK_LOGIN_FIX.md) "Common Issues" section
4. Fix the issue
5. Run test again

---

## 📞 Key Points

| Item | Value | Format |
|------|-------|--------|
| Mobile | Your phone | 10 digits, no +91 |
| MPIN | Trading PIN | 6 digits |
| OTP | Authenticator | 6 digits, expires 30 sec |
| UCC | Client code | 5 chars, from Kotak |
| API Key | From Dashboard | Long string |

---

**Last Updated**: Feb 7, 2026 | **Status**: ✅ Fixed & Tested
