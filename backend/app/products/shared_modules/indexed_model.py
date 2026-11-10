"""Declarative indexed-account bindings for the existing projection engine.

Two segment slots are the illustrative model shape, not an engine limit. All
state is scalar and carried through prior_output, so every transition is traced.
"""

from app.core.formula_engine.formulas import register_function
from .segment import SegmentState, rollforward_segment_term
from .fixed_crediting import fixed_crediting
from .surrender_charge import cash_surrender_value

STATE = {"base_close": "base_balance", "interim_val": "current_interim_value",
         "elapsed_close": "elapsed_months", "start_close": "start_index_level",
         "matured_close": "is_matured", "sweep": "swept_value",
         "credited_rate": "credited_rate"}


def segment_source(slot, kind):
    return {"type": kind, "dimensions": {"segment_id": slot,
            "strategy_type": {"column": f"{slot}_strategy"},
            "index_code": {"column": f"{slot}_index_code"}}}


def state_unit(field):
    return {"elapsed_close": "month", "start_close": "index", "matured_close": "indicator",
            "credited_rate": "rate"}.get(field, "USD")
INPUTS = {"base": ("number", None), "strategy": ("string", None),
          "index_code": ("string", None), "term_months": ("number", None),
          "cap": ("number", None), "downside_rate": ("number", None),
          "start_index": ("number", None), "elapsed": ("number", 0),
          "initial_value": ("number", None), "participation_rate": ("number", 1),
          "spread": ("number", 0), "external_interim": ("number", None)}


def variable(name, kind, *, unit="USD", data_type="number", default=None, source=None,
             required=True, override=False):
    if data_type == "string":
        unit = "code"
    return {"name": name, "display_name": name.replace("_", " ").title(),
            "description": f"Illustrative indexed model: {name}.", "kind": kind,
            "unit": unit, "data_type": data_type, "required": required,
            "default_value": default, "source": source or {"type": kind, "column": name},
            "allow_scenario_override": override}


def opening(projection_month, initial, previous):
    return initial if projection_month == 1 else previous


def segment_transition(prefix, values, *, deduct=True):
    def get(suffix):
        return values[f"{prefix}_{suffix}"]
    first = values["projection_month"] == 1
    state = SegmentState(
        segment_id=prefix, strategy_type=get("strategy"), index_code=get("index_code"),
        term_months=get("term_months"), elapsed_months=get("elapsed") if first else get("elapsed_previous"),
        start_index_level=get("start_index") if first else get("start_previous"),
        base_balance=get("base") if first else get("base_previous"),
        current_interim_value=get("initial_value") if first else get("value_previous"),
        cap=get("cap"), downside_rate=get("downside_rate"),
        is_matured=False if first else bool(get("matured_previous")),
        participation_rate=get("participation_rate"), spread=get("spread"),
    )
    provisional = rollforward_segment_term(
        state, get("index_level"), 0.0, values["withdrawal_mode"],
        interim_method=values["interim_method"], rollover_rule=values["rollover_rule"],
        external_interim_value=get("external_interim"),
    )
    if not deduct:
        return provisional
    deductions = values["gross_withdrawal"] + values["fee"]
    total = values["account_value_before_deductions"]
    if min(values["gross_withdrawal"], values["fee"]) < 0 or deductions > total:
        raise ValueError("Withdrawals and fees exceed available account value")
    gross = provisional.current_interim_value + provisional.swept_value
    amount = deductions * gross / total if total else 0.0
    return rollforward_segment_term(
        state, get("index_level"), amount, values["withdrawal_mode"],
        interim_method=values["interim_method"], rollover_rule=values["rollover_rule"],
        external_interim_value=get("external_interim"))


def segment_function(prefix, field):
    # Only strings in the closure: repeat registrations have stable fingerprints.
    def calculate(**values):
        if field == "gross_value":
            provisional = segment_transition(prefix, values, deduct=False)
            return provisional.current_interim_value + provisional.swept_value
        return float(getattr(segment_transition(prefix, values), field))
    return calculate


def portfolio_open(projection_month, account_value, account_value_previous):
    return opening(projection_month, account_value, account_value_previous)


