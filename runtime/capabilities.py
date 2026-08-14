"""Risoluzione deterministica delle capability effettive per invocazione.

Il manifest dichiara il tetto di autorita'. Una clausola ``when`` puo'
restringere quel tetto al valore finale di un argomento tipizzato; non puo'
ampliarlo e una clausola non valida non diventa mai effettiva. I predicati
ammessi sono volutamente pochi e chiusi: valore scalare, valore non vuoto e
presenza di un campo tipizzato negli elementi di un array.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityCondition:
    arg: str
    predicate: str
    values: tuple[str, ...] = ()
    item_key: str | None = None


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
    if not isinstance(when, dict):
        raise CapabilityConditionError(
            "capability_when_shape",
            "when must be an object",
        )

    shape = set(when)
    supported_shapes = (
        {"arg", "values"},
        {"arg", "nonempty"},
        {"arg", "any_item_has"},
    )
    if shape not in supported_shapes:
        raise CapabilityConditionError(
            "capability_when_shape",
            "when must be exactly one of: arg+values, arg+nonempty, "
            "arg+any_item_has",
        )

    arg = when.get("arg")
    if not isinstance(arg, str) or not arg.strip():
        raise CapabilityConditionError(
            "capability_when_arg", "when.arg must be a non-empty string",
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

    if shape == {"arg", "values"}:
        values = when.get("values")
        if (not isinstance(values, list) or not values
                or any(not isinstance(value, str) or not value.strip()
                       for value in values)):
            raise CapabilityConditionError(
                "capability_when_values",
                "when.values must be a non-empty list of non-empty strings",
            )
    else:
        values = []

    if shape == {"arg", "values"} and "enum" in arg_schema:
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

    if shape == {"arg", "nonempty"}:
        if when.get("nonempty") is not True:
            raise CapabilityConditionError(
                "capability_when_nonempty",
                "when.nonempty must be true",
            )
        declared_type = arg_schema.get("type")
        declared_types = (
            set(declared_type) if isinstance(declared_type, list)
            else {declared_type}
        )
        if not declared_types & {"string", "array", "object"}:
            raise CapabilityConditionError(
                "capability_when_nonempty_type",
                f"args.properties.{arg} must declare a container type",
            )
        return CapabilityCondition(arg=arg, predicate="nonempty")

    if shape == {"arg", "any_item_has"}:
        item_key = when.get("any_item_has")
        if not isinstance(item_key, str) or not item_key.strip():
            raise CapabilityConditionError(
                "capability_when_item_key",
                "when.any_item_has must be a non-empty string",
            )
        if arg_schema.get("type") != "array":
            raise CapabilityConditionError(
                "capability_when_item_array",
                f"args.properties.{arg} must have type array",
            )
        item_schema = arg_schema.get("items")
        item_properties = (
            item_schema.get("properties") if isinstance(item_schema, dict)
            else None
        )
        if (not isinstance(item_schema, dict)
                or item_schema.get("type") != "object"
                or not isinstance(item_properties, dict)
                or item_key not in item_properties):
            raise CapabilityConditionError(
                "capability_when_item_unknown",
                f"{item_key!r} is not declared in args.properties.{arg}.items",
            )
        return CapabilityCondition(
            arg=arg, predicate="any_item_has", item_key=item_key,
        )

    return CapabilityCondition(
        arg=arg, predicate="values", values=tuple(values),
    )


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
        matches = False
        if condition.predicate == "values":
            matches = selected in condition.values
        elif condition.predicate == "nonempty":
            matches = bool(selected)
        elif condition.predicate == "any_item_has":
            matches = (
                isinstance(selected, list)
                and any(
                    isinstance(item, dict)
                    and item.get(condition.item_key) is not None
                    for item in selected
                )
            )
        if matches:
            out.append(capability)
    return out
