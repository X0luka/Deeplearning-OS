"""Stage only desktop build sources and verified SDK, never runtime data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]

def stage(destination, distribution, user, instance, ports):
    destination = Path(destination).absolute()
    if destination.exists() or destination.resolve() != destination:
        raise ValueError("Choose a new output folder without symbolic links")
    for value, pattern in [(distribution,r"[A-Za-z0-9][A-Za-z0-9_.-]*"),(user,r"[a-z_][a-z0-9_-]*"),(instance,r"dev-[a-z0-9][a-z0-9-]{0,35}")]:
        if not re.fullmatch(pattern,value):
            raise ValueError("Invalid desktop configuration value")
    repository = str(ROOT/"components/AgentOS-K")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+/components/AgentOS-K",repository):
        raise ValueError("Current desktop launcher requires a Linux checkout path without spaces or non-ASCII characters")
    if len(set(ports))!=3 or any(p<1024 or p>65535 or p in {7777,8601,8602,8777,8701,8702} for p in ports):
        raise ValueError("Choose three distinct unprivileged development ports")
    lock=json.loads((ROOT/"toolchain.json").read_text())["webview2"]
    sdk=ROOT/".local/tools"/("webview2-"+lock["version"])
    manifest=json.loads((sdk/"sdk-files.json").read_text())
    if manifest["version"]!=lock["version"] or manifest["sha256"]!=lock["sha256"]:
        raise ValueError("SDK lock mismatch")
    expected={"Microsoft.Web.WebView2.Core.dll","Microsoft.Web.WebView2.WinForms.dll","WebView2Loader.dll","LICENSE.txt"}
    if set(manifest["files"])!=expected:
        raise ValueError("Unexpected SDK file list")
    for name in expected:
        if hashlib.sha256((sdk/name).read_bytes()).hexdigest()!=manifest["files"][name]:
            raise ValueError("SDK file hash mismatch")
    sources=["scripts/build_desktop.ps1","toolchain.json",
             "components/AgentOS-K/desktop/AgentOS.cs",
             "components/AgentOS-K/desktop/DesktopConfiguration.cs",
             "components/AgentOS-K/desktop/desktop.json",
             "components/AgentOS-K/tests/DesktopConfigurationTest.cs",
             "components/AgentOS-K/tests/DesktopPaneTest.cs"]
    for name in sources:
        path=destination/name;path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/name,path)
    for name in sorted(expected|{"sdk-files.json"}):
        path=destination/"sdk"/name;path.parent.mkdir(exist_ok=True)
        shutil.copy2(sdk/name,path)
    config=dict(instance=instance,distribution=distribution,user=user,repository=repository,
                host_port=str(ports[0]),wiki_port=str(ports[1]),paper_port=str(ports[2]))
    (destination/"desktop.development.json").write_text(json.dumps(config,indent=2)+"\n")
    return {"status":"staged","folder":str(destination),"files":len(sources)+len(expected)+2,"launched":False}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    p.add_argument("--distribution",required=True)
    p.add_argument("--user",required=True)
    p.add_argument("--instance",default="dev-main")
    p.add_argument("--ports",type=int,nargs=3,default=[18777,18778,18779])
    a=p.parse_args()
    print(json.dumps(stage(a.output,a.distribution,a.user,a.instance,a.ports)))
