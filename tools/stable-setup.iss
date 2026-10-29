#define AppVersion Trim(FileRead(FileOpen(AddBackslash(SourcePath) + "..\VERSION.txt")))
#ifndef BundleDir
  #define BundleDir AddBackslash(SourcePath) + "..\dist\" + AppVersion + "-compat\FilmInvertNative"
#endif
#ifndef AppFileVersion
  #define AppFileVersion AppVersion
#endif
#ifndef ReleaseDate
  #define ReleaseDate "20261028"
#endif
[Setup]
AppId=FilmInvertNative.Stable.{#AppVersion}
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
SetupIconFile=..\assets\film-invert.ico
UninstallDisplayIcon={app}\app\assets\film-invert.ico
OutputDir=..\release
OutputBaseFilename=FilmInvert-Native-{#AppVersion}-setup-win64-{#ReleaseDate}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
Uninstallable=yes
UninstallFilesDir={app}\uninstall
InfoBeforeFile=..\INSTALL-NOTES.txt
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktop}"; GroupDescription: "{cm:Shortcuts}"; Flags: unchecked
[CustomMessages]
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
#include "stable-files.iss"
Source: "installed-layout.json"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\FilmInvert Native {#AppVersion}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-I -m film_invert_launcher"; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\film-invert.ico"
Name: "{group}\{cm:UserGuide}"; Filename: "{app}\软件介绍.html"; Languages: chinesesimplified
Name: "{group}\{cm:UserGuide}"; Filename: "{app}\README.en.html"; Languages: english
Name: "{group}\{cm:UninstallProgram} {#AppVersion}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\FilmInvert Native {#AppVersion}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-I -m film_invert_launcher"; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\film-invert.ico"; Tasks: desktopicon
[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-I -m film_invert_launcher"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram}"; Flags: nowait postinstall skipifsilent
[Code]
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
