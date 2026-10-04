' EmptyOS desktop launcher — double-click or pin to taskbar.
' Opens the EmptyOS daily-driver window with no console flash (pythonw + hidden).
' The running --app window carries the EmptyOS favicon as its taskbar identity;
' this .vbs is only the launcher. To start the daemon too if it's down, change
' the script args below to include  --start  (triggers restart.bat / UAC).
Option Explicit
Dim sh, fso, root
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
' 0 = hidden window, False = don't wait for the launcher to exit.
sh.Run "pythonw """ & root & "\scripts\eos_desktop.py""", 0, False
