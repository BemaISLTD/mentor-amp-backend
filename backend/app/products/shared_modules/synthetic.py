"""Deterministic illustrative data, never actuarial expected results."""


def block_rows(code="RILA", count=30):
    rows = []
    for i in range(count):
        av = 50000 + (i % 20) * 25000
        weights = ((0.5, 0.0), (0.4, 0.4), (0.5, 0.5))[i % 3]
        row = {"policy_id": f"{code}-{i + 1:03d}", "product_type": code,
               "issue_date": "2025-12-31", "issue_age": 45 + i % 30,
               "gender": "M" if i % 2 == 0 else "F", "premium": av, "account_value": av,
               "fixed_av": av * (1 - sum(weights)), "fixed_rate": 0.03,
               "withdrawal_mode": "pro_rata", "interim_method": "prorated_intrinsic",
               "rollover_rule": "auto_renew", "death_benefit_rule": "account_value",
               "gross_withdrawal": 0, "fee": 0, "path_id": 0,
               "minimum_guarantee": 0, "mva_amount": 0}
        for slot, weight in enumerate(weights, 1):
            prefix = f"segment_{slot}"
            row.update({f"{prefix}_base": av * weight, f"{prefix}_initial_value": av * weight,
                        f"{prefix}_strategy": "ptp" if code == "FIA" else "floor" if i % 3 == 2 else "buffer",
                        f"{prefix}_index_code": "SP500" if slot == 1 else "RUSSELL2000",
                        f"{prefix}_term_months": 12 if slot == 1 or code == "FIA" else 36,
                        f"{prefix}_cap": 0.08 if slot == 1 else 0.12,
                        f"{prefix}_downside_rate": 0.10 if slot == 1 else 0.15,
                        f"{prefix}_start_index": 100.0, f"{prefix}_participation_rate": 1.0,
                        f"{prefix}_elapsed": 0, f"{prefix}_spread": 0,
                        f"{prefix}_external_interim": None})
        rows.append(row)
    return rows


def index_paths(months=72):
    rows = []
    for path, annual in enumerate((0.04, 0.12, -0.12, -0.25)):
        for index in ("SP500", "RUSSELL2000"):
            for month in range(1, months + 1):
                # Stress declines for a year, then recovers; distinct index trajectories.
                rate = annual * (1.2 if index == "RUSSELL2000" else 1)
                level = 100 * (1 + rate) ** (month / 12)
                if path == 3 and month > 12:
                    level = 100 * (1 + rate) * 1.08 ** ((month - 12) / 12)
                rows.append({"path_id": path, "month": month, "index_code": index, "level": level})
    return rows
