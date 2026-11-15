@echo off
rem Copy the integration into a Home Assistant container over SSH and restart it.
rem Works with Dropbear (DietPi), which has no scp/sftp support.
rem
rem   tools\deploy_to_ha.cmd root@192.168.1.50 homeassistant
rem   tools\deploy_to_ha.cmd                  (host read from tools\deploy_host.txt)
rem
rem tools\deploy_host.txt is gitignored: put your user@host on its first line.

set HOST=%~1
if "%HOST%"=="" if exist "%~dp0deploy_host.txt" set /p HOST=<"%~dp0deploy_host.txt"
if "%HOST%"=="" (
  echo Usage: tools\deploy_to_ha.cmd user@host [container]
  echo    or: put user@host in tools\deploy_host.txt
  exit /b 1
)
set CONTAINER=%~2
if "%CONTAINER%"=="" set CONTAINER=homeassistant

echo Deploying grandstream_gwn to %CONTAINER% on %HOST% ...
tar -cf - --exclude __pycache__ -C "%~dp0..\custom_components" grandstream_gwn | ssh %HOST% "docker exec %CONTAINER% mkdir -p /config/custom_components && docker exec -i %CONTAINER% tar -xf - -C /config/custom_components && echo Files in container: && docker exec %CONTAINER% ls /config/custom_components/grandstream_gwn && echo Restarting %CONTAINER% ... && docker restart %CONTAINER%"
if errorlevel 1 (
  echo.
  echo Deploy FAILED. Check the host, password and container name.
  exit /b 1
)
echo.
echo Done. Wait about a minute, then press Ctrl+F5 in the browser.
