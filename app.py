from datetime import datetime, timezone
from pathlib import Path
import csv
import hashlib
import os

from flask import Flask, redirect, render_template, request, url_for

app = Flask(__name__)

# O disco do Render Free é efêmero: os registros podem ser perdidos após reinícios.
LOG = Path(os.environ.get("LOG_FILE", "/tmp/eventos.csv"))
LOG.parent.mkdir(parents=True, exist_ok=True)
SALT = os.environ.get("CAMPAIGN_HASH_SALT", "troque-este-salt")


def ip_hash(ip: str) -> str:
    return hashlib.sha256(f"{SALT}|{ip}".encode("utf-8")).hexdigest()[:16]


def record_event(campaign: str, name: str) -> None:
    new_file = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if new_file:
            writer.writerow(["timestamp_utc", "campaign", "event", "anonymous_ip", "user_agent"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            campaign[:100],
            name,
            ip_hash(request.remote_addr or ""),
            request.headers.get("User-Agent", "")[:300],
        ])


@app.get("/")
def home():
    return redirect(url_for("training", campaign="ADS2026"))


@app.get("/click")
def click():
    campaign = request.args.get("id", "ADS2026")
    record_event(campaign, "LINK_CLICADO")
    return redirect(url_for("training", campaign=campaign))


@app.get("/treinamento")
def training():
    campaign = request.args.get("campaign", "ADS2026")
    record_event(campaign, "PAGINA_ABERTA")
    return render_template("training.html", campaign=campaign)


@app.post("/evento")
def event_endpoint():
    campaign = request.form.get("campaign", "ADS2026")
    event_name = request.form.get("event", "")
    if event_name in {"BOTAO_ACESSAR_CLICADO", "TREINAMENTO_CONCLUIDO"}:
        record_event(campaign, event_name)
    return ("", 204)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
