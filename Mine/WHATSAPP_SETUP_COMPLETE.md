# WhatsApp Notifications - Complete Setup Guide (ONE FILE)

## 📱 What This Does

When a **trend changes** in your options chart (BUY → SELL, SIDEWAY → BUY, etc.), your phone automatically receives a **WhatsApp message** with the alert.

Example:
```
📊 Options Chart Alert

🚀 Trend Changed: BUY → SELL (14:30:45)
```

---

## 🎯 What You Need (3 Requirements)

Your app needs **3 pieces of information** to send messages:

### 1️⃣ **WHATSAPP_TOKEN**
- **What it is:** A secret key to authenticate with Facebook's servers
- **Format:** Very long string (100+ characters)
- **Where to get:** Facebook Business Settings → System Users → Generate Token
- **Example:** `EAABsbCS1iHgBAO7ZAaBOZCxJwHn5vpXZAdXd...`

### 2️⃣ **WHATSAPP_PHONE_NUMBER_ID**
- **What it is:** The ID of your WhatsApp Business phone number
- **Format:** Numbers only (12+ digits)
- **Where to get:** Facebook WhatsApp → API Setup → Phone Numbers
- **Example:** `108945832345091`

### 3️⃣ **WHATSAPP_TO_NUMBER**
- **What it is:** Your phone number (recipient)
- **Format:** Country code (91 for India) + 10-digit number
- **Where to get:** Your own phone
- **Example:** `918880802168` (91 = India, 8880802168 = your number)

---

## 📋 STEP-BY-STEP: Get Your Credentials

### STEP 1: Create Facebook Account (5 minutes)