def fixed_before(projection_month, fixed_av, fixed_previous, fixed_rate):
    value = opening(projection_month, fixed_av, fixed_previous)
    return value + fixed_crediting(value, fixed_rate)


def before_deductions(segment_1_gross_value, segment_2_gross_value, fixed_av_before_deductions):
    return segment_1_gross_value + segment_2_gross_value + fixed_av_before_deductions


def fixed_close(fixed_av_before_deductions, gross_withdrawal, fee,
                account_value_before_deductions, segment_1_sweep, segment_2_sweep):
    value = fixed_av_before_deductions
    deduction = gross_withdrawal + fee
    if min(gross_withdrawal, fee) < 0 or deduction > account_value_before_deductions:
        raise ValueError("Withdrawals and fees exceed available account value")
    deduction = deduction * value / account_value_before_deductions if account_value_before_deductions else 0
    return max(0.0, value - deduction) + segment_1_sweep + segment_2_sweep


def total_close(segment_1_interim_val, segment_2_interim_val, fixed_av_close):
    return segment_1_interim_val + segment_2_interim_val + fixed_av_close


def surrender_value(account_value_close, surrender_charge_rate, mva_amount, minimum_guarantee):
    return max(minimum_guarantee, cash_surrender_value(account_value_close, surrender_charge_rate) + mva_amount)


def death_benefit(account_value_close, minimum_guarantee, death_benefit_rule):
    if death_benefit_rule == "account_value":
        return account_value_close
    if death_benefit_rule == "greater_of_av_and_guarantee":
        return max(account_value_close, minimum_guarantee)
    raise ValueError("Unsupported death benefit rule")


def interest_credited(account_value_close, account_value_open, gross_withdrawal, fee):
    return account_value_close - account_value_open + gross_withdrawal + fee


def net_cash_flow(gross_withdrawal, fee):
    return fee - gross_withdrawal


def total_account_value(account_value_close):
    return account_value_close


CORE = {"account_value_open": portfolio_open, "fixed_av_close": fixed_close,
        "fixed_av_before_deductions": fixed_before, "account_value_before_deductions": before_deductions,
        "total_account_value_close": total_account_value,
        "account_value_close": total_close, "cash_surrender_value": surrender_value,
        "death_benefit": death_benefit, "interest_credited": interest_credited,
        "net_cash_flow": net_cash_flow}


