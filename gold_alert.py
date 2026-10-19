
"""
Hyderabad 22K Gold Price Alert
Source: Goodreturns
Python 3.12+ | Standard library only

Monitors Hyderabad 22K gold price per gram.
Sends Telegram alerts when price is below threshold.
"""

import argparse
import hashlib
import json
import os
import re
import sys

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ==========================================
# CONFIGURATION
# ==========================================

SOURCE_URL = (
    "https://www.goodreturns.in/"
    "gold-rates/hyderabad.html"
)

# Change your alert threshold here.
THRESHOLD_INR = "13600"

# Can override threshold using GitHub variable:
# GOLD_THRESHOLD_INR

STATE_PATH = Path("state.json")


# ==========================================
# PRICE VALIDATION
# ==========================================

def money(value):
    """Convert INR value into Decimal."""

    cleaned = (
        str(value)
        .replace("₹", "")
        .replace(",", "")
        .strip()
    )

    try:
        result = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(
            f"Invalid INR value: {value!r}"
        ) from None

    if not result.is_finite() or result <= 0:
        raise ValueError(
            "INR amount must be positive and finite"
        )

    return result


def parse_price_cell(value):
    """
    Parse Goodreturns prices such as:

    ₹13,570
    ₹13,570 (+35)
    ₹13,570 (-75)
    ₹13,570 (0)

    Extract ONLY the primary gold price.
    """

    value = " ".join(
        value.replace("\u00a0", " ").split()
    )

    pattern = (
        r"^₹?\s*"
        r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
        r"(?:\s*\(\s*[+-]?\s*"
        r"[0-9][0-9,]*(?:\.[0-9]{1,2})?"
        r"\s*\))?"
        r"$"
    )

    match = re.fullmatch(pattern, value)

    if not match:
        raise ValueError(
            f"Unexpected price cell format: {value!r}"
        )

    return money(match.group(1))


# ==========================================
# HTML TABLE PARSER
# ==========================================

