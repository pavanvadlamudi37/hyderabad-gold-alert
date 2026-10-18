"""Hyderabad 22K INR/gram monitor. Python 3.12+, standard library only."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SOURCE_URL = "https://www.goodreturns.in/gold-rates/hyderabad.html"
# Set your limit here, or override with repository variable GOLD_THRESHOLD_INR.
THRESHOLD_INR = "13600"
STATE_PATH = Path("state.json")


def money(value):
    try:
        result = Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except InvalidOperation:
        raise ValueError("Invalid INR amount") from None
    if not result.is_finite() or result <= 0:
        raise ValueError("INR amount must be positive and finite")
    return result


class PageParser(HTMLParser):
    """Collect tables with their nearest preceding heading; ignore scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.heading = ""
        self.heading_parts = None
        self.h1 = []
        self.heading_tag = None
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if self.skip:
            return
        if tag in ("h1", "h2", "h3", "h4"):
            self.heading_parts, self.heading_tag = [], tag
        elif tag == "table":
            if self.table is not None:
                raise ValueError("Nested price tables are unsupported")
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.skip:
            return
        if self.heading_parts is not None:
            self.heading_parts.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if self.skip:
            return
        if tag == self.heading_tag:
            self.heading = " ".join(" ".join(self.heading_parts).split())
            if tag == "h1":
                self.h1.append(self.heading)
            self.heading_parts, self.heading_tag = None, None
        elif tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append((self.heading, self.table))
            self.table = None


def extract_price(html):
    page = PageParser()
    page.feed(html)
    if not any(re.search(r"Gold Rate in Hyderabad", h, re.I) for h in page.h1):
        raise ValueError("Expected Hyderabad gold page heading missing (possibly blocked)")
    prices = []
    for heading, rows in page.tables:
        if not re.fullmatch(r"Today Gold Price Per Gram in Hyderabad\s*\(INR\)", heading, re.I):
            continue
        for header in rows:
            if not header or header[0].lower() not in ("gram", "grams"):
                continue
            columns = [i for i, cell in enumerate(header)
                       if re.fullmatch(r"22\s*K(?:\s+Gold)?", cell, re.I)]
            if len(columns) != 1:
                raise ValueError("22K column missing or ambiguous")
            for row in rows:
                if row and re.fullmatch(r"1(?:\.0+)?(?:\s*(?:g|gram))?", row[0], re.I):
                    if len(row) != len(header):
                        raise ValueError("Price row/header width mismatch")
                    value = row[columns[0]]
                    if not re.fullmatch(r"₹?\s*[0-9][0-9,]*(?:\.[0-9]{1,2})?", value):
                        raise ValueError("Unexpected price cell format")
                    prices.append(money(value))
    if len(prices) != 1:
        raise ValueError("Expected exactly one current Hyderabad 22K one-gram price")
    return prices[0]


def fetch_page():
    request = Request(SOURCE_URL, headers={
        "User-Agent": "HyderabadGoldAlert/1.0 (personal price monitor)",
        "Accept": "text/html", "Cache-Control": "no-cache",
    })
    try:
        with urlopen(request, timeout=30) as response:
            if response.geturl().rstrip("/") != SOURCE_URL:
                raise RuntimeError("Unexpected source redirect")
            return response.read().decode("utf-8")
    except HTTPError as error:
        raise RuntimeError(f"GoodReturns HTTP {error.code}; no alert or state update") from None
    except (URLError, TimeoutError):
        raise RuntimeError("GoodReturns request failed; no alert or state update") from None


def load_state(path):
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or state.get("version") != 1
            or not isinstance(state.get("context"), str)
            or not isinstance(state.get("last_price"), str)):
        raise ValueError("Invalid state; inspect it before resetting")
    money(state["last_price"])
    return state


def needs_alert(price, threshold, state, context):
    # First qualifying observation or any changed qualifying price. Equality is excluded.
    return price < threshold and (state is None or state["context"] != context
                                 or money(state["last_price"]) != price)


def send_telegram(token, chat_id, text):
    payload = json.dumps({"chat_id": chat_id, "text": text,
                          "link_preview_options": {"is_disabled": True}}).encode()
    request = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
                      headers={"Content-Type": "application/json"}, method="POST")
    # Do not retry sendMessage: a timeout may occur after delivery. Never log token URLs.
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError):
        raise RuntimeError("Telegram send failed or delivery is uncertain; state unchanged") from None
    if result.get("ok") is not True or not isinstance(result.get("result", {}).get("message_id"), int):
        raise RuntimeError("Telegram did not confirm delivery; state unchanged")


def run(price, threshold, path, context, sender):
    state = load_state(path)
    alert = needs_alert(price, threshold, state, context)
    if alert:
        sender(f"Hyderabad 22K gold: ₹{price:,.2f} per gram\n"
               f"Below your limit of ₹{threshold:,.2f} per gram.\n"
               f"Observed: {datetime.now(timezone.utc).isoformat()}\n{SOURCE_URL}\n"
               "Indicative rate; taxes and making charges extra.")
    updated = {"version": 1, "context": context, "last_price": str(price)}
    if state != updated:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    return alert


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Fetch and evaluate without sending or saving")
    parser.add_argument("--html", type=Path, help="Parse a saved HTML file instead of fetching")
    args = parser.parse_args()
    threshold = money(os.environ.get("GOLD_THRESHOLD_INR") or THRESHOLD_INR)
    price = extract_price(args.html.read_text(encoding="utf-8") if args.html else fetch_page())
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    # Bot, recipient and threshold changes start a fresh alert context, without storing credentials.
    context = hashlib.sha256(f"{token}|{chat}|{threshold.normalize()}".encode()).hexdigest()
    print(f"Hyderabad 22K: INR {price}/gram; strict threshold: INR {threshold}")
    if args.dry_run:
        print(f"Dry run: would alert = {needs_alert(price, threshold, load_state(STATE_PATH), context)}; no state saved")
        return
    if not token or not chat:
        raise ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    sent = run(price, threshold, STATE_PATH, context, lambda text: send_telegram(token, chat, text))
    print("Alert confirmed" if sent else "No alert needed")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
