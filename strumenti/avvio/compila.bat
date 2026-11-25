@echo off
rem Ricompila OpenHoop.exe (nella cartella principale) col compilatore C# incluso in Windows.
cd /d "%~dp0"
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /optimize+ ^
  /win32icon:..\..\02_script\brand\openhoop.ico /out:..\..\OpenHoop.exe ^
  /r:System.Windows.Forms.dll /r:System.Drawing.dll OpenHoop.cs
