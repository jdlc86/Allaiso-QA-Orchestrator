#!/usr/bin/env python3
"""Non-destructive workstation readiness probe. Never prints secret values."""
from __future__ import annotations
import json, os, platform, shutil, subprocess
from pathlib import Path


def version(cmd):
    exe = shutil.which(cmd)
    if not exe: return {"present": False}
    for args in ([exe,"--version"],[exe,"version"]):
        try:
            out=subprocess.run(args,capture_output=True,text=True,timeout=5)
            txt=(out.stdout or out.stderr).strip().splitlines()
            if txt: return {"present":True,"path":exe,"version":txt[0][:200]}
        except Exception: pass
    return {"present":True,"path":exe,"version":"unknown"}

report={
 "os":{"system":platform.system(),"release":platform.release(),"machine":platform.machine(),"python":platform.python_version()},
 "tools":{name:version(name) for name in ["git","gh","node","npm","python","python3","opencloud"]},
 "browsers":{name:bool(shutil.which(name)) for name in ["google-chrome","chrome","chromium","chromium-browser","msedge","firefox"]},
 "runner_env":{"github_actions":os.getenv("GITHUB_ACTIONS")=="true","runner_name":os.getenv("RUNNER_NAME"),"runner_os":os.getenv("RUNNER_OS")},
 "notes":["Command discovery is heuristic. OpenCloud may be installed under another executable name; local AI must inspect its actual installation."]
}
Path(".runtime").mkdir(exist_ok=True)
Path(".runtime/node-probe.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps(report,indent=2))
