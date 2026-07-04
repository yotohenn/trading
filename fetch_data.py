#!/usr/bin/env python3
"""
Insider & Congress Trade Monitor - data fetcher.
Free, keyless public sources only:
  - SEC EDGAR daily indexes + Form 4 XML (insiders; 2-business-day lag)
  - SEC EDGAR 13F filings (institutions; quarterly, 45-day lag)
  - Senate/House Stock Watcher community datasets (congress PTRs; 30-45 day lag)
  - unitedstates.io legislators dataset (photos/party lookup)

Zero third-party dependencies: Python 3.8+ standard library only.
Writes data.js next to monitor.html. Run daily (Task Scheduler / cron).

This tool reads MANDATORY PUBLIC DISCLOSURES. It is informational only and
is not investment advice. Scores are heuristic signal strength, not
probabilities of profit.
"""

import json, os, re, sys, time, datetime as dt
import urllib.request, urllib.error
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

with open(os.path.join(HERE, "config.json"), "r", encoding="utf-8") as f:
    CFG = json.load(f)

UA = "TradeMonitor/1.0 (personal research; contact %s)" % CFG.get("contact_email", "user@example.com")
SPACING = float(CFG.get("request_spacing_seconds", 0.13))
NOTICES = []
_last_req = [0.0]

def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg))

def notice(msg):
    log("NOTICE: " + msg)
    NOTICES.append(msg)

