#!/usr/bin/env python3
# Generation Timestamp: 2026-09-21T14:45:00Z
"""
apply_rt10_medium_render.py — memory-hermes v2.11.0, pre-deploy amendment

WHAT
  Option C for RT-10 confidence rendering on the astral_recall tool path:
    LOW     ->  "\u26a0 low confidence: <reason>"     (unchanged)
    MEDIUM  ->  "medium: <reason>"                 (was "\u26a0 medium confidence: ...")
    HIGH    ->  key dropped                        (unchanged)
  plus a `confidence_legend` on the tool result whenever at least one result
  carries a rendered label, mirroring the server's augmented-prompt preamble
  so both paths give the LLM the same instruction.

  Review follow-up, folded into the same pass:
    - v2.11.0 changelog header gains an E9 entry recording the deviation.
    - the astral_recall call-site comment is amended (it described the
      pre-E9 glyph-on-medium render).
    - the helper docstring's "absence of a warning" sentence tightened:
      the field's absence is the signal now that MEDIUM renders a label.
    - tests: medium-empty-reason case ("medium", no reason) added.

WHY
  Measured label distribution on bot-jarmo (2026-09-21): 270 high / 977 medium
  / 91 low. With the glyph on MEDIUM, ~80% of recall results carry a warning,
  so "absence of a warning is the high-confidence signal" stops working, and
  the recall path disagrees with the server preamble (which treats medium as
  neutral). Deviation from SPEC-METAMEMORY-001 v1.1 §7.1 — record in E9.

SAFETY
  - Dry run by default; pass --apply to write.
  - Every anchor must match exactly once or nothing is written.
  - Timestamped .bak-pre-e9-* backup next to each touched file (matches the
    .gitignore *.bak-pre-* pattern — the F2 convention).
  - Idempotent: a second run reports "already applied".

RUN (from the repo root, /srv/oc-projects/memory_hermes/memory-hermes)
  python apply_rt10_medium_render.py            # dry run
  python apply_rt10_medium_render.py --apply
"""
from __future__ import annotations

import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

PLUGIN = Path("astral-memory/__init__.py")
TESTS = Path("tests/test_provider_contract.py")

# ── astral-memory/__init__.py ───────────────────────────────────────────────

C1_OLD = r'''# v2.11.0 CHANGES (RT-10 — SPEC-METAMEMORY-001 v1.1 §7.1, plugin handoff):
#   NEW  — _confidence_warning(): renders the server's RT-10 confidence
'''
C1_NEW = r'''# v2.11.0 CHANGES (RT-10 — SPEC-METAMEMORY-001 v1.1 §7.1, plugin handoff):
#   E9   — pre-deploy amendment (2026-09-21, recorded in server session
#          E9): MEDIUM renders as a plain "medium[: reason]" label, not a
#          warning. Measured bot-jarmo distribution (270 high / 977 medium
#          / 91 low) put the glyph on ~80% of results, which breaks
#          "absence of a warning is the signal" and disagrees with the
#          server's augmented-prompt preamble (medium = neutral). The ⚠ is
#          reserved for LOW. NEW: _CONFIDENCE_LEGEND attached to
#          astral_recall results whenever at least one carries a rendered
#          label (only when labelled > 0, so the no-confidence render
#          stays byte-identical to v2.10.0).
#   NEW  — _confidence_warning(): renders the server's RT-10 confidence
'''

P1_OLD = r'''    """RT-10 (SPEC-METAMEMORY-001 v1.1 §7.1): LOW/MEDIUM confidence warning.
'''
P1_NEW = r'''    """RT-10 confidence render (SPEC-METAMEMORY-001 v1.1 §7.1, amended E9).

    LOW carries the warning glyph; MEDIUM renders as a plain label (no
    glyph) because ~73% of the measured corpus is medium and a warning on
    the majority is noise, and because the server preamble treats medium
    as neutral.
'''

P1B_OLD = r'''    HIGH is silent — absence of a warning is the high-confidence signal.
'''
P1B_NEW = r'''    HIGH is silent — the field's absence is the high-confidence signal.
'''

P2_OLD = r'''    label = str(conf.get("label") or "")
    if label not in ("medium", "low"):
        return ""
    reason = str(conf.get("reason") or "").strip()
    if reason:
        return f"\u26a0 {label} confidence: {reason}"
    return f"\u26a0 {label} confidence"
'''
P2_NEW = r'''    label = str(conf.get("label") or "")
    reason = str(conf.get("reason") or "").strip()
    if label == "low":
        if reason:
            return f"\u26a0 low confidence: {reason}"
        return "\u26a0 low confidence"
    if label == "medium":
        return f"medium: {reason}" if reason else "medium"
    return ""


# RT-10 (E9): recall-path counterpart of the server's augmented-prompt
# preamble, attached only when at least one result carries a rendered label
# so the no-confidence render stays byte-identical to v2.10.0.
_CONFIDENCE_LEGEND = (
    "\u26a0 marks a low-confidence memory: limited or old evidence, so "
    "mention that briefly instead of stating it as fact. 'medium' is usable "
    "as is; prefer newer memories where they conflict. No confidence field "
    "means well-supported by past use."
)
'''

