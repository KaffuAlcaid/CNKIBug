import ast
from pathlib import Path


def test_core_packages_do_not_import_frontend_layers():
    package_root = Path(__file__).resolve().parents[1] / "cnkibug"
    violations = []
    for package_name in ("core", "browser", "cnki", "fileio", "workflow"):
        for path in (package_root / package_name).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module is None:
                    continue
                imports_frontend = (
                    node.module in {"cnkibug.app", "cnkibug.gui"}
                    or node.module.startswith(("cnkibug.app.", "cnkibug.gui."))
                    or (node.level == 2 and node.module in {"app", "gui"})
                    or (
                        node.level == 2
                        and node.module.startswith(("app.", "gui."))
                    )
                )
                if imports_frontend:
                    violations.append(f"{path.relative_to(package_root)}:{node.lineno}")

    assert violations == []
