# sf_kpi_jwt_runner.py
# Minimal, single-file runner that:
# - signs a JWT (JSON Web Token) to get a Salesforce access token
# - queries Time_Entry__c via SOQL (Salesforce Object Query Language)
# - computes KPIs (Key Performance Indicators)
# - writes a small HTML report
# - (optional) asks an LLM (Large Language Model) for a summary if OPENAI_API_KEY is set
# Rotation-safe: supports multiple private keys and auto-learns which key works per tenant.

import os, sys, time, json, pathlib, requests, pandas as pd
import jwt  # PyJWT
from simple_salesforce import Salesforce

# ---- Read config from environment variables ----
SF_CLIENT_ID = os.environ.get("SF_CLIENT_ID", "").strip()
SF_JWT_USERNAME = os.environ.get("SF_JWT_USERNAME", "").strip()  # Salesforce username to act as
SF_DOMAIN = os.environ.get("SF_DOMAIN", "login").strip()         # login | test | <my-domain>.my.salesforce.com
SF_PRIVATE_KEY_PATH = os.environ.get("SF_PRIVATE_KEY_PATH", "").strip()  # fallback if SF_KEYS not provided

# Optional rotation/multi-key settings
SF_KEYS = os.environ.get("SF_KEYS", "").strip()  # e.g., "2025:/path/key-2025.pem,2030:/path/key-2030.pem"
TENANT_ID = os.environ.get("TENANT_ID", "default").strip()
KEYMAP_PATH = os.path.expanduser(os.environ.get("KEYMAP_PATH", "~/.sf_kpi_keymap.json"))

# Optional: OpenAI for LLM (Large Language Model) summary
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

if not SF_CLIENT_ID or not SF_JWT_USERNAME:
    sys.exit("Missing SF_CLIENT_ID or SF_JWT_USERNAME")
if not SF_KEYS and not SF_PRIVATE_KEY_PATH:
    sys.exit("Provide either SF_KEYS (key_id:path,...) or SF_PRIVATE_KEY_PATH")

# ---------- Key-ring helpers (for zero-downtime cert rotation) ----------
def load_keymap():
    try:
        with open(KEYMAP_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_keymap(m):
    try:
        pathlib.Path(os.path.dirname(KEYMAP_PATH) or ".").mkdir(parents=True, exist_ok=True)
        with open(KEYMAP_PATH, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=2)
    except Exception:
        pass  # best-effort

def parse_keyring():
    """
    Parse SF_KEYS env var into a list of (key_id, path).
    Example: "2025:/home/you/keys/key-2025.pem,2030:/home/you/keys/key-2030.pem"
    Fallback: if SF_KEYS is empty, use SF_PRIVATE_KEY_PATH with key_id 'default'.
    """
    keys = []
    if SF_KEYS:
        for part in SF_KEYS.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" not in part:
                continue
            kid, path = part.split(":", 1)
            keys.append((kid.strip(), os.path.expanduser(path.strip())))
    elif SF_PRIVATE_KEY_PATH:
        keys = [("default", os.path.expanduser(SF_PRIVATE_KEY_PATH))]
    return keys

def audience_from_domain(domain: str) -> str:
    if domain in ("login", "test"):
        return f"https://{domain}.salesforce.com"
    # treat as full host (e.g., yourdomain.my.salesforce.com)
    return f"https://{domain}"

def get_access_token_via_jwt():
    aud = audience_from_domain(SF_DOMAIN)
    keyring = parse_keyring()
    if not keyring:
        sys.exit("No keys found. Set SF_KEYS or SF_PRIVATE_KEY_PATH.")

    keymap = load_keymap()
    preferred_kid = keymap.get(TENANT_ID)

    # Try last-known-good first, then others
    ordered = []
    if preferred_kid:
        ordered = [pair for pair in keyring if pair[0] == preferred_kid] + [pair for pair in keyring if pair[0] != preferred_kid]
    else:
        ordered = keyring[:]

    last_err = None
    for kid, key_path in ordered:
        try:
            with open(key_path, "rb") as f:
                private_key = f.read()

            now = int(time.time())
            payload = {
                "iss": SF_CLIENT_ID,
                "sub": SF_JWT_USERNAME,
                "aud": aud,
                "exp": now + 180,  # 3 minutes
            }
            # 'kid' header is useful for your logs; Salesforce may ignore it
            assertion = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})

            resp = requests.post(
                f"{aud}/services/oauth2/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                timeout=30,
            )

            if resp.ok:
                tok = resp.json()
                # Remember which key worked for this tenant
                keymap[TENANT_ID] = kid
                save_keymap(keymap)
                return tok["access_token"], tok["instance_url"]
            else:
                last_err = (resp.status_code, resp.text)
        except Exception as e:
            last_err = (type(e).__name__, str(e))

    # All keys failed
    raise RuntimeError(f"JWT exchange failed for tenant='{TENANT_ID}'. Last error: {last_err}")

