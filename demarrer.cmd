@echo off
rem =====================================================================
rem  Pocket Lab - double-cliquez sur ce fichier pour demarrer la console.
rem  Tout est explique a l'ecran ; la fenetre reste ouverte en cas d'erreur.
rem =====================================================================
title Pocket Lab - console de trading
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0demarrer.ps1" %*
