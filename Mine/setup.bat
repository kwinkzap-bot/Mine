@echo off
REM Quick Start Script for Trading Options Strategy Platform (Windows)

echo.
echo ==========================================
echo Trading Options Strategy - Quick Start
echo ==========================================
echo.

REM Check Python version
echo Checking Python version...
python --version
if %errorlevel% neq 0 (
    echo Error: Python not found. Please install Python 3.8+
    pause
    exit /b 1
)

REM Create virtual environment
if not exist "venv" (
    echo.
    echo Creating virtual environment...
    python -m venv venv
    echo Virtual environment created
)

REM Activate virtual environment
echo.
echo Activating virtual environment...
call venv\Scripts\activate.bat
echo Virtual environment activated

REM Install dependencies
echo.
echo Installing dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo Error: Failed to install dependencies
    pause
    exit /b 1
)
echo Dependencies installed

REM Check .env file
echo.
echo Checking .env file...
if not exist ".env" (
    echo Creating .env template...
    (
        echo # Flask Configuration
        echo FLASK_ENV=development
        echo FLASK_HOST=127.0.0.1
        echo FLASK_PORT=5000
        echo.
        echo # Zerodha API
        echo API_KEY=your_api_key_here
        echo ACCESS_TOKEN=your_access_token_here
        echo.
        echo # Security
        echo SECRET_KEY=dev-key-change-in-production
        echo.
        echo # Logging
        echo LOG_LEVEL=INFO
    ) > .env
    echo .env template created. Please update with your credentials.
) else (
    echo .env file found
)

REM Run syntax check
echo.
echo Running syntax validation...
python -m py_compile app\__init__.py app\config.py app\extensions.py
if %errorlevel% neq 0 (
    echo Syntax validation failed
    pause
    exit /b 1
)
echo Syntax validation passed

REM Display completion
echo.
echo ==========================================
echo Setup Complete!
echo ==========================================
echo.
echo Next steps:
echo 1. Update .env with your Zerodha credentials
echo 2. Run: python run.py
echo 3. Visit: http://127.0.0.1:5000
echo.
echo Documentation:
echo - STRUCTURE.md - New folder structure
echo - MIGRATION_GUIDE.md - Migration from old structure
echo - OPTIMIZATION_SUMMARY.md - What was optimized
echo.
pause
