' EmptyOS Code — opens the /code IDE window (file tree + diff + terminal + chat)
' as a chromeless app window. Double-click or pin to taskbar.
' Same daemon as EmptyOS.vbs, different surface. Needs the :9000 daemon running.
Option Explicit
Dim sh, fso, root
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
sh.Run "pythonw """ & root & "\scripts\eos_desktop.py"" --code", 0, False
