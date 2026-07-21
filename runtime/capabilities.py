"""Risoluzione deterministica delle capability effettive per invocazione.

Il manifest dichiara il tetto di autorita'. Una clausola ``when`` puo'
restringere quel tetto al valore finale di un argomento tipizzato; non puo'
ampliarlo e una clausola non valida non diventa mai effettiva.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityCondition:
    arg: str
    values: tuple[str, ...]


class CapabilityConditionError(ValueError):
    """Clausola ``when`` non valida, con codice stabile per l'admission."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_condition(capability: dict, args_schema: dict) -> CapabilityCondition | None:
    """Valida e restituisce la clausola chiusa ``when`` di una capability.

    ``None`` significa che la capability e' incondizionata. Gli errori sono
    distinti dall'assenza e vengono trattati fail-closed dal resolver.
    """
    if "when" not in capability:
        return None

    when = capability.get("when")
    if not isinstance(when, dict) or set(when) != {"arg", "values"}:
        raise CapabilityConditionError(
            "capability_when_shape",
            "when must have exactly 'arg' and 'values'",
        )

    arg = when.get("arg")
    values = when.get("values")
    if not isinstance(arg, str) or not arg.strip():
        raise CapabilityConditionError(
            "capability_when_arg", "when.arg must be a non-empty string",
        )
    if (not isinstance(values, list) or not values
            or any(not isinstance(value, str) or not value.strip()
                   for value in values)):
        raise CapabilityConditionError(
            "capability_when_values",
            "when.values must be a non-empty list of non-empty strings",
        )

    properties = (
        args_schema.get("properties") if isinstance(args_schema, dict) else None
    )
    if not isinstance(properties, dict) or arg not in properties:
        raise CapabilityConditionError(
            "capability_when_arg_unknown",
            f"when.arg {arg!r} is not declared in args.properties",
        )
    arg_schema = properties.get(arg)
    if not isinstance(arg_schema, dict):
        raise CapabilityConditionError(
            "capability_when_arg_schema",
            f"args.properties.{arg} must be an object",
        )

    if "enum" in arg_schema:
        enum = arg_schema.get("enum")
        if (not isinstance(enum, list)
                or any(not isinstance(value, str) for value in enum)):
            raise CapabilityConditionError(
                "capability_when_enum",
                f"args.properties.{arg}.enum must be a list of strings",
            )
        outside = sorted(set(values) - set(enum))
        if outside:
            raise CapabilityConditionError(
                "capability_when_value_enum",
                f"when.values are outside args.properties.{arg}.enum: {outside}",
            )

    return CapabilityCondition(arg=arg, values=tuple(values))


def effective_capabilities(capabilities, args_schema, args) -> list[dict]:
    """Restituisce le capability effettive per gli argomenti finali.

    Il valore esplicito in ``args`` precede il default dello schema. Una
    condizione malformata, non dichiarata o non corrispondente concede zero.
    """
    out: list[dict] = []
    for capability in capabilities or []:
        if not isinstance(capability, dict):
            continue
        try:
            condition = parse_condition(capability, args_schema)
        except CapabilityConditionError:
            continue
        if condition is None:
            out.append(capability)
            continue

        properties = args_schema.get("properties")
        arg_schema = properties[condition.arg]
        if isinstance(args, dict) and condition.arg in args:
            selected = args[condition.arg]
        else:
            selected = arg_schema.get("default")
        if selected in condition.values:
            out.append(capability)
    return out
