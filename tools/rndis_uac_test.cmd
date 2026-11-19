@echo off
setlocal
set "PS1=%~dp0rndis_uac_test.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','%PS1%')"
endlocal
