; Inno Setup script. Built in CI from the PyInstaller folder in dist\MacroTool.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E0B7C1A-6F4E-4C4B-9C2A-3D5F2B7A9E11}
AppName=Macro Tool
AppVersion={#AppVersion}
AppPublisher=YatoVoid
AppPublisherURL=https://github.com/YatoVoid/Macro-Tool
AppSupportURL=https://github.com/YatoVoid/Macro-Tool/issues
DefaultDirName={autopf}\Macro Tool
DefaultGroupName=Macro Tool
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\out
OutputBaseFilename=MacroTool-Setup
SetupIconFile=..\..\build\icon.ico
UninstallDisplayIcon={app}\MacroTool.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\..\dist\MacroTool\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\..\dist\macro-tool-cli.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Macro Tool"; Filename: "{app}\MacroTool.exe"
Name: "{autodesktop}\Macro Tool"; Filename: "{app}\MacroTool.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MacroTool.exe"; Description: "Start Macro Tool"; Flags: nowait postinstall skipifsilent
