"""Bounded ShinyColorsDB card-detail importer (explicit IDs only, private snapshots).

Usage (network only with --fetch; default is a dry run that prints the plan):

    .venv/Scripts/python.exe tools/shinycolorsdb_import.py --ids 1040130050,2040060210 --fetch

* Mapping is exact: a 10-digit Enza ID must equal ``enzaId`` in a local index file
  (catalog / latestPInfo / latestSInfo / getAllTable shapes).  No name matching.
* Serial requests, 1.5-2 s apart, no cookies, no redirects, robots.txt honoured,
  bounded body size, ETag/If-None-Match against earlier snapshots, stop on any
  HTTP error without retry.
* Raw JSON lands in ``dev_assets/private/shinycolorsdb/snapshot-YYYYMMDD/`` with
  a manifest (URL, sha256, retrieval time, cardHash).  Never commit these files.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import random
import re
import sys
import time
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / "dev_assets" / "private"
DEFAULT_SNAPSHOT_ROOT = PRIVATE_ROOT / "shinycolorsdb"
DEFAULT_INDEXES = (
    PRIVATE_ROOT / "true_end" / "shinycolorsdb-20261006" / "catalog.json",
    PRIVATE_ROOT / "shinycolorsdb" / "latestPInfo.json",
    PRIVATE_ROOT / "shinycolorsdb" / "latestSInfo.json",
)
API_HOST = "api.shinycolors.moe"
API_BASE = f"https://{API_HOST}"
USER_AGENT = "MaaShinyColorsResearch/0.1 (+local card-data pilot; serial, cached)"
ROBOTS_AGENT = "MaaShinyColorsResearch"
DEFAULT_MAX_IDS = 20      # above this an explicit --allow-many is required
HARD_MAX_IDS = 100        # never a bulk fetch, even with --allow-many
MAX_BODY_BYTES = 2_000_000
MIN_INTERVAL, MAX_INTERVAL = 1.5, 2.0
# 10-digit Enza ID: [12] = P/S, then 0 (ordinary) or 9 (IdolRoad 19xx / P-Cup 29xx special cards).
ENZA_RE = re.compile(r"^[12][09]\d{8}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ENDPOINT = {"P": "/info/pCardInfo", "S": "/info/sCardInfo"}


class ImportRefused(ValueError):
    """Input or policy violation; nothing was fetched for the refused request."""


def parse_ids(values: Iterable[str], *, allow_many: bool = False) -> list[str]:
    ids: list[str] = []
    for value in values:
        for part in re.split(r"[\s,]+", value.strip()):
            if not part:
                continue
            if not ENZA_RE.match(part):
                raise ImportRefused(f"not a 10-digit Enza card ID: {part!r}")
            if part not in ids:
                ids.append(part)
    if not ids:
        raise ImportRefused("--ids is required (explicit card list only)")
    if len(ids) > HARD_MAX_IDS:
        raise ImportRefused(f"{len(ids)} IDs exceeds the hard cap {HARD_MAX_IDS}; bulk fetch is not allowed")
    if len(ids) > DEFAULT_MAX_IDS and not allow_many:
        raise ImportRefused(f"{len(ids)} IDs exceeds {DEFAULT_MAX_IDS}; pass --allow-many after user approval")
    return ids


def _index_entries(payload: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, Mapping):
                yield item
    elif isinstance(payload, Mapping):
        if "enzaId" in payload:
            yield payload
        for value in payload.values():
            if isinstance(value, (list, dict)):
                yield from _index_entries(value)


def load_index(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Exact enzaId -> {card_uuid, kind, card_hash, name}; conflicting UUIDs are refused."""
    index: dict[str, dict[str, Any]] = {}
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        for entry in _index_entries(json.loads(path.read_text(encoding="utf-8"))):
            enza, uuid, ctype = entry.get("enzaId"), entry.get("cardUuid"), entry.get("cardType")
            if not (isinstance(enza, str) and ENZA_RE.match(enza) and isinstance(uuid, str)
                    and UUID_RE.match(uuid) and isinstance(ctype, str) and ctype[:2] in ("P_", "S_")):
                continue
            row = dict(card_uuid=uuid, kind=ctype[0], card_type=ctype, card_hash=entry.get("cardHash"),
                       name=entry.get("cardName"), index_file=str(path))
            old = index.get(enza)
            if old and (old["card_uuid"] != uuid or old["kind"] != row["kind"]):
                raise ImportRefused(f"index conflict for {enza}: {old['card_uuid']} vs {uuid}")
            if not old or (row["card_hash"] and not old.get("card_hash")):
                index[enza] = row
    return index


