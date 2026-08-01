#!/usr/bin/env python3
"""
CIM rename-plan generator  --  DRY RUN ONLY. Renames nothing.

Outputs (all written OUTSIDE the source folder, into --out):
    rename_plan.csv   old -> new, with extracted fields + confidence + reason
    ledger.csv        old name, sha256, size, mtime  (chain of custody / undo source)
    apply.ps1         PowerShell that performs the renames, hard-failing on collision
    undo.ps1          PowerShell that reverses them

Usage:
    python scripts/cims_rename_plan.py --src "C:\\Users\\me\\Dropbox\\CIMs2" \\
                                       --out "C:\\Users\\me\\Desktop\\cims2_plan" \\
                                       [--overrides overrides.csv]

Design rules enforced:
  * Additive prefix only: "[AC-ST-City] <original name>". The original name is kept
    (normalised only for characters Windows rejects; ledger.csv holds it verbatim), and
    the prefix is filing metadata rather than identity -- webapp.services strips it
    before duplicate detection, so renaming a CIM never orphans it from the deal or
    comp-DB row it was ingested as.
  * ABSTAIN, never guess. Low confidence -> [ZZ-ZZ-ZZ], which sorts to the bottom as a
    work queue. Files that are not PDFs or ZIPs are left alone entirely. apply_pairing
    is the one inference that fills a ZZ row, and it can only ever reach REVIEW.
  * Casefolded collision check across renames AND the files left in place. Hard fail.
    Never overwrite.
  * Zips are listed from the archive central directory; at most two inner PDFs are
    decompressed to identify the data room, never the whole archive, and cloud
    placeholders are skipped rather than hydrated.
  * Cloud-sync placeholders are detected and skipped, not hydrated -- see the platform
    guard in main(), which refuses to run off-Windows because that detection is a
    Windows-only API and would otherwise fail open and pull down every byte.

Per-file judgement calls belong in --overrides, not in this file. A hardcoded override
table silently follows the script to the next folder it is pointed at.
"""

import argparse
import csv
import hashlib
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------- constants

BROKERS = [
    "marcus & millichap", "marcus and millichap", "cbre", "colliers",
    "cushman & wakefield", "cushman and wakefield", "jll", "jones lang",
    "newmark", "svn", "berkadia", "sharplaunch", "ten-x", "crexi",
    "the storage acquisition group", "argus self storage", "skyview advisors",
    "bellomy & associates", "matthews real estate", "walker & dunlop",
]

# Marcus & Millichap activity IDs, e.g. ZAH0320384 -- externally-recognised deal keys.
ACTIVITY_RE = re.compile(r"\b(Z[A-Z]{2}\d{6,8})\b")

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
ST_CODES = set(STATES.values())

_ST_ALT = "|".join(STATES)
# The zip separator is \s* rather than \s+ on purpose: real CIM covers set the address
# with no space at all ("63CedarMillsRd,Gordonville,TX76245" in Texoma 377 OM.pdf).
_ZIP = r"[\.,]?\s*(\d{5})(?:-\d{4})?"
# "…, Springfield, MO 65801"  /  "… | YORK, SC 29745"  /  "Rogers, Arkansas 72756"
# The leading boundary is load-bearing: without it the city group runs backwards into the
# street line and you get "BeachRdLincolnville" instead of "Lincolnville".
ADDR_RE = re.compile(
    r"(?:^|[,|•·])\s*([A-Za-z][A-Za-z\.\-' ]{2,28}?),\s*"
    r"([A-Z]{2}|" + _ST_ALT + r")" + _ZIP, re.IGNORECASE)
# Fallback for covers with no comma before the city. The street-suffix filter does the
# work instead.
ADDR_RE_LOOSE = re.compile(
    r"([A-Za-z][A-Za-z\.\-' ]{2,28}?),\s*([A-Z]{2}|" + _ST_ALT + r")" + _ZIP,
    re.IGNORECASE)
