param(
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$WebView2Directory
)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$source = Join-Path $repo "components/AgentOS-K"
$compiler = Join-Path $env:WINDIR "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
if (!(Test-Path -LiteralPath $compiler)) { throw "Missing .NET Framework C# compiler" }
if (Test-Path -LiteralPath $OutputDirectory) { throw "Choose a new build output directory; existing directories are not overwritten." }
$sdkManifest = Get-Content -LiteralPath (Join-Path $WebView2Directory "sdk-files.json") -Raw | ConvertFrom-Json
$toolchain = Get-Content -LiteralPath (Join-Path $repo "toolchain.json") -Raw | ConvertFrom-Json
if ($sdkManifest.sha256 -ne $toolchain.webview2.sha256 -or $sdkManifest.version -ne $toolchain.webview2.version) { throw "SDK does not match toolchain lock" }
foreach ($entry in $sdkManifest.files.PSObject.Properties) {
    if ($entry.Name -notin @("Microsoft.Web.WebView2.Core.dll","Microsoft.Web.WebView2.WinForms.dll","WebView2Loader.dll","LICENSE.txt")) { throw "Unexpected SDK filename" }
    if ((Get-FileHash -LiteralPath (Join-Path $WebView2Directory $entry.Name) -Algorithm SHA256).Hash -ne $entry.Value) { throw "SDK file hash mismatch" }
}
$refs = @("Microsoft.Web.WebView2.Core.dll","Microsoft.Web.WebView2.WinForms.dll","WebView2Loader.dll","LICENSE.txt")
foreach ($name in $refs) {
    if ($null -eq $sdkManifest.files.PSObject.Properties[$name]) { throw "Missing SDK hash: $name" }
}
foreach ($name in $refs) {
    if (!(Test-Path -LiteralPath (Join-Path $WebView2Directory $name))) { throw "Missing WebView2 reference: $name" }
}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
$common = @("/nologo","/r:System.dll","/r:System.Core.dll","/r:System.Web.Extensions.dll")
& $compiler @common /target:winexe /platform:x64 /r:System.Drawing.dll /r:System.Windows.Forms.dll "/out:$(Join-Path $OutputDirectory 'Deeplearning-OS-Development.exe')" "/r:$(Join-Path $WebView2Directory $refs[0])" "/r:$(Join-Path $WebView2Directory $refs[1])" (Join-Path $source "desktop/AgentOS.cs") (Join-Path $source "desktop/DesktopConfiguration.cs")
if ($LASTEXITCODE -ne 0) { throw "Desktop compilation failed" }
& $compiler @common /target:exe "/out:$(Join-Path $OutputDirectory 'DesktopConfigurationTest.exe')" (Join-Path $source "desktop/DesktopConfiguration.cs") (Join-Path $source "tests/DesktopConfigurationTest.cs")
if ($LASTEXITCODE -ne 0) { throw "Configuration-test compilation failed" }
& $compiler @common /target:winexe /platform:x64 /main:PaneTest /r:System.Drawing.dll /r:System.Windows.Forms.dll "/out:$(Join-Path $OutputDirectory 'DesktopPaneTest.exe')" "/r:$(Join-Path $WebView2Directory $refs[0])" "/r:$(Join-Path $WebView2Directory $refs[1])" (Join-Path $source "desktop/AgentOS.cs") (Join-Path $source "desktop/DesktopConfiguration.cs") (Join-Path $source "tests/DesktopPaneTest.cs")
if ($LASTEXITCODE -ne 0) { throw "Pane-test compilation failed" }
& $compiler @common /target:winexe /platform:x64 /main:PaneTest /r:System.Drawing.dll /r:System.Windows.Forms.dll "/out:$(Join-Path $OutputDirectory 'DesktopCitationTest.exe')" "/r:$(Join-Path $WebView2Directory $refs[0])" "/r:$(Join-Path $WebView2Directory $refs[1])" (Join-Path $source "desktop/AgentOS.cs") (Join-Path $source "desktop/DesktopConfiguration.cs") (Join-Path $source "tests/DesktopCitationTest.cs")
if ($LASTEXITCODE -ne 0) { throw "Citation-test compilation failed" }
Copy-Item -LiteralPath (Join-Path $source "desktop/desktop.json") -Destination (Join-Path $OutputDirectory "desktop.json")
& (Join-Path $OutputDirectory "DesktopConfigurationTest.exe") $OutputDirectory
if ($LASTEXITCODE -ne 0) { throw "Configuration validation failed" }
foreach ($name in $refs) {
    $destinationName = if ($name -eq "LICENSE.txt") {"LICENSE-WebView2.txt"} else {$name}
    Copy-Item -LiteralPath (Join-Path $WebView2Directory $name) -Destination (Join-Path $OutputDirectory $destinationName)
}
Copy-Item -LiteralPath (Join-Path $WebView2Directory "sdk-files.json") -Destination (Join-Path $OutputDirectory "sdk-files.json")
$configuration = '<?xml version="1.0" encoding="utf-8"?><configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.6.2"/></startup></configuration>'
$configuration | Set-Content -LiteralPath (Join-Path $OutputDirectory "Deeplearning-OS-Development.exe.config") -Encoding utf8
$hashes = Get-ChildItem -LiteralPath $OutputDirectory -File | ForEach-Object { [ordered]@{name=$_.Name;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash} }
[ordered]@{schema=1;architecture="win-x64";sdk_version=$sdkManifest.version;sdk_sha256=$sdkManifest.sha256;configuration_test="passed";desktop_launched=$false;files=$hashes} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $OutputDirectory "build-manifest.json") -Encoding utf8
Write-Output "Desktop SDK files bundled and configuration-tested. Desktop configuration remains disabled; no shell installed or launched; Runtime not installed."