# ---------- Data layer ----------
def fetch_time_entries(sf: Salesforce):
    # Adjust object/fields if your org differs
    soql = """
    SELECT Id, OwnerId, Hours__c, Billable__c, Billable_Amount__c, Project__c, Start_Time__c
    FROM Time_Entry__c
    WHERE Start_Time__c = LAST_N_MONTHS:3
    """
    recs = sf.query_all(soql)["records"]
    df = pd.DataFrame(recs).drop(columns=["attributes"], errors="ignore")
    return df

def compute_kpis(df: pd.DataFrame):
    # Ensure required columns exist
    if "Billable__c" not in df.columns:
        df["Billable__c"] = False

    df["Hours__c"] = pd.to_numeric(df.get("Hours__c"), errors="coerce").fillna(0)
    df["Billable_Amount__c"] = pd.to_numeric(df.get("Billable_Amount__c"), errors="coerce").fillna(0)

    # Normalize boolean when Salesforce returns strings
    if df["Billable__c"].dtype == object:
        df["Billable__c"] = df["Billable__c"].astype(str).str.lower().isin(["true", "1", "t", "yes", "y"])

    total_hours = float(df["Hours__c"].sum())
    billable_mask = df["Billable__c"] == True
    billable_hours = float(df.loc[billable_mask, "Hours__c"].sum())
    billings_total = float(df["Billable_Amount__c"].sum())
    utilization = (billable_hours / total_hours) if total_hours > 0 else 0.0

    return total_hours, billable_hours, billings_total, utilization

# ---------- Reporting ----------
def write_report(total_hours, billable_hours, billings_total, utilization, summary_text):
    html = f"""<!doctype html><html><head><meta charset='utf-8'><title>Salesforce KPI Report</title>
    <style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;margin:32px}}
    .kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}
    .card{{border:1px solid #eee;padding:12px;border-radius:8px}}</style></head><body>
    <h1>Salesforce KPI (Key Performance Indicator) — Time Entries (Last 3 Months)</h1>
    <div class='kpi'>
      <div class='card'><b>Total Hours</b><br/>{total_hours:.1f}</div>
      <div class='card'><b>Billable Hours</b><br/>{billable_hours:.1f}</div>
      <div class='card'><b>Utilization</b><br/>{utilization:.1%}</div>
      <div class='card'><b>Total Billings</b><br/>${billings_total:,.0f}</div>
    </div>
    <h2>AI Summary (LLM = Large Language Model)</h2>
    <pre style="white-space:pre-wrap">{summary_text}</pre>
    <hr/><small>Generated by sf_kpi_jwt_runner.py</small></body></html>"""
    out = "report_time_entries.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Wrote {out}")

# ---------- Optional LLM summary ----------
def maybe_llm_summary(total_hours, billable_hours, billings_total, utilization):
    if not OPENAI_API_KEY:
        return "(OpenAI not configured; set OPENAI_API_KEY to enable AI summary.)"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        kpi_text = (
            "KPIs (last 3 months):\n"
            f"- Total Hours: {total_hours:.1f}\n"
            f"- Billable Hours: {billable_hours:.1f}\n"
            f"- Utilization: {utilization:.1%}\n"
            f"- Total Billings: ${billings_total:,.0f}\n"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a business analyst. Summarize KPIs clearly and suggest 2 actions. Never invent numbers beyond the input."},
                {"role": "user", "content": kpi_text},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"(LLM error: {e})"

# ---------- Main ----------
def main():
    access_token, instance_url = get_access_token_via_jwt()
    sf = Salesforce(instance_url=instance_url, session_id=access_token)

    df = fetch_time_entries(sf)
    if df.empty:
        print("No rows returned for the last 3 months. Check object/fields or date window.")
    total_hours, billable_hours, billings_total, utilization = compute_kpis(df)
    summary_text = maybe_llm_summary(total_hours, billable_hours, billings_total, utilization)
    write_report(total_hours, billable_hours, billings_total, utilization, summary_text)

if __name__ == "__main__":
    main()