# "… Waterloo IA 50701" - no comma at all. Common in folder/file names.
ADDR_RE_NOCOMMA = re.compile(
    r"([A-Za-z][A-Za-z\.\-' ]{2,28}?)\s+([A-Z]{2})\s+(\d{5})\b")

# Tokens that are never part of a city name; used to trim a city that has run
# backwards into the street line ('Industry Drive Bastrop' -> 'Bastrop').
NOISE_TOKENS = {
    "zip", "address", "location", "city", "property", "site", "state",
    "the", "at", "of", "and", "in", "on", "is", "to", "rsf", "psf", "nrsf",
    "sf", "sqft", "acres", "acre", "units", "unit", "price", "cap", "rate",
    "offering", "memorandum", "subject", "located", "situated",
}

STREET_SUFFIX = re.compile(
    r"\b(road|rd|street|st|avenue|ave|boulevard|blvd|drive|dr|highway|hwy|hway|lane|ln|"
    r"way|court|ct|circle|cir|parkway|pkwy|place|pl|trail|trl|route|rte|loop|pike|"
    r"terrace|ter|suite|ste|floor|fl|unit|building|bldg|north|south|east|west|"
    r"n|s|e|w|ne|nw|se|sw|us|sr|fm|county|co)$", re.IGNORECASE)

# Asset-class keyword scoring. Order matters only for tie-breaks.
CLASS_KEYWORDS = {
    "MAR": ["marina", "wet slip", "dry stack", "boat slip", "waterfront dockage"],
    "BRV": ["boat & rv", "boat and rv", "rv & boat", "rv and boat", "rv storage",
            "boat storage", "recreational vehicle storage", "covered rv"],
    "OIS": ["industrial outdoor storage", "outdoor industrial storage", "ios ",
            "contractor yard", "truck parking", "laydown yard", "trailer storage"],
    "SS":  ["self storage", "self-storage", "mini storage", "climate controlled",
            "climate-controlled", "storage units", "nrsf", "net rentable"],
}
STAGE_KEYWORDS = {
    "LAND": ["vacant land", "development land", "shovel ready", "shovel-ready",
             "entitled land", "raw land", "development site", "build-to-suit pad"],
    "LU":   ["lease-up", "lease up", "leaseup", "certificate of occupancy",
             "recently completed", "stabilizing"],
}
DOC_KEYWORDS = {
    "OCC": ["occupancy report", "master occupancy", "rent roll"],
}

WIN_RESERVED = ({"CON", "PRN", "AUX", "NUL"}
                | {f"COM{i}" for i in range(1, 10)}
                | {f"LPT{i}" for i in range(1, 10)})
ILLEGAL = '<>:"/\\|?*'
MAX_TOTAL_PATH = 200          # deliberately below the 260 MAX_PATH limit
MIN_TEXT_CHARS = 200          # below this the PDF is treated as scanned -> abstain
PREFIX_RE = re.compile(r"^\[[^\]]{1,60}\]\s")

OVERRIDE_FIELDS = ("ac", "st", "city", "prop", "confidence", "reason")

# ---------------------------------------------------------------- helpers