def plan(ids: list[str], index: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    jobs, missing = [], []
    for enza in ids:
        row = index.get(enza)
        if row is None:
            missing.append(enza)
            continue
        if not enza.startswith("1" if row["kind"] == "P" else "2"):
            raise ImportRefused(f"{enza}: index kind {row['kind']} contradicts the ID prefix")
        url = f"{API_BASE}{ENDPOINT[row['kind']]}?{urlencode({'cardId': row['card_uuid']})}"
        jobs.append(dict(enza_id=enza, url=url, **row))
    return jobs, missing


def check_url(url: str) -> str:
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != API_HOST or parts.port or parts.username
            or parts.password or parts.fragment):
        raise ImportRefused("only https://api.shinycolors.moe is allowed")
    if parts.path not in (*ENDPOINT.values(), "/robots.txt", "/info/idolList", "/info/idolInfo"):
        raise ImportRefused(f"endpoint not allowlisted: {parts.path}")
    if parts.path == "/info/idolInfo" and not re.fullmatch(r"idolId=[1-9]\d{0,3}", parts.query):
        raise ImportRefused("idolInfo requires one bounded numeric idolId")
    if parts.path in ("/robots.txt", "/info/idolList") and parts.query:
        raise ImportRefused("unexpected query for index endpoint")
    return url


def resolve_out_dir(out_dir: Path | None, today: str) -> Path:
    path = Path(out_dir) if out_dir else DEFAULT_SNAPSHOT_ROOT / f"snapshot-{today}"
    if not path.resolve().is_relative_to(PRIVATE_ROOT.resolve()):
        raise ImportRefused("snapshots must stay under dev_assets/private")
    return path


def previous_etags(snapshot_root: Path) -> dict[str, dict[str, Any]]:
    """Latest known ETag/file per URL from earlier manifests (for If-None-Match)."""
    known: dict[str, dict[str, Any]] = {}
    for manifest in sorted(Path(snapshot_root).glob("snapshot-*/manifest.json")):
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8")).get("entries", [])
        except (OSError, ValueError):
            continue
        for e in entries:
            if e.get("etag") and e.get("file"):
                path = manifest.parent / e["file"]
                if path.is_file() and sha256(path.read_bytes()).hexdigest() == e.get("sha256"):
                    known[e["url"]] = dict(etag=e["etag"], path=path, sha256=e["sha256"])
    return known


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ImportRefused("redirect refused; review the source first")


Response = tuple[int, Mapping[str, str], bytes]


def urllib_transport(url: str, headers: Mapping[str, str]) -> Response:
    opener = build_opener(NoRedirect())  # no HTTPCookieProcessor: cookies never sent or stored
    try:
        with opener.open(Request(url, headers=dict(headers)), timeout=30) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            return response.status, dict(response.headers.items()), body
    except HTTPError as exc:
        if exc.code == 304:
            return 304, dict(exc.headers.items()), b""
        raise


