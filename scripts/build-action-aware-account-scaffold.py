"""Create controlled STATIC copies once; no runtime monkeypatch/exec/import hack.

The generated diff is limited to new class/version/imports and the four day
action seams. Old source bytes, constructors and historical proofs stay intact.
"""
import ast
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    trail = (ROOT / "service/research_volatility_trail.py").read_text("utf-8")
    tree = ast.parse(trail)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "VolatilityTrailAccount")
    klass = "\n".join(trail.splitlines()[node.lineno - 1:node.end_lineno]) + "\n"
    klass = klass.replace("class VolatilityTrailAccount(FixedSignalAccount):", "class ActionAwareVolatilityAccount(ActionAccountMixin, FixedSignalAccount):", 1)
    klass = klass.replace("VolatilityTrailAccount checkpoint restore is not supported", "Action-aware account checkpoint restore is not supported")
    klass = klass.replace("Declared corporateActions are unsupported by volatilityTrail", "Use only the separately validated researchDistributions contract")
    klass = klass.replace('"referencePolicy": "Ratio is unchanged by inherited simultaneous riskEntryPrice/closePeak conversion; raw historical rights uncertainty remains."',
        '"referencePolicy": "Ratio stays invariant through explicit event conversion; grants and cash are separate research ledger movements."')
    header = '''"""Isolated action-aware volatility account, synthetic engineering version only.

Volatility rule methods are controlled static copies of the frozen prior rule.
The ActionAccountMixin provides explicit event/cash/quantity/valuation seams.
"""
from collections import Counter
from copy import deepcopy
from .portfolio_engine import _digest
from .research_fixed_signal_account import FixedSignalAccount
from .research_action_account_core import ActionAccountMixin
from .research_volatility_trail import (WINDOW_SLOTS, TR_COUNT, MULTIPLIER,
    DECISION_STATUSES, WINDOW_STATUSES, _positive, prior_mean_tr_pct20)

VERSION = "research-action-aware-volatility-synthetic-v1"

'''
    engine = (ROOT / "service/portfolio_engine.py").read_text("utf-8")
    tree = ast.parse(engine)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PortfolioEngine")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_step_day")
    day = "\n".join(line[4:] for line in engine.splitlines()[method.lineno - 1:method.end_lineno]) + "\n"
    substitutions = {"def _step_day(self, day):": "def action_step_day(self, day):",
        "apply_actions(self, day)": "self._apply_before_open(day)",
        "asset_adjustment(s)": "self._action_asset_adjustment()",
        "capture_rights(self, day)": "self._capture_record_close(day)",
        "self._reference(symbol, index)": "self._continuity_reference(symbol, index)"}
    for old, new in substitutions.items():
        if day.count(old) != 1:
            raise ValueError("Unexpected source seam: " + old)
        day = day.replace(old, new, 1)
    day_header = '''"""Static daily-flow copy; generated substitutions independently checked in tests."""
from copy import deepcopy
from .portfolio_engine import nominal_limit
from .execution_evidence import audit_opening_fills
from .portfolio_rotation import decide_rotation

'''
    for name, content in (("research_action_account.py", header + klass),
                          ("research_action_account_day.py", day_header + day)):
        path = ROOT / "service" / name
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        print(name, hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
