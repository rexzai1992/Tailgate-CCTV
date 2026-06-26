@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "APP_NAME=CCTV Tailgate"
set "REQUESTED_PORT=%~1"
if "%REQUESTED_PORT%"=="" set "REQUESTED_PORT=8080"
set "PORT=%REQUESTED_PORT%"

echo.
echo %APP_NAME% - Windows first-time installer and launcher
echo Project: %CD%
echo.

if not exist "requirements.txt" (
    echo requirements.txt was not found.
    echo Put this .bat file in the project folder, then run it again.
    pause
    exit /b 1
)

if not exist "src\main.py" (
    echo src\main.py was not found.
    echo This does not look like the CCTV Tailgate project folder.
    pause
    exit /b 1
)

call :PickPort
if not "%PORT%"=="%REQUESTED_PORT%" (
    echo Port %REQUESTED_PORT% is busy. Using port %PORT% instead.
    echo.
)

call :FindPython
if not defined SYSTEM_PY (
    call :InstallPython
)

if not defined SYSTEM_PY (
    echo.
    echo Could not install or find Python 3.11+ automatically.
    echo Install Python 3.11 or 3.12 from https://www.python.org/downloads/windows/
    echo Make sure "Add python.exe to PATH" is checked, then run this file again.
    pause
    exit /b 1
)

echo Using Python: %SYSTEM_PY%
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment in .venv...
    "%SYSTEM_PY%" -m venv .venv
    if errorlevel 1 (
        echo.
        echo Failed to create .venv.
        pause
        exit /b 1
    )
)

set "PYTHON=.venv\Scripts\python.exe"

echo Making sure pip is available...
"%PYTHON%" -m ensurepip --upgrade >nul 2>nul

echo Upgrading installer tools...
"%PYTHON%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo.
    echo Failed to upgrade pip/setuptools/wheel.
    pause
    exit /b 1
)

echo Installing app requirements. This can take several minutes on a fresh PC...
"%PYTHON%" -m pip install --prefer-binary -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install requirements.
    echo Check the error above, then run this file again.
    pause
    exit /b 1
)

if not exist "config.yaml" (
    if not exist "config.example.yaml" (
        echo config.example.yaml was not found, so config.yaml cannot be created.
        pause
        exit /b 1
    )
    echo Creating config.yaml from config.example.yaml...
    copy "config.example.yaml" "config.yaml" >nul
)

if not exist ".env" (
    if exist ".env.example" (
        echo Creating .env from .env.example...
        copy ".env.example" ".env" >nul
    ) else (
        echo Creating empty .env...
        type nul > ".env"
    )
)

if not exist "captures" mkdir "captures"
if not exist "logs" mkdir "logs"
if not exist "data" mkdir "data"
if not exist "secrets" mkdir "secrets"

echo.
echo Starting dashboard...
echo Dashboard: http://127.0.0.1:%PORT%/
echo API docs:   http://127.0.0.1:%PORT%/docs
echo.
echo The browser will open automatically in a few seconds.
echo Press Ctrl+C in this window to stop the app.
echo.

start "" cmd /c "timeout /t 8 /nobreak >nul && start http://127.0.0.1:%PORT%/"
"%PYTHON%" -m src.main --port %PORT%
set "APP_EXIT=%ERRORLEVEL%"

echo.
echo App stopped with exit code %APP_EXIT%.
pause
exit /b %APP_EXIT%

:PickPort
for %%P in (%REQUESTED_PORT% 8080 8081 8082 8083 8084 8085) do (
    netstat -ano | findstr /R /C:":%%P .*LISTENING" >nul 2>nul
    if errorlevel 1 (
        set "PORT=%%P"
        exit /b 0
    )
)
exit /b 0

:FindPython
set "SYSTEM_PY="
call :PythonFromLauncher -3.12
if defined SYSTEM_PY exit /b 0
call :PythonFromLauncher -3.11
if defined SYSTEM_PY exit /b 0
call :PythonFromLauncher -3
if defined SYSTEM_PY exit /b 0
call :PythonFromCommand python
if defined SYSTEM_PY exit /b 0
call :PythonFromCommand python3
if defined SYSTEM_PY exit /b 0
call :PythonFromPath "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined SYSTEM_PY exit /b 0
call :PythonFromPath "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if defined SYSTEM_PY exit /b 0
call :PythonFromPath "%ProgramFiles%\Python312\python.exe"
if defined SYSTEM_PY exit /b 0
call :PythonFromPath "%ProgramFiles%\Python311\python.exe"
exit /b 0

:PythonFromLauncher
for /f "delims=" %%P in ('py %~1 -c "import sys; assert sys.version_info[:2] ^>= (3, 11); print(sys.executable)" 2^>nul') do set "SYSTEM_PY=%%P"
exit /b 0

:PythonFromCommand
for /f "delims=" %%P in ('%~1 -c "import sys; assert sys.version_info[:2] ^>= (3, 11); print(sys.executable)" 2^>nul') do set "SYSTEM_PY=%%P"
exit /b 0

:PythonFromPath
if exist "%~1" (
    "%~1" -c "import sys; assert sys.version_info[:2] >= (3, 11)" >nul 2>nul
    if not errorlevel 1 set "SYSTEM_PY=%~1"
)
exit /b 0

:InstallPython
echo Python 3.11+ was not found. Trying to install Python automatically...
where winget >nul 2>nul
if errorlevel 1 (
    echo Windows Package Manager winget was not found.
    exit /b 1
)

echo Installing Python 3.12 with winget...
winget install --exact --id Python.Python.3.12 --source winget --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
    echo Python 3.12 install failed. Trying Python 3.11...
    winget install --exact --id Python.Python.3.11 --source winget --scope user --accept-package-agreements --accept-source-agreements
)

echo Installing Microsoft Visual C++ runtime if needed...
winget install --exact --id Microsoft.VCRedist.2015+.x64 --source winget --accept-package-agreements --accept-source-agreements >nul 2>nul

call :FindPython
exit /b 0
