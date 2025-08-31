# sf-kpi-jwt

QUICKSTART — Salesforce KPI Runner (JWT only, no refresh tokens)

What this is:
- A single Python script that uses JWT (JSON Web Token) to get an access token from Salesforce.
- It then queries your Time_Entry__c object, computes KPIs (Key Performance Indicators), and writes an HTML report.
- Optional: sends the KPIs to an LLM (Large Language Model, e.g., OpenAI) to generate a plain-English summary.

You DO NOT need GitHub to use this (but now that you have it, great!).

STEP 0 — One-time Salesforce setup
1) Generate a key pair (private key + public certificate):
   openssl genrsa -out server.key 2048
   openssl req -new -x509 -key server.key -out server.crt -days 1825 -subj "/CN=sf-integration"
   # Optional PKCS#8:
   openssl pkcs8 -topk8 -nocrypt -in server.key -out server.pem
   Upload server.crt to your External Client App (ECA). Keep server.key/server.pem private.

2) In Salesforce (Setup → App Manager → New External Client App):
   - Upload server.crt
   - Scope: api
   - Assign a Permission Set to your integration user
   - Copy the Client ID (Consumer Key)

3) Pick your login host:
   - Production: login.salesforce.com
   - Sandbox: test.salesforce.com
   - If SSO (Single Sign-On): use your My Domain (e.g., yourdomain.my.salesforce.com)

STEP 1 — Run on PythonAnywhere (or locally)
1) Upload files into /home/YOURUSER/sf_kpi_jwt/
2) Create/activate a virtualenv and: pip install -r requirements.txt
3) Put your PRIVATE KEY at /home/YOURUSER/keys/shared.pem (chmod 600)
4) Set environment variables:
   SF_CLIENT_ID=<ECA Client ID>
   SF_JWT_USERNAME=<Salesforce integration user email>
   SF_DOMAIN=login                # or test, or yourdomain.my.salesforce.com
   SF_PRIVATE_KEY_PATH=/home/YOURUSER/keys/shared.pem
   OPENAI_API_KEY=<optional, for LLM>
5) Test:
   cd /home/YOURUSER/sf_kpi_jwt && python sf_kpi_jwt_runner.py
6) Schedule (PythonAnywhere Scheduled Task):
   cd /home/YOURUSER/sf_kpi_jwt && /usr/bin/env python3 sf_kpi_jwt_runner.py
Outputs: report_time_entries.html in that folder.

DON'T COMMIT SECRETS
Add to your .gitignore (even if you used the Python template):
  *.pem
  *.key
  *.crt
  .env
  keys/
  reports/
  __pycache__/
  *.sqlite
