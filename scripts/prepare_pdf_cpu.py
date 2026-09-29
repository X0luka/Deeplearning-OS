"""Separate CPU-only Docling environment; never mutate component or system environments."""
import argparse
import json
from pathlib import Path
import subprocess
import tomllib
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/".local"
ap=argparse.ArgumentParser()
ap.add_argument("step",choices=["resolve","install"])
ap.add_argument("--proxy", default="")
ap.add_argument("--uv", default=str(LOCAL/"tools/uv"))
ap.add_argument("--offline", action="store_true")
args=ap.parse_args()
if args.proxy:
    parsed=urlsplit(args.proxy)
    if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit("Use an HTTP(S) proxy without embedded credentials")
folder=LOCAL/"pdf-cpu"
folder.mkdir(exist_ok=True)
lock=folder/"requirements.txt"
env={"PATH":"/usr/bin:/bin","HOME":str(LOCAL/"setup-home"),"LANG":"C.UTF-8",
     "UV_CACHE_DIR":str(LOCAL/"uv-cache"),"UV_LINK_MODE":"copy","UV_PYTHON_DOWNLOADS":"never",
     "UV_HTTP_TIMEOUT":"30","UV_HTTP_RETRIES":"1","UV_CONCURRENT_DOWNLOADS":"2",
     "UV_CONCURRENT_BUILDS":"1","UV_CONCURRENT_INSTALLS":"1",
     "NO_PROXY":"localhost,127.0.0.1,::1","PYTHONNOUSERSITE":"1"}
if args.proxy:
    env.update(HTTP_PROXY=args.proxy,HTTPS_PROXY=args.proxy)
uv=args.uv
def run(command,name,timeout=480):
    if args.offline:
        command.append("--offline")
    log=LOCAL/"preparation"/("pdf-cpu-"+name+".log")
    with log.open("w") as output:
        result=subprocess.run(command,cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=timeout)
    print(json.dumps({"step":name,"exit_code":result.returncode,"log":str(log)}),flush=True)
    if result.returncode:
        print(log.read_text()[-2500:])
        raise SystemExit(result.returncode)
if args.step=="resolve":
    packages=tomllib.loads((ROOT/"components/PaperReader/uv.lock").read_text())["package"]
    constraints=folder/"constraints.txt"
    constraints.write_text("\n".join(p["name"]+"=="+p["version"] for p in packages if "registry" in p.get("source",{}) and not p["name"].startswith("nvidia-"))+"\n")
    run([uv,"--no-config","pip","compile",str(ROOT/"components/PaperReader/pyproject.toml"),
        "--extra","pdf-reading","--constraint",str(constraints),"--torch-backend","cpu",
        "--python","/usr/bin/python3","--generate-hashes","--output-file",str(lock)],"resolve",240)
else:
    lock=ROOT/"requirements/pdf-cpu-py312-linux.txt"
    text=lock.read_text()
    if "nvidia-" in text.lower() or "torch==" not in text or "+cpu" not in text:
        raise SystemExit("CPU lock invariant failed")
    interpreter=folder/"venv/bin/python"
    if not interpreter.exists():
        run([uv,"--no-config","venv","--python","/usr/bin/python3",str(folder/"venv")],"venv",30)
    run([uv,"--no-config","pip","sync","--python",str(interpreter),"--torch-backend","cpu",
        "--require-hashes",str(lock)],"install")
