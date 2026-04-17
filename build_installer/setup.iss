; JV Measurements — Inno Setup Script
; Requires: Inno Setup 6+ (https://jrsoftware.org/isinfo.php)
; Build: Open this file in Inno Setup Compiler and click Build → Compile

#define AppName "JV Measurements"
#define AppVersion "2.5.0"
#define AppPublisher "UFABC — Lucas Polimante"
#define AppURL "https://github.com/Polimante/jv-measurements"

[Setup]
AppId={{8F3A2B1C-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\JV Measurements
DefaultGroupName={#AppName}
AllowNoIcons=no
OutputDir=..\dist
OutputBaseFilename=JV-Measurements-Setup-v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\app\assets\icon.ico
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\*.py";           DestDir: "{app}";           Flags: ignoreversion
Source: "..\app\*";          DestDir: "{app}\app";        Flags: ignoreversion recursesubdirs
Source: "..\requirements.txt"; DestDir: "{app}";          Flags: ignoreversion
Source: "..\version.json";   DestDir: "{app}";            Flags: ignoreversion
Source: "..\CHANGELOG.md";   DestDir: "{app}";            Flags: ignoreversion
Source: "..\LICENSE";        DestDir: "{app}";            Flags: ignoreversion
Source: "..\launcher.bat";   DestDir: "{app}";            Flags: ignoreversion
Source: "..\install.bat";    DestDir: "{app}";            Flags: ignoreversion
Source: "..\docs\*";         DestDir: "{app}\docs";       Flags: ignoreversion recursesubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\JV Measurements";          Filename: "{app}\launcher.bat"; IconFilename: "{app}\app\assets\icon.ico"; WorkingDir: "{app}"
Name: "{group}\Uninstall JV Measurements"; Filename: "{uninstallexe}"
Name: "{commondesktop}\JV Measurements";  Filename: "{app}\launcher.bat"; IconFilename: "{app}\app\assets\icon.ico"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\install.bat"; Description: "Install Python dependencies (recommended)"; Flags: postinstall shellexec skipifsilent

[Code]
function InitializeSetup(): Boolean;
var
  PythonPath: String;
begin
  Result := True;
  if not RegQueryStringValue(HKLM, 'SOFTWARE\Python\PythonCore\3.9\InstallPath',  '', PythonPath) and
     not RegQueryStringValue(HKLM, 'SOFTWARE\Python\PythonCore\3.10\InstallPath', '', PythonPath) and
     not RegQueryStringValue(HKLM, 'SOFTWARE\Python\PythonCore\3.11\InstallPath', '', PythonPath) and
     not RegQueryStringValue(HKLM, 'SOFTWARE\Python\PythonCore\3.12\InstallPath', '', PythonPath) and
     not RegQueryStringValue(HKLM, 'SOFTWARE\Python\PythonCore\3.13\InstallPath', '', PythonPath) then
  begin
    if MsgBox('Python 3.9 or higher is required but was not found on this computer.' + #13#10 +
              #13#10 +
              'Please install Python from python.org before installing JV Measurements.' + #13#10 +
              #13#10 +
              'Do you want to open the Python download page now?',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      ShellExec('open', 'https://www.python.org/downloads/', '', '', SW_SHOW, ewNoWait, PythonPath);
    end;
    Result := False;
  end;
end;
