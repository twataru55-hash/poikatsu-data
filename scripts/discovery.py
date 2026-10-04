"""Discover all eligible official URLs, then refresh a bounded, rotating subset.

The work/ ledger is local-only. It stores crawl coverage, never grants publication
permission, and its deletion only causes additional safe fetches.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlparse, parse_qs

from collect import extract_links, html_to_text
from common import WORK_DIR, domain_allowed, http_session, load_json, now_jst, save_json, canonical_detail_url


def source_for_detail(parent: dict, url: str) -> dict:
    follow = parent.get("follow") or {}
    return {**parent, "id": parent["id"] + "--" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:8],
            "url": url, "name": parent["name"] + "（詳細ページ）", "parent": parent["id"],
            "render": follow.get("render", parent.get("render", "static"))}


def unwrap_official_link(url: str, domains: set) -> str:
    """Resolve the observed d-point redirect wrapper without executing JavaScript.
    The public URL names its destination; only existing official domains qualify.
    No arbitrary destination is requested or added to the allowlist.
    """
    parsed = urlparse(url)
    if (parsed.hostname == "dpoint.docomo.ne.jp" and parsed.path in
            ("/cp_7/dpc_lp_rdt_common/index.html", "/cp_7/dpc_lp_rdt_common/direct.html",
             "/cp_2/dpc_lp_rdt_cp/index.html")):
        targets = parse_qs(parsed.query).get("redirectID", [])
        if len(targets) != 1 or not targets[0].startswith("https://") or not domain_allowed(targets[0], domains):
            raise ValueError("公式転送リンクの転送先は許可された公式ドメイン外")
        return targets[0]
    return url


def resolve_official_link(url: str, hosts: list, domains: set, fetcher) -> str:
    """Read redirects only from specifically configured public tracking hosts.
    Never request an arbitrary redirect destination outside the official allowlist.
    """
    for _ in range(5):
        if domain_allowed(url, domains):
            return url
        if urlparse(url).hostname not in hosts:
            raise RuntimeError("転送先が許可された公式ドメイン外")
        if not fetcher.allowed(url):
            raise RuntimeError("robots.txt で取得禁止")
        fetcher._wait(urlparse(url).netloc)
        response = http_session(url, fetcher.config).get(
            url, headers={"User-Agent": fetcher.ua}, timeout=fetcher.timeout,
            allow_redirects=False, stream=True)
        try:
            if response.status_code not in (301, 302, 303, 307, 308):
                raise RuntimeError(f"転送を確認できない: HTTP {response.status_code}")
            url = urljoin(url, response.headers.get("Location", ""))
            if not url.startswith("https://"):
                raise RuntimeError("HTTPS以外への転送")
        finally:
            response.close()
    raise RuntimeError("転送回数が上限")


def discover_and_refresh(page, config, snapshots, fetcher, force=False, audit=None):
    follow = page.source.get("follow") or {}
    pattern = re.compile(follow["pattern"])
    domains = set(page.source.get("domains") or [])
    ledger_path = WORK_DIR / "discovery" / (page.source["id"] + ".json")
    previous = load_json(ledger_path, {})
    checked = previous.get("checked", {})
    links = list(page.links or [])
    listing_urls = {page.source["url"]}
    pagination = follow.get("pagination_pattern")
    failures, excluded, eligible = [], [], {}
    # Explicit pagination has its own bound. Every omitted list URL is reported.
    if pagination:
        # Newly discovered next-page links also enter this bounded traversal.
        for _label, url in links:
            if not re.search(pagination, url) or not domain_allowed(url, domains) or url in listing_urls:
                continue
            if len(listing_urls) >= int(follow.get("max_list_pages", 3)):
                excluded.append({"url": url, "reason": "一覧ページの取得上限"})
                continue
            listing_urls.add(url)
            try:
                if not fetcher.allowed(url):
                    raise RuntimeError("robots.txt で取得禁止")
                html = fetcher.get_js(url) if page.source.get("render") == "js" else fetcher.get_static(url)
                links.extend(extract_links(html, url))
            except Exception as e:
                failures.append({"source_id": page.source["id"], "url": url, "error": str(e)[:200]})
    links.extend(("設定済みの公式詳細ページ", u) for u in follow.get("seeds", []))
    redirect_hosts = follow.get("redirect_hosts", [])
    redirect_labels = follow.get("redirect_label_pattern", ".*")
    for label, raw_url in links:
        url = raw_url
        if urlparse(url).hostname in redirect_hosts:
            if not re.search(redirect_labels, label):
                excluded.append({"url": raw_url, "label": label, "reason": "店頭対象外の転送リンク"})
                continue
            try:
                url = resolve_official_link(url, redirect_hosts, domains, fetcher)
            except Exception as e:
                failures.append({"source_id": page.source["id"], "url": raw_url, "error": str(e)[:200]})
                continue
        try:
            url = unwrap_official_link(url, domains)
        except ValueError as e:
            excluded.append({"url": raw_url, "label": label, "reason": str(e)})
            continue
        url = canonical_detail_url(url)
        if not pattern.search(url) or not domain_allowed(url, domains):
            excluded.append({"url": raw_url, "label": label, "reason": "詳細パターンまたは公式ドメインの対象外"})
            continue
        eligible.setdefault(url, label)
    # Oldest local check first, including previously ingested URLs. New URLs are
    # prioritised but cannot permanently starve older URLs once discoveries settle.
    listing_order = {u: i for i, u in enumerate(eligible)}
    ordered = sorted(eligible, key=lambda u: (checked.get(u, {}).get("attempted_at", checked.get(u, {}).get("at", "")), listing_order[u]))
    budget = int(follow.get("max_new", 20))
    got, unchanged, unvisited = [], [], []
    now = now_jst().isoformat(timespec="seconds")
    for index, url in enumerate(ordered):
        src = source_for_detail(page.source, url)
        if index >= budget:
            unvisited.append(url)
            continue
        try:
            if not fetcher.allowed(url):
                raise RuntimeError("robots.txt で取得禁止")
            html = fetcher.get_js(url) if src["render"] == "js" else fetcher.get_static(url)
            final_url = getattr(fetcher, "last_url", None) or url
            if not domain_allowed(final_url, domains):
                raise RuntimeError("取得先が許可された公式ドメイン外へ転送")
            text = html_to_text(html, src)
            if len(text) < 50:
                raise RuntimeError("本文がほぼ空")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            checked[url] = {"at": now, "attempted_at": now, "hash": digest, "source_id": src["id"]}
            (WORK_DIR / "pages").mkdir(parents=True, exist_ok=True)
            (WORK_DIR / "pages" / (src["id"] + ".txt")).write_text(text, encoding="utf-8")
            from prepare import link_section
            from collect import save_page_assets
            save_page_assets(html, src, final_url)
            revision = int(config.get("manual", {}).get("extraction_revision", 0))
            snap = snapshots.get(src["id"], {})
            if force or snap.get("hash") != digest or snap.get("extraction_revision", 0) != revision:
                got.append((src, text + link_section(extract_links(html, final_url)), digest))
            else:
                unchanged.append(src["id"])
        except Exception as e:
            checked.setdefault(url, {}).update(attempted_at=now, error=str(e)[:200])
            failures.append({"source_id": src["id"], "url": url, "error": str(e)[:200]})
    result = {"source_id": page.source["id"], "at": now, "listing_pages": sorted(listing_urls),
              "discovered": [{"url": u, "label": t} for u, t in eligible.items()],
              "checked": checked, "fetched": min(len(ordered), budget), "unchanged": unchanged,
              "unvisited": unvisited, "excluded": excluded, "failed": failures}
    save_json(ledger_path, result)
    if audit is not None:
        audit.update({k: v for k, v in result.items() if k != "checked"})
    return got, failures
