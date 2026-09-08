from datetime import datetime, timezone
import base64
import json
import os
import re
import threading

from flask import Flask, redirect, render_template, request, url_for

from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# Google Docs usado para os registros.
GOOGLE_DOC_ID = os.environ.get(
    "GOOGLE_DOC_ID",
    "1vh1kREco2S27GkhuoFww9alkrro05e1StFQiBu2FXKo",
)

GOOGLE_SERVICE_ACCOUNT_B64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_B64", "")

DOCS_SCOPES = ["https://www.googleapis.com/auth/documents"]
_docs_lock = threading.Lock()
_docs_service = None


def get_docs_service():
    """Cria o cliente da Google Docs API uma única vez por processo."""
    global _docs_service

    if _docs_service is not None:
        return _docs_service

    if not GOOGLE_SERVICE_ACCOUNT_B64:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_B64 não configurada no Render.")

    credentials_info = json.loads(
        base64.b64decode(GOOGLE_SERVICE_ACCOUNT_B64).decode("utf-8")
    )

    credentials = service_account.Credentials.from_service_account_info(
        credentials_info,
        scopes=DOCS_SCOPES,
    )

    _docs_service = build(
        "docs",
        "v1",
        credentials=credentials,
        cache_discovery=False,
    )
    return _docs_service


