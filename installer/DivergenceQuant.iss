; DivergenceQuant Windows installer (Inno Setup 6)
; Per-user install, no admin required; data/ databases are installed with the app and preserved.

#define MyAppName "DivergenceQuant"
#define MyAppExeName "DivergenceQuant.exe"
#define MyAppPublisher "DivergenceQuant"
#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif
#define RepoRoot AddBackslash(SourcePath) + ".."

[Setup]
AppId={{981FA943-9B47-4692-9C31-66250E0BCDD9}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments=DivergenceQuant desktop terminal (self-contained, embedded SQLite)
DefaultDirName={localappdata}\Programs\DivergenceQuant
DefaultGroupName=DivergenceQuant
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#RepoRoot}\dist
OutputBaseFilename=DivergenceQuant-Setup-{#MyAppVersion}-x64
SetupIconFile={#RepoRoot}\assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes
RestartApplications=no
AllowNoIcons=yes
SetupLogging=yes
VersionInfoVersion={#MyAppVersion}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Main program, runtime, frontend, config and docs; data/ handled separately to protect user data
Source: "{#RepoRoot}\dist\DivergenceQuant\*"; DestDir: "{app}"; Excludes: "data\*"; Flags: ignoreversion recursesubdirs createallsubdirs
; First install writes pre-created empty databases; upgrades keep existing databases
Source: "{#RepoRoot}\dist\DivergenceQuant\data\*"; DestDir: "{app}\data"; Flags: onlyifdoesntexist uninsneveruninstall recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\data"

[Icons]
Name: "{group}\DivergenceQuant"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,DivergenceQuant}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\DivergenceQuant"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch DivergenceQuant"; Flags: nowait postinstall skipifsilent
