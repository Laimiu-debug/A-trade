; A no-install launcher. Runtime files are immutable per build; user data stays
; in the application's existing APPDATA workspace, outside this cache.
Unicode true
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
Name "A-trade"
Caption "正在准备 A-trade"
OutFile "${ATRADE_OUTPUT}"
Icon "${ATRADE_ICON}"
RequestExecutionLevel user
SetCompressor /FINAL zlib
SetCompress auto
CRCCheck on
AutoCloseWindow true
ShowInstDetails nevershow
BrandingText "A-trade"
InstallButtonText "启动"
LoadLanguageFile "${NSISDIR}\Contrib\Language files\SimpChinese.nlf"
VIProductVersion "${ATRADE_VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "A-trade"
VIAddVersionKey /LANG=2052 "FileDescription" "A-trade 股票研究与回测"
VIAddVersionKey /LANG=2052 "FileVersion" "${ATRADE_VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "A-trade"
Page instfiles

Var CacheRoot
Var RuntimeRoot
Var StageRoot
Var MutexHandle
Var DirectoryHandle
Var CacheValid
Var FailureStage

Function CheckCache
  StrCpy $CacheValid 0
  IfFileExists "$RuntimeRoot\A-trade.exe" 0 cacheInvalid
  IfFileExists "$RuntimeRoot\resources\python\python.exe" 0 cacheInvalid
  IfFileExists "$RuntimeRoot\resources\python\python313.dll" 0 cacheInvalid
  IfFileExists "$RuntimeRoot\resources\python\python313.zip" 0 cacheInvalid
  IfFileExists "$RuntimeRoot\resources\app\package.json" 0 cacheInvalid
  IfFileExists "$RuntimeRoot\resources\app\desktop\main.cjs" 0 cacheInvalid
  IfFileExists "$RuntimeRoot\resources\app\desktop\preload.cjs" 0 cacheInvalid
  IfFileExists "$RuntimeRoot\resources\app\preview\index.html" 0 cacheInvalid
  ClearErrors
  FileOpen $0 "$RuntimeRoot\.atrade-runtime-ready" r
  IfErrors cacheInvalid
  FileRead $0 $1
  FileClose $0
  StrCmp $1 "${ATRADE_CACHE_KEY}" 0 cacheInvalid
  StrCpy $CacheValid 1
  cacheInvalid:
FunctionEnd

Function CloseHandles
  ${If} $DirectoryHandle != 0
    System::Call 'kernel32::CloseHandle(p $DirectoryHandle)'
    StrCpy $DirectoryHandle 0
  ${EndIf}
  ${If} $MutexHandle != 0
    System::Call 'kernel32::ReleaseMutex(p $MutexHandle)'
    System::Call 'kernel32::CloseHandle(p $MutexHandle)'
    StrCpy $MutexHandle 0
  ${EndIf}
FunctionEnd

Function Fail
  System::Call 'kernel32::GetLastError()i.r2'
  FileOpen $R9 "$TEMP\A-trade-startup-error.txt" w
  FileWrite $R9 "stage=$FailureStage$\r$\nwinerror=$2$\r$\nresult=$0$\r$\ncache=$CacheRoot$\r$\nruntime=$RuntimeRoot$\r$\nstaging=$StageRoot$\r$\n"
  FileClose $R9
  Call CloseHandles
  MessageBox MB_OK|MB_ICONSTOP "A-trade 运行环境准备失败。请确认本机有足够的磁盘空间、缓存目录可写，并关闭其他 A-trade 后重试。账户和研究记录未被修改。" /SD IDOK
  SetErrorLevel 1
  Quit
FunctionEnd

Function .onInit
  StrCpy $FailureStage "platform"
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "A-trade 需要 64 位 Windows。" /SD IDOK
    SetErrorLevel 1
    Quit
  ${EndIf}
  SetShellVarContext current
  StrCpy $MutexHandle 0
  StrCpy $DirectoryHandle 0
  ReadEnvStr $CacheRoot "ATRADE_RUNTIME_CACHE"
  StrCpy $FailureStage "cache-path"
  ${If} $CacheRoot == ""
    StrCpy $CacheRoot "$LOCALAPPDATA\A-trade\Runtime"
  ${EndIf}
  ; Only accept absolute paths before deriving any staging/cleanup targets.
  StrCpy $0 $CacheRoot 2 1
  StrCmp $0 ":\" cacheRootAbsolute
  StrCpy $0 $CacheRoot 2
  StrCmp $0 "\\" cacheRootAbsolute
  Call Fail
  cacheRootAbsolute:
  ClearErrors
  CreateDirectory "$CacheRoot"
  IfErrors badCacheRoot
  GetFullPathName $0 "$CacheRoot"
  IfErrors badCacheRoot
  StrCmp $0 "" badCacheRoot
  StrCpy $CacheRoot $0
  Goto cacheRootReady
  badCacheRoot:
  Call Fail
  cacheRootReady:
  StrCpy $RuntimeRoot "$CacheRoot\${ATRADE_CACHE_KEY}"
  StrCpy $StageRoot "$RuntimeRoot.staging"
  Call CheckCache
  ${If} $CacheValid == 1
    SetSilent silent
  ${EndIf}
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "--start-hidden" $0
  ${IfNot} ${Errors}
    SetSilent silent
  ${EndIf}
