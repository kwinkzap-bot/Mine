#!/bin/bash
# Diagnostic script to test Flask app in both debug and normal modes

echo "═══════════════════════════════════════════════════════════"
echo "Flask App 403 Error Diagnostic Test"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Function to test endpoints
test_endpoints() {
    local mode=$1
    echo "Testing in $mode mode..."
    echo ""
    
    # Give app time to start
    sleep 3
    
    echo "1️⃣ Testing home page (/) :"
    curl -s -I http://127.0.0.1:5000/ | head -1
    echo ""
    
    echo "2️⃣ Testing debug status endpoint (/debug/status) :"
    curl -s http://127.0.0.1:5000/debug/status | python3 -m json.tool 2>/dev/null || echo "Could not parse JSON"
    echo ""
    
    echo "3️⃣ Testing options-chart page (/options-chart) :"
    curl -s -I http://127.0.0.1:5000/options-chart | head -1
    echo ""
}

# Test in Debug Mode
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║ MODE 1: DEBUG MODE (with auto-reload)                    ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "Starting Flask in DEBUG mode..."
echo "Command: FLASK_ENV=development python3 main.py"
echo ""
echo "⏱️ Press Ctrl+C to stop the server and move to next test"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

cd /Users/kavinkumar/Mine/Mine
FLASK_ENV=development python3 main.py &
APP_PID=$!
sleep 5

test_endpoints "DEBUG"

kill $APP_PID 2>/dev/null || true
sleep 2

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║ MODE 2: NORMAL MODE (production-like)                    ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "Starting Flask in NORMAL mode..."
echo "Command: FLASK_ENV=production python3 main.py"
echo ""
echo "⏱️ Press Ctrl+C to stop the server"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

FLASK_ENV=production python3 main.py &
APP_PID=$!
sleep 5

test_endpoints "NORMAL"

kill $APP_PID 2>/dev/null || true

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║ TEST COMPLETE                                             ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "📊 Results Summary:"
echo ""
echo "If both modes show:"
echo "  ✅ 200 OK for home page - No 403 issue"
echo "  ❌ 403 Forbidden - Issue is in app code (not just debug)"
echo ""
echo "If only DEBUG mode shows 403:"
echo "  ⚠️  Possible issue with debug middleware or reloader"
echo ""
echo "If only NORMAL mode shows 403:"
echo "  ⚠️  Possible issue with production config"
echo ""
