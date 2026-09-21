#ifndef AppVersion
  #define AppVersion "0.0.1"
#endif
#ifndef AppPublisher
  #define AppPublisher "Cabyss"
#endif
#ifndef AppExeName
  #define AppExeName "SmartPlayBuddy.exe"
#endif
#ifndef AppNameEN
  #define AppNameEN "SmartPlayBuddy"
#endif
#ifndef AppNameZH
  #define AppNameZH "智玩搭档"
#endif
#define AppGuid "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"

[Setup]
AppId={{{#AppGuid}}
AppName={#AppNameEN}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#AppNameEN}
DefaultGroupName={#AppNameEN}
AllowNoIcons=yes
OutputDir=..\output
OutputBaseFilename={#AppNameEN}_Setup_v{#AppVersion}
SetupIconFile=..\src\smartplaybuddy\ui\resources\icons\logo.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
CreateUninstallRegKey=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinesesimplified.AppDisplayName={#AppNameZH}
english.AppDisplayName={#AppNameEN}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#AppNameEN}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\{#AppNameEN}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\{#AppNameEN}\drivers\*"; DestDir: "{app}\drivers"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\{#AppNameEN}\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{cm:AppDisplayName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{cm:AppDisplayName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppNameEN, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RegWriteStringValue(HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#AppGuid}}_is1',
      'DisplayName', CustomMessage('AppDisplayName'));
end;

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im "{#AppExeName}"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode = 0 then
    Sleep(500);
end;
