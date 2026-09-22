#define AppName "AutoBJCE"
#define AppVersion "2.2"
#define AppPublisher "AutoBJCE"
#define AppExeName "AutoBJCE.exe"

[Setup]
AppId={{8D058AF9-5C52-4A6C-9C7A-C3CFA42700F2}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=Output
OutputBaseFilename=AutoBJCE2.2-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "..\dist\AutoBJCE\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
function ExeInAppPaths(const ExeName: String): Boolean;
begin
  Result :=
    RegKeyExists(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\' + ExeName) or
    RegKeyExists(HKCU, 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\' + ExeName) or
    RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\' + ExeName);
end;

function IsChromiumBrowserInstalled(): Boolean;
begin
  { 任意 Chromium 内核浏览器都可以：Chrome / Edge / Brave / Vivaldi / Opera / 国产双核 }
  Result :=
    ExeInAppPaths('chrome.exe') or
    ExeInAppPaths('msedge.exe') or
    ExeInAppPaths('brave.exe') or
    ExeInAppPaths('vivaldi.exe') or
    ExeInAppPaths('opera.exe') or
    ExeInAppPaths('chromium.exe') or
    ExeInAppPaths('360chrome.exe') or
    ExeInAppPaths('360se.exe') or
    ExeInAppPaths('QQBrowser.exe') or
    ExeInAppPaths('sogouexplorer.exe') or
    FileExists(ExpandConstant('{pf}\Google\Chrome\Application\chrome.exe')) or
    FileExists(ExpandConstant('{pf32}\Google\Chrome\Application\chrome.exe')) or
    FileExists(ExpandConstant('{localappdata}\Google\Chrome\Application\chrome.exe')) or
    FileExists(ExpandConstant('{pf}\Microsoft\Edge\Application\msedge.exe')) or
    FileExists(ExpandConstant('{pf32}\Microsoft\Edge\Application\msedge.exe')) or
    FileExists(ExpandConstant('{pf}\BraveSoftware\Brave-Browser\Application\brave.exe')) or
    FileExists(ExpandConstant('{pf32}\BraveSoftware\Brave-Browser\Application\brave.exe')) or
    FileExists(ExpandConstant('{localappdata}\BraveSoftware\Brave-Browser\Application\brave.exe')) or
    FileExists(ExpandConstant('{localappdata}\Vivaldi\Application\vivaldi.exe')) or
    FileExists(ExpandConstant('{localappdata}\Programs\Opera\opera.exe'));
end;

procedure InitializeWizard;
begin
  if not IsChromiumBrowserInstalled() then
  begin
    MsgBox(
      '未检测到 Chromium 内核浏览器。'#13#10#13#10 +
      'AutoBJCE 支持 Chrome / Edge / Brave / Vivaldi / Opera 等任意 Chromium 内核浏览器，' +
      '程序会自动探测，也可以在界面中手动指定。'#13#10#13#10 +
      '你可以继续安装，但首次启动前请先安装其中之一。',
      mbInformation,
      MB_OK
    );
  end;
end;
