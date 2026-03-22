#!/usr/bin/env python3
"""
consolidate.py — Transforms raw metadata-plugin-modernizer data into structured JSON.

ENV:
    INPUT_DIR      default: .  (workspace root = checkout of metadata-plugin-modernizer)
    OUTPUT_DIR     default: plugin-modernizer-stats
    MAX_ERROR_RATE default: 0.02  (fraction of plugin-copy failures tolerated)
"""

import hashlib, json, os, re, shutil, sys
from datetime import datetime, timezone
from pathlib import Path

INPUT_BASE     = Path(os.environ.get("INPUT_DIR",  ".")).resolve()
OUTPUT_BASE    = Path(os.environ.get("OUTPUT_DIR", "/tmp/plugin-modernizer-stats")).resolve()
MAX_ERROR_RATE = float(os.environ.get("MAX_ERROR_RATE", "0.02"))

SUMMARY_MD  = INPUT_BASE / "reports" / "summary.md"
RECIPES_SRC = INPUT_BASE / "reports" / "recipes"
RECIPES_OUT = OUTPUT_BASE / "recipes"
PLUGINS_OUT = OUTPUT_BASE / "plugins-reports"
EXCLUDED_DIRS = frozenset([".github", "reports", ".git", "scripts"])

# Both '*' and '-' bullets are valid in the actual summary.md.
_RE_OVERVIEW = re.compile(r"^[-*]\s+\*\*(.+?)\*\*:\s*(.+)$")
_RE_RECIPE   = re.compile(r"^[-*]\s+([\w.]+):\s+(\d+)\s+failures?$")
_RE_PLUGIN   = re.compile(r"^[-*]\s+\[([^\]]+)\]\([^)]+\)$")

_KNOWN_SECTIONS = frozenset([
    "overview",
    "recipes",
    "plugins",
    "pr",
])

error_count        = 0  # total warnings
plugin_error_count = 0  # plugin-copy failures only (used for error-rate check)


class ParseError(Exception):
    pass


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")


def warn(msg, is_plugin_error=False):
    global error_count, plugin_error_count
    print(f"[WARN] {msg}", file=sys.stderr)
    error_count += 1
    if is_plugin_error:
        plugin_error_count += 1


def to_int(v, field):
    try:
        return int(v.replace(",", "").strip())
    except (ValueError, TypeError):
        raise ParseError(f"Expected int for '{field}', got: {v!r}")


def to_float(v, field):
    try:
        return float(v.replace("%", "").replace(",", "").strip())
    except (ValueError, TypeError):
        raise ParseError(f"Expected float for '{field}', got: {v!r}")


def parse_timestamp(raw):
    for fmt in ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).isoformat()
    except ValueError:
        raise ParseError(f"Cannot parse timestamp: {raw!r}")


