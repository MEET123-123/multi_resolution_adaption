"""Bounded color expressions for offline evidence; unresolved inputs stay explicit."""
import re
from multires_app.engine.scene_engine import Evaluator,ExpressionError


class ColorEvaluator(Evaluator):
    def _node(self,node):
        import ast
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in {'rgb','argb'} and not node.keywords:
            n=3 if node.func.id=='rgb' else 4
            if len(node.args)!=n:raise ExpressionError('invalid color channel count')
            channels=[max(0,min(255,int(float(self._node(a))))) for a in node.args]
            if n==3:channels.insert(0,255)
            return sum(v<<(8*(3-i)) for i,v in enumerate(channels))
        return super()._node(node)


def color_value(raw,runtime):
    if re.fullmatch(r'#[0-9a-fA-F]{3,4}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}',raw):return raw
    before=dict(runtime.issues)
    try:
        value=ColorEvaluator(runtime.lookup).evaluate(raw)
        if any(before.get(k)!=v for k,v in runtime.issues.items()):return raw
        if isinstance(value,str):return value
        return '#'+format(int(value)&0xffffffff,'08x')
    except (ExpressionError,ValueError,TypeError,OverflowError):return raw
