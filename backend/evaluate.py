"""Score the classifier against your hand labels (Block 3 eval).

Classifies any labeled email that has no result yet for the current model and prompt
version (about $0.02 for 50), then compares. Run it again after every prompt change.
"""

import os
from collections import Counter

import anthropic

import db
from classifier import CATEGORIES, MODEL, PROMPT_VERSION, classify
from classify import ENV_PATH, INPUT_PRICE, MAX_ATTEMPTS, OUTPUT_PRICE, load_env
from label import load_labels

FAILED = "failed"  # stands in for the model's category when it gave no valid answer


def get_or_classify(conn, client, item):
    """Return the item's analysis row for the current prompt version, classifying it first
    if it has none (or a failure that can still be retried). Also return the tokens spent."""
    analysis = db.get_analysis(conn, item["id"], MODEL, PROMPT_VERSION)
    if analysis is not None and (analysis["status"] == "ok" or analysis["attempts"] >= MAX_ATTEMPTS):
        return analysis, 0, 0
    outcome = classify(client, item)
    db.save_analysis(conn, item["id"], MODEL, PROMPT_VERSION, outcome.result, outcome.error,
                     outcome.input_tokens, outcome.output_tokens)
    conn.commit()
    return db.get_analysis(conn, item["id"], MODEL, PROMPT_VERSION), outcome.input_tokens, outcome.output_tokens


def percent(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "0/0"


def main() -> None:
    labels = load_labels()
    if not labels:
        print("No labels yet: run label.py first")
        return
    load_env(ENV_PATH)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"No ANTHROPIC_API_KEY: add it to {ENV_PATH}")
        return
    client = anthropic.Anthropic(max_retries=4)

    conn = db.connect()
    try:
        # Pair each label with the model's answer: (label, item, analysis row).
        pairs, missing, spent_in, spent_out = [], 0, 0, 0
        for (account, external_id), label in labels.items():
            item = db.get_item(conn, account, external_id)
            if item is None:  # no longer stored, e.g. after a rebuild of the database
                missing += 1
                continue
            try:
                analysis, used_in, used_out = get_or_classify(conn, client, item)
            except anthropic.APIError as error:
                print(f"Stopping: {type(error).__name__}: {error}")
                return
            spent_in += used_in
            spent_out += used_out
            pairs.append((label, item, analysis))
        report(pairs, missing, spent_in, spent_out)
    finally:
        conn.close()


def report(pairs, missing, spent_in, spent_out) -> None:
    """Print the scores and every disagreement."""
    n = len(pairs)
    model_category = lambda a: a["category"] if a["status"] == "ok" else FAILED
    ok_pairs = [(l, i, a) for l, i, a in pairs if a["status"] == "ok"]

    # Category: failures count as wrong.
    correct = sum(1 for l, _, a in pairs if l["category"] == model_category(a))
    print(f"\n{MODEL}, prompt {PROMPT_VERSION}: {n} labeled emails"
          + (f" ({missing} labels skipped: email no longer stored)" if missing else ""))
    print(f"Category accuracy:  {percent(correct, n)}")

    # Confusion table: rows are your labels, columns the model's answers.
    columns = list(CATEGORIES) + [FAILED]
    table = Counter((l["category"], model_category(a)) for l, _, a in pairs)
    print("\n" + "you \\ model".ljust(17) + "".join(c[:15].rjust(16) for c in columns))
    for row in CATEGORIES:
        print(row.ljust(17) + "".join(str(table[(row, c)]).rjust(16) for c in columns))

    # action_required: recall = of the emails that need you, how many were caught;
    # precision = of the emails flagged as needing you, how many really do.
    really = sum(1 for l, _, _ in pairs if l["category"] == "action_required")
    flagged = sum(1 for _, _, a in pairs if model_category(a) == "action_required")
    both = table[("action_required", "action_required")]
    print(f"\naction_required recall:     {percent(both, really)}   (missed ones cost you)")
    print(f"action_required precision:  {percent(both, flagged)}   (false alarms cost attention)")

    # Importance, only where the model answered.
    diffs = [abs(int(l["importance"]) - a["importance"]) for l, _, a in ok_pairs]
    print(f"Importance exact:   {percent(sum(d == 0 for d in diffs), len(diffs))}")
    print(f"Importance within 1: {percent(sum(d <= 1 for d in diffs), len(diffs))}")

    # Deadlines: compare your date with the model's.
    kinds = Counter()
    for l, _, a in ok_pairs:
        kinds[deadline_kind(l["deadline"] or None, a["deadline"])] += 1
    print("Deadlines:          " + ", ".join(f"{kinds[k]} {k}" for k in
                                          ("match", "wrong date", "missed", "extra", "none")))

    failures = Counter(a["error"] for _, _, a in pairs if a["status"] != "ok")
    if failures:
        print(f"Failed:             {dict(failures)}")
    cost = (spent_in * INPUT_PRICE + spent_out * OUTPUT_PRICE) / 1_000_000
    print(f"Spent this run:     {spent_in:,} tokens in, {spent_out:,} out (about ${cost:.4f})")

    # Every disagreement, so you can decide who was right.
    print("\nDisagreements (category differs, importance off by 2+, or deadlines differ):")
    for l, item, a in pairs:
        cat_differs = l["category"] != model_category(a)
        imp_far = a["status"] == "ok" and abs(int(l["importance"]) - a["importance"]) >= 2
        dl_differs = a["status"] == "ok" and deadline_kind(l["deadline"] or None, a["deadline"]) not in ("match", "none")
        if not (cat_differs or imp_far or dl_differs):
            continue
        subject = (item["subject"] or "(no subject)")[:70]
        print(f"\n  item {item['id']}: {subject}")
        print(f"    you:   {l['category']}, {l['importance']}, deadline {l['deadline'] or '-'}")
        if a["status"] == "ok":
            print(f"    model: {a['category']}, {a['importance']}, deadline {a['deadline'] or '-'}")
            print(f"    reason: {a['reason']}")
            if a["deadline_evidence"]:
                print(f"    evidence: \"{a['deadline_evidence']}\"")
        else:
            print(f"    model: failed ({a['error']})")


def deadline_kind(yours: str | None, models: str | None) -> str:
    """Name how two deadlines compare: match, wrong date, missed, extra or none."""
    if yours is None and models is None:
        return "none"
    if yours is None:
        return "extra"  # the model found a date you didn't: check whether you missed it
    if models is None:
        return "missed"
    return "match" if yours == models else "wrong date"


if __name__ == "__main__":
    main()
