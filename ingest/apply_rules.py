#!/usr/bin/env python3
"""
apply_rules.py — apply data/rules.yaml to a reagent JSON and emit a handling profile.

Usage:
    python ingest/apply_rules.py data/reagents/64-17-5.json
    python ingest/apply_rules.py data/reagents/64-17-5.json --out site/data/profiles/64-17-5.json
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).parent.parent
RULES_PATH = REPO_ROOT / "data" / "rules.yaml"


def _flag_value(properties: dict, key: str) -> Any:
    """
    Read a property value from a properties dict, handling both flat (v1)
    and sourced_boolean (v2) shapes transparently.
    """
    raw = properties.get(key)
    if isinstance(raw, dict) and "value" in raw:
        return raw["value"]
    return raw


def _flag_confidence(properties: dict, key: str) -> str:
    """Return the stored confidence for a sourced_boolean, or 'high' for flat values."""
    raw = properties.get(key)
    if isinstance(raw, dict) and "confidence" in raw:
        return raw["confidence"]
    return "high"


def _set_nested(d: dict, dotted_key: str, value: Any) -> None:
    """Set d[a][b][c] = value given dotted_key 'a.b.c'. Creates dicts as needed."""
    parts = dotted_key.split(".")
    node = d
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _evaluate_condition(prop_value: Any, condition: Any) -> bool:
    """
    Evaluate one condition against a property value.

    condition forms:
      - a plain value (bool, str, number): equality check
      - a dict like {gt: 5} / {gte: 5} / {lt: 5} / {lte: 5} / {eq: x} / {ne: x}
    """
    if prop_value is None:
        return False

    if isinstance(condition, dict):
        for op, operand in condition.items():
            if op == "gt"  and not (prop_value > operand):   return False
            if op == "gte" and not (prop_value >= operand):  return False
            if op == "lt"  and not (prop_value < operand):   return False
            if op == "lte" and not (prop_value <= operand):  return False
            if op == "eq"  and not (prop_value == operand):  return False
            if op == "ne"  and not (prop_value != operand):  return False
        return True
    else:
        return prop_value == condition


def evaluate_rule(rule: dict, properties: dict) -> bool:
    """
    Return True if all `when` conditions are satisfied.
    Uses _flag_value() so it handles both flat (v1) and sourced_boolean (v2) shapes.
    """
    when = rule.get("when", {})
    for key, condition in when.items():
        prop_value = _flag_value(properties, key)
        if not _evaluate_condition(prop_value, condition):
            return False
    return True


def _rule_confidence(rule: dict, properties: dict) -> str:
    """
    Confidence of the fired rule = lowest confidence among the flags that triggered it.
    Falls back to 'high' for numeric/flat properties.
    """
    tiers = {"high": 0, "medium": 1, "low": 2}
    worst = "high"
    for key in rule.get("when", {}):
        conf = _flag_confidence(properties, key)
        if tiers.get(conf, 2) > tiers.get(worst, 0):
            worst = conf
    return worst


def apply_rules(reagent: dict, rules: list[dict]) -> dict:
    """
    Fire applicable rules and assemble a handling profile.

    Returns:
        {
          "handling_profile": {...},   # merged `then` directives from fired rules
          "rules_fired": [             # details of each fired rule
              {"id": ..., "because": ..., "cite": ...,
               "confidence": ..., "field_consensus": bool},
              ...
          ],
          "conflicts": []              # placeholder — surfaced in future phase
        }
    """
    properties = reagent.get("properties", {})
    # physical_state is a top-level field — add it for rule matching
    props_extended = dict(properties)
    props_extended["physical_state"] = reagent.get("physical_state")

    handling_profile: dict = {}
    rules_fired: list[dict] = []

    for rule in rules:
        if evaluate_rule(rule, props_extended):
            # merge `then` directives into the profile
            then = rule.get("then", {})
            for dotted_key, value in then.items():
                _set_nested(handling_profile, dotted_key, value)

            cite = rule.get("cite", "")
            is_field_consensus = "field consensus" in cite.lower()
            rules_fired.append({
                "id": rule["id"],
                "because": rule.get("because", "").strip(),
                "cite": cite,
                "confidence": _rule_confidence(rule, props_extended),
                "field_consensus": is_field_consensus,
            })

    return {
        "reagent_name": reagent.get("name"),
        "cas": reagent.get("cas"),
        "handling_profile": handling_profile,
        "rules_fired": rules_fired,
        "conflicts": [],
    }


def main():
    parser = argparse.ArgumentParser(description="Apply rules.yaml to a reagent JSON.")
    parser.add_argument("reagent_path", help="Path to reagent JSON file")
    parser.add_argument("--out", help="Write handling profile to this path instead of stdout")
    parser.add_argument(
        "--write-bench-knowledge", action="store_true",
        help="Overwrite bench_knowledge in the source reagent JSON with the "
             "'because' text from each fired rule (deterministic, no prose).",
    )
    args = parser.parse_args()

    reagent_path = Path(args.reagent_path)
    if not reagent_path.exists():
        print(f"ERROR: reagent file not found: {reagent_path}", file=sys.stderr)
        sys.exit(1)

    with open(reagent_path) as f:
        reagent = json.load(f)

    with open(RULES_PATH) as f:
        rules = yaml.safe_load(f)

    profile = apply_rules(reagent, rules)

    # Optionally overwrite bench_knowledge in the source reagent JSON.
    if args.write_bench_knowledge:
        bullets = [r["because"] for r in profile["rules_fired"]]
        reagent["bench_knowledge"] = bullets
        # Remove striking_fact if still present (field is retired).
        reagent.pop("striking_fact", None)
        reagent_path.write_text(json.dumps(reagent, indent=2) + "\n")
        n = len(bullets)
        print(f"bench_knowledge updated ({n} bullet{'s' if n != 1 else ''} from fired rules) → {reagent_path}")

    output = json.dumps(profile, indent=2)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output)
        print(f"Handling profile written to {out_path}")
    else:
        print(output)


if __name__ == "__main__":
    main()