def norm_text(s: str) -> str:
    """NFKD-normalise, strip problem codepoints, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s)
    for bad, good in (("\u2013", "-"), ("\u2014", "-"), ("\u2018", "'"),
                      ("\u2019", "'"), ("\u201c", '"'), ("\u201d", '"'),
                      ("\u00a0", " "), ("\u00ad", "")):
        s = s.replace(bad, good)
    s = "".join(c for c in s if ord(c) < 128 or c.isalnum())
    return re.sub(r"\s+", " ", s).strip()


def safe_component(s: str) -> str:
    """Make a string safe for a Windows filename component."""
    s = norm_text(s)
    s = "".join(c for c in s if c not in ILLEGAL)
    s = re.sub(r"[\s_]+", "", s)   # CamelCase-ish token: 'Ocean Springs' -> 'OceanSprings'
    return s.strip(". ")


def sha256(path: Path, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for blk in iter(lambda: fh.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def placeholder_detection_available() -> bool:
    """True only where st_file_attributes exists (Windows). Everywhere else the
    placeholder check cannot answer, and answering 'no' would hydrate the file."""
    return hasattr(os.stat_result, "st_file_attributes")


def is_placeholder(path: Path) -> bool:
    """True if the file is a Dropbox/OneDrive online-only stub. Reading it would
    hydrate gigabytes. Callers must gate on placeholder_detection_available()."""
    try:
        attrs = os.stat(path).st_file_attributes            # Windows only
    except AttributeError:
        return False
    FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
    FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
    FILE_ATTRIBUTE_OFFLINE = 0x00001000
    return bool(attrs & (FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
                         | FILE_ATTRIBUTE_RECALL_ON_OPEN
                         | FILE_ATTRIBUTE_OFFLINE))

# ---------------------------------------------------------------- extraction


def pdf_text(path: Path, pages=8):
    """Return (cover_lines, body_text).

    pdfplumber, matching extract/pdf_reader.py -- one PDF library in the repo. The
    cover is returned as discrete lines so a title can be told apart from an address."""
    import pdfplumber
    cover_lines, per = [], []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            if i >= pages:
                break
            txt = page.extract_text() or ""
            if i == 0:
                cover_lines = [ln for ln in (norm_text(x) for x in txt.splitlines()) if ln]
            per.append(norm_text(txt))
    return cover_lines, " ".join(per)


def near_broker(text: str, pos: int, window: int = 110) -> bool:
    """Is this address sitting inside a broker's own signature block?

    The window is deliberately tight. A wide window applied to a whole-document blob
    matches the disclaimer page's dozens of broker mentions and suppresses every real
    address in the file -- that bug cost 12 files on the first run."""
    seg = text[max(0, pos - window): pos + window].lower()
    return any(b in seg for b in BROKERS)


def _tidy_city(c: str) -> str:
    """Trim a captured city back to just the city.

    The regex often runs backwards into the street line, producing
    'Industry Drive Bastrop' or 'ZIP Mesa'. Walk the tokens from the right and stop at
    the first one that cannot be part of a city name: a street suffix, a noise word, or
    anything containing a digit or a period."""
    c = c.strip(" .,-|")
    c = re.sub(r"([A-Za-z])\s+([A-Z])(?=\s|$)", r"\1\2", c)   # 'DECATU R' -> 'DECATUR'
    keep = []
    for t in reversed(c.split()):
        tl = t.strip(".,").lower()
        if (STREET_SUFFIX.match(tl) or tl in NOISE_TOKENS
                or any(ch.isdigit() for ch in t) or "." in t):
            break
        keep.append(t)
        if len(keep) == 3:            # no US city name needs more than three words here
            break
    c = " ".join(reversed(keep)) if keep else ""

    # Normalise casing. 'YORK' -> 'York', but leave genuine CamelCase alone so
    # 'McKinney' and 'LaGrange' survive intact.
    def fix(w):
        return w if re.fullmatch(r"(?:[A-Z][a-z]+){1,3}", w) else w.title()
    return " ".join(fix(w) for w in c.split())


def _harvest(rx, text: str):
    out = []
    for m in rx.finditer(text):
        if near_broker(text, m.start()):
            continue
        city = _tidy_city(m.group(1))
        st_raw = m.group(2)
        st = st_raw.upper() if len(st_raw) == 2 else STATES[st_raw.lower()]
        if st not in ST_CODES or len(city) < 3:
            continue
        # A 'city' ending in a street suffix is really the tail of the street line.
        last = city.split()[-1] if city.split() else city
        if STREET_SUFFIX.match(last) or re.match(r"^\d", city):
            continue
        out.append((city, st, m.group(3)))
    return out


def find_locations(text: str):
    """All (city, ST, zip) hits not sitting next to a broker's own office address."""
    for rx in (ADDR_RE, ADDR_RE_LOOSE, ADDR_RE_NOCOMMA):
        out = _harvest(rx, text)
        if out:
            return out
    return []


