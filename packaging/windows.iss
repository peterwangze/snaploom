#define AppName "Snaploom"
#define AppExe "Snaploom.exe"

[Setup]
AppId={{653FFABA-0E9F-4804-AB44-41F2ED68A938}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Snaploom
AppPublisherURL=https://github.com/peterwangze/snaploom
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
OutputDir={#ReleaseDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#IconPath}
UninstallDisplayIcon={app}\{#AppExe}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  ProcessQueryLimitedInformation = $1000;
  StillActive = 259;

function OpenProcess(Access: DWORD; InheritHandle: BOOL; ProcessId: DWORD): THandle;
  external 'OpenProcess@kernel32.dll stdcall';

function GetExitCodeProcess(Process: THandle; var ExitCode: DWORD): BOOL;
  external 'GetExitCodeProcess@kernel32.dll stdcall';

function QueryFullProcessImageName(Process: THandle; Flags: DWORD; ImagePath: String;
  var PathLength: DWORD): BOOL;
  external 'QueryFullProcessImageNameW@kernel32.dll stdcall';

function CloseHandle(Handle: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

function SnaploomProfileInUse: Boolean;
var
  ProfileDir, ImagePath, ImageName: String;
  LockLines: TArrayOfString;
  ProcessId: Integer;
  Process: THandle;
  ExitCode, PathLength: DWORD;
begin
  Result := False;
  ProfileDir := GetEnv('SNAPLOOM_DATA_DIR');
  if ProfileDir = '' then
    ProfileDir := ExpandConstant('{localappdata}\Snaploom')
  else if (ProfileDir = '~') or (Copy(ProfileDir, 1, 2) = '~\') or
    (Copy(ProfileDir, 1, 2) = '~/') then
    ProfileDir := GetEnv('USERPROFILE') + Copy(ProfileDir, 2, Length(ProfileDir));

  if not LoadStringsFromLockedFile(AddBackslash(ProfileDir) + 'instance.lock', LockLines) then
    Exit;
  if GetArrayLength(LockLines) = 0 then
    Exit;
  ProcessId := StrToIntDef(Trim(LockLines[0]), 0);
  if ProcessId <= 0 then
    Exit;

  Process := OpenProcess(ProcessQueryLimitedInformation, False, ProcessId);
  if Process = 0 then
    Exit;
  try
    if not GetExitCodeProcess(Process, ExitCode) then
      Exit;
    if ExitCode <> StillActive then
      Exit;
    PathLength := 32768;
    SetLength(ImagePath, PathLength);
    if not QueryFullProcessImageName(Process, 0, ImagePath, PathLength) then
      Exit;
    ImageName := ExtractFileName(Copy(ImagePath, 1, PathLength));
    Result := (CompareText(ImageName, '{#AppExe}') = 0) or
      (CompareText(ImageName, 'Snaploom-cli.exe') = 0);
  finally
    CloseHandle(Process);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if SnaploomProfileInUse then begin
    Log('Snaploom setup blocked by running instance');
    Result := 'Snaploom is still running. Finish or cancel the current capture, then right-click the Snaploom tray icon and choose Quit. Run Setup again.' +
      #13#10#13#10 +
      'Snaploom 仍在运行。请先完成或取消当前截图，然后右键单击 Snaploom 托盘图标并选择「退出」，再运行安装程序。';
  end;
end;
