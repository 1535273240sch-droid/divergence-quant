Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
exePath = currentDir & "\dist\DivergenceQuant\DivergenceQuant.exe"
WshShell.CurrentDirectory = currentDir & "\dist\DivergenceQuant"
WshShell.Run """" & exePath & """", 1, False