P0_OLD = r'''                # v2.11.0 (RT-10, SPEC-METAMEMORY-001 v1.1 §7.1): render
                # confidence as a warning — LOW/MEDIUM carry the mandated
                # warning string on the result; HIGH and malformed payloads
                # are dropped so `confidence` appears only as a warning
                # (absence of a warning IS the high-confidence signal).
                # Results without the key (pre-RT-10 server or kill
'''
P0_NEW = r'''                # v2.11.0 (RT-10, SPEC-METAMEMORY-001 v1.1, amended E9):
                # render confidence — LOW carries the ⚠ warning, MEDIUM a
                # plain "medium[: reason]" label (E9: the corpus is ~73%
                # medium; a warning on the majority is noise). HIGH and
                # malformed payloads are dropped so the confidence field
                # appears only as a rendered label — its absence is the
                # high-confidence signal. Results without the key
                # (pre-RT-10 server or kill
'''

P3_OLD = r'''                # switch) are untouched — byte-identical to v2.10.0.
                for r in results:
                    if not isinstance(r, dict) or "confidence" not in r:
                        continue
                    warning = _confidence_warning(r)
                    if warning:
                        r["confidence"] = warning
                    else:
                        r.pop("confidence", None)
'''
P3_NEW = r'''                # switch) are untouched — byte-identical to v2.10.0.
                labelled = 0
                for r in results:
                    if not isinstance(r, dict) or "confidence" not in r:
                        continue
                    warning = _confidence_warning(r)
                    if warning:
                        r["confidence"] = warning
                        labelled += 1
                    else:
                        r.pop("confidence", None)
                if labelled:
                    data["confidence_legend"] = _CONFIDENCE_LEGEND
'''

# ── tests/test_provider_contract.py ─────────────────────────────────────────

T1_OLD = r'''    assert "\u26a0 medium confidence: mixed signals" in out
'''
T1_NEW = r'''    parsed = json.loads(out)
    conf = parsed["results"][0]["confidence"]
    assert conf == "medium: mixed signals"
    assert "\u26a0" not in conf, "glyph is reserved for LOW (E9)"
    assert "confidence_legend" in parsed


def test_recall_medium_empty_reason_label_only(provider, http, monkeypatch):
    _serve_search(http, monkeypatch, [
        {"id": "m-med-quiet", "text": "Middling, quiet reason", "similarity": 0.5,
         "confidence": {"score": 0.45, "label": "medium", "reason": ""}},
    ])
    out = provider.handle_tool_call("astral_recall", {"query": "q"})
    parsed = json.loads(out)
    assert parsed["results"][0]["confidence"] == "medium"
    assert "confidence_legend" in parsed
'''

EDITS = {
    PLUGIN: [("changelog E9 entry", C1_OLD, C1_NEW),
             ("docstring", P1_OLD, P1_NEW),
             ("docstring signal sentence", P1B_OLD, P1B_NEW),
             ("render function + legend constant", P2_OLD, P2_NEW),
             ("call-site comment", P0_OLD, P0_NEW),
             ("recall render site", P3_OLD, P3_NEW)],
    TESTS: [("medium test assertion + empty-reason medium case", T1_OLD, T1_NEW)],
}


def plan(path: Path):
    """Return (new_text, report_lines, ok). Never writes."""
    text = path.read_text(encoding="utf-8")
    lines, ok = [], True
    for name, old, new in EDITS[path]:
        if new in text and old not in text:
            lines.append(f"  = {name}: already applied")
            continue
        n = text.count(old)
        if n != 1:
            lines.append(f"  ! {name}: anchor matched {n} times (need exactly 1)")
            ok = False
            continue
        text = text.replace(old, new)
        lines.append(f"  + {name}: will apply")
    return text, lines, ok


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    missing = [p for p in EDITS if not p.is_file()]
    if missing:
        print("Run from the repo root. Missing:", ", ".join(map(str, missing)))
        return 2

    results, all_ok = {}, True
    for path in EDITS:
        new_text, lines, ok = plan(path)
        print(path)
        print("\n".join(lines))
        results[path] = new_text
        all_ok &= ok

    if path_needs_json_import(results[TESTS]):
        print(f"  ! {TESTS}: `import json` not found — add it before running tests")
        all_ok = False

    if not all_ok:
        print("\nNothing written: fix the anchors above first.")
        return 1
    if not apply:
        print("\nDry run OK. Re-run with --apply to write.")
        return 0

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for path, new_text in results.items():
        if new_text == path.read_text(encoding="utf-8"):
            continue
        shutil.copy2(path, path.with_name(f"{path.name}.bak-pre-e9-{stamp}"))
        path.write_text(new_text, encoding="utf-8")
        print(f"written: {path}  (backup .bak-pre-e9-{stamp})")
    print("\nNext: ASTRAL_CONTRACT_REQUIRE=1 PYTHONPATH=/srv/oc-projects/hermes "
          "pytest tests/ -q")
    return 0


def path_needs_json_import(test_text: str) -> bool:
    return "import json" not in test_text


if __name__ == "__main__":
    sys.exit(main())
