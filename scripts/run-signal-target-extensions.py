"""Run the three registered signal extensions on development data only.

The baseline runner is privately loaded and configured in this process. Its
source, lifecycle, source freezing, Top5/control policy and event execution are
unchanged. No real run starts by importing this module.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.portfolio_config import normalize_config
from service.signal_target_extensions import CANDIDATES, ExtensionScorer

_spec = importlib.util.spec_from_file_location("_registered_signal_extension_runtime", Path(__file__).with_name("run-signal-target-study.py"))
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)
source_tools = runtime.source_tools
REGISTRATION = ROOT / "docs/signal-target-candidates-v1.json"
REGISTRATION_SHA256 = "018fbce1f93e96aa1025791009593bb3a4f6a28609274db1da09544e1e6030bf"
STRATEGIES = tuple(row[0] for row in CANDIDATES)
GROUPS = tuple(sid for candidate in STRATEGIES for sid in (candidate, "control_" + candidate))
_baseline_protocol, _baseline_evaluate = runtime.make_protocol, runtime.evaluate_signals


def registration():
    if source_tools.file_hash(REGISTRATION) != REGISTRATION_SHA256:
        raise ValueError("The preregistered candidate document changed; register a new study version")
    return source_tools.read_json(REGISTRATION)


def settings(args):
    bases = [base for _, base, _ in CANDIDATES]
    return normalize_config({
        "startDate": args.start, "endDate": args.end, "initialCapital": 1000000,
        "strategies": [{"id": base, "kind": base, "params": params, "pool": "base"} for _, base, params in CANDIDATES],
        "combinations": [], "allocation": {"mode": "equal", "strategyIds": bases},
        "weighting": "equal", "topN": 5, "maxPositions": 5, "maxStockWeight": .1,
        "rebalanceMode": "risk", "maxHoldingDays": 0, "stopLossPct": 0, "takeProfitPct": 0, "trailingStopPct": 0,
        "universe": {"strictHistory": not args.diagnostic_unverified_history, "excludeSt": True,
                     "minListingDays": 120, "minAmount20": 50000000},
    })


def make_protocol(args):
    document = registration()
    protocol = _baseline_protocol(args)
    paths = (Path(__file__), ROOT / "service/signal_target_extensions.py", REGISTRATION)
    protocol["implementation"].update({path.relative_to(ROOT).as_posix(): source_tools.file_hash(path) for path in paths})
    protocol.update(schema="signal-target-extensions-development-v1", studyKind="preregistered-fixed-signal-extensions",
                    registration=document, registrationSha256=REGISTRATION_SHA256,
                    candidateLog="all-extension-qualified-candidates-before-top-five;original-base-score-preserved",
                    filterPolicy="complete-daily-base-trigger-set-then-registered-filter-then-top-five;no-parameter-scan",
                    historyPolicy="missing-required-exchange-session-is-unknown-and-rejected;benchmark-must-be-CSI300",
                    riskBoundaryJointStatistics={"implemented": False, "reason": "first-extension-pass-reuses-existing-MAE-only;no-stop-policy-change"})
    return protocol


def evaluate_signals(dataset, config, signals):
    result = _baseline_evaluate(dataset, config, signals)
    for group in result["groups"]:
        if not group["strategyId"].startswith("control_"):
            group["role"] = "registered-extension"
    result["riskBoundaryJointStatistics"] = {"implemented": False, "reason": "existing-MAE-only;no-stop-policy-change"}
    return result


# These bindings belong solely to this private module object. No source file or
# shared canonical import is patched; the frozen baseline CLI remains intact.
runtime.STRATEGIES, runtime.GROUPS = STRATEGIES, GROUPS
runtime.DailyScorer = ExtensionScorer
runtime.settings, runtime.make_protocol = settings, make_protocol
runtime.evaluate_signals = evaluate_signals
runtime.__doc__ = __doc__
execute, main = runtime.execute, runtime.main
collect_signals = runtime.collect_signals


if __name__ == "__main__":
    raise SystemExit(main())
