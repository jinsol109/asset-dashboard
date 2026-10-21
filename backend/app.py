import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from threading import Lock

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

KST = timezone(timedelta(hours=9))
MODE = os.getenv("KIWOOM_MODE", "demo").strip().lower()
KIWOOM_BASE_URLS = {
    "real": "https://api.kiwoom.com",
    "demo": "https://mockapi.kiwoom.com",
}
if MODE not in KIWOOM_BASE_URLS:
    raise RuntimeError("KIWOOM_MODE must be 'real' or 'demo'.")

APP_KEY = os.getenv("KIWOOM_APP_KEY", "")
APP_SECRET = os.getenv("KIWOOM_APP_SECRET", "")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
DASHBOARD_ORIGIN = os.getenv(
    "DASHBOARD_ORIGIN", "https://jinsol109.github.io"
).rstrip("/")

app = FastAPI(title="Private Kiwoom Holdings API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[DASHBOARD_ORIGIN],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

bearer = HTTPBearer(auto_error=False)
_token_lock = Lock()
_cached_kiwoom_token: str | None = None
_kiwoom_token_expiry = 0.0
_login_attempts: dict[str, list[float]] = {}


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _issue_session() -> str:
    expires = int(time.time()) + 30 * 60
    payload = _b64(json.dumps({"exp": expires}, separators=(",", ":")).encode())
    signature = hmac.new(
        SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
    ).digest()
    return f"{payload}.{_b64(signature)}"


def _verify_session(token: str) -> bool:
    try:
        payload, signature = token.split(".", 1)
        expected = _b64(
            hmac.new(
                SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
            ).digest()
        )
        if not hmac.compare_digest(signature, expected):
            return False
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return int(claims["exp"]) > int(time.time())
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def _require_configuration() -> None:
    if not all((APP_KEY, APP_SECRET, DASHBOARD_PASSWORD, SESSION_SECRET)):
        raise HTTPException(status_code=503, detail="서비스 설정이 완료되지 않았습니다.")


def _authenticated(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    if not _verify_session(credentials.credentials):
        raise HTTPException(status_code=401, detail="로그인이 만료되었습니다.")


def _integer(value: object) -> int:
    try:
        return int(str(value or "0").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def _number(value: object) -> float:
    try:
        return float(str(value or "0").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


async def _get_kiwoom_token(client: httpx.AsyncClient) -> str:
    global _cached_kiwoom_token, _kiwoom_token_expiry
    with _token_lock:
        if _cached_kiwoom_token and time.time() < _kiwoom_token_expiry - 300:
            return _cached_kiwoom_token

    response = await client.post(
        f"{KIWOOM_BASE_URLS[MODE]}/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "appkey": APP_KEY,
            "secretkey": APP_SECRET,
        },
        headers={"Content-Type": "application/json;charset=UTF-8"},
    )
    response.raise_for_status()
    data = response.json()
    if data.get("return_code") not in (None, 0) or not data.get("token"):
        raise HTTPException(status_code=502, detail="키움 인증에 실패했습니다.")

    expires_text = str(data.get("expires_dt", ""))
    try:
        expires_at = datetime.strptime(expires_text, "%Y%m%d%H%M%S").replace(
            tzinfo=KST
        )
        expiry_timestamp = expires_at.timestamp()
    except ValueError:
        expiry_timestamp = time.time() + 23 * 60 * 60

    with _token_lock:
        _cached_kiwoom_token = data["token"]
        _kiwoom_token_expiry = expiry_timestamp
    return data["token"]


@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/api/session")
async def create_session(body: LoginRequest, request: Request):
    _require_configuration()
    now = time.time()
    client_ip = request.client.host if request.client else "unknown"
    attempts = [t for t in _login_attempts.get(client_ip, []) if now - t < 600]
    if len(attempts) >= 5:
        raise HTTPException(status_code=429, detail="잠시 후 다시 시도해 주세요.")
    attempts.append(now)
    _login_attempts[client_ip] = attempts

    if not hmac.compare_digest(body.password, DASHBOARD_PASSWORD):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다.")
    _login_attempts.pop(client_ip, None)
    return {"token": _issue_session(), "expires_in": 1800}


@app.get("/api/holdings", dependencies=[Depends(_authenticated)])
async def get_holdings():
    _require_configuration()
    async with httpx.AsyncClient(timeout=25.0) as client:
        token = await _get_kiwoom_token(client)
        response = await client.post(
            f"{KIWOOM_BASE_URLS[MODE]}/api/dostk/acnt",
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "api-id": "kt00018",
                "authorization": f"Bearer {token}",
            },
            json={"qry_tp": "1", "dmst_stex_tp": "KRX"},
        )
        response.raise_for_status()
        data = response.json()

    if data.get("return_code") not in (None, 0):
        raise HTTPException(status_code=502, detail="키움 잔고 조회에 실패했습니다.")

    raw_rows = data.get("acnt_evlt_remn_indv_tot", [])
    holdings = []
    for row in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(row, dict):
            continue
        holdings.append(
            {
                "code": str(row.get("stk_cd", "")).strip(),
                "name": str(row.get("stk_nm", "")).strip(),
                "quantity": _integer(row.get("rmnd_qty")),
                "availableQuantity": _integer(row.get("trde_able_qty")),
                "price": _integer(row.get("cur_prc")),
                "purchaseAmount": _integer(row.get("pur_amt")),
                "evaluationAmount": _integer(row.get("evlt_amt")),
                "profitLoss": _integer(row.get("evltv_prft")),
                "returnPct": _number(row.get("prft_rt")),
                "weightPct": _number(row.get("poss_rt")),
            }
        )

    return {
        "asOf": datetime.now(KST).isoformat(timespec="seconds"),
        "summary": {
            "purchaseAmount": _integer(data.get("tot_pur_amt")),
            "evaluationAmount": _integer(data.get("tot_evlt_amt")),
            "profitLoss": _integer(data.get("tot_evlt_pl")),
            "returnPct": _number(data.get("tot_prft_rt")),
        },
        "holdings": holdings,
    }