class Importer:
    def __init__(self, out_dir: Path, *, transport: Callable[[str, Mapping[str, str]], Response] = urllib_transport,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 etags: Mapping[str, Mapping[str, Any]] | None = None):
        self.out_dir, self.transport, self.sleep, self.clock = Path(out_dir), transport, sleep, clock
        self.etags = dict(etags or {})
        self.last_request: float | None = None
        self.min_interval = MIN_INTERVAL
        self.robots: RobotFileParser | None = None
        self.requests = 0

    def _get(self, url: str, extra: Mapping[str, str] | None = None) -> Response:
        check_url(url)
        if self.last_request is not None:
            gap = random.uniform(self.min_interval, max(self.min_interval, MAX_INTERVAL))
            wait = gap - (self.clock() - self.last_request)
            if wait > 0:
                self.sleep(wait)
        self.last_request = self.clock()
        self.requests += 1
        status, headers, body = self.transport(url, {"User-Agent": USER_AGENT, "Accept": "application/json",
                                                     **(extra or {})})
        if len(body) > MAX_BODY_BYTES:
            raise ImportRefused(f"response exceeds {MAX_BODY_BYTES} bytes: {url}")
        if status not in (200, 304):
            raise ImportRefused(f"HTTP {status} for {url}; stopping without retry")
        return status, headers, body

    def _robots(self) -> None:
        if self.robots is not None:
            return
        try:
            status, _, body = self._get(f"{API_BASE}/robots.txt")
        except HTTPError as exc:
            if exc.code != 404:
                raise
            status, body = 404, b""  # no robots file: standard semantics allow, still rate-limited
        robots = RobotFileParser()
        robots.parse(body.decode("utf-8", "replace").splitlines() if status == 200 else [])
        delay = robots.crawl_delay(ROBOTS_AGENT)
        if delay and float(delay) > 60:
            raise ImportRefused("robots crawl-delay exceeds the bounded session")
        self.min_interval = max(MIN_INTERVAL, float(delay or 0))
        self.robots = robots

    def _previous_manifest(self) -> tuple[list[dict[str, Any]], int]:
        """Entries of an earlier batch in the same snapshot (checksum-verified) and its request count."""
        path = self.out_dir / "manifest.json"
        if not path.is_file():
            return [], 0
        old = json.loads(path.read_text(encoding="utf-8"))
        kept = []
        for e in old.get("entries", []):
            body = self.out_dir / str(e.get("file"))
            if not body.is_file() or sha256(body.read_bytes()).hexdigest() != e.get("sha256"):
                raise ImportRefused(f"existing snapshot entry drifted: {e.get('file')}; use a new --out-dir")
            kept.append(e)
        return kept, int(old.get("requests") or 0)

    def fetch_index(self, url: str, cache_dir: Path) -> Any:
        """Read a small public index with the same robots, pacing and ETag policy as cards."""
        check_url(url)
        self._robots()
        if not self.robots.can_fetch(ROBOTS_AGENT, url):
            raise ImportRefused(f"robots.txt disallows {url}")
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = sha256(url.encode()).hexdigest()
        target, meta = cache_dir / f"{key}.json", cache_dir / f"{key}.meta.json"
        old = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
        valid = target.is_file() and sha256(target.read_bytes()).hexdigest() == old.get("sha256")
        status, headers, body = self._get(url, {"If-None-Match": old["etag"]}
                                          if valid and old.get("etag") else None)
        if status == 304:
            if not valid:
                raise ImportRefused("304 without verified index cache")
            return json.loads(target.read_text(encoding="utf-8"))
        headers = {k.lower(): v for k, v in headers.items()}
        if "json" not in headers.get("content-type", ""):
            raise ImportRefused(f"non-JSON index response: {url}")
        payload = json.loads(body.decode("utf-8"))
        target.write_bytes(body)
        meta.write_text(json.dumps({"url": url, "etag": headers.get("etag"),
                                    "sha256": sha256(body).hexdigest()}, indent=2), encoding="utf-8")
        return payload

    def _write_manifest(self, entries: list[dict[str, Any]], previous_requests: int) -> dict[str, Any]:
        manifest = dict(version=1, generated_by="tools/shinycolorsdb_import.py",
                        generated_at=datetime.now(timezone.utc).isoformat(), source=API_BASE,
                        note="Private research cache. Do not commit or redistribute.",
                        requests=previous_requests + self.requests, entries=entries)
        (self.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest

    def fetch(self, jobs: list[dict[str, Any]]) -> dict[str, Any]:
        """Fetch ``jobs`` serially.  Batches into the same snapshot merge into one manifest
        (same card -> replaced); the manifest is rewritten after every card so a stop leaves
        an accurate record of what was saved."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        previous, previous_requests = self._previous_manifest()
        batch_ids = {job["enza_id"] for job in jobs}
        entries = [e for e in previous if e.get("enza_id") not in batch_ids]
        self._robots()
        for job in jobs:
            if not self.robots.can_fetch(ROBOTS_AGENT, job["url"]):
                raise ImportRefused(f"robots.txt disallows {job['url']}")
            known = self.etags.get(job["url"])
            status, headers, body = self._get(job["url"], {"If-None-Match": known["etag"]} if known else None)
            retrieved = datetime.now(timezone.utc).isoformat()
            if status == 304:
                body = Path(known["path"]).read_bytes()
            ctype = {k.lower(): v for k, v in headers.items()}.get("content-type", "")
            if status == 200 and "json" not in ctype:
                raise ImportRefused(f"non-JSON response for {job['enza_id']}")
            card = json.loads(body.decode("utf-8"))
            if (not isinstance(card, Mapping) or card.get("enzaId") != job["enza_id"]
                    or card.get("cardUuid") != job["card_uuid"]
                    or not str(card.get("cardType", "")).startswith(job["kind"] + "_")):
                raise ImportRefused(f"response identity mismatch for {job['enza_id']}; stopping")
            name = f"{job['kind'].lower()}-{job['enza_id']}-{card.get('cardHash') or 'nohash'}.json"
            (self.out_dir / name).write_bytes(body)
            etag = {k.lower(): v for k, v in headers.items()}.get("etag") or (known or {}).get("etag")
            entries.append(dict(enza_id=job["enza_id"], card_uuid=job["card_uuid"], kind=job["kind"],
                                card_name=card.get("cardName"), url=job["url"], file=name,
                                sha256=sha256(body).hexdigest(), bytes=len(body), retrieved_at=retrieved,
                                http_status=status, etag=etag, card_hash=card.get("cardHash"),
                                index_card_hash=job.get("card_hash"),
                                card_hash_changed=bool(job.get("card_hash") and card.get("cardHash") != job["card_hash"]),
                                last_modified=card.get("lastModified"), release_date=card.get("releaseDate")))
            self._write_manifest(entries, previous_requests)
        return self._write_manifest(entries, previous_requests)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ids", action="append", required=True, help="explicit 10-digit Enza IDs (comma separated)")
    ap.add_argument("--index", type=Path, action="append", help="local index JSON (default: private catalog/latest*)")
    ap.add_argument("--out-dir", type=Path, help="snapshot dir under dev_assets/private (default snapshot-YYYYMMDD)")
    ap.add_argument("--allow-many", action="store_true", help=f"permit more than {DEFAULT_MAX_IDS} IDs (user approval)")
    ap.add_argument("--fetch", action="store_true", help="actually perform network requests (otherwise dry run)")
    args = ap.parse_args(argv)
    try:
        ids = parse_ids(args.ids, allow_many=args.allow_many)
        jobs, missing = plan(ids, load_index(args.index or DEFAULT_INDEXES))
        today = datetime.now().strftime("%Y%m%d")
        out_dir = resolve_out_dir(args.out_dir, today)
    except ImportRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    for job in jobs:
        print(f"{job['enza_id']} {job['kind']} {job['card_uuid']} {job['name']}")
    for enza in missing:
        print(f"{enza} MISSING (no exact enzaId in local indexes; not fetched)")
    if not args.fetch:
        print(f"dry run: {len(jobs)} card(s) would be fetched into {out_dir}")
        return 0
    try:
        manifest = Importer(out_dir, etags=previous_etags(out_dir.parent)).fetch(jobs)
    except (ImportRefused, HTTPError, OSError, ValueError) as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(jobs)} card(s); manifest now lists {len(manifest['entries'])} card(s) in {out_dir} "
          f"({manifest['requests']} requests in total)")
    return 0 if not missing else 3


if __name__ == "__main__":
    sys.exit(main())
