; PHX-092 (ADR-128): uninstall hooks for the PHOENIX NSIS installer.
;
; Tauri's uninstaller deletes only the files the installer copied. The bundled backend
; also holds files written at run time (Python bytecode caches), so its whole directory
; under the installation folder is removed, on uninstall and on update alike, so that
; no stale module from an older version can be imported.
;
; User data stays unless the user ticks "Delete the application data" on the uninstall
; confirmation page (never in silent or update mode). Then the default data directory
; %USERPROFILE%\.phoenix\data is removed as well. A custom PHOENIX_DATA_DIR is never
; deleted by the uninstaller.

!macro NSIS_HOOK_POSTUNINSTALL
  RMDir /r /REBOOTOK "$INSTDIR\backend"
  RMDir "$INSTDIR"
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
  ${AndIf} $PROFILE != ""
    RMDir /r "$PROFILE\.phoenix\data"
    RMDir "$PROFILE\.phoenix"
  ${EndIf}
!macroend
