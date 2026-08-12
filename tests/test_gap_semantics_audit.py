from scalpforge_strategy.gap_semantics_audit import _classify, _median


def test_gap_classification_distinguishes_inherited_interruptions() -> None:
    assert _classify(False, True, 12) == "short_intraday_interruption_only"
    assert _classify(False, True, 90) == "medium_intraday_interruption"
    assert _classify(False, True, 301) == "long_gap_or_closure"
    assert _classify(True, True, 12) == "five_minute_bar_discontinuity"
    assert _classify(False, False, 0) == "outcome_invalid_without_continuity_evidence"


def test_gap_median() -> None:
    assert _median([]) is None
    assert _median([8, 6, 10]) == 8
    assert _median([6, 8]) == 7
