$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Remove-Item -Force -ErrorAction SilentlyContinue '.\dist\NEON-SNAKE.exe'
py -m PyInstaller --noconfirm --clean --onefile --name NEON-SNAKE --icon 'snake-icon.ico' --add-data 'index.html;.' --add-data 'ads.mp4;.' launcher.py