1. Go to [https://www.facebook.com/](https://www.facebook.com/)
2. If you don't have account, click **Create new account**
3. Fill in your name, email, password
4. Verify your email
5. ✅ You now have a Facebook account

---

### STEP 2: Access Facebook Business (2 minutes)

1. Go to [https://business.facebook.com/](https://business.facebook.com/)
2. Log in with your Facebook account
3. You'll see the Business dashboard
4. ✅ You're in the right place

---

### STEP 3: Create a WhatsApp App (5 minutes)

1. In Business dashboard, look for **Apps** or **App Management** (left menu)
2. Click **+ Create App**
3. Fill in:
   - **App Name:** `Trading Options Alerts` (or any name)
   - **App Contact Email:** Your email
   - **App Purpose:** Select `Business Automation`
4. Click **Create App**
5. When asked, select **Business** as app type
6. ✅ Your app is created

---

### STEP 4: Add WhatsApp to Your App (3 minutes)

1. In your app dashboard, find **Products** (left sidebar)
2. Click **+ Add Product**
3. Search for **WhatsApp**
4. Click **Set Up** on the WhatsApp card
5. Follow the setup wizard
6. ✅ WhatsApp is added to your app

---

### STEP 5: Get Your WHATSAPP_TOKEN (5 minutes)

**This is the authentication key.**

1. In Business dashboard, go to **Business Settings** (gear icon)
2. Left menu: Click **Users** → **System Users**
3. Click **+ Create System User**
4. Fill in:
   - **Name:** `Trading Alerts Bot`
   - **Role:** Select `Admin`
5. Click **Create System User**
6. Click on the user you just created
7. Click **Generate Token**
8. In the popup:
   - **App:** Select your WhatsApp app
   - **Token Expires:** Select **Never** (important!)
   - **Permissions:** Make sure `whatsapp_business_messaging` is checked
9. Click **Generate Token**
10. **📋 COPY THE ENTIRE TOKEN** (it's very long)

```
Save it here: _________________________________________
```

✅ You now have WHATSAPP_TOKEN

---

### STEP 6: Get Your WHATSAPP_PHONE_NUMBER_ID (3 minutes)

**This identifies which WhatsApp account sends the message.**

1. Go to **Facebook Developers** → Your App
2. In left menu, click **WhatsApp**
3. Click **API Setup**
4. In left panel, click **Phone Numbers**
5. You'll see your phone number listed
6. Find your business phone number
7. Click on it to expand
8. **📋 COPY THE PHONE NUMBER ID** (just numbers, like: 108945832345091)

```
Save it here: _________________________________________
```

✅ You now have WHATSAPP_PHONE_NUMBER_ID

---

### STEP 7: Verify Your Phone Number (5 minutes)

**Important! WhatsApp needs to verify your phone number before sending messages.**

1. Go to **WhatsApp** → **Manage Phone Number**
2. Find your phone number in the list
3. Click **Verify** next to it
4. WhatsApp will send a **SMS code** to your phone
5. Enter the code in the form
6. Click **Confirm**
7. ✅ Your phone is now verified

---

### STEP 8: Get Your WHATSAPP_TO_NUMBER (1 minute)

**This is just your phone number formatted correctly.**

Your phone number: `8880802168`
Add country code (91 for India): `918880802168`

```
Your WHATSAPP_TO_NUMBER: 918880802168
```

---

## ⚙️ STEP-BY-STEP: Set Up on Your Mac

You now have 3 credentials. Now update your Mac so the app can use them.

### Option 1: Temporary (Just for testing - 5 minutes)

Open Terminal and run these 3 commands (one by one):

```bash
export WHATSAPP_TOKEN='paste_your_token_here'
```

```bash
export WHATSAPP_PHONE_NUMBER_ID='paste_your_phone_id_here'
```

```bash
export WHATSAPP_TO_NUMBER='918880802168'
```

**Verify they're set:**

```bash
echo $WHATSAPP_TOKEN
echo $WHATSAPP_PHONE_NUMBER_ID
echo $WHATSAPP_TO_NUMBER
```

All three should show values (not empty) ✅

---

### Option 2: Permanent (Recommended - 5 minutes)

This way, the variables survive Terminal restarts.

1. Open Terminal and run:
```bash
nano ~/.zshrc
```

2. Go to the very bottom of the file (use arrow keys)

3. Add these 3 lines:
```bash
export WHATSAPP_TOKEN='paste_your_token_here'
export WHATSAPP_PHONE_NUMBER_ID='paste_your_phone_id_here'
export WHATSAPP_TO_NUMBER='918880802168'
```

4. Save:
   - Press: **Ctrl+O**
   - Press: **Enter**
   - Press: **Ctrl+X**

5. Reload the config:
```bash
source ~/.zshrc
```

6. Verify (should show values):
```bash
echo $WHATSAPP_TOKEN
```

✅ Variables are now set permanently

---

## 🧪 STEP-BY-STEP: Test It Works

### Test 1: Check Variables Are Set

```bash
echo "Token: $WHATSAPP_TOKEN"
echo "Phone ID: $WHATSAPP_PHONE_NUMBER_ID"
echo "To Number: $WHATSAPP_TO_NUMBER"
```

**Expected output:** All three should show values (not empty)

If any show empty:
- You're in a different Terminal window
- Run the export commands again in THIS window

### Test 2: Run the Test Script

```bash
python test_notification.py
```

**Expected output:**
```
=== Checking Environment Variables ===

✓ WHATSAPP_TOKEN: EAABsbCS1iH****...
✓ WHATSAPP_PHONE_NUMBER_ID: 108945832345091
✓ WHATSAPP_TO_NUMBER: 918880802168

✓ All environment variables are set!

=== Testing Backend Endpoint ===

Sending POST request to: http://127.0.0.1:5000/api/send-notification
Payload: {...}

Response Status: 200
Response Body: {"success": true, "message": "Notification sent via WhatsApp", "method": "whatsapp"}

✓ Notification sent successfully via whatsapp!
```

### Test 3: Check Your Phone 📱

1. Open WhatsApp on your phone
2. You should see a message from your business number
3. The message should say:
```
📊 Options Chart Alert

Test Message from your app
```

✅ If you received it, everything works!

---

## 🚀 STEP-BY-STEP: Run Your App

Once testing is complete:

```bash
python run.py
```

Your app is now running.

**Go to:** http://127.0.0.1:5000/options-chart

---

## 🔔 What Triggers the Messages?

### Browser Notification ✅

**When:** Immediately when trend changes
**How:** Pop-up appears on your Mac screen
**Example:** "Trend Alert - Trend Changed: BUY → SELL"

### WhatsApp Message ✅

**When:** Immediately when trend changes
**How:** Message sent to 918880802168
**Who triggers it:** Your app detects the trend change and sends the message automatically
**Code location:** `static/js/options_chart_app.js` (line 408)

---

## 📊 How the Messaging Works

### 1. Trend Detection (Frontend)
```
Chart data updates every 5 minutes
    ↓
Trend calculation triggers
    ↓
Compare: previousTrend vs currentTrend
    ↓
If different → Trend changed! ✅
```

### 2. Message Sending (Automatic)
```
Trend change detected
    ↓
Send browser notification (pop-up)
    ↓
Call backend API: /api/send-notification
    ↓
Backend receives request
    ↓
Read credentials from environment:
  ├─ WHATSAPP_TOKEN
  ├─ WHATSAPP_PHONE_NUMBER_ID
  └─ WHATSAPP_TO_NUMBER
    ↓
Create WhatsApp message
    ↓
Send to Facebook API
    ↓
Facebook sends to WhatsApp
    ↓
Message arrives on your phone ✅
```

### 3. Where This Code Is

| File | What It Does | Line |
|------|------------|------|
| `static/js/options_chart_app.js` | Detects trend change, sends notification | 395-411 |
| `app/routes/api.py` | Receives notification request from frontend | 892-962 |
| `service/whatsapp_service.py` | Sends message to WhatsApp API | 40-62 |

---

## 📱 The Message Format

When a trend change happens, you receive:

```
📊 *Options Chart Alert*

🚀 Trend Changed: BUY → SELL (14:30:45)
```

This includes:
- **Emoji** - Visual indicator 📊
- **Alert type** - "Options Chart Alert"
- **Trend change** - From what to what
- **Timestamp** - When it changed

---

## ✅ Complete Verification Checklist

- [ ] Created Facebook account
- [ ] Accessed business.facebook.com
- [ ] Created WhatsApp app
- [ ] Generated WHATSAPP_TOKEN
- [ ] Copied WHATSAPP_PHONE_NUMBER_ID
- [ ] Verified phone number with SMS
- [ ] Set WHATSAPP_TOKEN in Mac environment
- [ ] Set WHATSAPP_PHONE_NUMBER_ID in Mac environment
- [ ] Set WHATSAPP_TO_NUMBER in Mac environment
- [ ] Verified variables with `echo $WHATSAPP_TOKEN`
- [ ] Ran `python test_notification.py` successfully
- [ ] Received test WhatsApp message
- [ ] Saved variables to ~/.zshrc (permanent)
- [ ] Started app with `python run.py`

✅ If all checked, you're ready!

---

## 🆘 Troubleshooting

### Problem: Variables show empty
```bash
echo $WHATSAPP_TOKEN
# Output: (empty)
```
**Solution:** You're in a new Terminal window. Run the export commands again in this window.

### Problem: Test script shows variables NOT SET
**Solution:** Run the export commands first, then run the test script in the SAME terminal window.

### Problem: Test script shows endpoint FAILED
```
Backend Endpoint: ✗ FAILED
```
**Solution:** Make sure Flask app is running
```bash
python run.py
```
Then in a DIFFERENT terminal window, run:
```bash
python test_notification.py
```

### Problem: No WhatsApp message received
**Check:**
1. Is your token valid? Generate a new permanent token if expired
2. Is your phone verified? Go to WhatsApp → Manage Phone Number → Should show "Verified"
3. Is your phone number format correct? Should be: 918880802168 (not +918880802168)
4. Check Flask logs for errors: Look at the output of `python run.py`

---

## 🎯 Summary

### What you did:
1. ✅ Got 3 credentials from Facebook (20 min)
2. ✅ Set them on your Mac (5 min)
3. ✅ Tested everything (2 min)

### What happens now:
- When trend changes → Browser notification appears ✅
- When trend changes → WhatsApp message sent to 918880802168 ✅

### Total time: ~27 minutes
### Difficulty: Easy

---

## 📞 Quick Reference

| Command | What It Does |
|---------|-------------|
| `export WHATSAPP_TOKEN='...'` | Set token temporarily |
| `export WHATSAPP_PHONE_NUMBER_ID='...'` | Set phone ID temporarily |
| `export WHATSAPP_TO_NUMBER='...'` | Set recipient number temporarily |
| `echo $WHATSAPP_TOKEN` | Check if token is set |
| `nano ~/.zshrc` | Edit permanent config |
| `source ~/.zshrc` | Reload config |
| `python test_notification.py` | Test the setup |
| `python run.py` | Start the app |

---

## 🎉 You're Done!

Your notifications are now fully set up and working. Every time a trend changes:
- 🔔 You'll get a browser pop-up
- 📱 You'll get a WhatsApp message

Enjoy your alerts! 🚀
