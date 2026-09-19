import pytest

from phikernel.carriage import CitationDecision
from phikernel.clockskin import (
    EXPLICIT_SIGNAL,
    WALL_RATE,
    ClockBudgetExceededError,
    ClockSkinContract,
    ClockSkinError,
    advance_explicit,
    advance_wall_time,
    close_clock_lease,
    cross_skin_handoff,
    issue_clock_lease,
    spend_ticks_with_warrant,
)
from phikernel.warrant import (
    ResourceBudget,
    Warrant,
    WarrantBudgetExceededError,
    WarrantDeniedError,
)


def _warrant(*, issued_at=100.0, lifetime=100.0):
    return Warrant.issue(
        issuer="human:mikey",
        bearer="organ:alpha",
        scopes=("execute:simulation/*",),
        budgets=(
            ResourceBudget("compute_ms", 1000.0),
            ResourceBudget("tokens", 5000.0),
        ),
        issued_at=issued_at,
        lifetime_seconds=lifetime,
    )


def test_different_wall_rate_skins_experience_different_local_time() -> None:
    sensor = ClockSkinContract(
        skin_id="skin:sensor",
        mode=WALL_RATE,
        tick_rate_hz=100.0,
    )
    deliberation = ClockSkinContract(
        skin_id="skin:deliberation",
        mode=WALL_RATE,
        tick_rate_hz=2.0,
    )
    sensor_lease = issue_clock_lease(
        sensor,
        subject_id="sensor:camera",
        granted_ticks=1000.0,
        opened_at=100.0,
    )
    deliberation_lease = issue_clock_lease(
        deliberation,
        subject_id="model:reasoner",
        granted_ticks=1000.0,
        opened_at=100.0,
    )

    sensor_after, sensor_receipt = advance_wall_time(
        sensor,
        sensor_lease,
        wall_now=101.0,
    )
    deliberation_after, deliberation_receipt = advance_wall_time(
        deliberation,
        deliberation_lease,
        wall_now=101.0,
    )

    assert sensor_receipt.ticks_advanced == pytest.approx(100.0)
    assert deliberation_receipt.ticks_advanced == pytest.approx(2.0)
    assert sensor_after.spent_ticks == pytest.approx(100.0)
    assert deliberation_after.spent_ticks == pytest.approx(2.0)


def test_human_skin_does_not_advance_from_waiting() -> None:
    human = ClockSkinContract(
        skin_id="skin:human",
        mode=EXPLICIT_SIGNAL,
    )
    lease = issue_clock_lease(
        human,
        subject_id="human:mikey",
        granted_ticks=10.0,
        opened_at=100.0,
    )

    with pytest.raises(ClockSkinError):
        advance_wall_time(human, lease, wall_now=10000.0)

    assert lease.spent_ticks == 0.0
    assert lease.remaining_ticks == 10.0


def test_human_skin_advances_only_with_explicit_signal() -> None:
    human = ClockSkinContract(
        skin_id="skin:human",
        mode=EXPLICIT_SIGNAL,
    )
    lease = issue_clock_lease(
        human,
        subject_id="human:mikey",
        granted_ticks=10.0,
        opened_at=100.0,
    )

    updated, receipt = advance_explicit(
        human,
        lease,
        ticks=1.0,
        signal_ref="operator:approve:001",
        at=500.0,
    )

    assert updated.spent_ticks == pytest.approx(1.0)
    assert receipt.explicit_signal_ref == "operator:approve:001"
    assert receipt.authority_change == "NONE"
    assert receipt.warrant_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_explicit_skin_rejects_empty_signal() -> None:
    human = ClockSkinContract(
        skin_id="skin:human",
        mode=EXPLICIT_SIGNAL,
    )
    lease = issue_clock_lease(
        human,
        subject_id="human:mikey",
        granted_ticks=10.0,
        opened_at=100.0,
    )

    with pytest.raises(ClockSkinError):
        advance_explicit(
            human,
            lease,
            ticks=1.0,
            signal_ref="",
            at=101.0,
        )


def test_clock_budget_overdraft_fails_without_partial_advance() -> None:
    organ = ClockSkinContract(
        skin_id="skin:organ",
        mode=WALL_RATE,
        tick_rate_hz=10.0,
    )
    lease = issue_clock_lease(
        organ,
        subject_id="organ:alpha",
        granted_ticks=5.0,
        opened_at=100.0,
    )

    with pytest.raises(ClockBudgetExceededError):
        advance_wall_time(organ, lease, wall_now=101.0)

    assert lease.spent_ticks == 0.0
    assert lease.remaining_ticks == 5.0