def client_ip():
    """
    No Render, X-Forwarded-For contém o IP original do cliente.
    Usa o primeiro endereço da lista e mantém um fallback para remote_addr.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip

    return request.remote_addr or ""


def truncate(value, size=1000):
    value = "" if value is None else str(value)
    return value[:size]


def safe_value(value):
    """
    Evita que conteúdo enviado pelo navegador seja usado para criar
    uma quantidade excessiva de texto no documento.
    """
    return truncate(value, 1000).replace("\r", " ").replace("\n", " ")


def browser_from_user_agent(ua):
    ua = ua or ""

    if re.search(r"Edg(?:e)?/", ua, re.I):
        browser = "Microsoft Edge"
    elif re.search(r"(?:OPR|Opera)/", ua, re.I):
        browser = "Opera"
    elif re.search(r"(?:Chrome|CriOS)/", ua, re.I):
        browser = "Chrome"
    elif re.search(r"(?:Firefox|FxiOS)/", ua, re.I):
        browser = "Firefox"
    elif re.search(r"(?:Safari)/", ua, re.I) and not re.search(r"Chrome|CriOS", ua, re.I):
        browser = "Safari"
    else:
        browser = "Desconhecido"

    return browser


def os_from_user_agent(ua):
    ua = ua or ""

    if "Windows NT" in ua:
        return "Windows"
    if "Mac OS X" in ua or "Macintosh" in ua:
        return "macOS"
    if "Android" in ua:
        return "Android"
    if "iPhone" in ua or "iPad" in ua or "iPod" in ua:
        return "iOS/iPadOS"
    if "Linux" in ua:
        return "Linux"

    return "Desconhecido"


def device_from_user_agent(ua):
    ua = ua or ""

    if re.search(r"Mobile|Android.*Mobile|iPhone|iPod", ua, re.I):
        return "Mobile"
    if re.search(r"iPad|Tablet|Android(?!.*Mobile)", ua, re.I):
        return "Tablet"
    return "Desktop/Outro"


def append_to_google_doc(text):
    """
    Acrescenta o registro ao final do Google Docs.
    O lock evita colisões entre requisições simultâneas no mesmo processo.
    """
    with _docs_lock:
        service = get_docs_service()

        document = (
            service.documents()
            .get(documentId=GOOGLE_DOC_ID)
            .execute()
        )

        body = document.get("body", {})
        content = body.get("content", [])

        end_index = 1
        if content:
            end_index = content[-1].get("endIndex", 1) - 1

        if end_index < 1:
            end_index = 1

        service.documents().batchUpdate(
            documentId=GOOGLE_DOC_ID,
            body={
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": end_index},
                            "text": text + "\n",
                        }
                    }
                ]
            },
        ).execute()


def record_event(event_name, campaign="ADS2026", client_data=None):
    """
    Registra dados do servidor + dados complementares fornecidos pelo
    navegador. Nunca registra o conteúdo dos campos de usuário/senha.
    """
    ua = request.headers.get("User-Agent", "")

    client_data = client_data or {}

    timestamp_utc = datetime.now(timezone.utc).isoformat()
    ip = client_ip()

    lines = [
        "============================================================",
        f"TIMESTAMP_UTC: {timestamp_utc}",
        f"EVENTO: {safe_value(event_name, 200)}",
        f"CAMPAIGN: {safe_value(campaign, 200)}",
        f"IP: {safe_value(ip, 200)}",
        f"METHOD: {request.method}",
        f"PATH: {safe_value(request.path, 500)}",
        f"QUERY_STRING: {safe_value(request.query_string.decode('utf-8', errors='replace'), 1000)}",
        f"REFERER: {safe_value(request.headers.get('Referer', ''), 1000)}",
        f"HOST: {safe_value(request.host, 500)}",
        f"BROWSER: {browser_from_user_agent(ua)}",
        f"OS: {os_from_user_agent(ua)}",
        f"DEVICE: {device_from_user_agent(ua)}",
        f"USER_AGENT: {safe_value(ua, 1500)}",
        f"ACCEPT_LANGUAGE: {safe_value(request.headers.get('Accept-Language', ''), 500)}",
        f"SEC_CH_UA: {safe_value(request.headers.get('Sec-CH-UA', ''), 500)}",
        f"SEC_CH_UA_MOBILE: {safe_value(request.headers.get('Sec-CH-UA-Mobile', ''), 100)}",
        f"SEC_CH_UA_PLATFORM: {safe_value(request.headers.get('Sec-CH-UA-Platform', ''), 200)}",
        f"X_FORWARDED_FOR: {safe_value(request.headers.get('X-Forwarded-For', ''), 1000)}",
        f"X_REAL_IP: {safe_value(request.headers.get('X-Real-IP', ''), 200)}",
    ]

    # Dados que só o navegador conhece. São opcionais.
    client_fields = {
        "CLIENT_TIMESTAMP": client_data.get("clientTimestamp"),
        "PAGE_URL": client_data.get("pageUrl"),
        "TIMEZONE": client_data.get("timezone"),
        "LANGUAGE": client_data.get("language"),
        "SCREEN": client_data.get("screen"),
        "VIEWPORT": client_data.get("viewport"),
        "DEVICE_PIXEL_RATIO": client_data.get("devicePixelRatio"),
        "COLOR_DEPTH": client_data.get("colorDepth"),
        "CORES_LOGICAS": client_data.get("hardwareConcurrency"),
        "DEVICE_MEMORY_GB": client_data.get("deviceMemory"),
        "TOUCH_POINTS": client_data.get("maxTouchPoints"),
        "PLATFORM_JS": client_data.get("platform"),
        "COOKIE_ENABLED": client_data.get("cookieEnabled"),
        "DO_NOT_TRACK": client_data.get("doNotTrack"),
        "ONLINE": client_data.get("onLine"),
        "REFERRER_JS": client_data.get("referrer"),
        "VISIBILITY_STATE": client_data.get("visibilityState"),
        "SESSION_ID": client_data.get("sessionId"),
        # Apenas indica preenchimento; não registra conteúdo de usuário/senha.
        "USUARIO_PREENCHIDO": client_data.get("usernameFilled"),
        "SENHA_PREENCHIDA": client_data.get("passwordFilled"),
    }

    for field, value in client_fields.items():
        if value is not None:
            lines.append(f"{field}: {safe_value(value, 1000)}")

    lines.append("")

    append_to_google_doc("\n".join(lines))


def parse_event_request():
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        campaign = payload.get("campaign", "ADS2026")
        event_name = payload.get("event", "")
        client_data = payload.get("clientData") or {}
    else:
        campaign = request.form.get("campaign", "ADS2026")
        event_name = request.form.get("event", "")
        client_data_raw = request.form.get("clientData", "{}")

        try:
            client_data = json.loads(client_data_raw)
        except (TypeError, ValueError):
            client_data = {}

    return campaign, event_name, client_data


@app.get("/")
def home():
    return redirect(url_for("training", campaign="ADS2026"))


@app.get("/click")
def click():
    campaign = request.args.get("id", "ADS2026")
    record_event(campaign=campaign, event_name="LINK_CLICADO")
    return redirect(url_for("training", campaign=campaign))


@app.get("/treinamento")
def training():
    campaign = request.args.get("campaign", "ADS2026")

    # Registra imediatamente o acesso, inclusive antes do JavaScript carregar.
    try:
        record_event(campaign=campaign, event_name="PAGINA_ABERTA")
    except Exception as exc:
        app.logger.exception("Falha ao registrar PAGINA_ABERTA: %s", exc)

    return render_template("training.html", campaign=campaign)


@app.post("/evento")
def event_endpoint():
    campaign, event_name, client_data = parse_event_request()

    allowed_events = {
        "CLIENTE_IDENTIFICADO",
        "BOTAO_ACESSAR_CLICADO",
        "TREINAMENTO_CONCLUIDO",
        "PAGE_UNLOAD",
        "PAGE_HIDDEN",
    }

    if event_name not in allowed_events:
        return ("", 400)

    try:
        record_event(
            campaign=campaign,
            event_name=event_name,
            client_data=client_data,
        )
    except Exception as exc:
        app.logger.exception("Falha ao registrar evento %s: %s", event_name, exc)
        # Mantém a página funcional mesmo se a API do Google estiver
        # temporariamente indisponível.
        return ("", 204)

    return ("", 204)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
    )
