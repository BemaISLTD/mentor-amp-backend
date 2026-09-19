"""Mortality rate lookup from a mortality table."""


def mortality_rate_lookup(
    age: int,
    gender: str,
    duration: int,
    mortality_table: list[dict],
) -> float:
    """Look up the mortality rate for a given age, gender, and duration.

    Args:
        age: Attained age in years
        gender: 'M' or 'F'
        duration: Policy duration in years
        mortality_table: List of dicts with keys: age, gender, mortality_rate, duration

    Returns:
        Mortality rate as a float (e.g., 0.015 for 1.5%)
    """
    for row in mortality_table:
        row_age = row.get("age")
        row_gender = row.get("gender")
        row_duration = row.get("duration")

        # Match on age, gender, and optionally duration
        age_match = row_age is not None and int(row_age) == int(age)
        gender_match = row_gender is None or str(row_gender).upper() == str(gender).upper()
        duration_match = row_duration is None or int(row_duration) == int(duration)

        if age_match and gender_match and duration_match:
            raw = row.get("mortality_rate", 0)
            return float(raw)

    # Fallback: return first row's rate if no exact match
    if mortality_table:
        raw = mortality_table[0].get("mortality_rate", 0)
        return float(raw)

    return 0.0
