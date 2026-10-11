"""Hand-label a random sample of emails for the eval set (Block 3).

Shows each email (never the model's answer, which would bias you), asks for your category,
importance and deadline, and saves after every email, so you can quit and resume anytime.
This is the one script that shows email text: it's for your eyes, and nothing is logged.
"""

import argparse
import csv
import random
from datetime import date
from pathlib import Path

import db
from classifier import email_facts

LABELS_PATH = Path(__file__).parent / "eval_labels.csv"  # gitignored: labels of real emails
FIELDS = ["account", "external_id", "category", "importance", "deadline"]
SEED = 2026  # fixed, so the "random" sample is the same emails every time
CATEGORY_KEYS = {"a": "action_required", "f": "fyi", "b": "bulk"}
PREVIEW_LINES = 40


class Quit(Exception):
    """You typed q."""


def load_labels(path: Path = LABELS_PATH) -> dict[tuple[str, str], dict]:
    """Return the saved labels, keyed by (account, external_id)."""
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {(row["account"], row["external_id"]): row for row in csv.DictReader(f)}


def save_label(label: dict, path: Path = LABELS_PATH) -> None:
    """Append one label to the CSV file, writing the header row if the file is new."""
    is_new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(label)


def ask(prompt: str, parse):
    """Ask until parse(answer) returns a value instead of raising ValueError. q quits."""
    while True:
        answer = input(prompt).strip()
        if answer.lower() == "q":
            raise Quit
        try:
            return parse(answer)
        except ValueError as error:
            print(f"  {error}")


def parse_category(answer: str) -> str | None:
    """Return a category for a/f/b, or None for s (skip)."""
    if answer.lower() == "s":
        return None
    if answer.lower() not in CATEGORY_KEYS:
        raise ValueError("type a, f or b (or s to skip, q to quit)")
    return CATEGORY_KEYS[answer.lower()]


def parse_importance(answer: str) -> int:
    if answer not in {"1", "2", "3", "4", "5"}:
        raise ValueError("type a number from 1 to 5")
    return int(answer)


def parse_deadline(answer: str) -> str:
    """Return YYYY-MM-DD, or "" for no deadline."""
    if not answer:
        return ""
    return date.fromisoformat(answer).isoformat()  # raises ValueError for anything else


def show(item, number: int, total: int) -> None:
    """Print the email the way the model sees it: the same facts, the same cut-off body."""
    facts = email_facts(item)
    print("\n" + "=" * 78)
    print(f"[{number}/{total}]  {facts['received']}")
    print(f"From:    {facts['sender']}")
    print(f"Subject: {facts['subject']}")
    print(f"You are in: {facts['addressed']}   Gmail Promotions: {facts['gmail_promotions']}")
    print("-" * 78)
    lines = facts["body"].splitlines()
    print("\n".join(lines[:PREVIEW_LINES]))
    if len(lines) > PREVIEW_LINES:
        if input(f"... {len(lines) - PREVIEW_LINES} more lines. Enter to skip them, m for more: ") == "m":
            print("\n".join(lines[PREVIEW_LINES:]))
    if facts["truncated"]:
        print("[... the model only sees up to here ...]")
    print("-" * 78)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=int, default=50, help="label until this many are saved")
    parser.add_argument(
        "--non-promotions", action="store_true",
        help="pick only emails Gmail didn't label Promotions (to find more action_required)",
    )
    args = parser.parse_args()

    conn = db.connect()
    try:
        labels = load_labels()
        keys = db.list_item_keys(conn, args.non_promotions)
        random.Random(SEED).shuffle(keys)  # a private generator, so the seed fixes the order
        to_label = [k for k in keys if (k["account"], k["external_id"]) not in labels]
        print(f"{len(labels)} labels saved; target {args.target}.")
        print("Categories: a = action_required (I must do something), "
              "f = fyi (worth knowing, nothing to do), b = bulk (mass mail).")

        for key in to_label:
            if len(labels) >= args.target:
                break
            show(db.get_item(conn, key["account"], key["external_id"]), len(labels) + 1, args.target)
            category = ask("Category a/f/b (s skip, q quit): ", parse_category)
            if category is None:
                continue
            label = {
                "account": key["account"],
                "external_id": key["external_id"],
                "category": category,
                "importance": ask("Importance 1-5: ", parse_importance),
                "deadline": ask("Deadline YYYY-MM-DD (Enter for none): ", parse_deadline),
            }
            save_label(label)
            labels[(label["account"], label["external_id"])] = label

        counts = {c: sum(1 for l in labels.values() if l["category"] == c) for c in CATEGORY_KEYS.values()}
        print(f"\n{len(labels)} labels saved: {counts}")
    except (Quit, KeyboardInterrupt, EOFError):
        print(f"\nStopped. {len(labels)} labels saved; run again to continue.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
