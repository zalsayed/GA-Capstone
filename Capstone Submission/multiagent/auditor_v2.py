import sys, os, importlib.util, argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name):
    for folder in (_HERE, _PARENT):
        path = os.path.join(folder, f"{name}.py")
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError(f"{name}.py not found in {_HERE} or {_PARENT}")


_load("scraper")
_load("ai")
_load("rules")
_load("output")
_load("screenshot")

import scraper as S
from pipeline import run_pipeline

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║        Bahrain.bh QA Auditor  v3.0 — Service-Parallel        ║
║   Each service: Scrape → Audit → Screenshot → Report         ║
╚══════════════════════════════════════════════════════════════╝"""


def _parse():
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=False)
    src.add_argument("--html", metavar="FILE", help="Entity page HTML")
    src.add_argument("--psid", nargs="+", help="One or more psIDs")
    src.add_argument("--esid", nargs="+", help="One or more esIDs")
    src.add_argument("--psid-file", metavar="CSV", help="CSV with services list")

    p.add_argument("--key", default="", metavar="KEY", help="Gemini API key")
    p.add_argument("--groq-key", default="", metavar="KEY", help="Groq API key")
    p.add_argument("--openrouter-key", default="", metavar="KEY", help="OpenRouter key")
    p.add_argument("--screenshots", action="store_true", help="Take screenshots")
    p.add_argument("--screenshots-dir", default="screenshots", help="Screenshot folder")
    p.add_argument(
        "--drive-key", default="", metavar="FILE", help="Drive client secret"
    )
    p.add_argument("--drive-folder", default="", metavar="ID", help="Drive folder ID")
    p.add_argument("--entity", default="", help="Entity name")
    p.add_argument(
        "--workers", type=int, default=3, help="Parallel workers (default: 3)"
    )
    p.add_argument(
        "--reviewer",
        action="store_true",
        help="Enable feedback-loop reviewer (second AI pass scoring issue quality)",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint — no need to re-specify --html or --psid",
    )
    p.add_argument(
        "--clear-checkpoint",
        action="store_true",
        help="Delete any saved checkpoint and start fresh",
    )
    p.add_argument(
        "--mode",
        choices=["rules", "ai", "full"],
        default="full",
        help=(
            "Audit mode: "
            "'rules' = deterministic checks only, no AI calls (instant, use when rate-limited); "
            "'ai' = AI only, no rule checks; "
            "'full' = rules + AI with smart deduplication (default)"
        ),
    )
    return p.parse_args()


def _setup_drive(drive_key: str):
    if not drive_key:
        return None
    try:
        import drive as D

        svc = D.init(drive_key)
        print("  ✓ Google Drive connected")
        return svc
    except Exception as e:
        print(f"  ✗ Drive auth failed: {e}")
        return None


def _build_jobs(args) -> list:
    jobs = []
    if args.html:
        _reports_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "reports"
        )
        os.makedirs(_reports_dir, exist_ok=True)
        raw = S.extract_services_from_html(
            args.html, os.path.join(_reports_dir, "services.csv")
        )
        for s in raw:
            jobs.append(
                dict(
                    psid=s["psid"],
                    name=s["name"],
                    url=s["url"],
                    is_eservice=s.get("is_eservice", False),
                )
            )
        ps = sum(1 for j in jobs if not j["is_eservice"])
        es = sum(1 for j in jobs if j["is_eservice"])
        print(f"  ✓ Extracted {len(jobs)} services ({ps} psID, {es} esID)")

    elif args.psid:
        for pid in args.psid:
            jobs.append(
                dict(
                    psid=pid,
                    name="",
                    url=S.BASE_URL.format(lang="en", psid=pid),
                    is_eservice=False,
                )
            )
    elif args.esid:
        for eid in args.esid:
            jobs.append(
                dict(
                    psid=eid,
                    name="",
                    url=S.ESERVICE_BASE_URL.format(lang="en", esid=eid),
                    is_eservice=True,
                )
            )
    elif args.psid_file:
        raw = S.load_services_csv(args.psid_file)
        for s in raw:
            jobs.append(
                dict(
                    psid=s["psid"],
                    name=s["name"],
                    url=s["url"],
                    is_eservice=s.get("is_eservice", False),
                )
            )
    return jobs


def main():
    print(BANNER)
    args = _parse()

    providers = (
        " → ".join(
            n
            for n, k in [
                ("Gemini", args.key),
                ("Groq", args.groq_key),
                ("OpenRouter", args.openrouter_key),
            ]
            if k
        )
        or "none"
    )
    print(f"  Providers    : {providers}")
    print(f"  Screenshots  : {'enabled' if args.screenshots else 'disabled'}")
    print(f"  Reviewer     : {'enabled' if args.reviewer else 'disabled'}")
    print(f"  Workers      : {args.workers}")
    mode = getattr(args, "mode", "full")
    print(
        f"  Audit mode   : {mode.upper()}{' (no AI calls)' if mode == 'rules' else ''}"
    )
    if getattr(args, "resume", False):
        print(f"  Mode         : RESUME from checkpoint")
    print(f"  Drive folder : {args.drive_folder or 'not configured'}\n")

    drive_svc = _setup_drive(args.drive_key)

    # Handle --clear-checkpoint first so it can be combined with a fresh run
    if getattr(args, "clear_checkpoint", False):
        from pipeline import clear_checkpoint as _clear_pl
        from supervisor import clear_checkpoint as _clear_sv

        _clear_pl()
        _clear_sv()
        print("  [checkpoint] cleared — starting fresh")

    # Handle --resume
    if getattr(args, "resume", False):
        from pipeline import _load_checkpoint, CHECKPOINT_FILE

        ck = _load_checkpoint()
        if not ck:
            print("  [resume] No checkpoint found.")
            print(
                "  [resume] Run the same command without --resume to start a new run."
            )
            sys.exit(1)
        done = {psid for psid, stage in ck.items() if str(stage).startswith("done")}
        failed = {psid for psid, stage in ck.items() if str(stage).startswith("failed")}
        print(
            f"  [resume] Checkpoint found — {len(done)} completed, {len(failed)} failed, retrying failed now"
        )
        # If no input source specified, resume only the failed/incomplete psids
        # from the checkpoint itself (no need to re-specify --html or --psid-file)
        if not any([args.html, args.psid, args.esid, args.psid_file]):
            pending_psids = [
                psid for psid, stage in ck.items() if not str(stage).startswith("done")
            ]
            if not pending_psids:
                print(
                    "  [resume] All services completed successfully — nothing to retry."
                )
                sys.exit(0)
            # Build job dicts from checkpoint psids, reconstructing URLs
            jobs = []
            for p in pending_psids:
                # Detect is_eservice from checkpoint value (stored as "stage|eservice")
                ck_val = str(ck.get(p, ""))
                is_es = ck_val.endswith("|eservice")
                if is_es:
                    url = S.ESERVICE_BASE_URL.format(lang="en", esid=p)
                else:
                    url = S.BASE_URL.format(lang="en", psid=p)
                jobs.append({"psid": p, "name": "", "url": url, "is_eservice": is_es})
        else:
            jobs = [j for j in _build_jobs(args) if j["psid"] not in done]
        print(f"  [resume] {len(jobs)} services remaining\n")
    else:
        if not any([args.html, args.psid, args.esid, args.psid_file]):
            print("  Error: one of --html, --psid, --esid, --psid-file is required.")
            sys.exit(1)
        jobs = _build_jobs(args)

    if not jobs:
        print("  No services found. Exiting.")
        sys.exit(0)

    print(f"  Total services: {len(jobs)}\n")

    run_pipeline(
        {
            # Services to process
            "pending_scrape": jobs,
            # Audit mode
            "audit_mode": getattr(args, "mode", "full"),
            # API keys
            "gemini_key": args.key,
            "groq_key": args.groq_key,
            "openrouter_key": args.openrouter_key,
            # Screenshots
            "take_screenshots": args.screenshots,
            "screenshots_dir": args.screenshots_dir,
            # Drive
            "drive_service": drive_svc,
            "drive_folder": args.drive_folder,
            # Meta
            "entity_name": args.entity,
            "max_workers": args.workers,
            "run_reviewer": getattr(args, "reviewer", False),
        }
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted — checkpoint saved, use same command to resume")
    except Exception as e:
        import traceback

        print(f"\nCRASH: {e}")
        traceback.print_exc()