def locate(cover_lines, body: str):
    """Cover page first, body only as fallback.

    The cover carries the subject property's address; the disclaimer pages carry the
    broker's. Searching the cover first avoids the confusion entirely rather than
    relying on the proximity heuristic to untangle it afterwards."""
    cov = find_locations(" ".join(cover_lines)) if cover_lines else []
    if cov:
        return cov, "cover page"
    return find_locations(body), "body text"


def classify(text: str):
    low = text.lower()
    scores = {k: sum(low.count(w) for w in ws) for k, ws in CLASS_KEYWORDS.items()}
    classes = [k for k in ("MAR", "BRV", "OIS", "SS") if scores[k] > 0]
    classes.sort(key=lambda k: -scores[k])
    if not classes:
        return "ZZ", scores
    top = classes[0]
    if len(classes) > 1 and scores[classes[1]] >= max(3, scores[top] * 0.35):
        return f"{top}+{classes[1]}", scores
    return top, scores


PORTFOLIO_RE = re.compile(
    r"\b(two|three|four|five|six|\d{1,2})[\s-]*(propert(y|ies)|facilit(y|ies)|site|store|"
    r"asset)s?[\s-]*(portfolio|offering)?\b(?=.{0,40}(portfolio|offering|package))"
    r"|\b(propert(y|ies)|storage|asset)\s+portfolio\b"
    r"|\bportfolio\s+(of|offering|sale)\b"
    r"|\bmulti[\s-]?(property|site)\b", re.IGNORECASE)


def is_portfolio(*texts) -> bool:
    return any(PORTFOLIO_RE.search(t or "") for t in texts)


def stage_tags(text: str):
    low = text.lower()
    return [k for k, ws in STAGE_KEYWORDS.items() if any(w in low for w in ws)]


def property_name(cover_lines) -> str:
    """First plausible title line on the cover page. Informational only -- this does NOT
    go into the filename, so a miss costs a blank CSV cell, not a bad rename."""
    junk = re.compile(
        r"^(offering memorandum|o f f e r i n g|confidential|for sale|price|"
        r"investment (summary|offering|highlights)|executive summary|"
        r"marketing (brochure|package)|table of contents|presented by|"
        r"exclusively (listed|offered)|non-endorsement|actual property|representative)",
        re.IGNORECASE)
    for c in cover_lines:
        c = c.strip(" -|,")
        if not (3 <= len(c) <= 60):
            continue
        if junk.match(c) or any(b in c.lower() for b in BROKERS):
            continue
        if re.match(r"^\d+\s", c) or ADDR_RE_LOOSE.search(c):   # address line, not a name
            continue
        if re.search(r"[\$%]", c) or not re.search(r"[A-Za-z]{3}", c):
            continue
        letters = sum(ch.isalpha() for ch in c)
        if letters < 3 or letters / len(c) < 0.5:
            continue
        return c
    return ""

# ---------------------------------------------------------------- per-file


def _blank_row(path: Path) -> dict:
    return dict(old=path.name, ext=path.suffix.lower(), ac="ZZ", st="ZZ", city="ZZ",
                prop="", zips="", activity="", tags="", confidence="LOW", reason="",
                text_chars=0, skip=0)


