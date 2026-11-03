@echo off
setlocal
cd /d "%~dp0"
echo Remember Me Windows Beta - local access only by default.
echo Web: http://127.0.0.1:1314/admin/console   MCP: http://127.0.0.1:1314/mcp
echo Stop: Ctrl+C once and wait, or use Web Ops maintenance then Shut Down Server.
"%~dp0runtime\python\python.exe" -I "%~dp0beta_start.py" %*
set "result=%errorlevel%"
echo Remember Me exited with code %result%.
pause
exit /b %result%
