#define AppName "Kakomon Trainer"
#define AppId "{{8F92AF4A-742E-4A56-A9B8-75D14FF34392}"
#define AppVersion GetEnv("APP_VERSION")
#define OutputName "KakomonTrainer-Setup"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\Kakomon Trainer
DefaultGroupName={#AppName}
SetupIconFile=..\desktop\KakomonTrainer\Assets\app.ico
UninstallDisplayIcon={app}\KakomonTrainer.exe
OutputDir=..\release
OutputBaseFilename={#OutputName}
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=no

[Files]
Source: "..\artifacts\desktop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\KakomonTrainer.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\KakomonTrainer.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作成する"; Flags: unchecked

[Run]
Filename: "{app}\KakomonTrainer.exe"; Description: "{#AppName} を起動する"; Flags: nowait postinstall skipifsilent
