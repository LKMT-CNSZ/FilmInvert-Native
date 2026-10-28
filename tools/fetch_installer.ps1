param([string]$Proxy = "", [string]$Destination = "")
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if (!$Destination) { $Destination = Join-Path $taskRoot 'build\release-tools' }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$downloadFile = Join-Path $Destination 'innosetup-6.7.3.exe'
$url = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
if (!(Test-Path -LiteralPath $downloadFile)) {
  if ($Proxy) { Invoke-WebRequest -Uri $url -Proxy $Proxy -OutFile $downloadFile }
  else { Invoke-WebRequest -Uri $url -OutFile $downloadFile }
}
$actualHash = (Get-FileHash -LiteralPath $downloadFile -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732') { throw 'Compiler SHA256 mismatch' }
$signature = Get-AuthenticodeSignature -LiteralPath $downloadFile
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Pyrsys B.V.*') { throw 'Compiler signature not verified' }
$compilerFolder = Join-Path $Destination 'InnoSetup'
$compilerProcess = Start-Process -FilePath $downloadFile -ArgumentList '/PORTABLE=1','/CURRENTUSER','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',('/DIR="' + $compilerFolder + '"') -WindowStyle Hidden -Wait -PassThru
if ($compilerProcess.ExitCode -ne 0) { throw ('Compiler extraction failed: ' + $compilerProcess.ExitCode) }
Write-Output (Join-Path $compilerFolder 'ISCC.exe')