def _analyse_zip(path: Path, r: dict) -> dict:
    try:
        with zipfile.ZipFile(path) as z:      # central directory only, no bulk decompression
            infos = z.infolist()[:4000]
            names = [i.filename for i in infos]
            # Read the first reasonably-sized PDF inside. This is what actually
            # identifies a data room -- path names alone almost never carry an address.
            inner, inner_err = "", ""
            cand = [i for i in infos
                    if i.filename.lower().endswith(".pdf") and 0 < i.file_size < 40_000_000]
            cand.sort(key=lambda i: i.file_size)
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td) / "inner.pdf"
                for i in cand[-1:] + cand[:1]:       # try the largest, then the smallest
                    try:
                        tmp.write_bytes(z.read(i))
                        _, bd = pdf_text(tmp, pages=8)
                    except Exception as e:
                        # Record it. A corrupt or encrypted inner PDF and a genuinely
                        # address-free archive must not abstain with the same wording.
                        inner_err = f"{Path(i.filename).name}: {type(e).__name__}"
                        continue
                    if len(bd) > MIN_TEXT_CHARS:
                        inner = bd
                        r["inner_pdf"] = i.filename
                        break
    except Exception as e:
        r["reason"] = f"ABSTAIN: unreadable archive ({type(e).__name__})"
        return r

    # Search order: the PDF inside, then the archive's own path names, then the
    # zip's filename (which the user often annotated with the address themselves).
    blob = " ".join([inner, norm_text(" ".join(names)), norm_text(path.stem)])
    r["text_chars"] = len(blob)
    r["ac"], _ = classify(blob)
    top = Path(names[0]).parts[0] if names else ""
    r["prop"] = top[:60]
    for src_name, src_txt in (("inner PDF", inner),
                              ("archive path names", norm_text(" ".join(names))),
                              ("zip filename", norm_text(path.stem))):
        locs = find_locations(src_txt)
        if not locs:
            continue
        st = {loc[1] for loc in locs}
        if len(st) > 1:
            r["st"], r["confidence"] = "MULTI", "REVIEW"
            r["reason"] = f"multiple states {sorted(st)} in {src_name}; confirm manually"
            return r
        r["city"], r["st"] = safe_component(locs[0][0]), locs[0][1]
        r["zips"] = ";".join(sorted({loc[2] for loc in locs})[:4])
        r["confidence"] = "HIGH" if src_name == "inner PDF" else "REVIEW"
        r["reason"] = f"{len(names)} entries; location from {src_name}"
        return r
    r["reason"] = (f"ABSTAIN: {len(names)} entries, no address in archive"
                   + (f"; top folder '{top}'" if top else "")
                   + (f"; inner PDF unreadable ({inner_err})" if inner_err else ""))
    return r


def analyse(path: Path, check_placeholders: bool = True) -> dict:
    """-> dict of extracted fields + confidence + reason."""
    r = _blank_row(path)

    if check_placeholders and is_placeholder(path):
        r["skip"] = 1
        r["reason"] = "ABSTAIN: online-only placeholder; reading it would hydrate the file"
        return r

    if r["ext"] == ".zip":
        return _analyse_zip(path, r)

    if r["ext"] != ".pdf":
        # Not a CIM at all. Prefixing it would only add noise to the folder, so this
        # file is left completely alone rather than filed into the ZZ work queue.
        r["skip"] = 1
        r["reason"] = "SKIP: not a PDF or ZIP; left untouched"
        return r

    try:
        cover, body = pdf_text(path, pages=8)
    except Exception as e:
        r["reason"] = f"ABSTAIN: PDF unreadable ({type(e).__name__}) - likely corrupt"
        return r

    r["text_chars"] = len(body)
    if len(body) < MIN_TEXT_CHARS:
        r["reason"] = f"ABSTAIN: only {len(body)} chars of text - scanned/rasterised, needs OCR"
        return r

    r["activity"] = ";".join(sorted(set(ACTIVITY_RE.findall(body))))

    locs, src = locate(cover, body)
    if not locs:                       # last resort: the user's own filename annotation
        locs, src = find_locations(norm_text(path.stem)), "filename"
    states = sorted({loc[1] for loc in locs})
    r["zips"] = ";".join(sorted({loc[2] for loc in locs})[:6])

    ac, scores = classify(body)
    r["ac"] = ac
    tags = stage_tags(body)
    low = body.lower()
    doc = next((k for k, ws in DOC_KEYWORDS.items() if any(w in low for w in ws)), "")
    if doc:
        tags.append(doc)
    r["tags"] = "-".join(tags)
    r["prop"] = property_name(cover)

    if not locs:
        r["reason"] = "ABSTAIN: no non-broker address found"
        return r
    if len(states) >= 2:
        r["st"], r["city"] = "MULTI", "ZZ"
        r["confidence"] = "REVIEW"
        r["reason"] = (f"multiple states {states} - portfolio or broker address leaked; "
                       "confirm manually")
        return r

    city = Counter(loc[0] for loc in locs).most_common(1)[0][0]   # most frequent city wins
    r["city"], r["st"] = safe_component(city), states[0]
    cover_txt = " ".join(cover)
    cover_cities = {c for c, _, _ in find_locations(cover_txt)}
    if is_portfolio(path.stem, cover_txt) or len(cover_cities) > 1:
        r["tags"] = "-".join(filter(None, [r["tags"], "PORT"]))
        r["confidence"] = "REVIEW"
        r["reason"] = (f"multi-property: cover shows {sorted(cover_cities)[:4] or 'portfolio wording'}"
                       " - the name carries only the lead city; confirm the rest")
        return r
    if ac == "ZZ":
        r["confidence"] = "REVIEW"
        r["reason"] = f"{src} address OK, asset class unresolved (scores {scores}) - likely image-only OM"
    elif src == "filename":
        r["confidence"] = "REVIEW"
        r["reason"] = f"address only from filename, not document text; class scores {scores}"
    else:
        r["confidence"] = "HIGH"
        r["reason"] = f"{src} address + class scores {scores}"
    return r

