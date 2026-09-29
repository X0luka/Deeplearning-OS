[CmdletBinding()]
param([string]$Output = "$PSScriptRoot\..\var\desktop-build")
$ErrorActionPreference = 'Stop'
$version = '1.0.3537.50'
$hash = '5EA526BBD728ADDA0DA4D31219267E96460494A427E4894C4E09D9F320F4B9AA'
New-Item -ItemType Directory -Force $Output | Out-Null
$Output = (Resolve-Path $Output).Path
$zip = Join-Path $Output 'webview2.zip'
if (!(Test-Path $zip)) {
    Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$version/microsoft.web.webview2.$version.nupkg" -OutFile $zip
}
if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $hash) { throw 'WebView2 SDK checksum mismatch' }
$sdk = Join-Path $Output 'sdk'
Expand-Archive $zip $sdk -Force
$core = Join-Path $sdk 'lib\net462\Microsoft.Web.WebView2.Core.dll'
$forms = Join-Path $sdk 'lib\net462\Microsoft.Web.WebView2.WinForms.dll'
$compiler = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (!(Test-Path $compiler)) { throw '.NET Framework 4.7.2+ is required.' }
& $compiler /nologo /target:winexe /platform:x64 "/win32icon:$PSScriptRoot\..\desktop\Deeplearning-OS.ico" "/out:$Output\AgentOS.exe" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/reference:$core" "/reference:$forms" "$PSScriptRoot\..\desktop\AgentOS.cs"
if ($LASTEXITCODE) { throw 'Desktop build failed' }
Copy-Item $core,$forms $Output -Force
Copy-Item "$PSScriptRoot\..\desktop\Deeplearning-OS.ico" $Output -Force
Copy-Item "$sdk\runtimes\win-x64\native\WebView2Loader.dll" $Output -Force
Copy-Item "$sdk\LICENSE.txt" "$Output\WebView2-LICENSE.txt" -Force
Copy-Item "$PSScriptRoot\..\desktop\desktop.json" $Output -Force
Add-Type -Path "$Output\Microsoft.Web.WebView2.Core.dll"
try { [Microsoft.Web.WebView2.Core.CoreWebView2Environment]::GetAvailableBrowserVersionString() }
catch { throw '请先安装 Microsoft Edge WebView2 Runtime：https://developer.microsoft.com/microsoft-edge/webview2/' }
Write-Output "Desktop build: $Output"