def test_clock_ticks_can_debit_warrant_resource_atomically() -> None:
    organ = ClockSkinContract(
        skin_id="skin:organ",
        mode=WALL_RATE,
        tick_rate_hz=10.0,
    )
    lease = issue_clock_lease(
        organ,
        subject_id="organ:alpha",
        granted_ticks=100.0,
        opened_at=100.0,
    )
    warrant = _warrant()

    result = spend_ticks_with_warrant(
        organ,
        lease,
        warrant,
        actor_id="organ:alpha",
        ticks=10.0,
        resource_kind="compute_ms",
        cost_per_tick=5.0,
        at=110.0,
    )

    assert lease.spent_ticks == 0.0
    assert warrant.remaining("compute_ms") == pytest.approx(1000.0)
    assert result.lease_after.spent_ticks == pytest.approx(10.0)
    assert result.warrant_after.remaining("compute_ms") == pytest.approx(950.0)
    assert result.warrant_after.scopes == warrant.scopes
    assert result.warrant_after.expires_at == warrant.expires_at
    assert result.clock_receipt.authority_change == "NONE"
    assert result.warrant_receipt.authority_change == "NONE"


def test_warrant_overdraft_blocks_clock_advance_atomically() -> None:
    organ = ClockSkinContract(
        skin_id="skin:organ",
        mode=WALL_RATE,
        tick_rate_hz=10.0,
    )
    lease = issue_clock_lease(
        organ,
        subject_id="organ:alpha",
        granted_ticks=100.0,
        opened_at=100.0,
    )
    warrant = _warrant()

    with pytest.raises(WarrantBudgetExceededError):
        spend_ticks_with_warrant(
            organ,
            lease,
            warrant,
            actor_id="organ:alpha",
            ticks=30.0,
            resource_kind="compute_ms",
            cost_per_tick=40.0,
            at=110.0,
        )

    assert lease.spent_ticks == 0.0
    assert warrant.remaining("compute_ms") == pytest.approx(1000.0)


def test_expired_warrant_blocks_local_time_spend_even_with_clock_budget_left() -> None:
    organ = ClockSkinContract(
        skin_id="skin:organ",
        mode=WALL_RATE,
        tick_rate_hz=10.0,
    )
    lease = issue_clock_lease(
        organ,
        subject_id="organ:alpha",
        granted_ticks=100.0,
        opened_at=100.0,
    )
    warrant = _warrant(issued_at=100.0, lifetime=5.0)

    with pytest.raises(WarrantDeniedError):
        spend_ticks_with_warrant(
            organ,
            lease,
            warrant,
            actor_id="organ:alpha",
            ticks=1.0,
            resource_kind="compute_ms",
            cost_per_tick=1.0,
            at=106.0,
        )

    assert lease.spent_ticks == 0.0


def test_non_bearer_cannot_spend_ticks_through_someone_elses_warrant() -> None:
    organ = ClockSkinContract(
        skin_id="skin:organ",
        mode=WALL_RATE,
        tick_rate_hz=10.0,
    )
    lease = issue_clock_lease(
        organ,
        subject_id="organ:alpha",
        granted_ticks=100.0,
        opened_at=100.0,
    )
    warrant = _warrant()

    with pytest.raises(WarrantDeniedError):
        spend_ticks_with_warrant(
            organ,
            lease,
            warrant,
            actor_id="organ:beta",
            ticks=1.0,
            resource_kind="compute_ms",
            cost_per_tick=1.0,
            at=101.0,
        )


def test_explicit_human_warrant_spend_requires_signal() -> None:
    human = ClockSkinContract(
        skin_id="skin:human",
        mode=EXPLICIT_SIGNAL,
    )
    lease = issue_clock_lease(
        human,
        subject_id="organ:alpha",
        granted_ticks=10.0,
        opened_at=100.0,
    )
    warrant = _warrant()

    with pytest.raises(ClockSkinError):
        spend_ticks_with_warrant(
            human,
            lease,
            warrant,
            actor_id="organ:alpha",
            ticks=1.0,
            resource_kind="compute_ms",
            cost_per_tick=1.0,
            signal_ref=None,
            at=101.0,
        )


