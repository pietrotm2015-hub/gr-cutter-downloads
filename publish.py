"""Publish APK-only updates. Uses the repository's ephemeral Actions token."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

repo = os.environ["GITHUB_REPOSITORY"]
api = "https://api.github.com/repos/" + repo
token = os.environ["GH_TOKEN"]
incoming = json.loads(Path("incoming.json").read_text())
code, name = int(incoming["versionCode"]), incoming["versionName"]
if code < 1 or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", name):
    raise SystemExit("Invalid version")
apk = base64.b64decode(Path("apk.base64").read_text(), validate=True)
digest = hashlib.sha256(apk).hexdigest()
if len(apk) > 100_000_000 or digest != incoming["sha256"]:
    raise SystemExit("APK integrity mismatch")
Path("verified.apk").write_bytes(apk)
tools = Path(os.environ["ANDROID_HOME"]) / "build-tools/35.0.0"
signature = subprocess.check_output([str(tools / "apksigner"), "verify", "--print-certs", "verified.apk"], text=True)
certificate = "2eb040a4abfbe76034fa6d13b03945501a1fbe5dc426d496b445b5ce307aacc6"
if "Signer #1 certificate SHA-256 digest: " + certificate not in signature:
    raise SystemExit("APK certificate mismatch")
package = subprocess.check_output([str(tools / "aapt"), "dump", "badging", "verified.apk"], text=True)
if f"package: name='com.globalrevolution.grcutter.pilot' versionCode='{code}' versionName='{name}'" not in package:
    raise SystemExit("APK package or version mismatch")

def call(url, data=None, method=None, content_type="application/json"):
    if isinstance(data, dict):
        data = json.dumps(data).encode()
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "Content-Type": content_type, "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=90) as response:
        body = response.read()
        return json.loads(body) if body else None

tag = "android-v" + name
filename = "GR_Cutter_Android_v" + name + ".apk"
manifest = dict(versionCode=code, versionName=name,
    apkUrl=f"https://github.com/{repo}/releases/download/{tag}/{filename}",
    sha256=digest, notes=incoming.get("notes", ""))
try:
    current = call(api + "/contents/latest.json")
    previous = json.loads(base64.b64decode(current["content"]))
    if int(previous["versionCode"]) > code:
        raise SystemExit("Older distribution cannot replace a newer update")
    if int(previous["versionCode"]) == code and previous["sha256"] != digest:
        raise SystemExit("Increment versionCode for changed binaries")
except urllib.error.HTTPError as error:
    if error.code != 404:
        raise
    current = None

try:
    release = call(api + "/releases/tags/" + tag)
except urllib.error.HTTPError as error:
    if error.code != 404:
        raise
    release = call(api + "/releases", dict(tag_name=tag, target_commitish="main",
        name="GR Cutter Android " + name, draft=True, prerelease=False))
if not release["draft"]:
    if digest not in release.get("body", ""):
        raise SystemExit("Published release differs; increment version")
else:
    for asset in release["assets"]:
        if asset["name"] in (filename, "GR_Cutter_Android.apk", "latest.json"):
            call(api + "/releases/assets/" + str(asset["id"]), method="DELETE")
    upload = release["upload_url"].split("{")[0]
    for asset in (filename, "GR_Cutter_Android.apk"):
        call(upload + "?name=" + asset, apk, "POST", "application/vnd.android.package-archive")
    call(upload + "?name=latest.json", json.dumps(manifest).encode(), "POST")
    call(api + "/releases/" + str(release["id"]), dict(draft=False, make_latest="true",
        body=manifest["notes"] + "\n\nInstale por cima para preservar o cache e a fila.\n\nSHA-256: `" + digest + "`"), "PATCH")

# Publish the feed only after its signed APK is available anonymously.
content = base64.b64encode((json.dumps(manifest, indent=2) + "\n").encode()).decode()
change = dict(message="Publish tested Android update " + name, content=content, branch="main")
if current:
    change["sha"] = current["sha"]
call(api + "/contents/latest.json", change, "PUT")
print("Public APK and update feed published: " + name)