def parse_summary_md(content, sha256):
    generated_at = total = failed = rate = None
    recipes, plugins, pr = [], [], {}
    section = None

    for lineno, line in enumerate(content.split("\n"), start=1):
        s = line.strip()
        if not s or s.startswith("# "):
            continue

        m = re.match(r"Generated on:\s*(.+)", s)
        if m:
            generated_at = parse_timestamp(m.group(1))
            continue

        if s.startswith("## "):
            heading = s[3:].strip()
            section = {
                "Overview":                       "overview",
                "Failures by Recipe":             "recipes",
                "Plugins with Failed Migrations": "plugins",
                "Pull Request Statistics":        "pr",
            }.get(heading)
            if section is None:
                warn(
                    f"Line {lineno}: unrecognised section heading '## {heading}' — "
                    "will be ignored. If this section contains required data the "
                    "upstream format may have changed."
                )
            continue

        if section == "overview":
            m = _RE_OVERVIEW.match(s)
            if m:
                k, v = m.group(1), m.group(2)
                if k == "Total Migrations":    total  = to_int(v, k)
                elif k == "Failed Migrations": failed = to_int(v, k)
                elif k == "Success Rate":      rate   = to_float(v, k)

        elif section == "recipes":
            m = _RE_RECIPE.match(s)
            if m:
                recipes.append({"recipeId": m.group(1), "failures": to_int(m.group(2), "failures")})

        elif section == "plugins":
            m = _RE_PLUGIN.match(s)
            if m:
                plugins.append(m.group(1))

        elif section == "pr":
            if not s.startswith("|") or "---" in s or "Status" in s:
                continue
            cells = [c.strip() for c in s.split("|") if c.strip()]
            if len(cells) >= 2:
                label, raw_val = cells[0], cells[1]
                if label in ("Total PRs", "Open PRs", "Closed PRs", "Merged PRs"):
                    pr[label] = to_int(raw_val, label)

    # ── Validate required fields ─────────────────────────────────────────────
    missing = [
        f for f, v in [
            ("generated_at",     generated_at),
            ("Total Migrations", total),
            ("Failed Migrations", failed),
            ("Success Rate",     rate),
        ] if v is None
    ]
    for k in ("Total PRs", "Open PRs", "Closed PRs", "Merged PRs"):
        if k not in pr:
            missing.append(k)
    if missing:
        raise ParseError(f"Missing required fields after parsing summary.md: {missing}")

    if failed > total:
        raise ParseError(f"failed_migrations ({failed}) > total_migrations ({total})")
    if not (0.0 <= rate <= 100.0):
        raise ParseError(f"success_rate {rate} not in [0, 100]")
        
    pr_sum = pr["Open PRs"] + pr["Closed PRs"] + pr["Merged PRs"]
    if pr_sum != pr["Total PRs"]:
        raise ParseError(
            f"PR counts inconsistent: "
            f"Open ({pr['Open PRs']}) + "
            f"Closed/unmerged ({pr['Closed PRs']}) + "
            f"Merged ({pr['Merged PRs']}) = {pr_sum}, "
            f"expected Total PRs = {pr['Total PRs']}."
        )

    # mergeRate = Merged / (Merged + Closed-unmerged): PR acceptance rate
    terminal = pr["Merged PRs"] + pr["Closed PRs"]
    merge_rate = round(pr["Merged PRs"] / terminal * 100, 2) if terminal else 0.0

    return {
        "schemaVersion": "1.0",
        "generatedAt":   generated_at,
        "dataSource":    "https://github.com/jenkins-infra/metadata-plugin-modernizer",
        "meta": {
            "source_sha256": sha256,
            "parsed_at":     datetime.now(timezone.utc).isoformat(),
        },
        "overview": {
            "totalPlugins":        0,          # filled in after copy_plugins()
            "totalMigrations":     int(total),
            "successfulMigrations": int(total - failed),
            "failedMigrations":    int(failed),
            "pendingMigrations":   None,       # filled in after build_recipe_stats()
            "successRate":         float(rate),
        },
        "pullRequests": {
            "totalPRs":  int(pr["Total PRs"]),
            "openPRs":   int(pr["Open PRs"]),
            "closedPRs": int(pr["Closed PRs"]),
            "mergedPRs": int(pr["Merged PRs"]),
            "mergeRate": float(merge_rate),
        },
        "failuresByRecipe":             recipes,
        "pluginsWithFailedMigrations":  sorted(plugins),
    }


