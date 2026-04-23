; JV Measurements â€” Inno Setup Script
; Requires: Inno Setup 6+ (https://jrsoftware.org/isinfo.php)
; Build: Open this file in Inno Setup Compiler and click Build â†’ Compile

#define AppName "JV Measurements"
#define AppVersion "2.6.0"
#define AppPublisher "UFABC â€” Lucas Polimante"
#define AppURL "https://github.com/Polimante/jv-measurements"

[Setup]
AppId={{8F3A2B1C-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName=JV Measurements
AppVersion=2.6.0
AppPublisher=UFABC - Lucas Polimante
AppPublisherURL=https://github.com/Polimante/jv-measurements
DefaultDirName={autopf}\JV Measurements
DefaultGroupName=JV Measurements
OutputDir=..\dist
OutputBaseFilename=JV-Measurements-Setup-v2.6.0
SetupIconFile=..\assets\icon.ico
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
Source: "..\assets\icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\version.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\launcher.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\launcher.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\install.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\JV Measurements"; Filename: "{app}\launcher.vbs"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Uninstall JV Measurements"; Filename: "{uninstallexe}"
Name: "{commondesktop}\JV Measurements"; Filename: "{app}\launcher.vbs"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\install.bat"; Description: "Install Python dependencies"; Flags: postinstall shellexec skipifsilent

[Code]
function PythonInstalled(): Boolean;
var
  Path: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\Python\PythonCore\3.9\InstallPath', '', Path) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Python\PythonCore\3.10\InstallPath', '', Path) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Python\PythonCore\3.11\InstallPath', '', Path) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Python\PythonCore\3.12\InstallPath', '', Path) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Python\PythonCore\3.9\InstallPath', '', Path) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Python\PythonCore\3.10\InstallPath', '', Path) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Python\PythonCore\3.11\InstallPath', '', Path) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Python\PythonCore\3.12\InstallPath', '', Path);
end;

procedure InstallPython();
var
  ResultCode: Integer;
  Msg: String;
begin
  Msg := 'Python was not found. It will be downloaded and installed automatically. This requires internet and may take a few minutes. Click OK to continue.';
  MsgBox(Msg, mbInformation, MB_OK);

  if not ShellExec('', 'powershell.exe',
    '-Command "Invoke-WebRequest -Uri ''https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe'' -OutFile ($env:TEMP+''\python_inst.exe''); Start-Process ($env:TEMP+''\python_inst.exe'') -ArgumentList ''/quiet InstallAllUsers=0 PrependPath=1 Include_test=0'' -Wait"',
    '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('Failed to install Python automatically. Please install Python 3.11 from python.org manually, then run install.bat in the installation folder.', mbError, MB_OK);
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not PythonInstalled() then
  begin
    InstallPython();
    if not PythonInstalled() then
      MsgBox('Python could not be verified. If the software fails to open, install Python 3.11 from python.org and run install.bat in the installation folder.', mbError, MB_OK);
  end;
end;