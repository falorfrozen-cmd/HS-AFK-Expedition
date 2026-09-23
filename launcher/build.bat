@echo off
setlocal enabledelayedexpansion
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
for /f "usebackq tokens=* delims=" %%i in (`""!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath"`) do set "VS_INSTALL=%%i"
if not defined VS_INSTALL exit /b 1
call "%VS_INSTALL%\VC\Auxiliary\Build\vcvars64.bat" >nul
cd /d "%~dp0"
if not exist obj mkdir obj
cl /nologo /std:c++20 /EHsc /O2 /MT Launcher.cpp /Fe:"AFK FARM.exe" /Fo:obj\ /link /SUBSYSTEM:WINDOWS user32.lib
exit /b %errorlevel%
