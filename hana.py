#!/usr/bin/env python3
"""
hana.py - Hiring Alerts, Newly Appeared.

Linux Usage:
    python3 hana.py                 # check once, report new postings
    python3 hana.py --all           # report every match, not just new ones
    python3 hana.py --reset         # forget everything seen so far
    python3 hana.py --list-boards   # show configured boards

On Windows the command is `py hana.py`

"""

import argparse
import json
import os
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import namedtuple
from datetime import datetime, timezone

# CONFIG - edit this section

# Words that make a posting interesting. Matching is case- and accent-
# insensitive.
KEYWORDS = [
    "lighting",
    "eclairage",
    "light artist",
    "shading",
    "render",
]

# Optional: only report postings whose location mentions one of these.
# Set to [] to disable location filtering entirely.
LOCATIONS = [
    "montreal",
    "quebec",
    "remote",
    "canada",
]

EXCLUDE = [
    "intern",
    "stagiaire",
    "recruiter",
]

BOARDS = [
    ("greenhouse", "haven", "Haven Studios"),
    ("greenhouse", "cloudchamberen", "Cloud Chamber"),
    ("smartrecruiters", "RodeoFX", "Rodeo FX"),
    ("workable", "bardel-entertainment", "Bardel Entertainment"),
    ("greenhouse", "highdive", "Highdive"),
    ("greenhouse", "2k", "2K Games"),
    ("greenhouse", "sonypicturesimageworks", "Sony Pictures Imageworks"),
    ("recruitee", "framestore", "Framestore"),
    ("recruitee", "squeezestudio", "Squeeze Studio"),
    ("workable", "pxo", "Pixomondo"),
    ("smartrecruiters", "ubisoft2", "Ubisoft"),
    ("lever", "bhvr", "Behaviour"),
]

TIMEOUT = 20  # seconds per request
USER_AGENT = "hana/1.0"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hana_state.json")


class Posting(namedtuple("Posting", "source company job_id title location url")):
    @property
    def key(self):
        return f"{self.source}:{self.company}:{self.job_id}"


def strip_accents(text):
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def join_parts(*parts):
    return ", ".join(p for p in parts if p)


def matches(posting):
    title = strip_accents(posting.title)
    location = strip_accents(posting.location)

    if any(strip_accents(word) in title for word in EXCLUDE):
        return False
    if not any(strip_accents(word) in title for word in KEYWORDS):
        return False
    if LOCATIONS and not any(strip_accents(place) in location for place in LOCATIONS):
        return False
    return True


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def from_greenhouse(token, company):
    data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    return [
        Posting(
            source="greenhouse",
            company=company,
            job_id=str(job.get("id", "")),
            title=(job.get("title") or "").strip(),
            location=((job.get("location") or {}).get("name") or "").strip(),
            url=job.get("absolute_url") or "",
        )
        for job in data.get("jobs", [])
    ]


def from_smartrecruiters(slug, company):
    out = []
    offset = 0
    while True:
        data = fetch_json(
            f"https://api.smartrecruiters.com/v1/companies/{slug}"
            f"/postings?limit=100&offset={offset}"
        )
        content = data.get("content", [])
        for job in content:
            loc = job.get("location") or {}
            job_id = str(job.get("id", ""))
            out.append(
                Posting(
                    source="smartrecruiters",
                    company=company,
                    job_id=job_id,
                    title=(job.get("name") or "").strip(),
                    location=join_parts(
                        loc.get("city"), loc.get("region"), loc.get("country")
                    ),
                    url=f"https://jobs.smartrecruiters.com/{slug}/{job_id}",
                )
            )
        offset += len(content)
        if not content or offset >= data.get("totalFound", offset):
            return out


def from_workable(slug, company):
    data = fetch_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    )
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    out = []
    for job in jobs:
        shortcode = str(job.get("shortcode") or job.get("id") or "")
        out.append(
            Posting(
                source="workable",
                company=company,
                job_id=shortcode,
                title=(job.get("title") or "").strip(),
                location=join_parts(
                    job.get("city"), job.get("state"), job.get("country")
                ),
                url=job.get("url")
                or f"https://apply.workable.com/{slug}/j/{shortcode}/",
            )
        )
    return out