# ---------------------------------------------------------------- naming


def build_name(r: dict) -> str:
    if r.get("skip"):
        return r["old"]
    tag = "-".join(x for x in [r["ac"], r["st"], r["city"]] if x)
    if r["tags"]:
        tag += "-" + r["tags"]
    stem = norm_text(r["old"])
    stem = "".join(c for c in stem if c not in ILLEGAL).strip(". ")
    if PREFIX_RE.match(stem):                     # idempotent: never double-prefix
        stem = PREFIX_RE.sub("", stem)
    return f"[{tag}] {stem}"


def load_overrides(path: str | None) -> dict:
    """Read the operator's verified per-file decisions.

    These are judgement calls -- 'this data room is the Kerrville deal, I checked the
    extracted folder' -- and they belong to one folder, not to this script."""
    if not path:
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("old") or "").strip()
            if not key:
                continue
            out[key] = {k: (row.get(k) or "").strip()
                        for k in OVERRIDE_FIELDS if (row.get(k) or "").strip()}
    return out

# ---------------------------------------------------------------- pairing


def _pair_tokens(s: str) -> set:
    s = re.sub(r"[^a-z0-9 ]", " ", norm_text(s).lower())
    drop = {"self", "storage", "the", "om", "pdf", "zip", "vault", "deal", "room",
            "mnet", "offering", "memorandum", "for", "sale", "and", "sharplaunch",
            "com", "inquiry", "full", "final", "reduced", "size", "low", "res"}
    return {t for t in s.split() if len(t) > 2 and t not in drop}


def apply_pairing(rows: list) -> None:
    """Give an unresolved file the identity of a resolved one it clearly belongs to.

    This is what links a data-room zip to its OM -- e.g. 'Everything Self Storage Deal
    Room.zip' to the OM filed under its street address. Always REVIEW, never HIGH."""
    resolved = [r for r in rows if r["st"] not in ("ZZ", "MULTI") and not r.get("skip")]
    for r in rows:
        if r["st"] != "ZZ" or r.get("skip"):
            continue
        mine = _pair_tokens(r["old"]) | _pair_tokens(r.get("prop", ""))
        best, score = None, 0
        for q in resolved:
            overlap = mine & (_pair_tokens(q["old"]) | _pair_tokens(q.get("prop", "")))
            if len(overlap) > score:
                best, score = q, len(overlap)
        if best and score >= 2:
            if r["ac"] == "ZZ":
                r["ac"] = best["ac"]
            r["st"], r["city"], r["confidence"] = best["st"], best["city"], "REVIEW"
            r["reason"] = (f"paired to '{best['old'][:44]}' on {score} shared name tokens "
                           "- verify this is the same property")

# ---------------------------------------------------------------- main


