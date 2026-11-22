!macro NSIS_HOOK_POSTINSTALL
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\extract-runtime.ps1" -InstallDir "$INSTDIR" -Mode Install'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Quantelune runtime installation failed (code $0). See $INSTDIR\quantelune-setup-error.log for details."
    Abort
  ${EndIf}
!macroend

!macro QuanteluneProtectLibrary ROOT
  ${If} ${FileExists} "${ROOT}\data\."
  ${OrIf} ${FileExists} "${ROOT}\storage\."
    ; The standard install used to keep research data inside the bundle directory.
    StrCpy $DeleteAppDataCheckboxState 0
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro QuanteluneProtectLibrary "$LOCALAPPDATA\${BUNDLEID}"
  !insertmacro QuanteluneProtectLibrary "$APPDATA\${BUNDLEID}"
  ; Tauri removes its bundled scripts before POSTUNINSTALL.
  InitPluginsDir
  CopyFiles /SILENT "$INSTDIR\extract-runtime.ps1" "$PLUGINSDIR\quantelune-cleanup.ps1"
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  IfFileExists "$INSTDIR\quantelune-installed-files.json" 0 done_runtime_uninstall
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\quantelune-cleanup.ps1" -InstallDir "$INSTDIR" -Mode Uninstall'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONEXCLAMATION "Some Quantelune runtime files could not be removed. Research data was preserved."
  ${EndIf}
  RMDir "$INSTDIR"
  done_runtime_uninstall:
!macroend