FunctionEnd

Function .onUserAbort
  Call CloseHandles
FunctionEnd

Section
  StrCpy $FailureStage "mutex-create"
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Local\Atrade-runtime-cache-v2")p.r0'
  StrCpy $MutexHandle $0
  ${If} $MutexHandle == 0
    Call Fail
  ${EndIf}
  StrCpy $FailureStage "mutex-wait"
  System::Call 'kernel32::WaitForSingleObject(p $MutexHandle, i 120000)i.r0'
  ${If} $0 != 0
  ${AndIf} $0 != 128
    Call Fail
  ${EndIf}
  ; Recheck after acquiring the mutex: another launcher may have finished.
  Call CheckCache
  ${If} $CacheValid != 1
    StrCpy $FailureStage "extract"
    SetDetailsPrint textonly
    DetailPrint "首次启动，正在准备运行环境。后续打开将直接使用缓存…"
    ClearErrors
    CreateDirectory "$CacheRoot"
    SetOutPath "$CacheRoot"
    ; These targets have a fixed build-key suffix and never contain user data.
    RMDir /r "$StageRoot"
    CreateDirectory "$StageRoot"
    ; NTFS compression is local to this empty runtime directory. Unsupported
    ; file systems keep the normal layout; no OS-wide compression is changed.
    nsExec::ExecToStack /TIMEOUT=5000 '"$SYSDIR\compact.exe" /C /I /Q "$StageRoot"'
    Pop $0
    Pop $1
    SetOutPath "$StageRoot"
    ClearErrors
    File /r "${ATRADE_APPLICATION}\*.*"
    IfErrors extractionFailed
    StrCpy $FailureStage "verify-and-share"
    ExecWait '"$StageRoot\resources\python\pythonw.exe" -B "$StageRoot\resources\app\service\runtime_cache.py" prepare "$CacheRoot" "$StageRoot"' $0
    ${If} $0 != 0
      Call Fail
    ${EndIf}
    FileOpen $0 "$StageRoot\.atrade-runtime-ready" w
    IfErrors extractionFailed
    FileWrite $0 "${ATRADE_CACHE_KEY}"
    FileClose $0
    IfErrors extractionFailed
    StrCpy $FailureStage "commit"
    SetOutPath "$CacheRoot"
    RMDir /r "$RuntimeRoot.previous"
    IfFileExists "$RuntimeRoot\*.*" 0 commitRuntime
    ClearErrors
    ; A running launcher holds a directory handle without FILE_SHARE_DELETE.
    ; Never overwrite files used by a running instance during cache repair.
    Rename "$RuntimeRoot" "$RuntimeRoot.previous"
    IfErrors extractionFailed
    commitRuntime:
    ClearErrors
    Rename "$StageRoot" "$RuntimeRoot"
    IfErrors extractionFailed
    RMDir /r "$RuntimeRoot.previous"
    Call CheckCache
    ${If} $CacheValid != 1
      Call Fail
    ${EndIf}
  ${EndIf}
  StrCpy $FailureStage "runtime-lease"
  System::Call 'kernel32::CreateFileW(w "$RuntimeRoot", i 0x80000000, i 3, p 0, i 3, i 0x02000000, p 0)p.r0'
  ${If} $0 == -1
    Call Fail
  ${EndIf}
  StrCpy $DirectoryHandle $0
  ; A failed optional cleanup must not prevent a verified runtime from opening.
  ExecWait '"$RuntimeRoot\resources\python\pythonw.exe" -B "$RuntimeRoot\resources\app\service\runtime_cache.py" collect "$CacheRoot" "$RuntimeRoot"' $0
  System::Call 'kernel32::ReleaseMutex(p $MutexHandle)'
  System::Call 'kernel32::CloseHandle(p $MutexHandle)'
  StrCpy $MutexHandle 0
  HideWindow
  SetOutPath "$RuntimeRoot"
  System::Call 'kernel32::SetEnvironmentVariableW(w "PORTABLE_EXECUTABLE_DIR", w "$EXEDIR")'
  System::Call 'kernel32::SetEnvironmentVariableW(w "PORTABLE_EXECUTABLE_FILE", w "$EXEPATH")'
  System::Call 'kernel32::SetEnvironmentVariableW(w "PORTABLE_EXECUTABLE_APP_FILENAME", w "A-trade")'
  ${GetParameters} $R0
  StrCpy $FailureStage "app-launch"
  ClearErrors
  ExecWait '"$RuntimeRoot\A-trade.exe" $R0' $0
  IfErrors extractionFailed
  SetErrorLevel $0
  Call CloseHandles
  Goto finished
  extractionFailed:
  Call Fail
  finished:
SectionEnd
