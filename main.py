"""OutbreakRadar backend. Run with: uvicorn main:app --reload"""
import hashlib, hmac, json, math, os, random, secrets, sqlite3, time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
DB = ROOT / "outbreakradar.db"
CATEGORIES = ["fever", "diarrhoea", "cough", "rash", "other"]
BASE = {"fever": 4, "diarrhoea": 3, "cough": 4, "rash": 2, "other": 2}
CITY = (13.5820, 74.7150)  # centre of the demo wards: change to your city
WEIGHTS = {"baseline": 40, "spatial": 30, "unique": 20, "health": 10}  # our design choice, not a validated model
LADDER = [(70, "Critical"), (45, "Rising"), (25, "Watch"), (0, "Normal")]
RANK = {"Normal": 0, "Watch": 1, "Rising": 2, "Critical": 3}
CLUSTER_M = 500          # cluster radius in metres
MIN_RATIO = 1.5          # volume gate: below 1.5x the ward's own normal, status stays Normal
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
RATE_PER_HOUR = 5        # max reports per citizen account per hour (strict, to limit misuse)
FIELD_RATE_PER_HOUR = 60 # verified health workers visit many households, so they get a higher (still capped) limit
FIELD_DUP_MINUTES = 0.5  # a verified worker may file the same symptom for the same ward many times, but not twice within 30 seconds (double taps)
FIELD_MAX_INDEPENDENT = 10  # one worker counts as at most 10 independent household visits toward an alert
AUTH_EMAIL = os.environ.get("OR_AUTHORITY_EMAIL", "authority@outbreakradar.local")
AUTH_PW = os.environ.get("OR_AUTHORITY_PASSWORD")  # if not set, a random password is generated and printed once on first start
DEMO = os.environ.get("OR_DEMO") == "1"          # outbreak simulator is OFF unless you start the server with OR_DEMO=1
COVER_M = 3000                                    # a location further than this from every ward centre is "not covered"
FAILS = {}               # failed sign-in attempts per email
ACTIONS = {
    "investigate": ("investigating", "Needs investigation"),
    "false_alarm": ("false_alarm", "Marked as false alarm"),
    "confirm": ("confirmed", "Confirmed by authority"),
    "escalate": ("escalated", "Escalated"),
}

SCHEMA = """
create table if not exists wards(id integer primary key, name text, lat real, lng real);
create table if not exists baselines(ward_id int, category text, normal_daily real, primary key(ward_id, category));
create table if not exists history(ward_id int, category text, day text, n int, primary key(ward_id, category, day));
create table if not exists reports(id integer primary key autoincrement, category text, severity text, ward_id int,
  lat real, lng real, reporter_id text, role text, ts text, is_simulated int default 0,
  user_id int, status text default 'ok');
create table if not exists alerts(id integer primary key autoincrement, ward_id int, category text, current_count int,
  baseline real, cluster int, unique_reporters int, score real, status text, workflow text default 'open',
  components text, text text, simulated int, created text, updated text);
create table if not exists alert_events(id integer primary key autoincrement, alert_id int, action text, ts text, actor text);
create table if not exists advisories(id integer primary key autoincrement, ward_id int, message text, ts text);
create table if not exists users(id integer primary key autoincrement, name text, email text unique, pw_hash text, salt text,
  role text, verified int default 0, status text default 'active', trust int default 100, created text);
create table if not exists sessions(token_hash text primary key, user_id int, expires text);
create table if not exists audit(id integer primary key autoincrement, actor text, action text, target text, detail text, ts text);
create table if not exists settings(k text primary key, v text);
create index if not exists rep_idx on reports(ward_id, category, ts);
"""


