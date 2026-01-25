# 🔑 How to Get Kotak Neo ACCESS_TOKEN

## ✅ You Were Right!

Consumer Key and Consumer Secret are **NOT needed** for direct REST API authentication.

You only need **1 thing**: **ACCESS_TOKEN** from Kotak Neo API Dashboard

---

## 📍 Step 1: Find ACCESS_TOKEN in Kotak Neo

### Option A: Kotak Neo Web Portal

1. Login to https://neo.kotak.com
2. Navigate to **API Dashboard** or **Developer Settings**
3. Look for section called **"Access Token"** or **"API Token"**
4. Copy the token (looks like: `Bearer abc123xyz...` or just `abc123xyz...`)

### Option B: Kotak Neo Mobile App

1. Open Kotak Neo app
2. Profile → Settings → API Settings (or Developer Options)
3. Look for **"Access Token"** field
4. Copy the full token string

### Option C: Request from Support

If you can't find it:

**Call**: 1800 209 9191 (toll-free, 8 AM - 8 PM)

**Script**:
```
"Hi, I need my ACCESS_TOKEN for Kotak Neo Trade API.
I'm building a trading application and need the permanent access token.
My Client Code is XV5PK."
```

They'll either:
- Tell you where to find it in the app/portal
- Email it to your registered email
- Generate a new one for you

---

## 📝 Step 2: Update .env File

Replace this line in your `.env`:

```env
KOTAK_ACCESS_TOKEN=REPLACE_WITH_YOUR_ACCESS_TOKEN_FROM_KOTAK_NEO_API_DASHBOARD
```

With your actual token:

```env
KOTAK_ACCESS_TOKEN=eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...
```

(or whatever format they provide - could be with or without "Bearer" prefix)

---

## ✅ What You Need (Updated List)

### Required in .env:

1. ✅ **KOTAK_ACCESS_TOKEN** ← Get from API Dashboard
2. ✅ **KOTAK_UCC** ← XV5PK (you have this)
3. ✅ **KOTAK_MOBILE_NUMBER** ← 8880802168 (you have this)
4. ✅ **KOTAK_MPIN** ← 491988 (you have this)

### At Login Time:

5. ✅ **6-digit TOTP** ← From authenticator app (Google/Microsoft Authenticator)

---

## 🔄 Authentication Flow (Simplified)

Your app now uses the **official REST API** approach:

```
Step 1: TOTP Login
POST https://mis.kotaksecurities.com/login/1.0/tradeApiLogin
Headers: {
  "Authorization": "YOUR_ACCESS_TOKEN",
  "neo-fin-key": "neotradeapi"
}
Body: {
  "mobileNumber": "+918880802168",
  "ucc": "XV5PK",
  "totp": "123456"  ← From authenticator app
}
→ Response: { "data": { "token": "VIEW_TOKEN", "sid": "VIEW_SID" }}

Step 2: MPIN Validate  
POST https://mis.kotaksecurities.com/login/1.0/tradeApiValidate
Headers: {
  "Authorization": "YOUR_ACCESS_TOKEN",
  "neo-fin-key": "neotradeapi",
  "sid": "VIEW_SID",
  "Auth": "VIEW_TOKEN"
}
Body: {
  "mpin": "491988"  ← Your 6-digit trading PIN
}
→ Response: {
  "data": {
    "token": "TRADING_TOKEN",  ← Use this for all API calls
    "sid": "TRADING_SID",
    "baseUrl": "https://cis.kotaksecurities.com"
  }
}
```

**No consumer_key or consumer_secret required!** 🎉

---

## 🧪 Testing

Once you add ACCESS_TOKEN to `.env`:

```bash
cd /Users/kavinkumar/Mine/Mine
python main.py
```

Then:
1. Open http://localhost:5000
2. Click "Login to Kotak Neo"
3. Enter current 6-digit code from authenticator app
4. Should see success! ✅

---

## 📋 What Changed

### Before (neo_api_client Python library):
- ❌ Needed: consumer_key, consumer_secret
- ❌ Library wrapper added complexity
- ❌ Error: "can only concatenate str (not 'NoneType')"

### After (Direct REST API):
- ✅ Only need: ACCESS_TOKEN
- ✅ Direct HTTP calls to Kotak endpoints
- ✅ Follows official Notion documentation exactly
- ✅ Simpler, cleaner, official approach

---

## 📞 Support

If you need help getting ACCESS_TOKEN:

- **Email**: support@kotaksecurities.com
- **Phone**: 1800 209 9191 (toll-free)
- **Hours**: 8:00 AM - 8:00 PM IST

Mention you need "ACCESS_TOKEN for Kotak Neo Trade API" and provide your Client Code (XV5PK).

---

## 🎯 Expected Console Output

After updating ACCESS_TOKEN:

```
[authenticate] =================================
[authenticate] Starting Kotak Neo authentication
[authenticate] =================================
[authenticate] Access Token: eyJhbGciOiJSUzI1Ni...
[authenticate] Mobile: +918880802168
[authenticate] UCC: XV5PK
[authenticate] MPIN: ******
[authenticate] TOTP: 123456
[authenticate] Step 1: TOTP Login...
[authenticate] Login status code: 200
[authenticate] ✓ TOTP Login successful
[authenticate] VIEW_TOKEN: eyJhbGciOiJSUzI1Ni...
[authenticate] VIEW_SID: abc-123-def-456
[authenticate] Step 2: MPIN Validate...
[authenticate] Validate status code: 200
[authenticate] ✓ MPIN Validation successful!
[authenticate] TRADING_TOKEN: eyJhbGciOiJSUzI1Ni...
[authenticate] TRADING_SID: xyz-789-uvw-012
[authenticate] BASE_URL: https://cis.kotaksecurities.com
[authenticate] =================================
[authenticate] Authentication completed successfully
[authenticate] =================================
```

Success! 🚀