def test_cross_skin_effect_requires_citeable_carriage() -> None:
    simulation = ClockSkinContract(
        skin_id="skin:simulation",
        mode=WALL_RATE,
        tick_rate_hz=1000.0,
    )
    human = ClockSkinContract(
        skin_id="skin:human",
        mode=EXPLICIT_SIGNAL,
    )
    citation = CitationDecision(
        carriage_id="carriage:abc",
        allowed=True,
        reason="carriage currently has citation rights",
        evaluated_at=120.0,
    )

    receipt = cross_skin_handoff(
        source_contract=simulation,
        target_contract=human,
        carriage_id="carriage:abc",
        citation=citation,
        handed_off_at=121.0,
    )

    assert receipt.allowed is True
    assert receipt.authority_change == "NONE"
    assert receipt.citation_law_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_open_contradiction_style_citation_denial_blocks_cross_skin_effect() -> None:
    simulation = ClockSkinContract(
        skin_id="skin:simulation",
        mode=WALL_RATE,
        tick_rate_hz=1000.0,
    )
    human = ClockSkinContract(
        skin_id="skin:human",
        mode=EXPLICIT_SIGNAL,
    )
    citation = CitationDecision(
        carriage_id="carriage:abc",
        allowed=False,
        reason="citation blocked by open contradiction",
        blocking_contradiction_ids=("contradiction:1",),
        evaluated_at=120.0,
    )

    receipt = cross_skin_handoff(
        source_contract=simulation,
        target_contract=human,
        carriage_id="carriage:abc",
        citation=citation,
        handed_off_at=121.0,
    )

    assert receipt.allowed is False
    assert "open contradiction" in receipt.reason


def test_cross_skin_handoff_rejects_mismatched_carriage_receipt() -> None:
    source = ClockSkinContract(
        skin_id="skin:a",
        mode=WALL_RATE,
        tick_rate_hz=1.0,
    )
    target = ClockSkinContract(
        skin_id="skin:b",
        mode=WALL_RATE,
        tick_rate_hz=1.0,
    )
    citation = CitationDecision(
        carriage_id="carriage:other",
        allowed=True,
        reason="fixture",
        evaluated_at=120.0,
    )

    receipt = cross_skin_handoff(
        source_contract=source,
        target_contract=target,
        carriage_id="carriage:expected",
        citation=citation,
        handed_off_at=121.0,
    )

    assert receipt.allowed is False
    assert "different carriage" in receipt.reason


def test_cross_skin_handoff_requires_distinct_skins() -> None:
    skin = ClockSkinContract(
        skin_id="skin:same",
        mode=WALL_RATE,
        tick_rate_hz=1.0,
    )
    citation = CitationDecision(
        carriage_id="carriage:abc",
        allowed=True,
        reason="fixture",
        evaluated_at=120.0,
    )

    with pytest.raises(ClockSkinError):
        cross_skin_handoff(
            source_contract=skin,
            target_contract=skin,
            carriage_id="carriage:abc",
            citation=citation,
            handed_off_at=121.0,
        )


def test_closed_clock_lease_cannot_advance() -> None:
    organ = ClockSkinContract(
        skin_id="skin:organ",
        mode=WALL_RATE,
        tick_rate_hz=1.0,
    )
    lease = issue_clock_lease(
        organ,
        subject_id="organ:alpha",
        granted_ticks=10.0,
        opened_at=100.0,
    )
    closed = close_clock_lease(lease, reason="protocol organ disbanded")

    with pytest.raises(ClockSkinError):
        advance_wall_time(organ, closed, wall_now=101.0)

    assert closed.closed is True
    assert closed.close_reason == "protocol organ disbanded"


def test_fast_simulation_cannot_make_human_time_advance() -> None:
    simulation = ClockSkinContract(
        skin_id="skin:simulation",
        mode=WALL_RATE,
        tick_rate_hz=1000.0,
    )
    human = ClockSkinContract(
        skin_id="skin:human",
        mode=EXPLICIT_SIGNAL,
    )
    simulation_lease = issue_clock_lease(
        simulation,
        subject_id="simulation:test",
        granted_ticks=5000.0,
        opened_at=100.0,
    )
    human_lease = issue_clock_lease(
        human,
        subject_id="human:mikey",
        granted_ticks=10.0,
        opened_at=100.0,
    )

    simulation_after, _ = advance_wall_time(
        simulation,
        simulation_lease,
        wall_now=101.0,
    )

    assert simulation_after.spent_ticks == pytest.approx(1000.0)
    assert human_lease.spent_ticks == 0.0
    assert human_lease.remaining_ticks == pytest.approx(10.0)
