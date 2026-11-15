"""Deterministic rule-based validators for Dubai itineraries and budgets."""

from __future__ import annotations

from datetime import time

from dubai_planner.models.schemas import (
    AccommodationOption,
    BudgetBreakdown,
    DayPlan,
    FinalItinerary,
    ReviewResult,
    TravelRequest,
    ValidationViolation,
)

_INTEREST_TAG_SYNONYMS: dict[str, set[str]] = {
    "cultural": {"cultural", "heritage", "museum", "art", "architecture", "history", "markets"},
    "cultural_attractions": {"cultural", "heritage", "museum", "art", "architecture", "history", "markets"},
    "culture": {"cultural", "heritage", "museum", "art", "architecture", "history"},
    "local_food": {"local_food", "emirati", "street_food", "heritage_eatery", "seafood", "restaurant"},
    "food": {"local_food", "emirati", "street_food", "seafood", "cafe", "restaurant"},
    "beach": {"beach", "beaches", "swimming"},
    "beaches": {"beach", "beaches", "swimming"},
    "art": {"art", "galleries", "museum", "indie_cafes"},
    "nature": {"nature", "park", "wildlife", "green_space"},
    "landmarks": {"landmarks", "skyline", "observation", "panoramic"},
}


def _minutes_since_midnight(value: time) -> int:
    return value.hour * 60 + value.minute


