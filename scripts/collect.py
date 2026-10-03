"""公式ページを取得し、前回から変わったページだけを返す（仕様書 §6・§7）。

- robots.txt で禁止されたページは取得しない
- 同じドメインへのアクセスは min_interval_sec 以上あける
- 本文は work/pages/<source_id>.txt に置く（git 管理外）。ハッシュだけ data/snapshots.json に残す
"""
from __future__ import annotations

import hashlib
import re
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from common import DATA_DIR, WORK_DIR, domain_of, http_session, load_json, now_jst, save_json

REMOVE_TAGS = ["script", "style", "noscript", "svg", "iframe", "template"]


@dataclass
class Page:
    source: dict
    text: str
    changed: bool
    ok: bool
    error: str = ""
    digest: str = ""
    links: list = None  # ページ内のリンク（絶対URL）。詳細ページをたどるのに使う


def html_to_text(html: str, source: dict | None = None) -> str:
    soup = BeautifulSoup(html, "lxml")
    # Only explicit, source-specific selectors remove related-campaign panels.
    # Do not remove generic sections which may contain eligibility or dates.
    for selector in (source or {}).get("exclude_selectors", []):
        for node in soup.select(selector):
            node.decompose()
    for t in soup(REMOVE_TAGS):
        t.decompose()
    for img in soup.find_all("img"):  # ロゴ画像だけで書かれた決済名などを拾う
        alt = (img.get("alt") or "").strip()
        img.replace_with(f" {alt} " if alt else "")
    selector = (source or {}).get("content_selector")
    root = soup.select_one(selector) if selector else None
    if selector and root is None:
        raise RuntimeError(f"本文セレクタが見つからない: {selector}")
    root = root or soup.find("main") or soup.body or soup
    lines = [ln.strip() for ln in root.get_text("\n").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """ページ内の <a href> を (リンクの文字, 絶対URL) にして、出てきた順に重複なしで返す（#以降は落とす）。"""
    soup = BeautifulSoup(html, "lxml")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        url = urldefrag(urljoin(base_url, a["href"].strip()))[0]
        if url.startswith("https://") and url not in seen:
            seen.add(url)
            label = " ".join(a.get_text(" ").split())
            if not label and a.find("img") is not None:
                label = (a.find("img").get("alt") or "").strip()
            out.append((label[:60], url))
    return out


class Fetcher:
    def __init__(self, config: dict):
        f = config.get("fetch", {})
        self.ua = f.get("user_agent", "PoikatsuKouryakuBot/1.0")
        self.timeout = int(f.get("timeout_sec", 20))
        self.retries = int(f.get("retries", 3))
        self.interval = float(f.get("min_interval_sec", 3))
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._pw = None
        self.config = config
        self.last_url = None

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = time.monotonic() - last
            if gap < self.interval:
                time.sleep(self.interval - gap)
        self._last[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._wait(p.netloc)
                r = http_session(base, self.config).get(base + "/robots.txt", headers={"User-Agent": self.ua}, timeout=self.timeout)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                else:
                    rp = None  # robots.txt が無い＝制限なし
            except requests.RequestException:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def get_static(self, url: str) -> str:
        err = ""
        for i in range(self.retries):
            try:
                self._wait(domain_of(url))
                r = http_session(url, self.config).get(url, headers={"User-Agent": self.ua}, timeout=self.timeout)
                if r.status_code == 200:
                    # Honour declared charset (HTTP or HTML), before heuristic
                    # detection. Short Japanese pages were misdetected as other encodings.
                    declared = re.search(r"charset\s*=\s*[\"']?([\w-]+)", r.headers.get("Content-Type", ""), re.I)
                    meta = re.search(br"charset\s*=\s*[\"']?([\w-]+)", r.content[:8192], re.I)
                    r.encoding = (declared.group(1) if declared else meta.group(1).decode("ascii") if meta else r.apparent_encoding) or "utf-8"
                    self.last_url = r.url
                    return r.text
                err = f"HTTP {r.status_code}"
                if r.status_code in (404, 410):
                    break
            except requests.RequestException as e:
                err = type(e).__name__
            time.sleep(2 * (i + 1))
        raise RuntimeError(err or "取得失敗")

    def get_js(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright  # 必要なときだけ読み込む
        except ImportError as e:
            raise RuntimeError("playwright 未インストール") from e
        self._wait(domain_of(url))
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=self.ua)
            page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)  # 一覧が描画されるのを待つ
            html = page.content()
            self.last_url = page.url
            browser.close()
            return html


def collect(sources: list[dict], config: dict, force: bool = False, record_snapshots: bool = True,
            record_status: bool = True) -> list[Page]:
    """record_snapshots / record_status を False にすると data/ に書かない（PC側の準備用）。"""
    snapshots = load_json(DATA_DIR / "snapshots.json", {})
    status = load_json(DATA_DIR / "source_status.json", {})
    fetcher = Fetcher(config)
    pages: list[Page] = []
    now = now_jst()
    weekday = now.weekday()  # 0=月曜
    (WORK_DIR / "pages").mkdir(parents=True, exist_ok=True)

    for src in sources:
        if src.get("enabled") is False:
            continue
        if src.get("frequency") == "weekly" and weekday != 0 and not force:
            continue
        sid = src["id"]
        st = status.setdefault(sid, {"fail_count": 0, "last_ok": None, "last_error": None, "issue_open": False})
        try:
            if not fetcher.allowed(src["url"]):
                raise RuntimeError("robots.txt で取得禁止")
            html = fetcher.get_js(src["url"]) if src.get("render") == "js" else fetcher.get_static(src["url"])
            text = html_to_text(html, src)
            if len(text) < 50:
                raise RuntimeError("本文がほぼ空（JS描画ページの可能性）")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            changed = force or snapshots.get(sid, {}).get("hash") != digest
            if record_snapshots:
                snapshots[sid] = {"hash": digest, "fetched_at": now.isoformat(timespec="seconds"), "chars": len(text)}
            (WORK_DIR / "pages" / f"{sid}.txt").write_text(text, encoding="utf-8")
            st.update(fail_count=0, last_ok=now.isoformat(timespec="seconds"), last_error=None)
            pages.append(Page(src, text, changed, True, digest=digest, links=extract_links(html, fetcher.last_url or src["url"])))
        except Exception as e:  # noqa: BLE001
            st["fail_count"] = int(st.get("fail_count", 0)) + 1
            st["last_error"] = str(e)[:200]
            pages.append(Page(src, "", False, False, str(e)[:200]))

    if record_snapshots:
        save_json(DATA_DIR / "snapshots.json", snapshots)
    if record_status:
        save_json(DATA_DIR / "source_status.json", status)
    return pages


def fetch_text(source: dict, config: dict) -> str:
    """1ページだけ取得して本文を返す（スナップショットは触らない）。"""
    fetcher = Fetcher(config)
    if not fetcher.allowed(source["url"]):
        raise RuntimeError("robots.txt で取得禁止")
    html = fetcher.get_js(source["url"]) if source.get("render") == "js" else fetcher.get_static(source["url"])
    return html_to_text(html, source)
