[Setup]
; Fixed GUID so future version upgrades are recognized as the same app in
; Add/Remove Programs instead of appearing as a separate, duplicate entry.
; Generate your own once with Tools > Generate GUID in the Inno Setup IDE
; (or any GUID generator) and keep this exact value for all future builds.
AppId={{A1E2C9F0-6B3D-4C7A-9A1E-2C9F06B3D4C7}
AppName=AlQemma
AppVersion=1.0.0
DefaultGroupName=AlQemma
Compression=lzma2
SolidCompression=yes
OutputBaseFilename=AlQemma_Setup
DisableProgramGroupPage=no
UninstallDisplayIcon={app}\AlQemma.exe
; Shows an "I accept the agreement" / "I do not accept" page before
; Select Destination Location. Setup won't proceed unless accepted.
LicenseFile=license.txt
; Prevents install/reinstall from silently failing to overwrite AlQemma.exe
; because the app (or its server) is still running.
AppMutex=AlQemmaSingleInstance
ShowComponentSizes=yes
; Installs to the current user's own LocalAppData\Programs, NOT Program
; Files. The app writes its database/logs/product images directly next
; to AlQemma.exe (see config.py's BASE_DIR), and Program Files blocks
; writes from a standard (non-admin) user - that mismatch is what was
; causing the exe to crash silently on first launch after install
; (console=False in alqemma.spec means there's no window to show the
; error, and AlQemma.bat has no `pause` after a crash, so the terminal
; just flashes and closes). LocalAppData is always writable by the
; installing user, and doesn't need admin rights to install into either.
DefaultDirName={localappdata}\Programs\AlQemma
PrivilegesRequired=lowest

[Types]
; Single type = the components list always shows all three items together,
; nothing for the user to pick between at the "setup type" step.
Name: "full"; Description: "Full installation"

[Components]
; Flags: fixed => checkbox is shown checked AND disabled/grayed out -
; the user sees exactly what's being installed but can't uncheck any of
; them. This is the mechanism you asked for.
Name: "main";       Description: "AlQemma Program";                Types: full; Flags: fixed
Name: "vcredist";    Description: "Visual C++ Redistributable";     Types: full; Flags: fixed

[Files]
; Core app, including the bundled Noto Naskh Arabic font used for PDF
; generation (app/services/pdf_utils.py) - no external browser/runtime
; to exclude or protect here, a single Source line covers everything.
Source: "Program\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs; Components: main

; VC++ Redistributable, bundled and run silently during setup, then removed.
; Sits at the project root alongside AlQemma.iss.
Source: "VC_redist.x86.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Components: vcredist

[Icons]
Name: "{group}\AlQemma"; Filename: "{app}\AlQemma.bat"; IconFilename: "{app}\AlQemma.exe"
Name: "{userdesktop}\AlQemma"; Filename: "{app}\AlQemma.bat"; IconFilename: "{app}\AlQemma.exe"

[Run]
; /install /quiet /norestart is the standard silent switch set for the
; Visual C++ Redistributable bootstrapper (vcredist doesn't use vscode-style
; /verysilent flags).
Filename: "{tmp}\VC_redist.x86.exe"; Parameters: "/install /quiet /norestart"; \
    Components: vcredist; Flags: waituntilterminated; StatusMsg: "Installing Visual C++ Redistributable..."

Filename: "{app}\AlQemma.bat"; Description: "Launch AlQemma"; Flags: postinstall skipifsilent
