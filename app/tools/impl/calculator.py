"""
Calculator tool. Deliberately does NOT use eval() on raw strings -- that's a
classic "looks fine in a demo, is an RCE vector" mistake. Uses Python's ast
module to parse and evaluate only a whitelisted set of arithmetic node types.
"""
import ast
import operator
from pydantic import BaseModel, field_validator

_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Mod: operator.mod,
}


class CalculatorInput(BaseModel):
    expression: str

    @field_validator("expression")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("expression must not be empty")
        return v


def _eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"Disallowed expression element: {ast.dump(node)}")


def run(args: dict) -> float:
    payload = CalculatorInput(**args)
    try:
        tree = ast.parse(payload.expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Could not parse expression: {e}")
    return _eval_node(tree.body)
