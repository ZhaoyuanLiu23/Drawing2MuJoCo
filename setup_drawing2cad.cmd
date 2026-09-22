@echo off
setlocal
cd /d "%~dp0"
if exist ".venv-drawing2cad\Scripts\python.exe" goto install
if "%~1"=="" goto default_python
"%~1" -m venv ".venv-drawing2cad"
if errorlevel 1 goto failed
goto install
:default_python
py -3.12 -m venv ".venv-drawing2cad"
if errorlevel 1 goto failed
:install
".venv-drawing2cad\Scripts\python.exe" -m pip install -r "requirements-drawing2cad-lock.txt"
if errorlevel 1 goto failed
echo Drawing2CAD environment is ready. Existing Panda dependencies were not changed.
exit /b 0
:failed
echo Setup failed. Use 64-bit Python 3.12, e.g. setup_drawing2cad.cmd "C:\path\to\python.exe"
echo See DRAWING2CAD.md. Do not install these dependencies into the Panda environment.
exit /b 1