class PageParser(HTMLParser):
    """
    Collect HTML tables with their preceding
    headings while ignoring scripts and styles.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)

        self.heading = ""
        self.heading_parts = None
        self.heading_tag = None

        self.h1 = []
        self.tables = []

        self.table = None
        self.table_heading = None
        self.row = None
        self.cell = None

        self.skip_tags = []

    def handle_starttag(self, tag, attrs):

        if tag in ("script", "style"):
            self.skip_tags.append(tag)
            return

        if self.skip_tags:
            return

        if tag in ("h1", "h2", "h3", "h4"):
            self.heading_parts = []
            self.heading_tag = tag

        elif tag == "table":
            if self.table is not None:
                raise ValueError(
                    "Nested tables are unsupported"
                )

            self.table = []
            self.table_heading = self.heading

        elif tag == "tr" and self.table is not None:
            self.row = []

        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):

        if self.skip_tags:
            return

        if self.heading_parts is not None:
            self.heading_parts.append(data)

        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):

        if self.skip_tags:
            if tag == self.skip_tags[-1]:
                self.skip_tags.pop()
            return

        if tag == self.heading_tag:
            heading = " ".join(
                " ".join(self.heading_parts).split()
            )

            self.heading = heading

            if tag == "h1":
                self.h1.append(heading)

            self.heading_parts = None
            self.heading_tag = None

        elif tag in ("td", "th"):
            if self.cell is not None:
                cell_text = " ".join(
                    " ".join(self.cell).split()
                )

                self.row.append(cell_text)
                self.cell = None

        elif tag == "tr":
            if self.row is not None:
                self.table.append(self.row)
                self.row = None

        elif tag == "table":
            if self.table is not None:
                self.tables.append(
                    (self.table_heading, self.table)
                )

                self.table = None
                self.table_heading = None


# ==========================================
# EXTRACT HYDERABAD 22K GOLD PRICE
# ==========================================

def extract_price(html):

    parser = PageParser()
    parser.feed(html)
    parser.close()

    # Verify correct city.
    valid_page = any(
        re.search(
            r"Gold Rate in Hyderabad",
            heading,
            re.IGNORECASE
        )
        for heading in parser.h1
    )

    if not valid_page:
        raise ValueError(
            "Hyderabad gold page heading missing. "
            "Page may have changed or access was blocked."
        )

    prices = []

    expected_heading = (
        r"Today Gold Price Per Gram "
        r"in Hyderabad\s*\(INR\)"
    )

    for heading, rows in parser.tables:

        if not re.fullmatch(
            expected_heading,
            heading or "",
            re.IGNORECASE
        ):
            continue

        # Find the header row containing 22K.
        for header in rows:

            if not header:
                continue

            if header[0].strip().lower() not in (
                "gram",
                "grams"
            ):
                continue

            # Identify the 22K column.
            columns = [
                index
                for index, cell in enumerate(header)
                if re.fullmatch(
                    r"22\s*K(?:\s+Gold)?",
                    cell.strip(),
                    re.IGNORECASE
                )
            ]

            if len(columns) != 1:
                raise ValueError(
                    "22K column missing or ambiguous"
                )

            gold_column = columns[0]

            # Find exactly the 1-gram row.
            for row in rows:

                if not row:
                    continue

                is_one_gram = re.fullmatch(
                    r"1(?:\.0+)?"
                    r"(?:\s*(?:g|gm|gram|grams))?",
                    row[0].strip(),
                    re.IGNORECASE
                )

                if not is_one_gram:
                    continue

                if len(row) != len(header):
                    raise ValueError(
                        "Price row/header width mismatch"
                    )

                raw_price = row[gold_column]

                price = parse_price_cell(raw_price)

                prices.append(price)

    if len(prices) != 1:
        raise ValueError(
            "Expected exactly one Hyderabad "
            "22K gold price for 1 gram. "
            f"Found {len(prices)}."
        )

    return prices[0]


# ==========================================
# FETCH GOODRETURNS PAGE
# ==========================================

def fetch_page():

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(X11; Linux x86_64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-IN,en;q=0.9",
        "Cache-Control": "no-cache",
    }

    request = Request(
        SOURCE_URL,
        headers=headers
    )

    try:
        with urlopen(request, timeout=30) as response:

            final_url = response.geturl().rstrip("/")

            if final_url != SOURCE_URL.rstrip("/"):
                raise RuntimeError(
                    "Unexpected Goodreturns redirect"
                )

            content = response.read()

            charset = response.headers.get_content_charset()
            charset = charset or "utf-8"

            return content.decode(
                charset,
                errors="replace"
            )

    except HTTPError as error:
        raise RuntimeError(
            f"Goodreturns HTTP {error.code}. "
            "No alert or state update."
        ) from None

    except (URLError, TimeoutError):
        raise RuntimeError(
            "Goodreturns request failed. "
            "No alert or state update."
        ) from None


# ==========================================
# STATE MANAGEMENT
# ==========================================

def load_state(path):

    if not path.exists():
        return None

    state = json.loads(
        path.read_text(encoding="utf-8")
    )

    if (
        not isinstance(state, dict)
        or state.get("version") != 1
        or not isinstance(state.get("context"), str)
        or not isinstance(state.get("last_price"), str)
    ):
        raise ValueError(
            "Invalid state.json. "
            "Inspect it before resetting."
        )

    money(state["last_price"])

    return state


def save_state(path, state):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary = path.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(state, indent=2) + "\n",
        encoding="utf-8"
    )

    temporary.replace(path)


# ==========================================
# ALERT LOGIC
# ==========================================

def needs_alert(
    price,
    threshold,
    state,
    context
):
    """
    Send an alert when:

    1. Price is strictly below threshold.
    2. No previous state exists.
    3. Bot/chat/threshold context changed.
    4. Price changed since last check.

    Equality does not trigger an alert.
    """

    if price >= threshold:
        return False

    if state is None:
        return True

    if state["context"] != context:
        return True

    previous_price = money(
        state["last_price"]
    )

    return previous_price != price


# ==========================================
# TELEGRAM MESSAGE SENDER
# ==========================================

def send_telegram(token, chat_id, message):

    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "link_preview_options": {
            "is_disabled": True
        }
    }).encode("utf-8")

    request = Request(
        (
            "https://api.telegram.org/"
            f"bot{token}/sendMessage"
        ),
        data=payload,
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urlopen(
            request,
            timeout=30
        ) as response:

            result = json.load(response)

    except (
        HTTPError,
        URLError,
        TimeoutError,
        ValueError
    ):
        raise RuntimeError(
            "Telegram delivery failed or "
            "could not be confirmed. "
            "State unchanged."
        ) from None

    if not isinstance(result, dict):
        raise RuntimeError(
            "Invalid Telegram response"
        )

    message_data = result.get("result")

    if (
        result.get("ok") is not True
        or not isinstance(message_data, dict)
        or not isinstance(
            message_data.get("message_id"),
            int
        )
    ):
        raise RuntimeError(
            "Telegram did not confirm delivery"
        )


# ==========================================
# MAIN MONITOR LOGIC
# ==========================================

def run(
    price,
    threshold,
    path,
    context,
    sender
):

    state = load_state(path)

    alert = needs_alert(
        price,
        threshold,
        state,
        context
    )

    if alert:

        observed = datetime.now(
            timezone.utc
        ).strftime(
            "%d %b %Y %H:%M UTC"
        )

        message = (
            "🚨 HYDERABAD GOLD ALERT 🚨\n\n"
            "Gold Purity: 22K\n"
            "Quantity: 1 gram\n\n"
            f"Current Price: ₹{price:,.2f}\n"
            f"Your Threshold: ₹{threshold:,.2f}\n\n"
            "✅ Gold is below your target!\n\n"
            f"Checked: {observed}\n\n"
            f"Source: {SOURCE_URL}\n\n"
            "Note: Indicative price. "
            "GST and making charges may apply."
        )

        # Only save state after confirmed delivery.
        sender(message)

    updated_state = {
        "version": 1,
        "context": context,
        "last_price": str(price)
    }

    if state != updated_state:
        save_state(
            path,
            updated_state
        )

    return alert


# ==========================================
# PROGRAM ENTRY
# ==========================================

def main():

    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check price without sending or saving"
    )

    parser.add_argument(
        "--html",
        type=Path,
        help="Use saved HTML instead of fetching"
    )

    args = parser.parse_args()

    # Threshold configuration.
    threshold_value = (
        os.environ.get("GOLD_THRESHOLD_INR")
        or THRESHOLD_INR
    )

    threshold = money(threshold_value)

    # Fetch source HTML.
    if args.html:
        html = args.html.read_text(
            encoding="utf-8"
        )
    else:
        html = fetch_page()

    # Extract Hyderabad 22K price.
    price = extract_price(html)

    print("=" * 45)
    print("HYDERABAD GOLD PRICE MONITOR")
    print("=" * 45)

    print("City: Hyderabad")
    print("Purity: 22K")
    print("Weight: 1 gram")

    print(
        f"Current Gold Price: ₹{price:,.2f}"
    )

    print(
        f"Alert Threshold: ₹{threshold:,.2f}"
    )

    token = os.environ.get(
        "TELEGRAM_BOT_TOKEN", ""
    )

    chat_id = os.environ.get(
        "TELEGRAM_CHAT_ID", ""
    )

    # Context fingerprint:
    # Changes to threshold, bot or chat
    # allow a new qualifying alert.
    context_string = (
        f"{token}|{chat_id}|"
        f"{threshold.normalize()}"
    )

    context = hashlib.sha256(
        context_string.encode("utf-8")
    ).hexdigest()

    if args.dry_run:

        state = load_state(STATE_PATH)

        alert = needs_alert(
            price,
            threshold,
            state,
            context
        )

        print(
            f"Dry Run - Would Alert: {alert}"
        )

        print("No Telegram message sent.")
        print("No state saved.")

        return

    if not token or not chat_id:
        raise ValueError(
            "Set TELEGRAM_BOT_TOKEN "
            "and TELEGRAM_CHAT_ID"
        )

    sent = run(
        price,
        threshold,
        STATE_PATH,
        context,
        lambda message: send_telegram(
            token,
            chat_id,
            message
        )
    )

    if sent:
        print("✅ Telegram alert confirmed!")
    else:
        print("No alert needed.")

    print("Monitor completed successfully.")


# ==========================================
# START
# ==========================================

if __name__ == "__main__":

    try:
        main()

    except Exception as error:
        print(
            f"ERROR: {error}",
            file=sys.stderr
        )

        sys.exit(1)
