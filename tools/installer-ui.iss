var
  Side: TPanel;
  Brand, StageTitle, FooterStatus, TaskStatus, ReadyMark: TNewStaticText;
  Steps: array[0..4] of TNewStaticText;
  CleanupVerified: Boolean;
  OldRetained: Boolean;
  AvailableSpace: TNewStaticText;
  FinishedShortcut: TNewCheckBox;
  ProgressLine: TBitmapImage;

function L(English, Chinese: String): String;
begin
  if ActiveLanguage='chinesesimplified' then Result:=Chinese else Result:=English;
end;

function TextOn(Parent: TWinControl; X,Y,W,H: Integer; Value: String; Size: Integer; Color: TColor): TNewStaticText;
begin
  Result := TNewStaticText.Create(WizardForm);
  Result.Parent := Parent;
  Result.SetBounds(ScaleX(X),ScaleY(Y),ScaleX(W),ScaleY(H));
  Result.AutoSize := False;
  Result.Caption := Value;
  Result.Font.Name := 'Segoe UI'; Result.Font.Size := Size;
  Result.Font.Color := Color; Result.WordWrap := True;
end;

procedure PictureOn(Parent: TWinControl; Name: String; X,Y,W,H: Integer);
var Img: TBitmapImage;
begin
  ExtractTemporaryFile(Name);
  Img := TBitmapImage.Create(WizardForm); Img.Parent := Parent;
  Img.SetBounds(ScaleX(X),ScaleY(Y),ScaleX(W),ScaleY(H));
  Img.Stretch := True; Img.Bitmap.LoadFromFile(ExpandConstant('{tmp}\')+Name);
end;

procedure InitializeWizard;
var I: Integer;
begin
  WizardForm.ClientWidth := ScaleX(900); WizardForm.ClientHeight := ScaleY(560);
  WizardForm.Font.Name := 'Segoe UI'; WizardForm.Font.Size := 9;
  WizardForm.MainPanel.Visible := False; WizardForm.Bevel.Visible := False;
  WizardForm.OuterNotebook.SetBounds(ScaleX(196),0,ScaleX(704),ScaleY(496));
  WizardForm.InnerNotebook.SetBounds(ScaleX(36),ScaleY(105),ScaleX(632),ScaleY(370));
  WizardForm.BackButton.SetBounds(ScaleX(604),ScaleY(512),ScaleX(90),ScaleY(34));
  WizardForm.NextButton.SetBounds(ScaleX(702),ScaleY(512),ScaleX(112),ScaleY(34));
  WizardForm.CancelButton.SetBounds(ScaleX(818),ScaleY(512),ScaleX(72),ScaleY(34));
  WizardForm.WizardBitmapImage.Visible := False; WizardForm.WizardBitmapImage2.Visible := False;
  Side := TPanel.Create(WizardForm); Side.Parent := WizardForm;
  Side.SetBounds(0,0,ScaleX(196),ScaleY(496)); Side.BevelOuter := bvNone; Side.Color := $202020;
  PictureOn(Side,'logo.bmp',22,20,56,56);
  Brand := TextOn(Side,24,85,160,60,'FilmInvert'+#13#10+'Native',16,$E1E6E8);
  TextOn(Side,24,158,155,45,L('FILM NEGATIVE', '胶片负片')+#13#10+L('PROCESSING', '处理工作流'),8,$8D9192);
  for I:=0 to 4 do Steps[I] := TextOn(Side,24,220+I*32,155,25,'',9,$8D9192);
  TextOn(Side,24,436,170,40,L('VERSION {#AppVersion}', '版本 {#AppVersion}')+#13#10+'WINDOWS 10 / 11   x64',8,$8D9192);
  StageTitle := TextOn(WizardForm.InnerPage,36,30,632,65,'',21,$E1E6E8);
  FooterStatus := TextOn(WizardForm,24,520,540,25,L('READY TO INSTALL', '准备安装'),8,$8D9192);
  WizardForm.WelcomeLabel1.SetBounds(ScaleX(36),ScaleY(30),ScaleX(632),ScaleY(52));
  WizardForm.WelcomeLabel1.Font.Size:=20; WizardForm.WelcomeLabel1.Caption:=L('Welcome to FilmInvert Native', '欢迎使用 FilmInvert Native');
  WizardForm.WelcomeLabel2.SetBounds(ScaleX(36),ScaleY(93),ScaleX(632),ScaleY(95));
  WizardForm.WelcomeLabel2.Caption:=L('A better way to process film negatives.', '为胶片负片打造的专业处理流程。')+#13#10+#13#10+
    L('FilmInvert Native provides a dedicated workflow for removing the orange mask and converting scanned film negatives into positive images.', 'FilmInvert Native 专注于胶片数字化，提供从橙色色罩去除、负片反转到色彩还原的完整工作流，将扫描后的胶片负片转换为正片。');
  PictureOn(WizardForm.WelcomePage,'negative.bmp',36,202,200,148);
  PictureOn(WizardForm.WelcomePage,'mask.bmp',248,202,200,148);
  PictureOn(WizardForm.WelcomePage,'positive.bmp',460,202,200,148);
  TextOn(WizardForm.WelcomePage,36,365,200,24,L('NEGATIVE  →', '负片  →'),9,$E1E6E8);
  TextOn(WizardForm.WelcomePage,248,365,200,24,L('MASK REMOVAL  →', '去除色罩  →'),9,$4A7DC7);
  TextOn(WizardForm.WelcomePage,460,365,200,24,L('POSITIVE', '正片'),9,$E1E6E8);
  TextOn(WizardForm.WelcomePage,36,428,632,30,L('SYNTHETIC REFERENCE   /   FRAME 001   /   MASK REMOVAL DEMO', '合成流程示例   /   第 001 帧   /   去色罩与反相演示'),8,$8D9192);
  WizardForm.FinishedHeadingLabel.SetBounds(ScaleX(36),ScaleY(68),ScaleX(632),ScaleY(60));
  WizardForm.FinishedHeadingLabel.Font.Size:=21;
  WizardForm.FinishedLabel.SetBounds(ScaleX(36),ScaleY(140),ScaleX(632),ScaleY(150));
  WizardForm.RunList.SetBounds(ScaleX(36),ScaleY(320),ScaleX(632),ScaleY(100));
  FinishedShortcut:=TNewCheckBox.Create(WizardForm); FinishedShortcut.Parent:=WizardForm.FinishedPage;
  FinishedShortcut.SetBounds(ScaleX(36),ScaleY(420),ScaleX(632),ScaleY(24));
  FinishedShortcut.Caption:=L('Create Desktop Shortcut', '创建桌面快捷方式');
  AvailableSpace:=TextOn(WizardForm.SelectDirPage,0,290,632,26,'',9,$8D9192);
  ReadyMark:=TextOn(WizardForm.FinishedPage,36,28,632,26,L('READY / VERIFIED INSTALLATION', '准备就绪 / 安装已验证'),10,$4A7DC7);
  WizardForm.ProgressGauge.SetBounds(0,ScaleY(80),ScaleX(632),ScaleY(3));
  ProgressLine:=TBitmapImage.Create(WizardForm); ProgressLine.Parent:=WizardForm.InstallingPage;
  ProgressLine.SetBounds(0,ScaleY(80),ScaleX(632),ScaleY(3));
  ProgressLine.Bitmap.Width:=ScaleX(632); ProgressLine.Bitmap.Height:=ScaleY(3);
  WizardForm.StatusLabel.SetBounds(0,0,ScaleX(632),ScaleY(30));
  WizardForm.FilenameLabel.SetBounds(0,ScaleY(36),ScaleX(632),ScaleY(28));
  TaskStatus := TextOn(WizardForm.InstallingPage,0,115,632,230,
    L('APPLICATION FILES', '应用程序文件')+#13#10+#13#10+L('CONFIGURATION', '配置')+#13#10+#13#10+L('SHORTCUTS', '快捷方式')+#13#10+#13#10+L('VERIFY INSTALLATION', '验证安装')+#13#10+#13#10+L('REMOVE OLD VERSION', '移除旧版本'),10,$8D9192);
end;

procedure CurPageChanged(CurPageID: Integer);
var I,Index: Integer; Names: array[0..4] of String; FreeMB,TotalMB: Cardinal;
begin
  Names[0]:=L('Welcome', '欢迎'); Names[1]:=L('Location', '安装位置'); Names[2]:=L('Options', '安装选项'); Names[3]:=L('Installation', '安装过程'); Names[4]:=L('Complete', '安装完成');
  Index:=0;
  case CurPageID of
    wpSelectDir: begin
      Index:=1; StageTitle.Caption:=L('Choose Installation Location', '选择安装位置');
      if GetSpaceOnDisk(AddBackslash(ExtractFileDrive(WizardDirValue)),True,FreeMB,TotalMB) then AvailableSpace.Caption:=L('Available on destination drive: ', '目标磁盘可用空间：')+IntToStr(FreeMB)+' MB';
    end;
    wpSelectTasks: begin Index:=2; StageTitle.Caption:=L('Installation Options', '安装选项'); end;
    wpPreparing,wpInstalling: begin Index:=3; StageTitle.Caption:=L('Installing FilmInvert Native', '正在安装 FilmInvert Native'); end;
    wpFinished: Index:=4;
  end;
  for I:=0 to 4 do begin
    Steps[I].Caption:=IntToStr(I+1)+'   '+Names[I]; Steps[I].Font.Color:=$8D9192;
    if I=Index then Steps[I].Font.Color:=$4A7DC7;
  end;
  if CurPageID=wpFinished then begin
    ReadyMark.Visible:=CleanupVerified;
    FinishedShortcut.Visible:=CleanupVerified and not WizardIsTaskSelected('desktopicon');
    if CleanupVerified then begin
      WizardForm.FinishedHeadingLabel.Caption:=L('Installation Complete', '安装完成');
      WizardForm.FinishedLabel.Caption:=L('FilmInvert Native is ready.', 'FilmInvert Native 已准备就绪。')+#13#10+#13#10+
        L('Your projects, photos, exports and user settings are preserved.', '您的工程、照片、导出文件和用户设置均已保留。')+#13#10+
        L('Upgrade details: ', '升级详情：')+ExpandConstant('{app}\upgrade-result.json');
      FooterStatus.Caption:=L('INSTALLATION VERIFIED', '安装验证通过');
      if OldRetained then begin
        WizardForm.FinishedLabel.Caption:=WizardForm.FinishedLabel.Caption+#13#10+#13#10+L('An older version was retained (running or cleanup failed). Close it and use Windows Apps settings to uninstall it later.', '旧版本仍在运行或清理未完成，因此已保留。请关闭旧版本后，在 Windows 应用设置中手动卸载。');
        FooterStatus.Caption:=L('NEW VERSION READY / OLD VERSION RETAINED', '新版本已就绪 / 旧版本已保留');
      end;
    end else begin
      WizardForm.FinishedHeadingLabel.Caption:=L('Verification needs attention', '安装验证未通过');
      WizardForm.FinishedLabel.Caption:=L('The new installation could not be verified. Older versions were not removed. See upgrade-result.json in the installation folder.', '新版本安装未能通过验证，旧版本未被移除。请查看安装目录中的 upgrade-result.json 了解详情。');
      WizardForm.RunList.Enabled:=False;
    end;
  end;
end;

function IsVerified: Boolean;
begin
  Result:=CleanupVerified;
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
begin
  if MaxProgress>0 then begin
    FooterStatus.Caption:=L('COPYING FILES / ', '正在复制文件 / ')+IntToStr(CurProgress*100 div MaxProgress)+'%';
    ProgressLine.Bitmap.Canvas.Brush.Color:=$393939; ProgressLine.Bitmap.Canvas.Pen.Color:=$393939;
    ProgressLine.Bitmap.Canvas.Rectangle(0,0,ProgressLine.Bitmap.Width,ProgressLine.Bitmap.Height);
    ProgressLine.Bitmap.Canvas.Brush.Color:=$4A7DC7; ProgressLine.Bitmap.Canvas.Pen.Color:=$4A7DC7;
    ProgressLine.Bitmap.Canvas.Rectangle(0,0,ProgressLine.Bitmap.Width*CurProgress div MaxProgress,ProgressLine.Bitmap.Height);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer; Args,Scope: String; Report: AnsiString;
begin
  if (CurStep=ssDone) and CleanupVerified and FinishedShortcut.Visible and FinishedShortcut.Checked then
    CreateShellLink(ExpandConstant('{autodesktop}\FilmInvert Native {#AppVersion}.lnk'),'FilmInvert Native',ExpandConstant('{app}\runtime\pythonw.exe'),'-I -m film_invert_launcher',ExpandConstant('{app}'),ExpandConstant('{app}\app\assets\filminvert-native-856209a72631.ico'),0,SW_SHOWNORMAL);
  if CurStep=ssPostInstall then begin
    WizardForm.CancelButton.Enabled:=False; FooterStatus.Caption:=L('VERIFYING NEW INSTALLATION', '正在验证新版本安装');
    TaskStatus.Caption:=L('APPLICATION FILES / COMPLETE', '应用程序文件 / 已完成')+#13#10+#13#10+L('CONFIGURATION / COMPLETE', '配置 / 已完成')+#13#10+#13#10+L('SHORTCUTS / COMPLETE', '快捷方式 / 已完成')+#13#10+#13#10+L('VERIFY INSTALLATION / IN PROGRESS', '验证安装 / 正在进行')+#13#10+#13#10+L('REMOVE OLD VERSION / AFTER VERIFICATION', '移除旧版本 / 验证通过后执行');
    Args:='-I -m installer_upgrade --result "'+ExpandConstant('{app}\upgrade-result.json')+'"';
    if WizardIsTaskSelected('cleanupold') then Args:=Args+' --remove';
    Scope:=ExpandConstant('{param:UPGRADESCOPE|}');
    if Scope<>'' then Args:=Args+' --scope-root "'+Scope+'"';
    CleanupVerified:=Exec(ExpandConstant('{app}\runtime\python.exe'),Args,ExpandConstant('{app}'),SW_HIDE,ewWaitUntilTerminated,Code) and (Code=0);
    if CleanupVerified then TaskStatus.Caption:=L('APPLICATION FILES / COMPLETE', '应用程序文件 / 已完成')+#13#10+#13#10+L('CONFIGURATION / COMPLETE', '配置 / 已完成')+#13#10+#13#10+L('SHORTCUTS / COMPLETE', '快捷方式 / 已完成')+#13#10+#13#10+L('VERIFY INSTALLATION / COMPLETE', '验证安装 / 已完成')+#13#10+#13#10+L('OLD VERSION / SEE UPGRADE REPORT', '旧版本处理 / 请查看升级报告');
    if LoadStringFromFile(ExpandConstant('{app}\upgrade-result.json'),Report) then begin
      Log(String(Report)); OldRetained:=Pos('"reason":',String(Report))>0;
    end;
  end;
end;
