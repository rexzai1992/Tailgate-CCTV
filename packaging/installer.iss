; Inno Setup script for the CCTV Tailgate Windows installer.
;
; Prerequisites on the BUILD machine only:
;   1. Build the app first:  build_windows.bat   (produces dist\CCTV-Tailgate\)
;   2. Install Inno Setup 6:  https://jrsoftware.org/isdl.php
;   3. Download the VC++ 2015-2022 x64 redistributable and save it as
;      packaging\redist\VC_redist.x64.exe   (https://aka.ms/vs/17/release/vc_redist.x64.exe)
;   4. Compile this script (open in Inno Setup and press F9, or run iscc).
;
; Output:  packaging\Output\CCTV-Tailgate-Setup.exe
;
; The resulting Setup.exe is fully self-contained: the TARGET PC needs no
; Python, no package manager, and no internet connection.

#define AppName "CCTV Tailgate"
#define AppVersion "2.0"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=CCTV Tailgate
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=Output
OutputBaseFilename=CCTV-Tailgate-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Admin lets us install the VC++ runtime and write to Program Files. The app's
; own data is stored under %LOCALAPPDATA%, so it still runs fine for any user.
PrivilegesRequired=admin

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; The entire PyInstaller output folder (bundles Python + all libraries + model).
Source: "..\dist\CCTV-Tailgate\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Visual C++ runtime, installed only if missing. Optional at build time.
Source: "redist\VC_redist.x64.exe"; DestDir: "{tmp}"; Check: VCRedistNeeded; Flags: skipifsourcedoesntexist deleteafterinstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\CCTV-Tailgate.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\CCTV-Tailgate.exe"; Tasks: desktopicon

[Run]
Filename: "{tmp}\VC_redist.x64.exe"; Parameters: "/install /quiet /norestart"; \
  StatusMsg: "Installing the Visual C++ runtime..."; Check: VCRedistNeeded
Filename: "{app}\CCTV-Tailgate.exe"; Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[Code]
function VCRedistNeeded: Boolean;
var
  Installed: Cardinal;
begin
  // Registry marker for the VC++ 2015-2022 x64 runtime.
  Result := not RegQueryDWordValue(
    HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
    'Installed', Installed) or (Installed <> 1);
end;