def now(minutes=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


def haversine(lat1, lng1, lat2, lng2):
    """Distance in metres between two lat/lng points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


@contextmanager
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def ward_pos(i, ctr=CITY):
    # Six placeholder wards in a 3 x 2 grid around the coverage centre (about 2.6 km apart east-west, 2.2 km north-south)
    return ctr[0] + (0.5 - i // 3) * 0.02, ctr[1] + (i % 3 - 1) * 0.024


def get_center(c):
    r = c.execute("select v from settings where k='center'").fetchone()
    return tuple(json.loads(r["v"])) if r else CITY


def init():
    with db() as c:
        c.executescript(SCHEMA)
        # Older builds shipped public demo accounts. Remove them so nobody can sign in with a known password.
        old = [r["id"] for r in c.execute("select id from users where email like '%@outbreakradar.demo'")]
        for uid in old:
            c.execute("delete from sessions where user_id=?", (uid,))
            c.execute("update reports set user_id=null where user_id=?", (uid,))
            c.execute("delete from users where id=?", (uid,))
        if not c.execute("select 1 from users where role='authority'").fetchone():
            pw = AUTH_PW or secrets.token_urlsafe(9)
            make_user(c, "Health Authority", AUTH_EMAIL, pw, "authority", 1)
            if not AUTH_PW:
                print(f"\n  First start. Authority account created.\n  Email:    {AUTH_EMAIL}\n  Password: {pw}\n"
                      "  Save this now. It is not shown again. Set OR_AUTHORITY_PASSWORD to choose your own.\n")
        ctr = get_center(c)
        for i in range(6):  # keep ward positions in sync with the coverage centre
            c.execute("update wards set lat=?, lng=? where id=?", (*ward_pos(i, ctr), i + 1))
        if c.execute("select count(*) from wards").fetchone()[0]:
            return
        rng = random.Random(7)
        for i in range(6):
            wid = i + 1
            lat, lng = ward_pos(i, ctr)
            c.execute("insert into wards values(?,?,?,?)", (wid, f"Ward {wid}", lat, lng))
            for cat, b in BASE.items():
                normal = max(1, b + (wid * 7 + len(cat)) % 3 - 1)
                c.execute("insert into baselines values(?,?,?)", (wid, cat, normal))
                for d in range(1, 31):  # 30-day history around the normal level (synthetic until real data is connected)
                    day = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d")
                    c.execute("insert into history values(?,?,?,?)", (wid, cat, day, max(0, round(rng.gauss(normal, 1)))))


# ---------------------------------------------------------------- detection

def evaluate(c, wid, cat):
    """Baseline ratio + spatial cluster + evidence check -> signal score and status."""
    ward = c.execute("select name from wards where id=?", (wid,)).fetchone()["name"]
    base = c.execute("select normal_daily from baselines where ward_id=? and category=?", (wid, cat)).fetchone()[0]
    rows = c.execute("select lat,lng,reporter_id,role,is_simulated,user_id from reports where ward_id=? and category=? and ts>=? and status='ok'",
                     (wid, cat, now(-48 * 60))).fetchall()
    cur = c.execute("select count(*) from reports where ward_id=? and category=? and ts>=? and status='ok'",
                    (wid, cat, now(-24 * 60))).fetchone()[0]
    n, ratio = len(rows), cur / base
    ids = [r["reporter_id"] for r in rows]
    # Citizens count once per account. A verified health worker's reports are separate household visits,
    # but one worker counts as at most FIELD_MAX_INDEPENDENT independent sources.
    seen, keys = {}, []
    for r in rows:
        if r["role"] == "health_worker":
            seen[r["reporter_id"]] = seen.get(r["reporter_id"], 0) + 1
            keys.append(f"{r['reporter_id']}#{min(seen[r['reporter_id']], FIELD_MAX_INDEPENDENT)}")
        else:
            keys.append(r["reporter_id"])
    unique = len(set(keys))
    hw = sum(r["role"] == "health_worker" for r in rows)
    cluster = max((sum(haversine(r["lat"], r["lng"], o["lat"], o["lng"]) <= CLUSTER_M for o in rows) for r in rows), default=0)

    comp = {
        "baseline": min(1, max(0, (ratio - 1) / 3)),                    # 1x = 0, 4x or more = 1
        "spatial": (cluster / n) * min(1, n / 5) if n else 0,            # share of reports within 500 m
        "unique": unique / n if n else 0,                                # share of independent reporters
        "health": min(1, (hw / n) / 0.2) if n else 0,                    # 20% health workers = full marks
    }
    score = round(sum(WEIGHTS[k] * v for k, v in comp.items()), 1)

    blocked = None
    if ratio >= MIN_RATIO and (unique < 3 or unique / n < 0.5):
        top = max(ids.count(i) for i in set(ids))
        blocked = (f"Not enough independent evidence: {top} of {n} reports come from one person or device "
                   f"({unique} unique reporters), so no alert fires.")
    if blocked:  # flag accounts that supplied most of a one-sided signal
        uids = [r["user_id"] for r in rows if r["user_id"] and r["role"] != "health_worker"]
        for uid in set(uids):
            if uids.count(uid) / n >= 0.5:
                auto_flag(c, uid, ward, cat, uids.count(uid), n)
    # Activity status and alert eligibility are intentionally separate.
    # A ward can be above its normal level but still be prevented from
    # raising an alert when the independent-evidence gate is not met.
    if ratio < MIN_RATIO:
        status = "Normal"
    else:
        status = next(s for t, s in LADDER if score >= t)
        # Crossing the local-volume gate should never be displayed as Normal.
        if status == "Normal":
            status = "Watch"

    eligible_for_alert = status in ("Rising", "Critical") and blocked is None

    return {
        "ward_id": wid, "ward": ward, "category": cat, "current": cur, "baseline": base, "ratio": round(ratio, 2),
        "cluster": cluster, "total": n, "unique": unique, "health_workers": hw, "score": score, "status": status,
        "components": comp, "blocked": blocked,
        "alert_blocked": bool(blocked),
        "eligible_for_alert": eligible_for_alert,
        "simulated": sum(r["is_simulated"] for r in rows),
        "text": (f"{ward}, {cat}: {base:g} \u2192 {cur} ({ratio:.1f}x normal), {cluster} within {CLUSTER_M} m, "
                 f"{unique} unique reporters, {hw} from health workers."),
    }


def log(c, aid, action, actor=None):
    c.execute("insert into alert_events(alert_id,action,ts,actor) values(?,?,?,?)", (aid, action, now(), actor))


def refresh_alert(c, s):
    """Create/update an authority alert only when activity is high enough
    and the independent-evidence gate has passed.

    The signal status describes observed activity; it is never forced back
    to Normal merely because an alert is blocked.
    """
    a = c.execute("select id,status from alerts where ward_id=? and category=? and workflow in ('open','investigating','escalated')",
                  (s["ward_id"], s["category"])).fetchone()
    if not a and not s["eligible_for_alert"]:
        return
    vals = (s["current"], s["baseline"], s["cluster"], s["unique"], s["score"], s["status"],
            json.dumps(s["components"]), s["text"], s["simulated"], now())
    if not a:
        cur = c.execute("insert into alerts(current_count,baseline,cluster,unique_reporters,score,status,components,text,"
                        "simulated,updated,ward_id,category,created) values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        vals + (s["ward_id"], s["category"], now()))
        log(c, cur.lastrowid, f"Alert raised at {s['status']}")
    else:
        if a["status"] != s["status"]:
            log(c, a["id"], f"Status changed: {a['status']} to {s['status']}")
        c.execute("update alerts set current_count=?,baseline=?,cluster=?,unique_reporters=?,score=?,status=?,components=?,"
                  "text=?,simulated=?,updated=? where id=?", vals + (a["id"],))


def sync(c, wid, cat):
    refresh_alert(c, evaluate(c, wid, cat))


def scan(c):
    out = []
    for w in c.execute("select * from wards").fetchall():
        sigs = [evaluate(c, w["id"], cat) for cat in CATEGORIES]
        for s in sigs:
            refresh_alert(c, s)
        top = max(sigs, key=lambda s: (RANK[s["status"]], s["score"]))
        out.append({"id": w["id"], "name": w["name"], "lat": w["lat"], "lng": w["lng"], "status": top["status"],
                    "score": top["score"], "top_category": top["category"], "signals": sigs})
    return out


# ---------------------------------------------------------------- helpers

def ward(c, wid):
    w = c.execute("select * from wards where id=?", (wid,)).fetchone()
    if not w:
        raise HTTPException(404, "Ward not found")
    return w


def offset(w, dx, dy):
    """Move dx metres east and dy metres north of a ward centre."""
    return w["lat"] + dy / 111320, w["lng"] + dx / (111320 * math.cos(math.radians(w["lat"])))


def add_report(c, cat, sev, wid, lat, lng, reporter, role, sim=0, ts=None, uid=None):
    c.execute("insert into reports(category,severity,ward_id,lat,lng,reporter_id,role,ts,is_simulated,user_id) values(?,?,?,?,?,?,?,?,?,?)",
              (cat, sev, wid, lat, lng, reporter, role, ts or now(), sim, uid))


# ---------------------------------------------------------------- accounts and security

def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


def make_user(c, name, email, pw, role, verified=0):
    salt = secrets.token_hex(16)
    return c.execute("insert into users(name,email,pw_hash,salt,role,verified,created) values(?,?,?,?,?,?,?)",
                     (name, email.lower(), hash_pw(pw, salt), salt, role, verified, now())).lastrowid


def pub(u):
    return {k: u[k] for k in ("id", "name", "email", "role", "verified", "status", "trust")}


def audit(c, actor, action, target, detail=""):
    c.execute("insert into audit(actor,action,target,detail,ts) values(?,?,?,?,?)", (actor, action, target, detail, now()))


def new_session(c, uid):
    tok = secrets.token_urlsafe(32)  # only a hash of the token is stored
    c.execute("insert into sessions values(?,?,?)", (hashlib.sha256(tok.encode()).hexdigest(), uid, now(12 * 60)))
    return tok


def current_user(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    h = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with db() as c:
        r = c.execute("select u.* from sessions s join users u on u.id=s.user_id where s.token_hash=? and s.expires>?",
                      (h, now())).fetchone()
    return dict(r) if r and r["status"] != "banned" else None


def need_user(u=Depends(current_user)):
    if not u:
        raise HTTPException(401, "Please sign in to continue.")
    return u


def need_authority(u=Depends(need_user)):
    if u["role"] != "authority":
        raise HTTPException(403, "Only the health authority can do this.")
    return u


def restrict(c, uid, status):
    # Suspended or banned accounts stop counting toward alerts (their reports are rejected).
    c.execute("update users set status=? where id=?", (status, uid))
    c.execute("update reports set status='rejected' where user_id=?", (uid,))
    if status == "banned":
        c.execute("delete from sessions where user_id=?", (uid,))


def penalise(c, uid, points, why):
    c.execute("update users set trust=max(0, trust-?) where id=?", (points, uid))
    u = c.execute("select status, trust from users where id=?", (uid,)).fetchone()
    if u["status"] == "active" and u["trust"] < 40:
        restrict(c, uid, "suspended")
        audit(c, "system", "auto_suspend", f"user {uid}", f"Trust score fell to {u['trust']} ({why})")


def auto_flag(c, uid, ward_name, cat, k, n):
    # One automatic flag per account per day, so repeated scans do not keep lowering trust.
    if c.execute("select 1 from audit where action='auto_flag' and target=? and ts>=?", (f"user {uid}", now(-24 * 60))).fetchone():
        return
    penalise(c, uid, 25, "flagged for one-sided reporting")
    audit(c, "system", "auto_flag", f"user {uid}", f"{k} of {n} reports for {ward_name}, {cat} came from this account")


# ---------------------------------------------------------------- API

app = FastAPI(title="OutbreakRadar")
init()


class ReportIn(BaseModel):
    category: Literal["fever", "diarrhoea", "cough", "rash", "other"]
    severity: Literal["low", "medium", "high"]
    ward_id: Optional[int] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class ActionIn(BaseModel):
    action: Literal["investigate", "false_alarm", "confirm", "escalate"]


class AdvisoryIn(BaseModel):
    ward_id: Optional[int] = None
    message: str = Field(min_length=5, max_length=500)


def nearest_ward(c, lat, lng):
    ws = c.execute("select * from wards").fetchall()
    w = min(ws, key=lambda x: haversine(lat, lng, x["lat"], x["lng"]))
    return w, haversine(lat, lng, w["lat"], w["lng"])


@app.post("/api/reports")
def post_report(r: ReportIn, u=Depends(need_user)):
    if u["status"] != "active":
        raise HTTPException(403, "Your account is suspended, so new reports are blocked. Please contact the health authority.")
    with db() as c:
        has_pos = r.lat is not None and r.lng is not None
        if r.ward_id is not None:                       # the reporter chose a ward: it wins
            w = ward(c, r.ward_id)
            near = has_pos and haversine(r.lat, r.lng, w["lat"], w["lng"]) <= COVER_M
        elif has_pos:                                   # otherwise use the ward nearest to the device position
            w, d = nearest_ward(c, r.lat, r.lng)
            if d > COVER_M:
                raise HTTPException(422, "Your location is outside the areas we cover so far. Please choose your ward from the list.")
            near = True
        else:
            raise HTTPException(422, "Share your location or choose your ward.")
        wid = w["id"]
        field = u["role"] == "health_worker" and bool(u["verified"])   # only verified workers get field limits
        limit = FIELD_RATE_PER_HOUR if field else RATE_PER_HOUR
        if c.execute("select count(*) from reports where user_id=? and ts>=?", (u["id"], now(-60))).fetchone()[0] >= limit:
            raise HTTPException(429, f"Report limit reached ({limit} per hour). Please try again later.")
        window = FIELD_DUP_MINUTES if field else 60
        if c.execute("select 1 from reports where user_id=? and ward_id=? and category=? and ts>=?",
                     (u["id"], wid, r.category, now(-window))).fetchone():
            raise HTTPException(409, "That report was just sent. Please wait a moment before sending it again." if field
                                else "You already reported this symptom for this ward in the last hour.")
        lat, lng = (r.lat, r.lng) if near else offset(w, random.uniform(-300, 300), random.uniform(-300, 300))
        role = "health_worker" if u["role"] == "health_worker" and u["verified"] else "citizen"  # unverified claims count as citizen
        add_report(c, r.category, r.severity, wid, lat, lng, f"user-{u['id']}", role, 0, None, u["id"])
        sync(c, wid, r.category)
    return {"ok": True, "ward": w["name"]}


@app.get("/api/config")
def get_config():
    return {"demo": DEMO}


@app.get("/api/locate")  # tells a visitor which ward they are in and how it looks. Coordinates are not stored.
def locate(lat: float, lng: float):
    with db() as c:
        w, d = nearest_ward(c, lat, lng)
        if d > COVER_M:
            return {"covered": False}
        sigs = [evaluate(c, w["id"], cat) for cat in CATEGORIES]
        top = max(sigs, key=lambda s: (RANK[s["status"]], s["score"]))
        return {"covered": True, "ward_id": w["id"], "ward": w["name"], "status": top["status"],
                "score": top["score"], "category": top["category"] if top["status"] != "Normal" else None}


@app.get("/api/wards")
def get_wards():
    with db() as c:
        return scan(c)


@app.get("/api/heat")  # ward-level only: exact coordinates never leave the backend
def get_heat(hours: int = 48, category: str = "", severity: str = ""):
    sql, args = "select ward_id, count(*) n from reports where status='ok' and ts>=?", [now(-hours * 60)]
    for col, val in (("category", category), ("severity", severity)):
        if val:
            sql += f" and {col}=?"
            args.append(val)
    with db() as c:
        return [[ward(c, r["ward_id"])["lat"], ward(c, r["ward_id"])["lng"], r["n"]]
                for r in c.execute(sql + " group by ward_id", args).fetchall()]


@app.get("/api/trend")
def get_trend(ward_id: int, category: str):
    with db() as c:
        h = c.execute("select day,n from history where ward_id=? and category=? order by day desc limit 6",
                      (ward_id, category)).fetchall()[::-1]
        today = c.execute("select count(*) from reports where ward_id=? and category=? and ts>=? and status='ok'",
                          (ward_id, category, now(-24 * 60))).fetchone()[0]
        base = c.execute("select normal_daily from baselines where ward_id=? and category=?", (ward_id, category)).fetchone()[0]
    return {"labels": [r["day"][5:] for r in h] + ["Last 24h"], "counts": [r["n"] for r in h] + [today], "baseline": base}


@app.get("/api/alerts")
def get_alerts():
    with db() as c:
        scan(c)
        rows = c.execute("select a.*, w.name ward from alerts a join wards w on w.id=a.ward_id "
                         "order by a.workflow in ('false_alarm','confirmed'), a.score desc").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["components"] = json.loads(d["components"])
            d["events"] = [dict(e) for e in c.execute("select action,actor,ts from alert_events where alert_id=? order by id", (r["id"],))]
            out.append(d)
        return out


@app.post("/api/alerts/{aid}/action")
def alert_action(aid: int, body: ActionIn, a=Depends(need_authority)):
    workflow, label = ACTIONS[body.action]
    with db() as c:
        if not c.execute("select 1 from alerts where id=?", (aid,)).fetchone():
            raise HTTPException(404, "Alert not found")
        c.execute("update alerts set workflow=? where id=?", (workflow, aid))
        log(c, aid, label, a["name"])
    return {"ok": True}


@app.get("/api/advisories")
def get_advisories():
    with db() as c:
        return [dict(r) for r in c.execute("select a.message, a.ts, coalesce(w.name,'All wards') ward from advisories a "
                                           "left join wards w on w.id=a.ward_id order by a.id desc limit 5")]


@app.post("/api/advisories")
def post_advisory(a: AdvisoryIn, u=Depends(need_authority)):
    with db() as c:
        if a.ward_id:
            ward(c, a.ward_id)
        c.execute("insert into advisories(ward_id,message,ts) values(?,?,?)", (a.ward_id, a.message.strip(), now()))
    return {"ok": True}


# ---------------------------------------------------------------- outbreak simulation
# Offsets (metres east, north of the ward centre). Reports go through the same insert and detection code as real ones.
SIM_WARD, SIM_CAT = 5, "fever"
STAGES = [
    ("Normal volume", [(-900, 600), (800, -800)]),
    ("Reports arriving", [(-300, -900), (900, 500), (0, 900)]),
    ("Unusual increase (Watch)", [(-800, -100), (500, -300), (-500, 800), (300, 100)]),
    ("Cluster forms (Rising)", [(-200, 200), (-120, 260), (-280, 120), (-150, 140), (-260, 280)]),
    ("Alert fires (Critical)", [(-180, 230), (-240, 190), (-100, 210), (-210, 300), (-320, 220), (-140, 320)]),
]
CUM = [2, 5, 9, 14, 20]


def need_demo(a=Depends(need_authority)):
    if not DEMO:
        raise HTTPException(404, "Not found")
    return a


def sim_count(c):
    return c.execute("select count(*) from reports where is_simulated=1 and ward_id=? and category=?",
                     (SIM_WARD, SIM_CAT)).fetchone()[0]


@app.get("/api/simulate/state")
def sim_state():
    if not DEMO:
        raise HTTPException(404, "Not found")
    with db() as c:
        stage = sum(n <= sim_count(c) for n in CUM)
    return {"stage": stage, "total": len(STAGES), "label": STAGES[stage - 1][0] if stage else "Not started"}


@app.post("/api/simulate/next")
def sim_next(u=Depends(need_demo)):
    with db() as c:
        k = sim_count(c)
        stage = sum(n <= k for n in CUM)
        if stage >= len(STAGES):
            raise HTTPException(400, "Simulation finished. Press Reset to run it again.")
        w = ward(c, SIM_WARD)
        label, pts = STAGES[stage]
        for i, (dx, dy) in enumerate(pts):
            lat, lng = offset(w, dx, dy)
            role = "health_worker" if stage == 4 and i < 4 else "citizen"
            add_report(c, SIM_CAT, ("low", "medium", "medium", "high")[i % 4], SIM_WARD, lat, lng,
                       f"sim-{k + i + 1}", role, 1, now(-(len(pts) - i)))
        sync(c, SIM_WARD, SIM_CAT)
    return {"stage": stage + 1, "label": label}


@app.post("/api/simulate/spam")  # 15 reports from 2 accounts: high volume, no independent evidence
def sim_spam(u=Depends(need_demo)):
    with db() as c:
        w = ward(c, 3)
        ids = []
        for name, email in (("Spam demo account A", "spam.a@sim.local"), ("Spam demo account B", "spam.b@sim.local")):
            r = c.execute("select id from users where email=?", (email,)).fetchone()
            ids.append(r["id"] if r else make_user(c, name, email, secrets.token_hex(12), "citizen"))
        for i in range(15):
            lat, lng = offset(w, random.uniform(-120, 120), random.uniform(-120, 120))
            uid = ids[1 if i >= 13 else 0]
            add_report(c, "cough", "medium", 3, lat, lng, f"user-{uid}", "citizen", 1, now(-(15 - i)), uid)
        sync(c, 3, "cough")
    return {"ok": True}


@app.post("/api/simulate/reset")
def sim_reset(u=Depends(need_demo)):
    with db() as c:
        c.execute("delete from reports where is_simulated=1")
        c.execute("delete from users where email like '%@sim.local'")
        for t in ("alerts", "alert_events", "advisories", "audit"):
            c.execute(f"delete from {t}")
        scan(c)
    return {"ok": True}


# ---------------------------------------------------------------- sign in and accounts

class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    email: str = Field(pattern=EMAIL, max_length=120)
    password: str = Field(min_length=8, max_length=100)
    role: Literal["citizen", "health_worker"] = "citizen"


class LoginIn(BaseModel):
    email: str = Field(max_length=120)
    password: str = Field(max_length=100)


@app.post("/api/auth/register")
def register(b: RegisterIn):
    with db() as c:
        if c.execute("select 1 from users where email=?", (b.email.lower(),)).fetchone():
            raise HTTPException(409, "An account with this email already exists.")
        uid = make_user(c, b.name.strip(), b.email, b.password, b.role)
        audit(c, b.name.strip(), "register", f"user {uid}", b.role + (" (awaiting verification)" if b.role == "health_worker" else ""))
        return {"token": new_session(c, uid), "user": pub(c.execute("select * from users where id=?", (uid,)).fetchone())}


@app.post("/api/auth/login")
def login(b: LoginIn):
    key = b.email.lower()
    n, t = FAILS.get(key, (0, 0))
    if n >= 5 and time.time() - t < 600:
        raise HTTPException(429, "Too many failed attempts. Please try again in 10 minutes.")
    with db() as c:
        u = c.execute("select * from users where email=?", (key,)).fetchone()
        if not (u and hmac.compare_digest(hash_pw(b.password, u["salt"]), u["pw_hash"])):
            FAILS[key] = ((n + 1) if time.time() - t < 600 else 1, time.time())
            raise HTTPException(401, "Incorrect email or password.")
        if u["status"] == "banned":
            raise HTTPException(403, "This account has been banned by the health authority.")
        FAILS.pop(key, None)
        return {"token": new_session(c, u["id"]), "user": pub(u)}


@app.get("/api/auth/me")
def me(u=Depends(need_user)):
    return pub(u)


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        with db() as c:
            c.execute("delete from sessions where token_hash=?", (hashlib.sha256(authorization[7:].encode()).hexdigest(),))
    return {"ok": True}


class CenterIn(BaseModel):
    lat: float = Field(ge=-85, le=85)
    lng: float = Field(ge=-180, le=180)


@app.post("/api/admin/center")  # set the middle of the city or district the six wards are placed around
def set_center(b: CenterIn, a=Depends(need_authority)):
    with db() as c:
        for i in range(6):
            old = c.execute("select lat,lng from wards where id=?", (i + 1,)).fetchone()
            nlat, nlng = ward_pos(i, (b.lat, b.lng))
            c.execute("update reports set lat=lat+?, lng=lng+? where ward_id=?", (nlat - old["lat"], nlng - old["lng"], i + 1))
            c.execute("update wards set lat=?, lng=? where id=?", (nlat, nlng, i + 1))
        c.execute("insert or replace into settings values('center', ?)", (json.dumps([b.lat, b.lng]),))
        audit(c, a["name"], "set_coverage_centre", "wards", f"{b.lat:.4f}, {b.lng:.4f}")
        scan(c)
    return {"ok": True}


# ---------------------------------------------------------------- moderation (authority only)

class UserActionIn(BaseModel):
    action: Literal["verify", "suspend", "reinstate", "ban"]
    reason: str = Field(default="", max_length=200)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@app.get("/api/admin/users")
def admin_users(a=Depends(need_authority)):
    with db() as c:
        return [dict(r) for r in c.execute(
            "select u.id,u.name,u.email,u.role,u.verified,u.status,u.trust,"
            "(select count(*) from reports r where r.user_id=u.id) reports from users u "
            "where u.role!='authority' order by u.trust, u.id desc")]


@app.get("/api/admin/reports")
def admin_reports(a=Depends(need_authority)):
    with db() as c:
        return [dict(r) for r in c.execute(
            "select r.id,r.category,r.severity,w.name ward,r.ts,r.status,r.is_simulated,r.user_id,"
            "coalesce(u.name,r.reporter_id) who from reports r join wards w on w.id=r.ward_id "
            "left join users u on u.id=r.user_id order by r.id desc limit 40")]


@app.get("/api/admin/audit")
def admin_audit(a=Depends(need_authority)):
    with db() as c:
        return [dict(r) for r in c.execute("select actor,action,target,detail,ts from audit order by id desc limit 30")]


@app.post("/api/admin/users/{uid}/action")
def user_action(uid: int, b: UserActionIn, a=Depends(need_authority)):
    with db() as c:
        u = c.execute("select * from users where id=?", (uid,)).fetchone()
        if not u or u["role"] == "authority":
            raise HTTPException(404, "User not found")
        if b.action == "verify":
            if u["role"] != "health_worker":
                raise HTTPException(400, "Only health-worker accounts need verification.")
            c.execute("update users set verified=1 where id=?", (uid,))
        elif b.action == "reinstate":  # earlier rejected reports stay rejected
            c.execute("update users set status='active', trust=60 where id=?", (uid,))
        else:
            restrict(c, uid, "banned" if b.action == "ban" else "suspended")
        audit(c, a["name"], b.action, f"user {uid} ({u['name']})", b.reason)
        scan(c)
    return {"ok": True}


@app.post("/api/admin/reports/{rid}/reject")
def reject_report(rid: int, b: RejectIn, a=Depends(need_authority)):
    with db() as c:
        r = c.execute("select id,user_id from reports where id=?", (rid,)).fetchone()
        if not r:
            raise HTTPException(404, "Report not found")
        c.execute("update reports set status='rejected' where id=?", (rid,))
        if r["user_id"]:
            penalise(c, r["user_id"], 15, "report rejected")
        audit(c, a["name"], "reject_report", f"report {rid}", b.reason)
        scan(c)
    return {"ok": True}


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")