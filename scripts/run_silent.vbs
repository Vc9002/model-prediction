' run_silent.vbs -- Windows GUI launcher that suppresses all console windows.
' Runs the given command line with window style 0 (SW_HIDE).
Set WshShell = CreateObject("WScript.Shell")
Set args = WScript.Arguments
cmd = ""
For i = 0 To args.Count - 1
    If i > 0 Then cmd = cmd & " "
    cmd = cmd & args(i)
Next
If Len(cmd) > 0 Then
    WshShell.Run cmd, 0, False
End If
