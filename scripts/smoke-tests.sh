#!/bin/bash

# Smoke test checklist for integrated docker-compose stack
# Validates that frontend, backend, and end-to-end chat flow work correctly

set -euo pipefail

FRONTEND_URL="http://localhost"
BACKEND_URL="http://localhost:8000"
CURL_TIMEOUT=10

echo "🧪 Starting smoke tests for integrated stack..."
echo ""

# Test 1: Frontend reachability
echo "1. ✓ Testing frontend reachability on port 80..."
if curl -s --max-time $CURL_TIMEOUT -o /dev/null -w "%{http_code}" "$FRONTEND_URL/" | grep -q "200"; then
  echo "   ✓ Frontend is reachable (HTTP 200)"
else
  echo "   ✗ Frontend not responding (expected HTTP 200)"
  exit 1
fi
echo ""

# Test 2: Backend health endpoint
echo "2. ✓ Testing backend /api/health endpoint..."
HEALTH_RESPONSE=$(curl -s --max-time $CURL_TIMEOUT "$BACKEND_URL/api/health")
if echo "$HEALTH_RESPONSE" | grep -q "status.*ok"; then
  echo "   ✓ Backend health check passed"
  echo "   Response: $HEALTH_RESPONSE"
else
  echo "   ✗ Backend health check failed"
  echo "   Response: $HEALTH_RESPONSE"
  exit 1
fi
echo ""

# Test 3: Chat endpoint reachability (no actual AI call, just endpoint check)
echo "3. ✓ Testing backend /api/chat endpoint..."
if curl -s --max-time $CURL_TIMEOUT -X OPTIONS "$BACKEND_URL/api/chat" -o /dev/null -w "%{http_code}" | grep -q "204\|200"; then
  echo "   ✓ Chat endpoint is reachable"
else
  echo "   ✗ Chat endpoint not responding"
  exit 1
fi
echo ""

# Test 4: Frontend can reach backend (test if frontend can make requests)
echo "4. ✓ Testing CORS and cross-origin request from frontend context..."
CORS_HEADERS=$(curl -s --max-time $CURL_TIMEOUT -X OPTIONS "$BACKEND_URL/api/chat" \
  -H "Origin: http://localhost" \
  -H "Access-Control-Request-Method: POST" \
  -D - -o /dev/null)
if echo "$CORS_HEADERS" | grep -qi "^access-control-allow-origin: http://localhost"; then
  echo "   ✓ CORS preflight test passed"
else
  echo "   ✗ CORS preflight test failed (HTTP $CORS_TEST)"
  exit 1
fi
echo ""

echo "════════════════════════════════════════════"
echo "✓ All smoke tests passed!"
echo "════════════════════════════════════════════"
echo ""
echo "🚀 Stack is ready for manual testing:"
echo "   - Frontend: $FRONTEND_URL"
echo "   - Backend:  $BACKEND_URL"
echo "   - Try sending a chat message through the frontend UI"
echo ""
