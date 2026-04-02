#!/usr/bin/env python3
"""
build_mcpb.py - Gera o arquivo seipro.mcpb (Desktop Extension para Claude).

Uso:
    python3 build_mcpb.py

Produz: dist/seipro.mcpb
"""

import json
import os
import shutil
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DIST_DIR = PROJECT_ROOT / "dist"
OUTPUT_NAME = "seipro.mcpb"

# Icone do SEI Pro (ajuste o caminho se necessario)
ICON_SOURCES = [
    PROJECT_ROOT / "icon.png",
    Path.home() / "Documents/Git/Lab2Code/SEI Pro/sei-pro/dist/icons/sei_pro.png",
]

# Arquivos e pastas a incluir no .mcpb
INCLUDE = [
    "manifest.json",
    "pyproject.toml",
    "bootstrap.py",
    "README.md",
    "icon.png",
    "src/mcp_seipro/",
]

# Padroes a ignorar
IGNORE_PATTERNS = {
    "__pycache__",
    ".pyc",
    ".egg-info",
    ".DS_Store",
    ".git",
    ".env",
}


def should_ignore(path: Path) -> bool:
    for part in path.parts:
        for pattern in IGNORE_PATTERNS:
            if pattern in part:
                return True
    return False


def ensure_icon():
    dest = PROJECT_ROOT / "icon.png"
    if dest.exists():
        return dest
    for src in ICON_SOURCES:
        if src.exists():
            shutil.copy2(src, dest)
            print(f"  [*] Icone copiado de {src}")
            return dest
    print("  [!] Icone nao encontrado. O .mcpb sera criado sem icone.")
    return None


def build():
    print()
    print("=" * 50)
    print("  Build: seipro.mcpb")
    print("=" * 50)
    print()

    # Validar manifest.json
    manifest_path = PROJECT_ROOT / "manifest.json"
    if not manifest_path.exists():
        print("  [ERRO] manifest.json nao encontrado.")
        return
    manifest = json.loads(manifest_path.read_text())
    print(f"  [*] {manifest['display_name']} v{manifest['version']}")

    # Garantir icone
    icon = ensure_icon()

    # Criar dist/
    DIST_DIR.mkdir(exist_ok=True)
    output = DIST_DIR / OUTPUT_NAME

    # Montar o ZIP
    count = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for item_name in INCLUDE:
            item = PROJECT_ROOT / item_name
            if not item.exists():
                if item_name == "icon.png" and not icon:
                    continue
                print(f"  [!] Pulando {item_name} (nao existe)")
                continue

            if item.is_file():
                zf.write(item, item_name)
                count += 1
            elif item.is_dir():
                for root, dirs, files in os.walk(item):
                    root_path = Path(root)
                    for f in files:
                        file_path = root_path / f
                        if should_ignore(file_path):
                            continue
                        arcname = str(file_path.relative_to(PROJECT_ROOT))
                        zf.write(file_path, arcname)
                        count += 1

    size_kb = output.stat().st_size / 1024
    print(f"  [*] {count} arquivos empacotados")
    print(f"  [*] Gerado: {output} ({size_kb:.0f} KB)")
    print()
    print("  Para instalar no Claude Desktop:")
    print(f"    Abra {output} com duplo-clique")
    print()


if __name__ == "__main__":
    build()
