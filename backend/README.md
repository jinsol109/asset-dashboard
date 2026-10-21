# Kiwoom holdings API

Read-only FastAPI service for the stock dashboard. It requests Kiwoom's domestic
account evaluation balance API (`kt00018`) and does not expose order routes.

## Deploy on Railway

Create a service from this repository and set its **Root Directory** to
`/backend`. Use:

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`

Set the following variables in Railway's service Variables panel. Never commit
the actual values or put them in the dashboard HTML.

- `KIWOOM_MODE`: `real` for the registered account, or `demo` for a mock account
- `KIWOOM_APP_KEY`: the new key issued by Kiwoom
- `KIWOOM_APP_SECRET`: the matching new secret
- `DASHBOARD_PASSWORD`: a unique, strong password for viewing this dashboard
- `SESSION_SECRET`: a random secret generated locally, for example with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `DASHBOARD_ORIGIN`: `https://jinsol109.github.io`

After deployment:

1. Enable Railway static outbound IPs and copy the assigned outbound IPv4
   addresses into Kiwoom's allowed IP list.
2. Verify the service's `/health` endpoint returns `{"ok":true}`.
3. Set the Railway public service URL in the `kiwoom-api-base` meta tag in
   `stock.html`, without a trailing slash.
4. Deploy the static site and use the dashboard password to load holdings.

The page keeps its session token in memory only. The token expires after 30
minutes. The API responds with `Cache-Control: no-store` and enables CORS only
for the GitHub Pages origin above.
