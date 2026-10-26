"""Contextualize pure touch predicates without changing mutable script state."""

import re

TOKENS = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|#([A-Za-z_][\w.]*)")
INVERSE = re.compile(r"\(\(#(touch_(?:begin_)?[xy])-\(([^()]*)\)\)/\(([^()]*)\)\)")
TOUCH = {"touch_x", "touch_y", "touch_begin_x", "touch_begin_y"}


def pure_touch_definitions(nodes):
    """Only derived values can be inlined; command/persistent variables cannot."""
    nodes = list(nodes)
    assigned = {a.get("name") for tag, a in nodes if tag == "VariableCommand"}
    raw = {
        a["name"]: a["expression"]
        for tag, a in nodes
        if tag == "Var"
        and "name" in a
        and "expression" in a
        and a["name"] not in assigned
        and a.get("persist", "false").lower() != "true"
    }
    result = {}
    visiting = set()

    def expand(name):
        if name in result:
            return result[name]
        if name not in raw or name in visiting:
            return None
        visiting.add(name)
        found = False

        def replace(m):
            nonlocal found
            if m[1] in TOUCH:
                found = True
                return m[0]
            child = expand(m[1])
            if child is not None:
                found = True
                return "(" + child + ")"
            return m[0]

        value = TOKENS.sub(replace, raw[name])
        visiting.remove(name)
        result[name] = value if found else None
        return result[name]

    for name in raw:
        expand(name)
    return {k: v for k, v in result.items() if v is not None}


def expand_touch(expression, definitions):
    return TOKENS.sub(
        lambda m: "(" + definitions[m[1]] + ")" if m[1] in definitions else m[0], expression
    )


def has_touch(expression):
    return any(m[1] in TOUCH for m in TOKENS.finditer(expression))


def restore_inverse(expression):
    def restore(m):
        for term in m.group(2, 3):
            if not re.fullmatch(r"#mr_[a-z0-9_]+|-?\d+(?:\.\d+)?(?:e[+-]?\d+)?", term):
                raise ValueError("Unexpected gesture coordinate adapter")
        return "#" + m[1]

    return INVERSE.sub(restore, expression)


def is_capture_trigger(node):
    expected = {"sx", "sy", "tx", "ty"}
    names = [c.get("name", "") for c in node]
    prefix = names[0].rsplit("_", 1)[0] if names else ""
    attributes = (
        {"action": "down"},
        {"action": "down", "condition": f"eq(#{prefix}_sx,#{prefix}_sx)"},
    )
    return (
        node.tag == "Trigger"
        and node.attrib in attributes
        and len(names) == 4
        and {n.rsplit("_", 1)[-1] for n in names} == expected
        and len({n.rsplit("_", 1)[0] for n in names}) == 1
        and all(
            c.tag == "VariableCommand"
            and set(c.attrib) == {"name", "expression"}
            and not list(c)
            and re.fullmatch(r"mr_[0-9a-f]+_(?:sx|sy|tx|ty)", c.get("name", ""))
            and re.fullmatch(
                r"#mr_[a-z0-9_]+|-?\d+(?:\.\d+)?(?:e[+-]?\d+)?", c.get("expression", "")
            )
            for c in node
        )
    )