class ItineraryValidator:
    """Runs deterministic checks on budget, duration, duplicates, schedule, and preferences."""

    def __init__(self, *, math_tolerance_aed: float = 0.5) -> None:
        self.math_tolerance_aed = math_tolerance_aed

    def validate(
        self,
        *,
        request: TravelRequest,
        days: list[DayPlan],
        budget: BudgetBreakdown,
        accommodation: AccommodationOption | None = None,
    ) -> ReviewResult:
        """Validate a draft or final itinerary against all 5 deterministic rule categories."""
        violations: list[ValidationViolation] = []

        self._check_budget(request=request, budget=budget, violations=violations)
        self._check_duration(request=request, days=days, violations=violations)
        self._check_duplicates(days=days, violations=violations)
        self._check_schedule_feasibility(days=days, violations=violations)
        self._check_preferences(
            request=request,
            days=days,
            accommodation=accommodation,
            violations=violations,
        )

        if accommodation is None:
            violations.append(
                ValidationViolation(
                    type="missing_info",
                    severity="error",
                    message="Accommodation option is missing from the itinerary.",
                )
            )

        errors = [v for v in violations if v.severity == "error"]
        passed = len(errors) == 0
        instructions = self._build_revision_instructions(violations, request=request, budget=budget)

        if passed:
            summary = "All deterministic checks passed (budget, duration, duplicates, schedule, preferences)."
        else:
            summary = f"Detected {len(errors)} deterministic violation(s): " + "; ".join(
                v.type for v in errors
            )

        return ReviewResult(
            passed=passed,
            violations=violations,
            revision_instructions=instructions,
            summary=summary,
        )

    def validate_itinerary(self, itinerary: FinalItinerary) -> ReviewResult:
        """Convenience wrapper accepting a complete FinalItinerary model."""
        return self.validate(
            request=itinerary.request,
            days=itinerary.days,
            budget=itinerary.budget,
            accommodation=itinerary.accommodation,
        )

    def _check_budget(
        self,
        *,
        request: TravelRequest,
        budget: BudgetBreakdown,
        violations: list[ValidationViolation],
    ) -> None:
        category_sum = round(
            budget.accommodation_aed
            + budget.food_aed
            + budget.transportation_aed
            + budget.activities_aed,
            2,
        )
        if abs(category_sum - budget.total_aed) > self.math_tolerance_aed:
            violations.append(
                ValidationViolation(
                    type="budget_exceeded",
                    severity="error",
                    message=(
                        f"Budget category sum (AED {category_sum:.2f}) does not match "
                        f"total_aed (AED {budget.total_aed:.2f})."
                    ),
                )
            )

        if budget.total_aed > request.budget_aed + self.math_tolerance_aed:
            excess = round(budget.total_aed - request.budget_aed, 2)
            violations.append(
                ValidationViolation(
                    type="budget_exceeded",
                    severity="error",
                    message=(
                        f"Projected total AED {budget.total_aed:.2f} exceeds budget cap "
                        f"AED {request.budget_aed:.2f} by AED {excess:.2f}."
                    ),
                )
            )

    def _check_duration(
        self,
        *,
        request: TravelRequest,
        days: list[DayPlan],
        violations: list[ValidationViolation],
    ) -> None:
        if len(days) != request.duration_days:
            violations.append(
                ValidationViolation(
                    type="missing_info",
                    severity="error",
                    message=(
                        f"Itinerary has {len(days)} day(s), expected {request.duration_days} day(s)."
                    ),
                )
            )

        expected_numbers = list(range(1, request.duration_days + 1))
        actual_numbers = [day.day_number for day in days]
        if actual_numbers != expected_numbers:
            violations.append(
                ValidationViolation(
                    type="missing_info",
                    severity="error",
                    message=(
                        f"Day numbers {actual_numbers} do not match sequential range {expected_numbers}."
                    ),
                )
            )

        for day in days:
            if not day.activities:
                violations.append(
                    ValidationViolation(
                        type="missing_info",
                        severity="error",
                        day_number=day.day_number,
                        message=f"Day {day.day_number} has no scheduled activities.",
                    )
                )

    def _check_duplicates(
        self,
        *,
        days: list[DayPlan],
        violations: list[ValidationViolation],
    ) -> None:
        seen_ids: dict[str, int] = {}
        for day in days:
            for activity in day.activities:
                place_id = activity.place.id
                if place_id in seen_ids:
                    first_day = seen_ids[place_id]
                    violations.append(
                        ValidationViolation(
                            type="duplicate_activity",
                            severity="error",
                            day_number=day.day_number,
                            item_id=place_id,
                            message=(
                                f"Place '{activity.place.name}' (id='{place_id}') is duplicated "
                                f"on Day {first_day} and Day {day.day_number}."
                            ),
                        )
                    )
                else:
                    seen_ids[place_id] = day.day_number

    def _check_schedule_feasibility(
        self,
        *,
        days: list[DayPlan],
        violations: list[ValidationViolation],
    ) -> None:
        for day in days:
            activities = day.activities
            for idx in range(len(activities) - 1):
                curr = activities[idx]
                nxt = activities[idx + 1]

                curr_start = _minutes_since_midnight(curr.start_time)
                curr_end = _minutes_since_midnight(curr.end_time)
                nxt_start = _minutes_since_midnight(nxt.start_time)

                if nxt_start < curr_start:
                    violations.append(
                        ValidationViolation(
                            type="schedule_conflict",
                            severity="error",
                            day_number=day.day_number,
                            item_id=nxt.place.id,
                            message=(
                                f"Day {day.day_number}: '{nxt.place.name}' starts at "
                                f"{nxt.start_time.strftime('%H:%M')} before '{curr.place.name}' "
                                f"starts ({curr.start_time.strftime('%H:%M')})."
                            ),
                        )
                    )
                    continue

                if nxt_start < curr_end:
                    violations.append(
                        ValidationViolation(
                            type="schedule_conflict",
                            severity="error",
                            day_number=day.day_number,
                            item_id=nxt.place.id,
                            message=(
                                f"Day {day.day_number}: '{nxt.place.name}' ({nxt.start_time.strftime('%H:%M')}) "
                                f"overlaps with '{curr.place.name}' ending at {curr.end_time.strftime('%H:%M')}."
                            ),
                        )
                    )
                    continue

                distinct_location = curr.place.id != nxt.place.id
                if distinct_location:
                    gap_mins = nxt_start - curr_end
                    if curr.transit_to_next is None:
                        violations.append(
                            ValidationViolation(
                                type="schedule_conflict",
                                severity="error",
                                day_number=day.day_number,
                                item_id=curr.place.id,
                                message=(
                                    f"Day {day.day_number}: Missing transit segment between "
                                    f"'{curr.place.name}' and '{nxt.place.name}'."
                                ),
                            )
                        )
                    elif curr.transit_to_next.duration_mins <= 0:
                        violations.append(
                            ValidationViolation(
                                type="schedule_conflict",
                                severity="error",
                                day_number=day.day_number,
                                item_id=curr.place.id,
                                message=(
                                    f"Day {day.day_number}: Transit buffer between "
                                    f"'{curr.place.name}' and '{nxt.place.name}' must be > 0 mins."
                                ),
                            )
                        )
                    elif gap_mins < curr.transit_to_next.duration_mins:
                        violations.append(
                            ValidationViolation(
                                type="schedule_conflict",
                                severity="error",
                                day_number=day.day_number,
                                item_id=nxt.place.id,
                                message=(
                                    f"Day {day.day_number}: Only {gap_mins} min gap between "
                                    f"'{curr.place.name}' and '{nxt.place.name}', but transit requires "
                                    f"{curr.transit_to_next.duration_mins} mins."
                                ),
                            )
                        )

    def _check_preferences(
        self,
        *,
        request: TravelRequest,
        days: list[DayPlan],
        accommodation: AccommodationOption | None,
        violations: list[ValidationViolation],
    ) -> None:
        all_activities = [act for day in days for act in day.activities]

        # 1. Interest coverage check
        present_tokens: set[str] = set()
        for act in all_activities:
            present_tokens.add(act.place.category.lower())
            for tag in act.place.tags:
                present_tokens.add(tag.lower())

        for interest in request.interests:
            norm = interest.strip().lower().replace(" ", "_")
            synonyms = _INTEREST_TAG_SYNONYMS.get(norm, {norm})
            if not (present_tokens & synonyms):
                violations.append(
                    ValidationViolation(
                        type="preference_mismatch",
                        severity="error",
                        message=(
                            f"Requested interest '{interest}' is not represented in any scheduled activity."
                        ),
                    )
                )

        # 2. Crowd preference check
        if request.crowd_preference == "low":
            if accommodation is not None and accommodation.crowd_level == "high":
                violations.append(
                    ValidationViolation(
                        type="preference_mismatch",
                        severity="error",
                        item_id=accommodation.id,
                        message=(
                            f"Accommodation '{accommodation.name}' in '{accommodation.area}' has "
                            "high crowd_level, conflicting with crowd_preference='low'."
                        ),
                    )
                )

            for day in days:
                for act in day.activities:
                    if act.place.crowd_level == "high":
                        violations.append(
                            ValidationViolation(
                                type="preference_mismatch",
                                severity="error",
                                day_number=day.day_number,
                                item_id=act.place.id,
                                message=(
                                    f"Day {day.day_number}: '{act.place.name}' has high crowd_level, "
                                    "conflicting with crowd_preference='low'."
                                ),
                            )
                        )

    def _build_revision_instructions(
        self,
        violations: list[ValidationViolation],
        *,
        request: TravelRequest,
        budget: BudgetBreakdown,
    ) -> list[str]:
        if not violations:
            return []

        instructions: list[str] = []
        seen: set[str] = set()

        def _add(text: str) -> None:
            if text not in seen:
                seen.add(text)
                instructions.append(text)

        for v in violations:
            if v.type == "budget_exceeded":
                if budget.alternatives:
                    alt_text = "; ".join(
                        f"{a.description} (save ~AED {a.estimated_savings_aed:.0f})"
                        for a in budget.alternatives[:2]
                    )
                    _add(f"Reduce total cost below AED {request.budget_aed:.0f}: {alt_text}")
                else:
                    _add(
                        f"Reduce accommodation, dining, or paid attraction costs so total_aed <= AED {request.budget_aed:.0f}."
                    )
            elif v.type == "duplicate_activity":
                _add(f"Replace duplicate activity '{v.item_id}' on Day {v.day_number} with a unique POI.")
            elif v.type == "schedule_conflict":
                _add(
                    f"Fix Day {v.day_number} time slots and ensure positive transit buffers between stops."
                )
            elif v.type == "preference_mismatch":
                _add(v.message)
            elif v.type == "missing_info":
                _add(v.message)

        return instructions
