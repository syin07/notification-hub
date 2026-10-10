"""Classify emails that have no result yet for the current model and prompt version."""

import argparse
import os
from pathlib import Path

import anthropic

import db
from classifier import MODEL, PROMPT_VERSION, classify

ENV_PATH = Path(__file__).parent.parent / ".env"  # gitignored; holds ANTHROPIC_API_KEY
MAX_ATTEMPTS = 3  # an email that failed this often is left alone until the prompt changes

# Claude Haiku 5.5 prices in dollars per million tokens (Oct 2026, prompts under 100K tokens).
INPUT_PRICE = 0.10
OUTPUT_PRICE = 0.50


def load_env(path: Path) -> None:
    """Copy KEY=value lines from a .env file into the environment, keeping values already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="most emails to classify (default 10)")
    args = parser.parse_args()

    load_env(ENV_PATH)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"No ANTHROPIC_API_KEY: add it to {ENV_PATH}")
        return
    client = anthropic.Anthropic(max_retries=4)  # the SDK retries 429s and 5xx with backoff

    conn = db.connect()
    try:
        items = db.items_to_classify(conn, MODEL, PROMPT_VERSION, MAX_ATTEMPTS, args.limit)
        print(f"{len(items)} emails to classify with {MODEL}, prompt {PROMPT_VERSION}")

        ok = failed = input_tokens = output_tokens = 0
        for item in items:
            try:
                outcome = classify(client, item)
            except anthropic.APIError as error:
                # An outage, a bad key or a bug in our request: not this email's fault, so
                # stop without marking anything failed. Sync is unaffected; rerun later.
                print(f"Stopping: {type(error).__name__}: {error}")
                break
            db.save_analysis(
                conn, item["id"], MODEL, PROMPT_VERSION, outcome.result, outcome.error,
                outcome.input_tokens, outcome.output_tokens,
            )
            conn.commit()  # each answer is paid for: keep it even if a later one crashes
            input_tokens += outcome.input_tokens
            output_tokens += outcome.output_tokens

            if outcome.result is not None:
                ok += 1
                r = outcome.result
                due = f", due {r['deadline']}" if r["deadline"] else ""
                print(f"  item {item['id']}: {r['category']}, importance {r['importance']}{due}")
            else:
                failed += 1
                print(f"  item {item['id']}: failed, {outcome.error}")

        cost = (input_tokens * INPUT_PRICE + output_tokens * OUTPUT_PRICE) / 1_000_000
        print(f"\n{ok} classified, {failed} failed. "
              f"Tokens: {input_tokens:,} in, {output_tokens:,} out (about ${cost:.4f})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
