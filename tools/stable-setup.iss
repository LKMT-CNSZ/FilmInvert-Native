#define AppVersion Trim(FileRead(FileOpen(AddBackslash(SourcePath) + "..\VERSION.txt")))
#ifndef ProductAppId
  #define ProductAppId "FilmInvertNative.Stable." + AppVersion
#endif
#ifndef BundleDir
  #define BundleDir AddBackslash(SourcePath) + "..\dist\" + AppVersion + "-compat\FilmInvertNative"
#endif
#ifndef AppFileVersion
  #define AppFileVersion AppVersion
#endif
#ifndef OutputSuffix
  #define OutputSuffix ""
#endif
#ifndef ReleaseDate
  #define ReleaseDate "20261028"
#endif
[Setup]
AppId={#ProductAppId}
AppName=FilmInvert Native
AppVersion={#AppVersion}
AppVerName=FilmInvert Native {#AppVersion}
VersionInfoVersion={#AppFileVersion}
VersionInfoDescription=FilmInvert Native Windows x64 Setup
DefaultDirName={localappdata}\Programs\FilmInvert Native\{#AppVersion}
DefaultGroupName=FilmInvert Native {#AppVersion}
UsePreviousAppDir=no
UsePreviousGroup=no
UsePreviousTasks=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableProgramGroupPage=yes
AllowNoIcons=yes
SetupIconFile=..\assets\filminvert-native-856209a72631.ico
UninstallDisplayIcon={app}\app\assets\filminvert-native-856209a72631.ico
OutputDir=..\release
OutputBaseFilename=FilmInvert-Native-{#AppVersion}-setup-win64-{#ReleaseDate}{#OutputSuffix}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern dark
WizardBackColor=#171717
DisableWelcomePage=no
DisableDirPage=no
DisableReadyPage=yes
WizardResizable=yes
CloseApplications=no
RestartApplications=no
Uninstallable=yes
UninstallFilesDir={app}\uninstall

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktop}"; GroupDescription: "{cm:Shortcuts}"; Flags: unchecked
Name: "startmenu"; Description: "{cm:AddStartMenu}"
Name: "cleanupold"; Description: "{cm:RemoveOld}"
[CustomMessages]
english.AddStartMenu=Add to Start Menu
chinesesimplified.AddStartMenu=添加到开始菜单
english.RemoveOld=Remove older registered versions after successful installation (preserve user data)
chinesesimplified.RemoveOld=新版本安装验证成功后移除已登记旧版本（保留用户数据）
english.CreateDesktop=Create a desktop shortcut
english.Shortcuts=Shortcuts:
english.UserGuide=User guide
english.UninstallProgram=Uninstall FilmInvert Native
english.LaunchProgram=Launch FilmInvert Native
english.EmptyDirectory=Choose an empty folder. Existing installations and user files are retained. Back up your data and install into a new version folder.
chinesesimplified.CreateDesktop=创建桌面快捷方式
chinesesimplified.Shortcuts=快捷方式：
chinesesimplified.UserGuide=软件介绍
chinesesimplified.UninstallProgram=卸载 FilmInvert Native
chinesesimplified.LaunchProgram=启动 FilmInvert Native
chinesesimplified.EmptyDirectory=请选择空目录。为保留旧安装及用户文件，请备份数据后安装到新的版本目录。
[Files]
Source: "installer-assets\*.bmp"; Flags: dontcopy
#include "stable-files.iss"
Source: "installed-layout.json"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\FilmInvert Native {#AppVersion}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-I -m film_invert_launcher"; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\filminvert-native-856209a72631.ico"; Tasks: startmenu
Name: "{group}\{cm:UserGuide}"; Filename: "{app}\软件介绍.html"; Languages: chinesesimplified; Tasks: startmenu
Name: "{group}\{cm:UserGuide}"; Filename: "{app}\README.en.html"; Languages: english; Tasks: startmenu
Name: "{group}\{cm:UninstallProgram} {#AppVersion}"; Filename: "{uninstallexe}"; Tasks: startmenu
Name: "{autodesktop}\FilmInvert Native {#AppVersion}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-I -m film_invert_launcher"; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\filminvert-native-856209a72631.ico"; Tasks: desktopicon
[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-I -m film_invert_launcher"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram}"; Flags: nowait postinstall skipifsilent; Check: IsVerified
[UninstallDelete]
Type: files; Name: "{autodesktop}\FilmInvert Native {#AppVersion}.lnk"
[Code]
#include "installer-ui.iss"
function NextButtonClick(CurPageID: Integer): Boolean;
var Names: TFindRec;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and (not WizardSilent) then begin
    if FindFirst(AddBackslash(WizardDirValue) + '*', Names) then begin
      try
        repeat
          if (Names.Name <> '.') and (Names.Name <> '..') then begin
            MsgBox(CustomMessage('EmptyDirectory'), mbError, MB_OK);
            Result := False;
            Exit;
          end;
        until not FindNext(Names);
      finally
        FindClose(Names);
      end;
    end;
  end;
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
var Names: TFindRec;
begin
  Result := '';
  { Also enforce for silent installs, which skip the directory wizard page. }
  if FindFirst(AddBackslash(ExpandConstant('{app}')) + '*', Names) then begin
    try
      repeat
        if (Names.Name <> '.') and (Names.Name <> '..') then begin
          Result := CustomMessage('EmptyDirectory');
          Exit;
        end;
      until not FindNext(Names);
    finally
      FindClose(Names);
    end;
  end;
end;
