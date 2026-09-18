from __future__ import annotations

import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def build_manifest(release: dict) -> dict:
    tag = release["tag_name"]
    if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v?\d+\.\d+\.\d+", tag):
        raise ValueError("Update manifests require a stable release tag")
    assets = [item for item in release["assets"] if item["name"] in ("CNKIBug-GUI.exe", "CNKIBug-GUI-x86_64.AppImage")]
    if not assets:
        raise ValueError("No GUI release assets are available")
    for asset in assets:
        if asset["state"] != "uploaded" or asset["size"] <= 0 or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", asset.get("digest") or ""):
            raise ValueError("A GUI release asset or its digest is not ready")
    return {
        "schema_version": 1,
        "tag_name": tag,
        "published_at": release["published_at"],
        "body": release.get("body") or "",
        "draft": False,
        "prerelease": False,
        "assets": [{key: asset[key] for key in ("name", "state", "size", "digest", "browser_download_url")} for asset in assets],
    }


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    tag = sys.argv[1]

    def api(path: str, data: dict | None = None, method: str | None = None, *, missing_ok: bool = False):
        request = Request(
            f"https://api.github.com/repos/{repository}{path}",
            data=None if data is None else json.dumps(data).encode("utf-8"), method=method,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json", "User-Agent": "CNKIBug-release"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise RuntimeError(f"GitHub request failed: HTTP {error.code}") from None

    release = api(f"/releases/tags/{quote(tag, safe='')}")
    latest = api("/releases/latest")
    if release["id"] != latest["id"]:
        print("The target is not the latest stable release; update manifest unchanged.")
        return
    content = json.dumps(build_manifest(release), ensure_ascii=False, indent=2) + "\n"
    ref = api("/git/ref/heads/updates", missing_ok=True)
    head = ref["object"]["sha"] if ref else None
    tree_data = {"tree": [{"path": "latest.json", "mode": "100644", "type": "blob", "content": content}]}
    if head:
        tree_data["base_tree"] = api(f"/git/commits/{head}")["tree"]["sha"]
    tree = api("/git/trees", tree_data)
    if tree["sha"] != tree_data.get("base_tree"):
        commit = api("/git/commits", {"message": f"Update manifest for {tag}", "tree": tree["sha"],
                                      "parents": [head] if head else []})
        if head:
            api("/git/refs/heads/updates", {"sha": commit["sha"], "force": False}, "PATCH")
        else:
            api("/git/refs", {"ref": "refs/heads/updates", "sha": commit["sha"]})
    print(f"Published updates/latest.json for {tag}")

    cache_key = os.environ.get("JSDMIRROR_API_KEY")
    if not cache_key:
        print("::notice::JSDMIRROR_API_KEY is not configured; CDN cache expiry controls manifest freshness.")
        return
    purge = Request(
        "https://cache.jsdmirror.com/api/v1/purge",
        data=json.dumps({"url": f"https://cdn.jsdmirror.com/gh/{repository}@updates/latest.json",
                         "method": "invalidate"}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": cache_key},
    )
    try:
        with urlopen(purge, timeout=30) as response:
            result = json.load(response)
        if not result.get("success"):
            print("::warning::JSDMirror did not accept the cache refresh request.")
    except (URLError, OSError, ValueError):
        print("::warning::JSDMirror cache refresh failed; the manifest remains published.")


if __name__ == "__main__":
    main()
