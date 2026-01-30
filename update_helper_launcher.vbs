Set WshShell = CreateObject("WScript.Shell")
Set objArgs = WScript.Arguments

' Build PowerShell command with all parameters
psPath = objArgs(0)
params = ""
For i = 1 To objArgs.Count - 1
    params = params & " " & objArgs(i)
Next

' Launch PowerShell hidden
WshShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & psPath & """" & params, 0, False
