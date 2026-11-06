; The compiler itself exposes Ver; the bootstrap EXE's PE version is 0.0.0.0.
; Keep this exact guard consistent with the pinned release in inputs.json.
#if Ver != EncodeVer(6, 7, 3)
  #error The pinned Inno Setup 6.7.3 compiler is required
#endif
; Inputs are generated/validated by build.ps1. No independent application version.
#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef InputsDir
  #error InputsDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef VcVersion
  #error VcVersion is required
#endif
#ifndef VcSha256
  #error VcSha256 is required
#endif
#ifndef Destination
  #error Destination is required
#endif
[Setup]
AppId={{82798CA8-F175-4C5C-A28B-38CBABBEFE09}
AppName=Remember Me Beta
AppVersion={#AppVersion}
AppPublisher=Remember Me
DefaultDirName={localappdata}\Programs\Remember Me Beta
DefaultGroupName=Remember Me Beta
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
Uninstallable=yes
UninstallDisplayName=Remember Me Beta
UninstallDisplayIcon={app}\remember-me.ico
SetupIconFile={#BundleDir}\remember-me.ico
SetupLogging=yes
OutputDir={#Destination}
OutputBaseFilename=remember-me-windows-x64-beta-setup
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=no
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
WizardStyle=modern
; Optional destructive actions require explicit unchecked-by-default choices and confirmation.
[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "start.cmd"
Source: "{#InputsDir}\vc_redist.x64.exe"; Flags: dontcopy
Source: "install-vcredist.ps1"; Flags: dontcopy
Source: "check-running.ps1"; Flags: dontcopy
Source: "{#InputsDir}\vc_redist.x64.exe"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "install-vcredist.ps1"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "uninstall-data.py"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "check-running.ps1"; DestDir: "{app}\setup"; Flags: ignoreversion
[Icons]
Name: "{group}\Remember Me Beta"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-I ""{app}\tray_launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\remember-me.ico"
Name: "{group}\Uninstall Remember Me Beta"; Filename: "{uninstallexe}"
[Code]
var
  UserSid: String;
  InstallMutex: LongWord;
  RebootRequired: Boolean;
  RemoveData, RemoveVC: Boolean;
  RemovalDataPath: String;

function CreateMutexW(Attributes: LongWord; InitialOwner: Boolean; Name: String): LongWord;
  external 'CreateMutexW@kernel32.dll stdcall';
function GetLastError: LongWord;
  external 'GetLastError@kernel32.dll stdcall';
function CloseHandle(Handle: LongWord): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function PowerShell: String;
begin
  Result := ExpandConstant('{sysnative}\WindowsPowerShell\v1.0\powershell.exe');
end;

function Quote(Value: String): String;
begin
  Result := '"' + Value + '"';
end;

function SetUserSid: Boolean;
var
  Code: Integer;
  Output: TExecOutput;
begin
  Result := False;
  if not ExecAndCaptureOutput(PowerShell,
    '-NoProfile -NonInteractive -Command "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value"',
    '', SW_HIDE, ewWaitUntilTerminated, Code, Output) then exit;
  if (Code <> 0) or Output.Error or (GetArrayLength(Output.StdOut) <> 1) then exit;
  UserSid := Trim(Output.StdOut[0]);
  Result := Pos('S-1-', UserSid) = 1;
end;

function MutexName(Kind: String): String;
begin
  Result := 'Global\RememberMeBeta' + Kind + '-v1-' + UserSid;
end;

function GateAndCheck: Boolean;
begin
  Result := False;
  if not SetUserSid then begin
    MsgBox('Unable to verify the Windows user identity. Setup cannot safely continue.', mbError, MB_OK);
    exit;
  end;
  if CheckForMutexes(MutexName('Install')) then begin
    MsgBox('Another Remember Me installation or removal is running.', mbError, MB_OK);
    exit;
  end;
  InstallMutex := CreateMutexW(0, False, MutexName('Install'));
  if (InstallMutex = 0) or (GetLastError = 183) then begin
    if InstallMutex <> 0 then CloseHandle(InstallMutex);
    InstallMutex := 0;
    MsgBox('Unable to acquire the Remember Me installation guard.', mbError, MB_OK);
    exit;
  end;
  Result := True;
end;

function Running(ScriptPath: String): Boolean;
var
  Code: Integer;
  Output: TExecOutput;
begin
  Result := True;
  if CheckForMutexes(MutexName('Launcher') + ',' + MutexName('Service')) then exit;
  if not ExecAndCaptureOutput(PowerShell,
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + Quote(ScriptPath) +
    ' -InstallDirectory ' + Quote(ExpandConstant('{app}')),
    '', SW_HIDE, ewWaitUntilTerminated, Code, Output) then exit;
  Result := (Code <> 0) or Output.Error;
end;

function RuntimeReady: Boolean;
var
  Installed, Major, Minor, Build, Revision: Cardinal;
  Current: String;
  CurrentPacked, RequiredPacked: Int64;
begin
  Result := False;
  if not RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) then exit;
  if Installed <> 1 then exit;
  if not RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Major', Major) then exit;
  if not RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Minor', Minor) then exit;
  if not RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Bld', Build) then exit;
  if not RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Rbld', Revision) then exit;
  Current := Format('%d.%d.%d.%d', [Major, Minor, Build, Revision]);
  if not StrToVersion(Current, CurrentPacked) then exit;
  if not StrToVersion('{#VcVersion}', RequiredPacked) then exit;
  Result := ComparePackedVersion(CurrentPacked, RequiredPacked) >= 0;
end;

function ContainsDirectory(Parent, Child: String): Boolean;
begin
  Parent := Lowercase(AddBackslash(ExpandFileName(Parent)));
  Child := Lowercase(AddBackslash(ExpandFileName(Child)));
  Result := Pos(Parent, Child) = 1;
end;

function SafeInstallDirectory: Boolean;
var
  ProgramDir, DataDir: String;
begin
  ProgramDir := ExpandConstant('{app}');
  DataDir := ExpandConstant('{localappdata}\remember-me-beta');
  Result := not ContainsDirectory(ProgramDir, DataDir) and not ContainsDirectory(DataDir, ProgramDir);
  DataDir := GetEnv('REMEMBER_ME_BETA_DATA_DIR');
  if DataDir <> '' then
    Result := Result and not ContainsDirectory(ProgramDir, DataDir) and not ContainsDirectory(DataDir, ProgramDir);
end;

function InitializeSetup: Boolean;
begin
  Result := GateAndCheck;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Logs: String;
begin
  Result := '';
  if not SafeInstallDirectory then begin
    Result := 'The program directory must not contain your existing data directory. Choose a separate program directory.';
    exit;
  end;
  ExtractTemporaryFile('check-running.ps1');
  if Running(ExpandConstant('{tmp}\check-running.ps1')) then begin
    Result := 'Remember Me is running, or its process state could not be verified. Use Tray Exit or Web maintenance > Shut Down Server, wait for it to stop, then retry. Back up your existing data directory before upgrading.';
    exit;
  end;
  if RuntimeReady then exit;
  if MsgBox('Remember Me needs the bundled Microsoft Visual C++ x64 Runtime. Only the Microsoft installer will request administrator approval. Install it now?', mbConfirmation, MB_YESNO) <> idYes then begin
    Result := 'Required VC++ installation was cancelled. Remember Me has not been installed.';
    exit;
  end;
  ExtractTemporaryFile('vc_redist.x64.exe');
  ExtractTemporaryFile('install-vcredist.ps1');
  Logs := ExpandConstant('{localappdata}\remember-me-beta\logs\setup');
  if not Exec(PowerShell,
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + Quote(ExpandConstant('{tmp}\install-vcredist.ps1')) +
    ' -InstallerPath ' + Quote(ExpandConstant('{tmp}\vc_redist.x64.exe')) +
    ' -ExpectedSha256 {#VcSha256} -MinimumVersion {#VcVersion} -EvidenceDirectory ' + Quote(Logs),
    '', SW_HIDE, ewWaitUntilTerminated, Code) then begin
    Result := 'Could not start the prerequisite helper. Error: ' + IntToStr(Code);
    exit;
  end;
  Log('VC++ helper exit code: ' + IntToStr(Code));
  if Code = 1602 then begin
    Result := 'VC++ installation or administrator approval was cancelled. Logs: ' + Logs;
    exit;
  end;
  if (Code <> 0) and (Code <> 3010) and (Code <> 1641) then begin
    Result := 'VC++ installation failed. Exit code: ' + IntToStr(Code) + '. Logs: ' + Logs;
    exit;
  end;
  if not RuntimeReady then begin
    Result := 'VC++ post-install verification failed. Logs: ' + Logs;
    exit;
  end;
  if Running(ExpandConstant('{tmp}\check-running.ps1')) then begin
    Result := 'Remember Me started during prerequisite installation. Exit it normally, then retry.';
    exit;
  end;
  RebootRequired := (Code = 3010) or (Code = 1641);
  // Do not install application files until the verified prerequisite is ready.
end;

function NeedRestart: Boolean;
begin
  Result := RebootRequired;
end;

function DataTask(Delete: Boolean): Boolean;
var
  Code: Integer;
  Args: String;
  Index: Integer;
  Output: TExecOutput;
begin
  Args := '-I -B ' + Quote(ExpandConstant('{app}\setup\uninstall-data.py')) +
    ' --program ' + Quote(ExpandConstant('{app}')) + ' --data ' + Quote(RemovalDataPath);
  if Delete then Args := Args + ' --delete';
  Result := ExecAndCaptureOutput(ExpandConstant('{app}\runtime\python\python.exe'),
    Args, '', SW_HIDE, ewWaitUntilTerminated, Code, Output);
  Result := Result and (Code = 0) and not Output.Error;
  if not Result then
    MsgBox('Data cleanup could not be completed safely. No further automatic cleanup will be attempted. Check the selected directory and close all writers. Uninstall log contains details.', mbError, MB_OK);
  Log('Data cleanup exit code: ' + IntToStr(Code));
  for Index := 0 to GetArrayLength(Output.StdOut) - 1 do Log(Output.StdOut[Index]);
  for Index := 0 to GetArrayLength(Output.StdErr) - 1 do Log(Output.StdErr[Index]);
end;

function ChooseRemovalOptions: Boolean;
var
  Form: TSetupForm;
  DataBox, VCBox: TNewCheckBox;
  PathEdit: TNewEdit;
  Description: TNewStaticText;
  ProceedButton, CancelButton: TNewButton;
  RecordedInstall: String;
begin
  Result := False;
  RemoveData := False;
  RemoveVC := False;
  // Silent uninstall always retains both; no destructive command-line flags.
  if UninstallSilent then begin Result := True; exit; end;
  RemovalDataPath := GetEnv('REMEMBER_ME_BETA_DATA_DIR');
  if RemovalDataPath = '' then begin
    if RegQueryStringValue(HKCU64, 'Software\RememberMeBeta\Launcher', 'DataInstallDirectory', RecordedInstall) and
       (CompareText(RemoveBackslashUnlessRoot(RecordedInstall), RemoveBackslashUnlessRoot(ExpandConstant('{app}'))) = 0) then
      RegQueryStringValue(HKCU64, 'Software\RememberMeBeta\Launcher', 'DataDirectory', RemovalDataPath);
    if RemovalDataPath = '' then RemovalDataPath := ExpandConstant('{localappdata}\remember-me-beta');
  end;
  Form := CreateCustomForm(ScaleX(540), ScaleY(280), False, False);
  try
    Form.Caption := 'Remember Me - Uninstall options';
    Form.Position := poScreenCenter;
    Description := TNewStaticText.Create(Form);
    Description.Parent := Form;
    Description.SetBounds(ScaleX(16), ScaleY(12), ScaleX(508), ScaleY(38));
    Description.AutoSize := False;
    Description.WordWrap := True;
    Description.Caption := 'Both options are OFF by default. Continue normally to keep your data and shared VC++ Runtime.';
    DataBox := TNewCheckBox.Create(Form);
    DataBox.Parent := Form;
    DataBox.SetBounds(ScaleX(16), ScaleY(58), ScaleX(508), ScaleY(24));
    DataBox.Caption := 'Delete ALL Remember Me data in the directory below';
    DataBox.Checked := False;
    PathEdit := TNewEdit.Create(Form);
    PathEdit.Parent := Form;
    PathEdit.SetBounds(ScaleX(16), ScaleY(88), ScaleX(508), ScaleY(24));
    PathEdit.Text := RemovalDataPath;
    VCBox := TNewCheckBox.Create(Form);
    VCBox.Parent := Form;
    VCBox.SetBounds(ScaleX(16), ScaleY(130), ScaleX(508), ScaleY(24));
    VCBox.Caption := 'Uninstall shared Microsoft Visual C++ x64 Runtime';
    VCBox.Checked := False;
    Description := TNewStaticText.Create(Form);
    Description.Parent := Form;
    Description.SetBounds(ScaleX(16), ScaleY(162), ScaleX(508), ScaleY(48));
    Description.AutoSize := False;
    Description.WordWrap := True;
    Description.Caption := 'WARNING: removing VC++ may break other applications and requires administrator approval. Data deletion is permanent even if VC++ removal is cancelled. Other data roots and external models remain.';
    ProceedButton := TNewButton.Create(Form);
    ProceedButton.Parent := Form;
    ProceedButton.SetBounds(ScaleX(300), ScaleY(234), ScaleX(108), ScaleY(28));
    ProceedButton.Caption := 'Continue';
    ProceedButton.ModalResult := mrOk;
    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.SetBounds(ScaleX(416), ScaleY(234), ScaleX(108), ScaleY(28));
    CancelButton.Caption := 'Cancel';
    CancelButton.Cancel := True;
    CancelButton.ModalResult := mrCancel;
    if Form.ShowModal <> mrOk then exit;
    RemoveData := DataBox.Checked;
    RemoveVC := VCBox.Checked;
    RemovalDataPath := Trim(PathEdit.Text);
    if RemoveData then begin
      if MsgBox('Permanently delete ALL memories, configuration, caches and logs in:' + #13#10 +
        RemovalDataPath + #13#10 + 'This cannot be undone. Back up first. Delete this directory?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> idYes then exit;
      if not DataTask(False) then exit;
    end;
    if RemoveVC and (MsgBox('The Microsoft VC++ x64 Runtime is SHARED. Removing it may stop other programs from working. Only the bundled matching version can be removed automatically. Continue?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> idYes) then exit;
    Result := True;
  finally
    Form.Free;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := GateAndCheck;
  if Result then begin
    Result := not Running(ExpandConstant('{app}\setup\check-running.ps1'));
    if not Result then MsgBox('Remember Me is running, or its process state could not be verified. Exit normally using the Tray or Web maintenance shutdown, wait, and run Uninstall again. Your memory data will be retained.', mbError, MB_OK);
    if Result then Result := ChooseRemovalOptions;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Command, OwnedCommand, Logs: String;
  Code: Integer;
begin
  if CurUninstallStep <> usUninstall then exit;
  if Running(ExpandConstant('{app}\setup\check-running.ps1')) then
    RaiseException('Remember Me started during uninstall preparation. Exit it normally and retry.');
  if RemoveData and not DataTask(True) then
    RaiseException('Data cleanup failed or was partial; application removal stopped. Inspect the selected directory.');
  if RemoveVC then begin
    Logs := ExpandConstant('{%TEMP}\RememberMe-uninstall');
    if not Exec(PowerShell, '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
      Quote(ExpandConstant('{app}\setup\install-vcredist.ps1')) +
      ' -Uninstall -InstallerPath ' + Quote(ExpandConstant('{app}\setup\vc_redist.x64.exe')) +
      ' -ExpectedSha256 {#VcSha256} -MinimumVersion {#VcVersion} -EvidenceDirectory ' + Quote(Logs),
      '', SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('Could not run the VC++ removal helper. Data already deleted, if selected.');
    if (Code <> 0) and (Code <> 3010) and (Code <> 1641) then
      RaiseException('VC++ removal failed or was cancelled. Data already deleted, if selected. Logs: ' + Logs);
    if (Code = 3010) or (Code = 1641) then
      MsgBox('VC++ removal requires a Windows restart. Save work and restart manually. No automatic restart will occur.', mbInformation, MB_OK);
  end;
  if RegQueryStringValue(HKCU64, 'Software\Microsoft\Windows\CurrentVersion\Run', 'RememberMeBeta', Command) and
     RegQueryStringValue(HKCU64, 'Software\RememberMeBeta\Launcher', 'AutostartCommand', OwnedCommand) and
     (Command = OwnedCommand) then begin
    if not RegDeleteValue(HKCU64, 'Software\Microsoft\Windows\CurrentVersion\Run', 'RememberMeBeta') then
      RaiseException('Unable to remove the owned Remember Me login startup entry. Check current-user registry permissions before uninstalling.');
    if not RegDeleteValue(HKCU64, 'Software\RememberMeBeta\Launcher', 'AutostartCommand') then
      Log('Could not remove the inactive login startup ownership marker.');
  end;
  // Unknown startup entries remain untouched; data removal is strictly opt-in.
end;

procedure DeinitializeSetup;
begin
  if InstallMutex <> 0 then CloseHandle(InstallMutex);
end;

procedure DeinitializeUninstall;
begin
  if InstallMutex <> 0 then CloseHandle(InstallMutex);
end;