def definition(code):
    import inspect
    prefix = f"{code.lower()}_illustrative"
    variables = [variable("projection_month", "context", unit="month"),
                 variable("policy_year", "context", unit="year")]
    for name in ("account_value", "fixed_av"):
        variables.append(variable(name, "input"))
    for name, default, dtype in (
        ("fixed_rate", 0.0, "number"), ("gross_withdrawal", 0.0, "number"),
        ("fee", 0.0, "number"), ("mva_amount", 0.0, "number"),
        ("minimum_guarantee", 0.0, "number"), ("path_id", 0, "number"),
        ("withdrawal_mode", "pro_rata", "string"),
        ("interim_method", "prorated_intrinsic", "string"),
        ("rollover_rule", "auto_renew", "string"),
        ("death_benefit_rule", "account_value", "string"),
    ):
        variables.append(variable(name, "input", default=default, required=False,
                                  unit="rate" if name == "fixed_rate" else "id" if name == "path_id" else "USD",
                                  data_type=dtype, override=name in {"path_id", "fixed_rate", "gross_withdrawal", "fee"}))
    formulas = []
    functions = dict(CORE)
    for name, func in CORE.items():
        formulas.append({"name": name, "output_variable": name,
                         "function_ref": f"{prefix}.{name}",
                         "dependencies": list(inspect.signature(func).parameters), "unit": "USD"})
        variables.append(variable(name, "formula"))
    for name, output in (("account_value_previous", "account_value_close"), ("fixed_previous", "fixed_av_close")):
        variables.append(variable(name, "prior_output", source={"type": "prior_output", "variable": output,
                                                                 "offset": 1, "initial_value": 0}))
    for slot in ("segment_1", "segment_2"):
        deps = ["projection_month", "account_value_open", "gross_withdrawal", "fee",
                "withdrawal_mode", "interim_method", "rollover_rule"]
        for suffix, (dtype, default) in INPUTS.items():
            name = f"{slot}_{suffix}"
            unit = ("rate" if suffix in {"cap", "downside_rate", "participation_rate", "spread"}
                    else "month" if suffix in {"term_months", "elapsed"}
                    else "index" if suffix == "start_index" else "USD")
            variables.append(variable(name, "input", unit=unit, data_type=dtype, default=default,
                                      required=default is None and suffix != "external_interim"))
            deps.append(name)
        index_name = f"{slot}_index_level"
        variables.append(variable(index_name, "factor", unit="index", source={
            "type": "factor", "table": f"{code}_INDEX_PATHS", "column": "level",
            "key_map": {"path_id": "path_id", "month": "projection_month", "index_code": f"{slot}_index_code"}}))
        deps.append(index_name)
        for suffix, output in (("base", "base_close"), ("value", "interim_val"),
                               ("elapsed", "elapsed_close"), ("start", "start_close"),
                               ("matured", "matured_close")):
            name = f"{slot}_{suffix}_previous"
            variables.append(variable(name, "prior_output", unit=state_unit(output), source={"type": "prior_output",
                "variable": f"{slot}_{output}", "offset": 1, "initial_value": 0}))
            deps.append(name)
        for suffix, field in {"gross_value": "gross_value", **STATE}.items():
            name = f"{slot}_{suffix}"
            variables.append(variable(name, "formula", unit=state_unit(suffix), source=segment_source(slot, "formula")))
            functions[name] = segment_function(slot, field)
            formulas.append({"name": name, "output_variable": name,
                             "dependencies": list(deps) + ([] if suffix == "gross_value" else ["account_value_before_deductions"]),
                             "function_ref": f"{prefix}.{name}", "unit": state_unit(suffix)})
    for name, table, key, column in (
        ("surrender_charge_rate", f"{code}_SURRENDER", "policy_year", "rate"),
        ("option_budget", f"{code}_OPTION_BUDGET", "policy_year", "budget"),
    ):
        variables.append(variable(name, "factor", unit="rate", source={"type": "factor", "table": table,
                                  "column": column, "key_map": {key: key}}))
    outputs = ["account_value_open", "account_value_close", "total_account_value_close", "fixed_av_close", "cash_surrender_value",
               "death_benefit", "gross_withdrawal", "fee", "interest_credited", "net_cash_flow", "option_budget"]
    outputs += [f"{slot}_{field}" for slot in ("segment_1", "segment_2") for field in STATE]
    published = [{"variable_name": name, "display_name": name.replace("_", " ").title(),
                  "unit": next(v["unit"] for v in variables if v["name"] == name), "dimension": "policy_month",
                  "aggregation": "sum" if name in {"gross_withdrawal", "fee", "interest_credited", "net_cash_flow"} else "end_of_period",
                  "description": "Illustrative engineering output; independent actuarial verification pending."}
                 for name in outputs]
    model = {"name": f"{code} illustrative M4", "product_code": code,
             "description": "Synthetic engineering model. NOT actuarially approved.",
             "version": {"version_label": "m4-v1", "block_name": f"Synthetic {code} block",
                         "profile_name": "Illustrative", "basis": "Illustrative", "methodology": "Indexed segment accounting",
                         "notes": "ACT-M4-DEC-01..15 pending owner review.",
                         "configuration": {"timing_convention": "EOM", "actuarial_status": "PENDING_OWNER_APPROVAL"}},
             "formula_group": {"name": f"{code} M4", "description": "Shared indexed primitives",
                               "lineage_state": "local", "version_label": "v1"}}
    return model, variables, formulas, published, functions


def register(code, functions):
    for name, func in functions.items():
        register_function(f"{code.lower()}_illustrative.{name}", func,
                          implementation_version="indexed-illustrative-m4-v2", illustrative=True,
                          expression_text=name, explanation="Shared indexed-account transition; illustrative configuration.")
