Write-Host "🚀 Project setup starting..."

# 1. Python check
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "❌ Python topilmadi. Python 3.13 o‘rnat."
    exit 1
}

$PY_VERSION = python - <<EOF
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
EOF

if ($PY_VERSION -ne "3.13") {
    Write-Host "❌ Python 3.13 kerak. Hozirgi: $PY_VERSION"
    exit 1
}

Write-Host "✅ Python $PY_VERSION OK"

# 2. venv yaratish
if (-not (Test-Path "venv")) {
    python -m venv venv
    Write-Host "✅ venv created"
}

# 3. activate
& .\venv\Scripts\Activate.ps1

# 4. pip upgrade
pip install --upgrade pip

# 5. deps install
pip install -r requirements.txt

# 6. .env check
if (-not (Test-Path ".env")) {
    Write-Host "⚠️ .env topilmadi"
    Copy-Item .env.example .env
    Write-Host "👉 .env .env.example dan yaratildi (edit qil!)"
}

Write-Host "🔥 Setup done."
Write-Host "Run:"
Write-Host "  .\venv\Scripts\activate"
Write-Host "  python bot.py"