def build_plan(files: list, src: Path, overrides: dict, check_placeholders: bool = True):
    """-> (rows, ledger, dupes). Pure enough to test; touches only reads."""
    ledger, by_hash = [], {}
    for p in files:
        ph = check_placeholders and is_placeholder(p)
        h = "" if ph else sha256(p)
        st = p.stat()
        ledger.append(dict(name=p.name, sha256=h, bytes=st.st_size,
                           mtime=st.st_mtime, placeholder=int(ph)))
        if h:
            by_hash.setdefault(h, []).append(p.name)
    dupes = {h: n for h, n in by_hash.items() if len(n) > 1}
    name_to_hash = {n: h for h, names in by_hash.items() for n in names}

    rows = []
    for p in files:
        r = analyse(p, check_placeholders=check_placeholders)
        rows.append(r)

    apply_pairing(rows)

    for r in rows:
        if r["old"] in overrides:
            r.update(overrides[r["old"]])
            r["skip"] = 0
        r["new"] = build_name(r)
        r["dup_of"] = ";".join(
            x for x in by_hash.get(name_to_hash.get(r["old"], ""), []) if x != r["old"])
        r["path_len"] = len(str(src)) + 1 + len(r["new"])
        probs = []
        if r["path_len"] > MAX_TOTAL_PATH:
            probs.append(f"PATH>{MAX_TOTAL_PATH}")
        if Path(r["new"]).stem.split(".")[0].upper() in WIN_RESERVED:
            probs.append("RESERVED_NAME")
        if r["new"] != r["new"].strip() or r["new"].rstrip().endswith("."):
            probs.append("TRAILING_DOT_OR_SPACE")
        if r["dup_of"]:
            probs.append("EXACT_DUPLICATE")
        r["flags"] = ";".join(probs)
    return rows, ledger, dupes


def find_collisions(rows: list) -> list:
    """Casefolded, because NTFS is case-insensitive and a Python set() is not.

    Left-alone rows are not renamed, but their names stay occupied -- a rename onto
    one is just as much a collision as a rename onto another rename. Seeding them
    first is what catches that; skipping them entirely reports a clean plan and
    leaves apply.ps1's runtime Test-Path to discover the clash one file at a time."""
    seen, collisions = {}, []
    for r in rows:
        if r.get("skip"):
            seen.setdefault(r["new"].casefold(), r["old"])
    for r in rows:
        if r.get("skip"):
            continue
        k = r["new"].casefold()
        if k in seen:
            collisions.append((seen[k], r["old"], r["new"]))
        seen[k] = r["old"]
    return collisions


def _ps_lit(s: str) -> str:
    """PowerShell single-quoted literal."""
    return "'" + s.replace("'", "''") + "'"


def render_scripts(rows: list, src: Path, collisions: list):
    apply_lines = [
        "# CIM rename - generated. Review rename_plan.csv FIRST.",
        "# Pause Dropbox sync before running. Nothing here overwrites.",
        "$ErrorActionPreference = 'Stop'",
        f"$src = {_ps_lit(str(src))}",
        "$fail = 0",
    ]
    if collisions:
        apply_lines.insert(1, "Write-Error 'Plan contains collisions - refusing to run. "
                              "Resolve them in rename_plan.csv and regenerate.'; exit 1")
    undo_lines = ["$ErrorActionPreference = 'Stop'", f"$src = {_ps_lit(str(src))}"]
    for r in rows:
        if r.get("skip") or r["new"] == r["old"]:
            apply_lines.append(f"# left alone: {r['old']}")
            continue
        if "EXACT_DUPLICATE" in (r["flags"] or ""):
            apply_lines.append(
                f"# SKIPPED duplicate: {r['old']}  (same sha256 as {r['dup_of']})")
            continue
        o, n = _ps_lit(r["old"]), _ps_lit(r["new"])
        apply_lines.append(
            f"if (Test-Path -LiteralPath (Join-Path $src {n})) "
            f"{{ Write-Host 'COLLISION, skipped: ' {n}; $fail++ }} "
            f"else {{ Rename-Item -LiteralPath (Join-Path $src {o}) -NewName {n} }}")
        undo_lines.append(f"Rename-Item -LiteralPath (Join-Path $src {n}) -NewName {o}")
    apply_lines.append('Write-Host "collisions skipped: $fail"')
    return "\n".join(apply_lines), "\n".join(undo_lines)