def copy_dir(src: Path, dest: Path) -> None:
    """Recursively copy src directory tree into dest.

    Fix #2 (copy_dir logic bug): the original one-liner evaluated the
    ternary condition and then unconditionally called shutil.copy2, meaning
    the recursive branch was never reached and subdirectories were silently
    skipped.  The explicit if/else below is correct and readable.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        target = dest / entry.name
        if entry.is_dir():
            copy_dir(entry, target)
        else:
            shutil.copy2(entry, target)


def copy_recipes():
    names = []
    if not RECIPES_SRC.exists():
        warn("reports/recipes/ not found — recipe data unavailable.")
        return names
    RECIPES_OUT.mkdir(parents=True, exist_ok=True)
    for f in sorted(RECIPES_SRC.glob("*.json")):
        try:
            content = f.read_text(encoding="utf-8")
            json.loads(content)                              # validate JSON
            (RECIPES_OUT / f.name).write_text(content, encoding="utf-8")
            names.append(f.stem)
        except (json.JSONDecodeError, OSError) as e:
            warn(f"Skipping recipe {f.name}: {e}")
    log(f"Copied {len(names)} recipe files.")
    return names


def copy_plugins():
    names = []
    PLUGINS_OUT.mkdir(parents=True, exist_ok=True)
    entries = sorted(
        e for e in INPUT_BASE.iterdir()
        if e.is_dir() and e.name not in EXCLUDED_DIRS
    )
    for i, entry in enumerate(entries):
        dest = PLUGINS_OUT / entry.name
        try:
            copied = False
            for sub in ("reports", "modernization-metadata"):
                src = entry / sub
                if src.exists():
                    copy_dir(src, dest / sub)
                    copied = True
            csv = entry / "failed-migrations.csv"
            if csv.exists():
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(csv, dest / csv.name)
                copied = True
            if copied:
                names.append(entry.name)
            else:
                warn(
                    f"Plugin '{entry.name}' had no recognisable content — skipped.",
                    is_plugin_error=True,
                )
        except OSError as e:
            warn(f"Failed to copy '{entry.name}': {e}", is_plugin_error=True)
        if (i + 1) % 50 == 0:
            log(f"  Processed {i + 1}/{len(entries)} plugins…")
    log(f"Copied {len(names)} plugin directories.")
    return sorted(names)


def build_recipe_stats(names):
    stats = []
    for name in names:
        try:
            data  = json.loads((RECIPES_OUT / f"{name}.json").read_text(encoding="utf-8"))
            total   = int(data.get("totalApplications", 0))
            success = int(data.get("successCount",      0))
            fail    = int(data.get("failureCount",       0))
            stats.append({
                "recipeId": str(data.get("recipeId", name)),
                "total":    total,
                "success":  success,
                "fail":     fail,
                "pending":  max(0, total - success - fail),
            })
        except (json.JSONDecodeError, OSError) as e:
            warn(f"Could not read recipe stats for '{name}': {e}")
    return stats


def build_timeline_and_tags(plugin_names):
    months: dict  = {}
    tag_map: dict = {}
    for name in plugin_names:
        path = PLUGINS_OUT / name / "reports" / "aggregated_migrations.json"
        if not path.exists():
            continue
        try:
            for m in json.loads(path.read_text(encoding="utf-8")).get("migrations", []):
                month = str(m.get("timestamp", ""))[:7]
                if len(month) == 7 and month[4] == "-":
                    bucket = months.setdefault(month, {"success": 0, "fail": 0})
                    if m.get("migrationStatus") == "success":
                        bucket["success"] += 1
                    else:
                        bucket["fail"] += 1
                for tag in (m.get("tags") or []):
                    tag_map[str(tag)] = tag_map.get(str(tag), 0) + 1
        except (json.JSONDecodeError, OSError):
            pass

    timeline = [
        {
            "month":   mo,
            "success": int(v["success"]),
            "fail":    int(v["fail"]),
            "total":   int(v["success"] + v["fail"]),
        }
        for mo, v in sorted(months.items())
    ]
    tags = [
        {"tag": t, "count": int(c)}
        for t, c in sorted(tag_map.items(), key=lambda x: (-x[1], x[0]))
    ]
    return timeline, tags


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    # Post-write round-trip validation.
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"Post-write validation failed for {path.name}: {e}")


def validate(plugin_names, recipe_names):
    failures = []

    def check(label, cond):
        if not cond:
            failures.append(label)
            print(f"FAIL: {label}", file=sys.stderr)

    check("At least 1 plugin processed", len(plugin_names) >= 1)

    summary_path = OUTPUT_BASE / "summary.json"
    check("summary.json exists", summary_path.exists())
    if summary_path.exists():
        try:
            p  = json.loads(summary_path.read_text(encoding="utf-8"))
            check(
                "summary.json has required keys",
                {"schemaVersion", "generatedAt", "overview", "pullRequests", "meta"} <= p.keys(),
            )
            ov = p.get("overview", {})
            check("totalMigrations is int",   isinstance(ov.get("totalMigrations"),   int))
            check("failedMigrations is int",  isinstance(ov.get("failedMigrations"),  int))
            check("successRate is float",     isinstance(ov.get("successRate"),        float))
            check("totalPRs is int",          isinstance(p.get("pullRequests", {}).get("totalPRs"), int))
        except (json.JSONDecodeError, OSError) as e:
            check(f"summary.json readable ({e})", False)

    index_path = OUTPUT_BASE / "plugin-recipes-index.json"
    check("plugin-recipes-index.json exists", index_path.exists())
    if index_path.exists():
        try:
            idx = json.loads(index_path.read_text(encoding="utf-8"))
            check(
                "index plugin count matches",
                len(idx.get("plugins", [])) == len(plugin_names),
            )
        except (json.JSONDecodeError, OSError) as e:
            check(f"index readable ({e})", False)

    if failures:
        sys.exit(1)

    if plugin_names:
        rate = plugin_error_count / len(plugin_names)
        if rate > MAX_ERROR_RATE:
            print(
                f"FAIL: plugin-copy error rate {rate * 100:.1f}% "
                f"> threshold {MAX_ERROR_RATE * 100:.1f}%",
                file=sys.stderr,
            )
            sys.exit(1)

    log("All validations passed.")


def main():
    if not INPUT_BASE.exists():
        print(
            f"ERROR: Input directory not found: {INPUT_BASE}\n"
            "Expected INPUT_DIR to be the metadata-plugin-modernizer workspace root.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Always start with a clean output directory so stale files are never
    # published if the current run produces fewer outputs than a previous one.
    if OUTPUT_BASE.exists():
        shutil.rmtree(OUTPUT_BASE)
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

    try:
        md_bytes = SUMMARY_MD.read_bytes()
        summary  = parse_summary_md(
            md_bytes.decode("utf-8"),
            hashlib.sha256(md_bytes).hexdigest(),
        )
    except (ParseError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    recipe_names = copy_recipes()
    plugin_names = copy_plugins()

    summary["overview"]["totalPlugins"] = len(plugin_names)

    recipe_stats = build_recipe_stats(recipe_names)
    summary["overview"]["pendingMigrations"] = (
        int(sum(r["pending"] for r in recipe_stats)) if recipe_stats else None
    )

    timeline, tags = build_timeline_and_tags(plugin_names)

    write_json(
        OUTPUT_BASE / "summary.json",
        {**summary, "recipes": recipe_stats, "timeline": timeline, "tags": tags},
    )
    log("Wrote summary.json")

    index = {
        "schemaVersion": "1.0",
        "generatedAt":   summary["generatedAt"],
        "plugins":       plugin_names,
        "recipes":       recipe_names,
    }
    write_json(OUTPUT_BASE / "plugin-recipes-index.json", index)
    log(
        f"Wrote plugin-recipes-index.json "
        f"({len(plugin_names)} plugins, {len(recipe_names)} recipes)"
    )
    
    validate(plugin_names, recipe_names)

    print(
        f"\nDone — {len(plugin_names)} plugins, {len(recipe_names)} recipes, "
        f"{error_count} warning(s) ({plugin_error_count} plugin-copy error(s)). "
        f"Output: {OUTPUT_BASE}"
    )


if __name__ == "__main__":
    main()
