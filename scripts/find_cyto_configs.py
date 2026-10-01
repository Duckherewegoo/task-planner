import ast
from pathlib import Path

old = Path("../src/task_planner/abandon/config_old.py")
tree = ast.parse(old.read_text(encoding="utf-8"))

want = {"CYTO_STYLESHEET", "GRAPH_CONFIGS",
        "FIT_JS_TEMPLATE", "ZOOM_BTN_JS_TEMPLATE", "ZOOM_SLIDER_JS_TEMPLATE"}

for node in tree.body:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id in want:
                print("─" * 72)
                print(f"# >>>>> {t.id}  <<<<<")
                print(ast.unparse(node))
                print()