CSV_COLS = ["confidence", "flags", "old", "new", "ac", "st", "city", "prop",
            "tags", "activity", "zips", "dup_of", "path_len", "text_chars", "reason"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--overrides", help="CSV of verified per-file decisions: "
                                        "old," + ",".join(OVERRIDE_FIELDS))
    ap.add_argument("--allow-non-windows", action="store_true",
                    help="Run where online-only placeholders cannot be detected. "
                         "Only safe on a folder that is fully local.")
    a = ap.parse_args(argv)
    src, out = Path(a.src), Path(a.out)

    if not src.is_dir():
        sys.exit(f"REFUSING: --src is not a directory: {src}")
    # Validate before creating anything: an --out under --src must not leave a
    # stray directory inside the synced folder on its way to being rejected.
    if out.resolve().is_relative_to(src.resolve()):
        sys.exit("REFUSING: --out is inside --src. The ledger must live outside the "
                 "synced folder.")
    out.mkdir(parents=True, exist_ok=True)

    check_ph = placeholder_detection_available()
    if not check_ph and not a.allow_non_windows:
        sys.exit(
            "REFUSING: online-only placeholder detection needs Windows "
            "(os.stat_result.st_file_attributes). Running here would read every file "
            "and hydrate a cloud-synced folder in full. Re-run from Windows Python, or "
            "pass --allow-non-windows if this folder is entirely local.")
    if not check_ph:
        print("WARNING: placeholder detection unavailable; every file will be read.")

    overrides = load_overrides(a.overrides)
    files = sorted([p for p in src.iterdir() if p.is_file()], key=lambda p: p.name.lower())
    print(f"{len(files)} files in {src}")
    if not files:
        sys.exit("REFUSING: no files in --src.")

    rows, ledger, dupes = build_plan(files, src, overrides, check_placeholders=check_ph)

    with (out / "ledger.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "sha256", "bytes", "mtime", "placeholder"])
        w.writeheader()
        w.writerows(ledger)

    for i, r in enumerate(rows, 1):
        mark = "left alone" if r.get("skip") else r["new"][:92]
        print(f"  [{i:>2}/{len(rows)}] {r['confidence']:<6} {mark}")

    collisions = find_collisions(rows)

    with (out / "rename_plan.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    apply_ps, undo_ps = render_scripts(rows, src, collisions)
    (out / "apply.ps1").write_text(apply_ps, encoding="utf-8")
    (out / "undo.ps1").write_text(undo_ps, encoding="utf-8")

    counts = Counter(r["confidence"] for r in rows)
    renamed = [r for r in rows if not r.get("skip")]
    print("\n" + "=" * 66)
    print(f"files            : {len(files)}  ({len(renamed)} to rename, "
          f"{len(files) - len(renamed)} left alone)")
    print(f"confidence       : {dict(counts)}")
    print(f"overrides applied: {sum(1 for r in rows if r['old'] in overrides)}")
    print(f"exact duplicates : {len(dupes)} group(s) -> {dupes}")
    print(f"collisions       : {len(collisions)}")
    for a_, b_, n_ in collisions:
        print(f"   !! {a_}  <->  {b_}   both -> {n_}")
    print(f"max path length  : {max(r['path_len'] for r in rows)} (budget {MAX_TOTAL_PATH})")
    print(f"flagged rows     : {sum(1 for r in rows if r['flags'])}")
    print(f"\nwrote {out / 'rename_plan.csv'}, ledger.csv, apply.ps1, undo.ps1")
    print("NOTHING HAS BEEN RENAMED.")
    if collisions:
        sys.exit("HARD FAIL: collisions present. Resolve them in rename_plan.csv "
                 "before applying.")


if __name__ == "__main__":
    main()