def from_recruitee(slug, company):
    data = fetch_json(f"https://{slug}.recruitee.com/api/offers/")
    return [
        Posting(
            source="recruitee",
            company=company,
            job_id=str(job.get("id", "")),
            title=(job.get("title") or "").strip(),
            location=join_parts(job.get("city"), job.get("country")),
            url=job.get("careers_url") or job.get("careers_apply_url") or "",
        )
        for job in data.get("offers", [])
    ]


def from_lever(slug, company):
    data = fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    return [
        Posting(
            source="lever",
            company=company,
            job_id=str(job.get("id", "")),
            title=(job.get("text") or "").strip(),
            location=((job.get("categories") or {}).get("location") or "").strip(),
            url=job.get("hostedUrl") or "",
        )
        for job in (data if isinstance(data, list) else [])
    ]


ADAPTERS = {
    "greenhouse": from_greenhouse,
    "smartrecruiters": from_smartrecruiters,
    "workable": from_workable,
    "recruitee": from_recruitee,
    "lever": from_lever,
}


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  ! could not read state file ({exc}); starting fresh", file=sys.stderr)
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except OSError as exc:
        print(f"  ! could not write state file: {exc}", file=sys.stderr)


def collect():
    found = []
    errors = []

    for adapter_name, identifier, company in BOARDS:
        adapter = ADAPTERS.get(adapter_name)
        if adapter is None:
            errors.append(f"{company}: unknown adapter '{adapter_name}'")
            continue
        try:
            postings = adapter(identifier, company)
        except urllib.error.HTTPError as exc:
            errors.append(f"{company}: HTTP {exc.code} - board may have moved")
            continue
        except (
            urllib.error.URLError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as exc:
            errors.append(f"{company}: {type(exc).__name__} - {exc}")
            continue

        hits = [p for p in postings if matches(p)]
        found.extend(hits)
        print(f"  {company:<24} {len(postings):>3} open, {len(hits)} matching")

    return found, errors


def run_once(show_all=False):
    now = datetime.now(timezone.utc)
    print(f"\n[{now.astimezone():%Y-%m-%d %H:%M}] checking {len(BOARDS)} board(s)...")

    found, errors = collect()

    state = load_state()
    new = [p for p in found if p.key not in state]
    for p in new:
        state[p.key] = {
            "first_seen": now.isoformat(),
            "title": p.title,
            "company": p.company,
            "url": p.url,
        }
    save_state(state)

    to_show = found if show_all else new
    label = "matching" if show_all else "NEW"

    if to_show:
        print(f"\n{len(to_show)} {label} posting(s):\n")
        for p in to_show:
            print(f"  {p.title}")
            print(f"    {p.company} - {p.location or 'location not listed'}")
            print(f"    {p.url}\n")
    else:
        print(f"\nNo {label.lower()} postings.")

    if errors:
        print("\nProblems:")
        for e in errors:
            print(f"  ! {e}")


def main():
    # For Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="HANA - Hiring Alerts, Newly Appeared. "
        "Monitors studio job boards for new postings."
    )
    ap.add_argument(
        "--all", action="store_true", help="show every match, not just new ones"
    )
    ap.add_argument("--reset", action="store_true", help="delete saved state and exit")
    ap.add_argument(
        "--list-boards", action="store_true", help="print configured boards and exit"
    )
    args = ap.parse_args()

    if args.reset:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            print(f"Removed {STATE_FILE}")
        else:
            print("No state file to remove.")
    elif args.list_boards:
        for adapter_name, identifier, company in BOARDS:
            print(f"  {company:<24} {adapter_name:<16} {identifier}")
    else:
        run_once(show_all=args.all)


if __name__ == "__main__":
    main()
