#!/usr/bin/env python3
"""
Find deeply nested code blocks in Python files.

This script identifies code with 5+ levels of indentation.
"""
import ast
import sys
from pathlib import Path
from typing import List, Tuple


class NestingAnalyzer(ast.NodeVisitor):
    def __init__(self):
        self.max_nesting = 0
        self.current_nesting = 0
        self.nested_locations = []

    def visit(self, node):
        # Track nesting for control flow statements
        if isinstance(node, (ast.For, ast.While, ast.If, ast.With, ast.Try)):
            self.current_nesting += 1
            self.max_nesting = max(self.max_nesting, self.current_nesting)

            if self.current_nesting >= 5:
                self.nested_locations.append({
                    'line': node.lineno,
                    'level': self.current_nesting,
                    'type': type(node).__name__
                })

            self.generic_visit(node)
            self.current_nesting -= 1
        else:
            self.generic_visit(node)


def analyze_file(file_path: Path) -> Tuple[int, List[dict]]:
    """Analyze a Python file for nesting depth."""
    try:
        content = file_path.read_text(encoding='utf-8')
        tree = ast.parse(content)

        analyzer = NestingAnalyzer()
        analyzer.visit(tree)

        return analyzer.max_nesting, analyzer.nested_locations
    except Exception as e:
        print(f"Error analyzing {file_path}: {e}", file=sys.stderr)
        return 0, []


def main():
    src_dir = Path('src')

    if not src_dir.exists():
        print("Error: src/ directory not found")
        return 1

    print("Analyzing Python files for deep nesting (5+ levels)...\n")
    print("=" * 80)

    results = []

    for py_file in src_dir.rglob('*.py'):
        max_nesting, locations = analyze_file(py_file)

        if max_nesting >= 5:
            results.append((py_file, max_nesting, locations))

    # Sort by max nesting depth
    results.sort(key=lambda x: x[1], reverse=True)

    if not results:
        print("No files with 5+ nesting levels found!")
        return 0

    print(f"Found {len(results)} files with deep nesting:\n")

    for file_path, max_nesting, locations in results:
        try:
            rel_path = file_path.relative_to(Path.cwd())
        except ValueError:
            rel_path = file_path
        print(f"\n[FILE] {rel_path}")
        print(f"   Max nesting: {max_nesting} levels")
        print(f"   Deep nesting locations: {len(locations)}")

        for loc in locations[:5]:  # Show first 5
            print(f"      Line {loc['line']}: {loc['level']} levels ({loc['type']})")

        if len(locations) > 5:
            print(f"      ... and {len(locations) - 5} more")

    print("\n" + "=" * 80)
    print(f"\nTotal files to refactor: {len(results)}")
    print("\nPriority order (by max nesting):")
    for i, (file_path, max_nesting, _) in enumerate(results[:10], 1):
        try:
            rel_path = file_path.relative_to(Path.cwd())
        except ValueError:
            rel_path = file_path
        print(f"  {i}. {rel_path} ({max_nesting} levels)")

    return 0


if __name__ == '__main__':
    exit(main())
