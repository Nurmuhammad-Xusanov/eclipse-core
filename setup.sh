#!/usr/bin/env bash
set -e

echo "🚀 Project setup starting..."

# 1. Python check
if ! command -v python3 &> /dev/null; then
  echo "❌ Python3 yo‘q. Oldin o‘rnat."
  exit 1
fi

# 2. venv yaratish
if [ ! -d "venv" ]; then
  python3 -m venv venv
  echo "✅ venv created"
fi

# 3. activate
source venv/bin/activate

# 4. pip upgrade
pip install --upgrade pip

# 5. deps install
pip install -r requirements.txt

# 6. .env check
if [ ! -f ".env" ]; then
  echo "⚠️ .env yo‘q"
  cp .env.example .env
  echo "👉 .env created from .env.example (edit it!)"
fi

echo "🔥 Setup done. Run:"
echo "source venv/bin/activate"
echo "python bot.py"
echo "NOTE: Make sure to add your cookies to cookies.txt for proper functionality."
