; JV Measurements â€” Inno Setup Script
; Requires: Inno Setup 6+ (https://jrsoftware.org/isinfo.php)
; Build: Open this file in Inno Setup Compiler and click Build â†’ Compile

#define AppName "JV Measurements"
#define AppVersion "2.5.0"
#define AppPublisher "UFABC â€” Lucas Polimante"
#define AppURL "https://github.com/Polimante/jv-measurements"

[Setup]
AppId={{8F3A2B1C-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName=JV Measurements
AppVersion=2.5.0
AppPublisher=UFABC - Lucas Polimante
AppPublisherURL=https://github.com/Polimante/jv-measurements
AppSupportURL=https://github.com/Polimante/jv-measurements
AppUpdatesURL=https://github.com/Polimante/jv-measurements/releases
DefaultDirName={autopf}\JV Measurements
DefaultGroupName=JV Measurements
AllowNoIcons=no
OutputDir=..\dist
OutputBaseFilename=JV-Measurements-Setup-v2.5.0
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\*.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\version.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\launcher.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\launcher.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\install.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\JV Measurements"; Filename: "wscript.exe"; Parameters: """{app}\launcher.vbs"""; WorkingDir: "{app}"
Name: "{group}\Uninstall JV Measurements"; Filename: "{uninstallexe}"
Name: "{commondesktop}\JV Measurements"; Filename: "wscript.exe"; Parameters: """{app}\launcher.vbs"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\install.bat"; Description: "Install Python dependencies (recommended)"; Flags: postinstall shellexec skipifsilent