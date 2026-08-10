' Screen Time desktop launcher: start background collection + open GUI (no console window)
Option Explicit
Dim fso, ws, base, pythonw
Set fso = CreateObject("Scripting.FileSystemObject")
Set ws = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = base & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    pythonw = fso.GetParentFolderName(fso.GetParentFolderName(base)) & "\python\pythonw.exe"
End If
ws.CurrentDirectory = base
ws.Run """" & pythonw & """ main.py dashboard", 0, False
