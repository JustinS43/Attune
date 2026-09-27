"""Page files: every script is served as JavaScript and revalidated on reload.

Section 4 - Pages, Engine & Demo. A browser refuses a module sent as text/plain, and one
refused module (modes/focused-layout.mjs on Windows) left the whole lens and demo blank.
"""

from __future__ import annotations

from pathlib import Path

from attune.server.app import PAGE_FOLDERS, WEB_ROOT


def test_every_page_script_is_javascript_and_revalidated(hub_env):
    scripts = [
        p
        for folder in PAGE_FOLDERS
        for p in (WEB_ROOT / folder).rglob("*")
        if p.suffix in (".js", ".mjs")
    ]
    assert any(p.suffix == ".mjs" for p in scripts)  # the case that broke
    for path in scripts:
        url = "/" + Path(path).relative_to(WEB_ROOT).as_posix()
        r = hub_env.client.get(url)
        assert r.status_code == 200, url
        assert "javascript" in r.headers["content-type"], (url, r.headers["content-type"])
        assert r.headers["cache-control"] == "no-cache", url
