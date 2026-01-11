#!/bin/bash
# Quick commands to debug HTTP 403 errors

# Check if your token is valid
echo "🔍 Checking token status..."
curl http://127.0.0.1:5000/api/debug/token-status

echo ""
echo ""

# Expected output if token is valid:
# {
#   "success": true,
#   "token_status": "VALID",
#   "user_name": "Your Name",
#   "broker": "Zerodha"
# }

echo "✅ If you see token_status: 'VALID' above, your token is good"
echo "❌ If you see token_status: 'EXPIRED_OR_INVALID', go to: http://127.0.0.1:5000/auth/login"
echo ""
echo "🔗 Login URL: http://127.0.0.1:5000/auth/login"
