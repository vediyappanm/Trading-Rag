from pathlib import Path
import os

file_path = Path(r"c:\Users\ELCOT\OneDrive\Desktop\Rag\src\infra_rag\main.py")
project_root = file_path.resolve().parent.parent.parent
index_html = project_root / "index.html"

print(f"File: {file_path}")
print(f"Resolved: {file_path.resolve()}")
print(f"Project Root: {project_root}")
print(f"Index HTML path: {index_html}")
print(f"Exists: {index_html.exists()}")

cwd = os.getcwd()
print(f"CWD: {cwd}")
print(f"Index in CWD: {(Path(cwd) / 'index.html').exists()}")
