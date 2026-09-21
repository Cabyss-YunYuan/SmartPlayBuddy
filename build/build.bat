@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   SmartPlayBuddy Build Script
echo ============================================
echo.

set BUILD_DIR=%~dp0
set PROJECT_ROOT=%BUILD_DIR%..\
set OUTPUT_DIR=%PROJECT_ROOT%output
set RUNTIME_DIR=%PROJECT_ROOT%runtime
set LOCALES_DIR=%PROJECT_ROOT%src\smartplaybuddy\utils\i18n\locales

echo [0/5] Checking dependencies...

where pyinstaller >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] PyInstaller not found. Run: pip install pyinstaller
    exit /b 1
)

for /f "usebackq tokens=*" %%N in (`python -c "import json;print(json.load(open(r'%LOCALES_DIR%\en_US.json','r',encoding='utf-8'))['app']['name'])"`) do set APP_NAME=%%N
for /f "usebackq tokens=*" %%N in (`python -c "import json;print(json.load(open(r'%LOCALES_DIR%\zh_CN.json','r',encoding='utf-8'))['app']['name'])"`) do set APP_NAME_ZH=%%N
for /f "usebackq tokens=*" %%V in (`python -c "import json;print(json.load(open(r'%LOCALES_DIR%\en_US.json','r',encoding='utf-8'))['app']['version'])"`) do set APP_VERSION=%%V
for /f "usebackq tokens=*" %%P in (`python -c "import json;print(json.load(open(r'%LOCALES_DIR%\en_US.json','r',encoding='utf-8'))['app']['publisher'])"`) do set APP_PUBLISHER=%%P
for /f "usebackq tokens=*" %%E in (`python -c "import json;print(json.load(open(r'%LOCALES_DIR%\en_US.json','r',encoding='utf-8'))['app']['exe_name'])"`) do set APP_EXE_NAME=%%E

set DIST_DIR=%PROJECT_ROOT%dist\%APP_NAME%

echo   App:     %APP_NAME%
echo   Version: %APP_VERSION%
echo.

echo [1/5] Cleaning old build artifacts...
if exist "%PROJECT_ROOT%dist" rmdir /s /q "%PROJECT_ROOT%dist"
if exist "%BUILD_DIR%pyinstaller_temp" rmdir /s /q "%BUILD_DIR%pyinstaller_temp"
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

echo [2/5] Preparing Python runtime...
for /f "tokens=*" %%V in ('python -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"') do set PYVER=%%V
echo   Python version: %PYVER%

if exist "%RUNTIME_DIR%\python.exe" (
    echo   Using cached runtime: %RUNTIME_DIR%
) else (
    echo   Setting up Python Embeddable runtime...
    if not exist "%BUILD_DIR%temp" mkdir "%BUILD_DIR%temp"

    set EMBED_ZIP=%BUILD_DIR%temp\python-embed.zip
    set EMBED_URL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip

    echo   Downloading: !EMBED_URL!
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;Invoke-WebRequest -Uri '!EMBED_URL!' -OutFile '!EMBED_ZIP!'"
    if not exist "!EMBED_ZIP!" (
        echo [ERROR] Download failed.
        exit /b 1
    )

    echo   Extracting...
    powershell -Command "Expand-Archive -Path '!EMBED_ZIP!' -DestinationPath '%RUNTIME_DIR%' -Force"

    echo   Enabling site-packages...
    for %%F in ("%RUNTIME_DIR%\python*._pth") do (
        powershell -Command "(Get-Content '%%F') -replace '#import site','import site' | Set-Content '%%F'"
    )

    echo   Installing pip...
    set GET_PIP=%BUILD_DIR%temp\get-pip.py
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '!GET_PIP!'"
    "%RUNTIME_DIR%\python.exe" "!GET_PIP!" --no-warn-script-location
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] pip installation failed.
        exit /b 1
    )

    del /q "%BUILD_DIR%temp\*" >nul 2>&1
    rmdir /q "%BUILD_DIR%temp" >nul 2>&1
    echo   Runtime ready: %RUNTIME_DIR%
)

echo [3/5] Building with PyInstaller...
python "%BUILD_DIR%gen_version_info.py" "%LOCALES_DIR%" "%BUILD_DIR%version_info.txt"
cd /d "%PROJECT_ROOT%"
pyinstaller SmartPlayBuddy.spec --workpath build\pyinstaller_temp --distpath dist --noconfirm
if %ERRORLEVEL% neq 0 (
    echo [ERROR] PyInstaller build failed!
    exit /b 1
)

echo [4/5] Copying drivers and runtime...
set DRIVERS_SRC=%PROJECT_ROOT%src\smartplaybuddy\drivers
set DRIVERS_DST=%DIST_DIR%\drivers

if not exist "%DRIVERS_DST%" mkdir "%DRIVERS_DST%"

for /d %%D in ("%DRIVERS_SRC%\*") do (
    set DRIVER_NAME=%%~nxD
    if "!DRIVER_NAME!" neq "__pycache__" (
        echo   Copying driver: !DRIVER_NAME!
        if not exist "%DRIVERS_DST%\!DRIVER_NAME!" mkdir "%DRIVERS_DST%\!DRIVER_NAME!"
        for %%F in (driver.py manifest.json requirements.txt) do (
            if exist "%%D\%%F" copy /y "%%D\%%F" "%DRIVERS_DST%\!DRIVER_NAME!\" >nul
        )
        if exist "%%D\packages" (
            echo   Copying packages: !DRIVER_NAME!/packages
            xcopy /e /i /q /y "%%D\packages" "%DRIVERS_DST%\!DRIVER_NAME!\packages" >nul
        )
    )
)

copy /y "%DRIVERS_SRC%\host.py" "%DRIVERS_DST%\" >nul
copy /y "%DRIVERS_SRC%\base.py" "%DRIVERS_DST%\" >nul

echo   Copying Python runtime...
xcopy /e /i /q /y "%RUNTIME_DIR%" "%DIST_DIR%\runtime" >nul

echo   Done.

echo [5/5] Generating installer...

set ISCC=
if exist "C:\Program Files (x86)\Inno Setup 7\ISCC.exe" (
    set "ISCC=C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
) else if exist "C:\Program Files\Inno Setup 7\ISCC.exe" (
    set "ISCC=C:\Program Files\Inno Setup 7\ISCC.exe"
)

if defined ISCC (
    echo   Using Inno Setup: %ISCC%
    "%ISCC%" "%BUILD_DIR%installer.iss" "/DAppVersion=%APP_VERSION%" "/DAppPublisher=%APP_PUBLISHER%" "/DAppExeName=%APP_EXE_NAME%" "/DAppNameEN=%APP_NAME%" "/DAppNameZH=%APP_NAME_ZH%"
    if !ERRORLEVEL! neq 0 (
        echo [WARN] Inno Setup failed, portable version is ready.
    ) else (
        echo.
        echo   Installer: %OUTPUT_DIR%\%APP_NAME%_Setup_v%APP_VERSION%.exe
    )
) else (
    echo [SKIP] Inno Setup 7 not found, skipping installer.
    echo        Portable: %DIST_DIR%
)

echo.
echo ============================================
echo   Build complete!
echo ============================================
echo.
echo   Portable: %DIST_DIR%
echo   Run:      %DIST_DIR%\%APP_EXE_NAME%
echo.