def http_get(url, binary=False, retries=3, timeout=30):
    """Polite GET with UA + spacing (SEC asks max ~10 req/s; we do ~7)."""
    wait = SPACING - (time.time() - _last_req[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Encoding": "identity",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            _last_req[0] = time.time()
            return data if binary else data.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            _last_req[0] = time.time()
            if e.code == 404:
                return None
            if e.code in (403, 429):
                time.sleep(2 + attempt * 3)  # back off politely
            elif attempt == retries - 1:
                raise
        except Exception:
            _last_req[0] = time.time()
            if attempt == retries - 1:
                raise
            time.sleep(1 + attempt * 2)
    return None

def load_cache(name, default):
    p = os.path.join(CACHE_DIR, name)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_cache(name, obj):
    with open(os.path.join(CACHE_DIR, name), "w", encoding="utf-8") as f:
        json.dump(obj, f)

# ---------------------------------------------------------------- EDGAR Form 4

def business_days_back(n):
    days, d = [], dt.date.today()
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    return days

IDX_SPLIT = re.compile(r"\s{2,}")

def daily_form4_entries(day):
    """Yield (cik, company, accession, date) for Form 4 filings on a date."""
    q = (day.month - 1) // 3 + 1
    url = "https://www.sec.gov/Archives/edgar/daily-index/%d/QTR%d/form.%s.idx" % (
        day.year, q, day.strftime("%Y%m%d"))
    text = http_get(url)
    if text is None:
        return []  # weekend/holiday/not yet posted
    out = []
    for line in text.splitlines():
        parts = IDX_SPLIT.split(line.strip())
        if len(parts) >= 5 and parts[0] == "4":
            company, cik, filed, path = parts[1], parts[2], parts[3], parts[4]
            m = re.search(r"(\d{10}-\d{2}-\d{6})", path)
            if m:
                out.append((cik.strip(), company.strip(), m.group(1), filed.strip()))
    return out

def txt(el, path):
    v = el.findtext(path)
    return v.strip() if v else ""

def num(el, path):
    v = el.findtext(path)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def parse_form4(xml_text, accession, filed_date):
    """Parse ownershipDocument -> record with open-market buys/sells."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except Exception:
        return None
    if root.tag != "ownershipDocument":
        return None
    issuer = root.find("issuer")
    if issuer is None:
        return None
    rec = {
        "accession": accession,
        "filed": filed_date,
        "issuerCik": txt(issuer, "issuerCik").lstrip("0") or "0",
        "issuerName": txt(issuer, "issuerName"),
        "ticker": txt(issuer, "issuerTradingSymbol").upper(),
        "planned": txt(root, "aff10b5One") == "1",
        "owners": [],
        "buys": [],
        "sells": [],
    }
    for ow in root.findall("reportingOwner"):
        rel = ow.find("reportingOwnerRelationship")
        rec["owners"].append({
            "cik": txt(ow, "reportingOwnerId/rptOwnerCik").lstrip("0"),
            "name": txt(ow, "reportingOwnerId/rptOwnerName"),
            "director": rel is not None and txt(rel, "isDirector") == "1",
            "officer": rel is not None and txt(rel, "isOfficer") == "1",
            "tenPct": rel is not None and txt(rel, "isTenPercentOwner") == "1",
            "title": txt(rel, "officerTitle") if rel is not None else "",
        })
    ndt = root.find("nonDerivativeTable")
    if ndt is not None:
        for tr in ndt.findall("nonDerivativeTransaction"):
            code = txt(tr, "transactionCoding/transactionCode")
            shares = num(tr, "transactionAmounts/transactionShares/value")
            price = num(tr, "transactionAmounts/transactionPricePerShare/value")
            ad = txt(tr, "transactionAmounts/transactionAcquiredDisposedCode/value")
            date = txt(tr, "transactionDate/value")
            after = num(tr, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
            if not shares or price is None:
                continue
            row = {"date": date, "shares": shares, "price": price,
                   "dollars": round(shares * price, 2), "after": after}
            if code == "P" and ad == "A" and price > 0:
                rec["buys"].append(row)
            elif code == "S" and ad == "D" and price > 0:
                rec["sells"].append(row)
    return rec if (rec["buys"] or rec["sells"]) else None

def accession_xml(cik, accession):
    nodash = accession.replace("-", "")
    base = "https://www.sec.gov/Archives/edgar/data/%s/%s" % (cik.lstrip("0"), nodash)
    idx = http_get(base + "/index.json")
    if not idx:
        return None
    try:
        items = json.loads(idx)["directory"]["item"]
    except Exception:
        return None
    xmls = [i["name"] for i in items if i["name"].lower().endswith(".xml")]
    if not xmls:
        return None
    # prefer the ownership doc; usually the only xml
    xmls.sort(key=lambda n: (0 if ("form4" in n.lower() or "ownership" in n.lower() or "doc4" in n.lower()) else 1, len(n)))
    return http_get(base + "/" + xmls[0])

def collect_form4_events():
    seen = set(load_cache("seen.json", []))
    events = load_cache("events.json", [])  # rolling window of parsed records
    max_new = int(CFG.get("max_filings_per_run", 6000))
    new_entries = []
    for day in business_days_back(int(CFG.get("lookback_business_days", 7))):
        try:
            for e in daily_form4_entries(day):
                if e[2] not in seen:
                    new_entries.append(e)
        except Exception as ex:
            notice("Could not read EDGAR daily index for %s (%s)" % (day, ex))
    log("EDGAR: %d unseen Form 4 filings to inspect (cap %d)" % (len(new_entries), max_new))
    new_entries = new_entries[:max_new]
    parsed = 0
    for i, (cik, company, accession, filed) in enumerate(new_entries):
        seen.add(accession)
        try:
            x = accession_xml(cik, accession)
            if not x:
                continue
            rec = parse_form4(x, accession, filed)
            if rec:
                events.append(rec)
                parsed += 1
        except Exception:
            continue
        if i and i % 250 == 0:
            log("  ...%d/%d filings inspected (%d with open-market trades)" % (i, len(new_entries), parsed))
            save_cache("seen.json", list(seen))
            save_cache("events.json", events)
    # keep a rolling 120-day window of events; prune seen to ~60k
    cutoff = (dt.date.today() - dt.timedelta(days=120)).strftime("%Y%m%d")
    events = [e for e in events if e["filed"].replace("-", "") >= cutoff]
    save_cache("seen.json", list(seen)[-60000:])
    save_cache("events.json", events)
    log("EDGAR: %d filings parsed with real open-market buys/sells this run; %d in rolling window" % (parsed, len(events)))
    return events

# ------------------------------------------------------------------- Congress

AMOUNT_RE = re.compile(r"\$([\d,]+)")

def amount_midpoint(s):
    if not s:
        return 0
    nums = [int(x.replace(",", "")) for x in AMOUNT_RE.findall(s)]
    if not nums:
        return 0
    return (nums[0] + nums[1]) // 2 if len(nums) >= 2 else nums[0]

def parse_date(s):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None

def fetch_congress():
    """Community-maintained mirrors of official STOCK Act PTR data."""
    rows = []
    freshest = None
    sources = [
        ("Senate", "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json",
         "senator", "transaction_date"),
        ("House", "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json",
         "representative", "transaction_date"),
    ]
    for label, url, who_key, date_key in sources:
        try:
            raw = http_get(url, timeout=180)
            data = json.loads(raw)
            log("%s dataset: %d rows" % (label, len(data)))
            for r in data:
                who = (r.get(who_key) or "").strip()
                tdate = parse_date(r.get(date_key) or "")
                ddate = parse_date(r.get("disclosure_date") or "")
                ttype = (r.get("type") or "").lower()
                if not who or not tdate:
                    continue
                rows.append({
                    "chamber": label,
                    "who": who,
                    "ticker": (r.get("ticker") or "").replace("--", "").strip().upper(),
                    "asset": r.get("asset_description") or "",
                    "type": "buy" if "purchase" in ttype else ("sell" if "sale" in ttype else ttype),
                    "date": tdate.isoformat(),
                    "disclosed": ddate.isoformat() if ddate else None,
                    "mid": amount_midpoint(r.get("amount") or ""),
                    "amount": r.get("amount") or "",
                    "owner": r.get("owner") or "",
                })
                best = ddate or tdate
                if best and (freshest is None or best > freshest):
                    freshest = best
        except Exception as ex:
            notice("%s congress dataset unavailable (%s). Congress signals degraded." % (label, ex))
    if freshest:
        age = (dt.date.today() - freshest).days
        if age > 45:
            notice("Congress dataset looks STALE (newest disclosure %s, %d days old). "
                   "Treat congress panels as historical until the community mirror updates; "
                   "official sources: disclosures-clerk.house.gov / efdsearch.senate.gov" % (freshest, age))
    return rows

def congress_recent(rows, days):
    cutoff = dt.date.today() - dt.timedelta(days=days)
    return [r for r in rows if parse_date(r["date"]) and parse_date(r["date"]) >= cutoff]

# ------------------------------------------------------------------------ 13F

def strip_ns(tag):
    return tag.split("}", 1)[-1]

def fetch_13f_for(cik10, expect):
    url = "https://data.sec.gov/submissions/CIK%s.json" % cik10
    raw = http_get(url)
    if not raw:
        return None, "no submissions data"
    sub = json.loads(raw)
    name = sub.get("name", "")
    if expect and expect.upper() not in name.upper():
        return None, "CIK %s resolved to '%s' (expected ~'%s') - skipped, fix config" % (cik10, name, expect)
    rec = sub.get("filings", {}).get("recent", {})
    forms, accs, dates, periods = rec.get("form", []), rec.get("accessionNumber", []), rec.get("filingDate", []), rec.get("reportDate", [])
    picks = [(accs[i], dates[i], periods[i] if i < len(periods) else "")
             for i in range(len(forms)) if forms[i] == "13F-HR"]
    if not picks:
        return None, "no 13F-HR filings found for %s" % name
    picks = picks[:2]
    snaps = []
    for acc, fdate, period in picks:
        nodash = acc.replace("-", "")
        base = "https://www.sec.gov/Archives/edgar/data/%s/%s" % (int(cik10), nodash)
        idx = http_get(base + "/index.json")
        if not idx:
            continue
        try:
            items = json.loads(idx)["directory"]["item"]
        except Exception:
            continue
        cands = [i["name"] for i in items if i["name"].lower().endswith(".xml")
                 and "primary_doc" not in i["name"].lower()]
        if not cands:
            continue
        cands.sort(key=lambda n: 0 if "info" in n.lower() else 1)
        x = http_get(base + "/" + cands[0])
        if not x:
            continue
        try:
            root = ET.fromstring(x.encode("utf-8"))
        except Exception:
            continue
        total, holdings = 0, {}
        for el in root.iter():
            if strip_ns(el.tag) == "infoTable":
                nm, val, sh = "", 0, 0
                for ch in el.iter():
                    t = strip_ns(ch.tag)
                    if t == "nameOfIssuer":
                        nm = (ch.text or "").strip()
                    elif t == "value":
                        try: val = int(float(ch.text))
                        except Exception: val = 0
                    elif t == "sshPrnamt":
                        try: sh = int(float(ch.text))
                        except Exception: sh = 0
                total += val
                if nm:
                    h = holdings.setdefault(nm, {"value": 0, "shares": 0})
                    h["value"] += val
                    h["shares"] += sh
        # 13F values are whole USD for filings since 2023
        snaps.append({"accession": acc, "filed": fdate, "period": period,
                      "total": total, "holdings": holdings})
    if not snaps:
        return None, "could not parse 13F info tables for %s" % name
    return {"name": name, "snaps": snaps}, None

# ------------------------------------------------------------------- scoring

def role_points(owner):
    t = (owner.get("title") or "").lower()
    if owner.get("officer") and any(k in t for k in ("ceo", "chief executive", "cfo", "chief financial", "president", "coo", "chief operating", "chair")):
        return 14, "C-suite buyer (%s)" % (owner.get("title") or "officer")
    if owner.get("tenPct"):
        return 10, "10%+ owner buying"
    if owner.get("officer"):
        return 7, "Officer buying (%s)" % (owner.get("title") or "officer")
    if owner.get("director"):
        return 5, "Director buying"
    return 3, "Insider buying"

def build_signals(events, congress_rows, fund_positions, issuer_info, curated_pols):
    today = dt.date.today()
    win = int(CFG.get("cluster_window_days", 21))
    cutoff = today - dt.timedelta(days=win)
    corro_days = int(CFG.get("congress_corroboration_days", 45))
    min_buy = float(CFG.get("min_purchase_dollars", 2000))

    by_issuer = {}
    hist_buyers = {}  # ownerCik -> earliest buy date seen (for opportunistic proxy)
    for e in events:
        for b in e["buys"]:
            d = parse_date(b["date"]) or parse_date(e["filed"])
            for ow in e["owners"]:
                k = ow["cik"]
                if k and (k not in hist_buyers or (d and d < hist_buyers[k])):
                    hist_buyers[k] = d
        d = parse_date(e["filed"])
        if not d or d < cutoff:
            continue
        by_issuer.setdefault((e["issuerCik"], e["issuerName"], e["ticker"]), []).append(e)

    congress_recent_rows = congress_recent(congress_rows, corro_days)
    signals = []
    for (icik, iname, ticker), recs in by_issuer.items():
        buyers, planned_only, total_buy, total_sell, latest = {}, True, 0.0, 0.0, None
        for e in recs:
            buy_sum = sum(b["dollars"] for b in e["buys"])
            total_sell += sum(s["dollars"] for s in e["sells"])
            if not e["buys"]:
                continue
            if not e["planned"]:
                planned_only = False
            for ow in e["owners"]:
                b = buyers.setdefault(ow["cik"], {"owner": ow, "dollars": 0.0, "planned": e["planned"], "after": None, "dates": []})
                b["dollars"] += buy_sum / max(1, len(e["owners"]))
                for t in e["buys"]:
                    b["dates"].append(t["date"])
                    if t.get("after"):
                        b["after"] = t["after"]
                    d = parse_date(t["date"])
                    if d and (latest is None or d > latest):
                        latest = d
            total_buy += buy_sum
        if total_buy < min_buy or not buyers:
            continue

        factors, score = [], 20
        factors.append({"label": "Open-market insider purchase(s): $%s total" % f"{int(total_buy):,}", "pts": 20})
        n = len(buyers)
        if n >= 3:
            score += 28; factors.append({"label": "CLUSTER: %d distinct insiders bought within %dd (strongest documented signal)" % (n, win), "pts": 28})
        elif n == 2:
            score += 15; factors.append({"label": "Cluster: 2 distinct insiders bought within %dd" % win, "pts": 15})
        rp, rl = max((role_points(b["owner"]) for b in buyers.values()), key=lambda x: x[0])
        score += rp; factors.append({"label": rl, "pts": rp})
        if total_buy >= 1_000_000:
            score += 14; factors.append({"label": "Large size (≥ $1M)", "pts": 14})
        elif total_buy >= 250_000:
            score += 9; factors.append({"label": "Meaningful size (≥ $250K)", "pts": 9})
        elif total_buy >= 50_000:
            score += 4; factors.append({"label": "Moderate size (≥ $50K)", "pts": 4})
        # holdings-increase conviction (approximate, first buyer with data)
        for b in buyers.values():
            aft = b.get("after")
            if aft and b["dollars"] > 0:
                # shares bought unknown per-owner here; use filing-level ratio when single buyer
                if len(buyers) == 1 and recs and recs[0]["buys"]:
                    sh = sum(t["shares"] for r2 in recs for t in r2["buys"])
                    before = aft - sh
                    if before > 0 and sh / before >= 0.20:
                        score += 6; factors.append({"label": "Position increased ≥20%", "pts": 6})
                    elif before > 0 and sh / before >= 0.05:
                        score += 3; factors.append({"label": "Position increased ≥5%", "pts": 3})
                break
        # opportunistic proxy: no prior buy in our rolling history
        first_time = all(hist_buyers.get(k) and hist_buyers[k] >= cutoff for k in buyers)
        if first_time and len(events) > 500:
            score += 6; factors.append({"label": "First purchase by these insider(s) in tracked history", "pts": 6})
        # congress corroboration
        pol_hits = [r for r in congress_recent_rows if r["type"] == "buy" and r["ticker"] and r["ticker"] == ticker]
        if pol_hits:
            score += 12
            names = sorted({p["who"] for p in pol_hits})
            factors.append({"label": "Congress corroboration: %s also disclosed buying (≤%dd)" % (", ".join(names[:3]), corro_days), "pts": 12})
            sic = (issuer_info.get(icik, {}).get("sicDescription") or "").lower()
            for p in pol_hits:
                for cp in curated_pols:
                    if any(mn in p["who"].lower() for mn in cp["match_names"]):
                        if any(kw in sic for kw in cp.get("committee_sectors", [])):
                            score += 8
                            factors.append({"label": "COMMITTEE OVERLAP: %s sits on %s; issuer is in that sector" % (cp["name"], "/".join(cp["committees"])), "pts": 8})
                            break
        # fund corroboration (name match vs latest 13F increases)
        for fp in fund_positions:
            nm_u = iname.upper()
            for hname, delta in fp.get("increases", [])[:400]:
                if len(hname) > 4 and (hname in nm_u or nm_u.split()[0] in hname):
                    score += 6; factors.append({"label": "13F corroboration: %s increased/opened a matching position last quarter (name match)" % fp["display"], "pts": 6})
                    break
            else:
                continue
            break
        # penalties
        if latest:
            stale = (today - latest).days - 3
            if stale > 0:
                p = min(12, stale)
                score -= p; factors.append({"label": "Staleness: latest buy %dd ago" % (today - latest).days, "pts": -p})
        if total_sell > total_buy:
            score -= 10; factors.append({"label": "Other insiders sold more ($%s) than was bought in window" % f"{int(total_sell):,}", "pts": -10})
        if planned_only:
            score = min(score, 25)
            factors.append({"label": "All buys under pre-set 10b5-1 plans (scheduled, weak signal) - score capped", "pts": 0})

        score = max(5, min(97, score))
        info = issuer_info.get(icik, {})
        signals.append({
            "ticker": ticker or "(no ticker)",
            "company": iname,
            "sector": info.get("sicDescription", ""),
            "score": score,
            "bucket": ("Very strong" if score >= 75 else "Strong" if score >= 55 else "Notable" if score >= 35 else "Weak"),
            "totalBuy": int(total_buy),
            "totalSell": int(total_sell),
            "buyers": [{"name": b["owner"]["name"], "title": b["owner"].get("title") or ("Director" if b["owner"]["director"] else "Insider"),
                        "dollars": int(b["dollars"])} for b in buyers.values()],
            "latestBuy": latest.isoformat() if latest else None,
            "factors": factors,
            "polHits": [{"who": p["who"], "date": p["date"], "amount": p["amount"]} for p in pol_hits][:5],
        })
    signals.sort(key=lambda s: -s["score"])
    return signals

# ------------------------------------------------------------------ profiles

def issuer_details(cik):
    cache = load_cache("issuers.json", {})
    if cik in cache:
        return cache
    raw = http_get("https://data.sec.gov/submissions/CIK%s.json" % cik.zfill(10))
    if raw:
        try:
            s = json.loads(raw)
            cache[cik] = {"sicDescription": s.get("sicDescription", ""), "name": s.get("name", "")}
        except Exception:
            cache[cik] = {}
    else:
        cache[cik] = {}
    save_cache("issuers.json", cache)
    return cache

def legislator_lookup():
    cache = load_cache("legislators.json", None)
    if cache:
        return cache
    try:
        raw = http_get("https://theunitedstates.io/congress-legislators/legislators-current.json", timeout=60)
        data = json.loads(raw)
        out = {}
        for m in data:
            nm = m.get("name", {})
            last = (nm.get("last") or "").lower()
            terms = m.get("terms", [])
            t = terms[-1] if terms else {}
            out[(nm.get("official_full") or "").lower()] = {
                "bioguide": m.get("id", {}).get("bioguide", ""),
                "party": t.get("party", "")[:1],
                "state": t.get("state", ""),
                "chamber": "Senate" if t.get("type") == "sen" else "House",
                "last": last,
            }
        save_cache("legislators.json", out)
        return out
    except Exception as ex:
        notice("Could not fetch legislators dataset for photos/party (%s)" % ex)
        return {}

def find_legislator(name, table):
    n = name.lower()
    if n in table:
        return table[n]
    for full, rec in table.items():
        if rec["last"] and rec["last"] in n:
            return rec
    return None

# ----------------------------------------------------------------------- main

def main():
    t0 = time.time()
    log("Starting run. Free public sources only; polite rate limits - a first run can take 10-25 min.")

    events = collect_form4_events()

    congress_rows = fetch_congress()
    lb_days = int(CFG.get("congress_leaderboard_days", 90))
    recent_rows = congress_recent(congress_rows, lb_days)
    prior_rows = [r for r in congress_congress_window(congress_rows, lb_days * 2, lb_days)] if congress_rows else []

    # 13F watchlist
    fund_positions, fund_boards = [], []
    for f in CFG.get("watchlist_funds", []):
        try:
            res, err = fetch_13f_for(f["cik"].zfill(10) if not f["cik"].startswith("0") else f["cik"], f.get("expect_name", ""))
        except Exception as ex:
            res, err = None, str(ex)
        if err:
            notice("13F %s: %s" % (f["display"], err))
        if not res:
            continue
        snaps = res["snaps"]
        cur = snaps[0]
        prev = snaps[1] if len(snaps) > 1 else None
        d_abs = cur["total"] - (prev["total"] if prev else 0)
        d_pct = (d_abs / prev["total"] * 100) if prev and prev["total"] else None
        increases = []
        if prev:
            for nm, h in cur["holdings"].items():
                pv = prev["holdings"].get(nm, {}).get("value", 0)
                if h["value"] > pv:
                    increases.append((nm.upper(), h["value"] - pv))
            increases.sort(key=lambda x: -x[1])
        top = sorted(cur["holdings"].items(), key=lambda kv: -kv[1]["value"])[:5]
        fund_positions.append({"display": f["display"], "increases": increases})
        fund_boards.append({
            "name": f["display"], "cik": f["cik"], "domain": f.get("domain", ""),
            "bio": f.get("bio", ""), "sector": f.get("sector", ""),
            "asOf": cur["period"] or cur["filed"], "filed": cur["filed"],
            "totalValue": cur["total"], "deltaValue": d_abs if prev else None,
            "deltaPct": round(d_pct, 1) if d_pct is not None else None,
            "topHoldings": [{"name": nm, "value": h["value"]} for nm, h in top],
            "topIncreases": [{"name": nm, "delta": dv} for nm, dv in increases[:5]],
        })
        log("13F %s: $%s as of %s" % (f["display"], f"{cur['total']:,}", cur["period"]))

    # issuer sectors for the top signal candidates only (limit fetches)
    win = int(CFG.get("cluster_window_days", 21))
    cutoff = (dt.date.today() - dt.timedelta(days=win))
    hot_issuers = {e["issuerCik"] for e in events if parse_date(e["filed"]) and parse_date(e["filed"]) >= cutoff and e["buys"]}
    issuer_info = load_cache("issuers.json", {})
    for cik in list(hot_issuers)[:300]:
        if cik not in issuer_info:
            issuer_info = issuer_details(cik)

    signals = build_signals(events, congress_rows, fund_positions, issuer_info, CFG.get("watchlist_politicians", []))
    log("Signals built: %d (top score %s)" % (len(signals), signals[0]["score"] if signals else "-"))

    # politician leaderboard: activity in last N days vs prior N days
    legs = legislator_lookup()
    pol_agg = {}
    for r in recent_rows:
        a = pol_agg.setdefault(r["who"], {"buy": 0, "sell": 0, "n": 0, "tickers": {}})
        a["n"] += 1
        if r["type"] == "buy":
            a["buy"] += r["mid"]
            if r["ticker"]:
                a["tickers"][r["ticker"]] = a["tickers"].get(r["ticker"], 0) + r["mid"]
        elif r["type"] == "sell":
            a["sell"] += r["mid"]
    prior_agg = {}
    for r in prior_rows:
        if r["type"] == "buy":
            prior_agg[r["who"]] = prior_agg.get(r["who"], 0) + r["mid"]
    pol_board = []
    curated = {tuple(cp["match_names"]): cp for cp in CFG.get("watchlist_politicians", [])}
    for who, a in pol_agg.items():
        cp = next((c for mns, c in curated.items() if any(m in who.lower() for m in mns)), None)
        leg = find_legislator(who, legs)
        prior = prior_agg.get(who, 0)
        dpct = round((a["buy"] - prior) / prior * 100, 1) if prior else None
        pol_board.append({
            "name": who,
            "party": (cp or {}).get("party") or (leg or {}).get("party", ""),
            "state": (cp or {}).get("state") or (leg or {}).get("state", ""),
            "chamber": (cp or {}).get("chamber") or (leg or {}).get("chamber", ""),
            "bioguide": (cp or {}).get("bioguide") or (leg or {}).get("bioguide", ""),
            "bio": (cp or {}).get("bio", ""),
            "sector": (cp or {}).get("sector", ""),
            "committees": (cp or {}).get("committees", []),
            "buyDollars": a["buy"], "sellDollars": a["sell"], "trades": a["n"],
            "deltaPct": dpct,
            "topTickers": sorted(a["tickers"].items(), key=lambda kv: -kv[1])[:5],
            "estimate": True,
        })
    pol_board.sort(key=lambda p: -p["buyDollars"])

    # insider leaderboard from window purchases
    ins_agg = {}
    for e in events:
        d = parse_date(e["filed"])
        if not d or d < cutoff or not e["buys"]:
            continue
        for ow in e["owners"]:
            share = 1.0 / max(1, len(e["owners"]))
            k = ow["cik"] or ow["name"]
            a = ins_agg.setdefault(k, {"name": ow["name"], "title": ow.get("title") or ("Director" if ow.get("director") else "Insider"),
                                       "company": e["issuerName"], "ticker": e["ticker"], "buy": 0.0,
                                       "after": None, "px": None, "sh": 0.0})
            for t in e["buys"]:
                a["buy"] += t["dollars"] * share
                a["sh"] += t["shares"] * share
                a["px"] = t["price"]
                if t.get("after"):
                    a["after"] = t["after"]
    ins_board = []
    for a in ins_agg.values():
        hv = a["after"] * a["px"] if (a["after"] and a["px"]) else None
        before = (a["after"] - a["sh"]) if a["after"] else None
        dpct = round(a["sh"] / before * 100, 1) if before and before > 0 else None
        ins_board.append({"name": a["name"], "title": a["title"], "company": a["company"], "ticker": a["ticker"],
                          "buyDollars": int(a["buy"]),
                          "holdingsValue": int(hv) if hv else None,
                          "deltaPct": dpct})
    ins_board.sort(key=lambda x: -x["buyDollars"])
    ins_board = ins_board[:40]

    data = {
        "generatedAt": dt.datetime.now().isoformat(timespec="seconds"),
        "mode": "LIVE",
        "params": {"clusterWindowDays": win, "lookbackBusinessDays": CFG.get("lookback_business_days"),
                   "congressWindowDays": lb_days},
        "notices": NOTICES,
        "signals": signals[:60],
        "insiderBoard": ins_board,
        "politicianBoard": pol_board[:40],
        "fundBoard": fund_boards,
        "scoreDoc": [
            "Base 20: at least one true open-market purchase (Form 4 code P; grants, ESPP and option exercises are excluded).",
            "+15/+28: 2 / 3+ distinct insiders buying within the window - cluster buys show roughly double the abnormal returns of solo buys in academic studies (Lakonishok & Lee 2001; 2iQ research).",
            "+5..14: seniority of the buyer (C-suite strongest, per C-suite predictiveness studies).",
            "+4..14: dollar size of the buying.",
            "+3/+6: insider increased their existing stake by 5%/20%+.",
            "+6: first buy by these insiders in tracked history (proxy for 'opportunistic' trades, Cohen-Malloy-Pomorski 2012).",
            "+12: a member of Congress disclosed buying the same ticker within 45 days; +8 more if the member sits on a committee overseeing the issuer's sector (highest-signal subset in the literature).",
            "+6: a watchlist fund opened/increased a matching position in its latest 13F (name-match heuristic).",
            "-1/day: staleness beyond 3 days; -10: insiders selling more than buying; 10b5-1-only buys capped at 25 (pre-scheduled).",
            "Scores are clamped 5-97 and are heuristic SIGNAL STRENGTH, not probabilities of profit. Historically, insider cluster buys averaged ~3.8% abnormal return over 21 trading days vs ~2% for non-cluster (2iQ) - averages across many events, not a promise about any single stock.",
        ],
    }
    out = os.path.join(HERE, "data.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("window.MONITOR_DATA = ")
        json.dump(data, f, ensure_ascii=False)
        f.write(";")
    log("Wrote %s in %.1f min. Open monitor.html to view." % (out, (time.time() - t0) / 60))

def congress_congress_window(rows, older_days, newer_days):
    """Rows between older_days and newer_days ago (for prior-period comparison)."""
    hi = dt.date.today() - dt.timedelta(days=newer_days)
    lo = dt.date.today() - dt.timedelta(days=older_days)
    for r in rows:
        d = parse_date(r["date"])
        if d and lo <= d < hi:
            yield r

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted - partial caches were saved; next run resumes.")
    except Exception as e:
        print("\nERROR: %s" % e)
        print("If this repeats, the source may be down - try again later.")
        sys.exit(1)
