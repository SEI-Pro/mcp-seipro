#!/usr/bin/env python3
"""setup_claude.py - Configura o MCP do SEI no Claude Desktop.

Uso:
    python3 setup_claude.py

Este script:
  1. Pergunta suas credenciais do SEI (URL opcional, usuario, senha)
  2. Cria um ambiente virtual e instala o todos
  3. Configura o Claude Desktop para usar o MCP do SEI

Nenhuma dependencia externa necessaria - usa apenas a stdlib do Python.
"""

import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
MIN_PYTHON = (3, 11)
MCP_SERVER_NAME = "todos"
VENV_HOME = Path.home() / ".todos"
VENV_DIR = VENV_HOME / ".venv"

# ---------------------------------------------------------------------------
# Helpers de UI
# ---------------------------------------------------------------------------


def banner() -> None:
    """Exibe o banner de boas-vindas do instalador."""
    sys.stdout.write("\n")
    sys.stdout.write("=" * 60 + "\n")
    sys.stdout.write("  todos  -  Instalador para Claude Desktop\n")
    sys.stdout.write("=" * 60 + "\n")
    sys.stdout.write("\n")
    sys.stdout.write("  Este script configura o servidor MCP do SEI no\n")
    sys.stdout.write("  aplicativo Claude Desktop (Claude Chat / Cowork).\n")
    sys.stdout.write("\n")
    sys.stdout.write("  O que sera feito:\n")
    sys.stdout.write("    1. Coletar suas credenciais do SEI\n")
    sys.stdout.write("    2. Criar ambiente virtual e instalar o todos\n")
    sys.stdout.write("    3. Configurar o Claude Desktop automaticamente\n")
    sys.stdout.write("\n")


def info(msg: str) -> None:
    """Exibe uma mensagem informativa."""
    sys.stdout.write(f"  [*] {msg}\n")


def warn(msg: str) -> None:
    """Exibe uma mensagem de aviso."""
    sys.stdout.write(f"  [!] {msg}\n")


def error(msg: str) -> None:
    """Exibe uma mensagem de erro."""
    sys.stdout.write(f"  [ERRO] {msg}\n")


def confirm(msg: str, *, default_yes: bool = True) -> bool:
    """Solicita confirmacao do usuario e retorna True para sim."""
    suffix = "[S/n]" if default_yes else "[s/N]"
    resp = input(f"  {msg} {suffix} ").strip().lower()
    if not resp:
        return default_yes
    return resp in ("s", "sim", "y", "yes")


# ---------------------------------------------------------------------------
# Fase 0: Deteccao de ambiente
# ---------------------------------------------------------------------------


def check_python() -> None:
    """Verifica se a versao do Python atende ao requisito minimo."""
    if sys.version_info < MIN_PYTHON:
        error(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ e necessario.")
        error(f"Versao atual: {sys.version}")
        sys.exit(1)
    info(f"Python {sys.version_info.major}.{sys.version_info.minor} detectado")


def get_config_path() -> Path:
    """Retorna o caminho do arquivo de configuracao do Claude Desktop."""
    system = platform.system()
    if system == "Darwin":
        p = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            error("Variavel APPDATA nao encontrada.")
            sys.exit(1)
        p = Path(appdata) / "Claude" / "claude_desktop_config.json"
    else:  # Linux
        p = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    info(f"Config do Claude Desktop: {p}")
    return p


def detect_uv() -> str | None:
    """Detecta se o utilitario uv esta disponivel no PATH."""
    uv = shutil.which("uv")
    if uv:
        info(f"uv encontrado: {uv} (instalacao sera mais rapida)")
    return uv


def detect_repo_root() -> Path | None:
    """Detecta o diretorio raiz do repositorio todos, se presente."""
    script_dir = Path(__file__).resolve().parent
    pyproject = script_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            if 'name = "todos"' in text:
                info(f"Repositorio todos detectado: {script_dir}")
                return script_dir
        except OSError:
            pass
    return None


# ---------------------------------------------------------------------------
# Fase 1: Prompts interativos
# ---------------------------------------------------------------------------


def prompt_url() -> str:
    """Solicita a URL da API REST do SEI (opcional)."""
    sys.stdout.write("\n")
    sys.stdout.write("  [1/5] URL da API do SEI (opcional)\n")
    sys.stdout.write(
        "        Exemplo: https://sei.orgao.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2\n"
    )
    sys.stdout.write("        Deixe em branco se sua instancia nao tiver mod-wssei instalado.\n")
    sys.stdout.write("\n")
    while True:
        url = input("        URL [Enter para pular]: ").strip()
        if not url:
            return ""
        parsed = urlparse(url)
        if not parsed.scheme:
            warn("URL deve comecar com https:// ou http://")
            continue
        if parsed.scheme == "http":
            warn("Voce esta usando http:// (sem criptografia).")
            if not confirm("Continuar mesmo assim?", default_yes=False):
                continue
        if "/api/v2" not in parsed.path and "/api/v1" not in parsed.path:
            warn("URL nao parece conter /api/v2. Verifique se esta correta.")
            if not confirm("Usar essa URL mesmo assim?", default_yes=False):
                continue
        return url.rstrip("/")


def prompt_web_url(default: str = "") -> str:
    """Solicita a URL base do SEI para o scraper web."""
    sys.stdout.write("\n")
    sys.stdout.write("  [1b/5] URL base do SEI (usada pelo scraper web)\n")
    sys.stdout.write("         Exemplo: https://sei.orgao.gov.br\n")
    if default:
        sys.stdout.write(f"         Derivada da URL da API: {default}\n")
    else:
        sys.stdout.write("         Obrigatoria para modo web-only (sem mod-wssei).\n")
    sys.stdout.write("\n")
    hint = f"[Enter para usar {default}]" if default else "URL base do SEI"
    while True:
        url = input(f"         {hint}: ").strip()
        if not url:
            if default:
                return default
            warn("URL base obrigatoria para modo web-only (sem mod-wssei).")
            continue
        parsed = urlparse(url)
        if not parsed.scheme:
            warn("URL deve comecar com https:// ou http://")
            continue
        if parsed.scheme == "http":
            warn("Voce esta usando http:// (sem criptografia).")
            if not confirm("Continuar mesmo assim?", default_yes=False):
                continue
        return url.rstrip("/")


def prompt_usuario() -> str:
    """Solicita o nome de usuario do SEI."""
    sys.stdout.write("\n")
    sys.stdout.write("  [2/5] Usuario do SEI\n")
    sys.stdout.write("\n")
    while True:
        user = input("        Usuario: ").strip()
        if user:
            return user
        warn("Usuario nao pode ser vazio.")


def prompt_senha() -> str:
    """Solicita a senha do SEI de forma segura."""
    sys.stdout.write("\n")
    sys.stdout.write("  [3/5] Senha do SEI\n")
    sys.stdout.write("        (a senha nao sera exibida enquanto voce digita)\n")
    sys.stdout.write("\n")
    while True:
        pwd = getpass.getpass("        Senha: ")
        if pwd:
            return pwd
        warn("Senha nao pode ser vazia.")


def prompt_orgao() -> str:
    """Solicita o codigo do orgao no SEI."""
    sys.stdout.write("\n")
    sys.stdout.write("  [4/5] Codigo do orgao no SEI\n")
    sys.stdout.write("        Use 0 para o orgao principal (padrao)\n")
    sys.stdout.write("\n")
    orgao = input("        Orgao [0]: ").strip()
    return orgao or "0"


def prompt_ssl() -> str:
    """Solicita preferencia de verificacao de certificado SSL."""
    sys.stdout.write("\n")
    sys.stdout.write("  [5/5] Verificar certificado SSL?\n")
    sys.stdout.write("        Desabilite apenas se o servidor usa certificado autoassinado.\n")
    sys.stdout.write("\n")
    if confirm("Verificar SSL?", default_yes=True):
        return "true"
    return "false"


# ---------------------------------------------------------------------------
# Fase 2: Instalacao
# ---------------------------------------------------------------------------


def create_venv(uv_path: str | None) -> None:
    """Cria o ambiente virtual para o todos."""
    if VENV_DIR.exists():
        info(f"Ambiente virtual ja existe: {VENV_DIR}")
        if confirm("Recriar o ambiente virtual?", default_yes=False):
            shutil.rmtree(VENV_DIR)
        else:
            return

    info(f"Criando ambiente virtual em {VENV_DIR} ...")
    VENV_HOME.mkdir(parents=True, exist_ok=True)

    if uv_path:
        # S603: uv_path comes from shutil.which("uv") — an absolute path resolved by the OS,
        # not user input. Remaining args are string literals and a Path constant (VENV_DIR).
        subprocess.run(
            [uv_path, "venv", str(VENV_DIR), "--python", f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"],
            check=True,
        )
    else:
        # S603: sys.executable is the absolute path of the running interpreter — not user input.
        # VENV_DIR is a Path constant derived from Path.home() — no external influence.
        subprocess.run(
            [sys.executable, "-m", "venv", str(VENV_DIR)],
            check=True,
        )
    info("Ambiente virtual criado.")


def get_pip(uv_path: str | None) -> list[str]:
    """Retorna o comando pip adequado para o ambiente virtual."""
    if uv_path:
        return [uv_path, "pip", "install", "--python", str(venv_python())]
    return [str(venv_python()), "-m", "pip", "install"]


def venv_python() -> Path:
    """Retorna o caminho do executavel Python no ambiente virtual."""
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def todos_command() -> Path:
    """Retorna o caminho do comando todos no ambiente virtual."""
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "todos.exe"
    return VENV_DIR / "bin" / "todos"


def install_package(repo_root: Path | None, uv_path: str | None) -> None:
    """Instala o pacote todos no ambiente virtual."""
    pip_cmd = get_pip(uv_path)

    if repo_root:
        info(f"Instalando todos do repositorio local ({repo_root}) ...")
        # S603: pip_cmd uses shutil.which("uv") or str(venv_python()) — both are absolute paths.
        # repo_root is a Path resolved from __file__, not from user-supplied input.
        subprocess.run(
            [*pip_cmd, "-e", str(repo_root)],
            check=True,
        )
    else:
        info("Instalando todos do GitHub ...")
        # S603: pip_cmd uses shutil.which("uv") or str(venv_python()) — both are absolute paths.
        # The git URL is a hard-coded string literal, not user input.
        subprocess.run(
            [*pip_cmd, "git+https://github.com/franklinbaldo/todos.git"],
            check=True,
        )

    cmd = todos_command()
    if not cmd.exists():
        error(f"Comando todos nao encontrado em {cmd}")
        error("A instalacao pode ter falhado. Verifique os erros acima.")
        sys.exit(1)

    info(f"todos instalado: {cmd}")


# ---------------------------------------------------------------------------
# Fase 3: Resumo
# ---------------------------------------------------------------------------


def print_summary(config_path: Path, command: str, env: dict, *, usar_keyring: bool) -> None:
    """Exibe o resumo da configuracao antes de salvar."""
    masked_env = {**env}
    if "SEI_SENHA" in masked_env:
        masked_env["SEI_SENHA"] = "********"

    sys.stdout.write("\n")
    sys.stdout.write("  " + "=" * 56 + "\n")
    sys.stdout.write("              Resumo da configuracao\n")
    sys.stdout.write("  " + "=" * 56 + "\n")
    sys.stdout.write("\n")
    sys.stdout.write(f"    Arquivo:      {config_path}\n")
    sys.stdout.write(f"    Servidor:     {MCP_SERVER_NAME}\n")
    sys.stdout.write(f"    Comando:      {command}\n")
    sys.stdout.write("\n")
    for k, v in masked_env.items():
        sys.stdout.write(f"    {k}: {v}\n")
    if usar_keyring:
        sys.stdout.write("    SEI_SENHA: [Salva com segurança no Keyring do sistema]\n")
    sys.stdout.write("\n")
    sys.stdout.write("  " + "-" * 56 + "\n")
    if usar_keyring:
        info("A senha será armazenada de forma criptografada")
        info("no cofre de credenciais seguro do seu sistema operacional.")
    else:
        warn("A senha sera armazenada em texto plano no arquivo")
        warn("de configuracao. Isso e o padrao do Claude Desktop")
        warn("para variaveis de ambiente de servidores MCP.")
    sys.stdout.write("  " + "-" * 56 + "\n")
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Fase 4: Escrever configuracao
# ---------------------------------------------------------------------------


def read_config(config_path: Path) -> dict:
    """Le e retorna a configuracao atual do Claude Desktop."""
    if not config_path.exists():
        return {}
    try:
        text = config_path.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        warn(f"Arquivo de configuracao corrompido: {config_path}")
        backup_config(config_path)
        warn("Backup criado. Iniciando com configuracao limpa.")
        return {}


def backup_config(config_path: Path) -> None:
    """Cria um backup do arquivo de configuracao com timestamp."""
    if not config_path.exists():
        return
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = config_path.with_suffix(f".json.bak.{ts}")
    shutil.copy2(config_path, bak)
    info(f"Backup: {bak}")


def write_config(config_path: Path, config: dict) -> None:
    """Serializa e grava a configuracao no arquivo do Claude Desktop."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    info(f"Configuracao salva em {config_path}")


def merge_sei_server(config: dict, command: str, env: dict) -> dict:
    """Insere ou atualiza a entrada do servidor SEI na configuracao MCP."""
    if "mcpServers" not in config:
        config["mcpServers"] = {}

    if MCP_SERVER_NAME in config["mcpServers"]:
        warn(f'Servidor "{MCP_SERVER_NAME}" ja existe na configuracao.')
        if not confirm("Sobrescrever?", default_yes=True):
            info("Mantendo configuracao existente.")
            sys.exit(0)

    config["mcpServers"][MCP_SERVER_NAME] = {
        "command": command,
        "env": env,
    }
    return config


# ---------------------------------------------------------------------------
# Fase 5: Sucesso
# ---------------------------------------------------------------------------


def print_success(config_path: Path) -> None:
    """Exibe a mensagem de sucesso apos a instalacao."""
    sys.stdout.write("\n")
    sys.stdout.write("  " + "=" * 56 + "\n")
    sys.stdout.write("              Instalacao concluida!\n")
    sys.stdout.write("  " + "=" * 56 + "\n")
    sys.stdout.write("\n")
    sys.stdout.write("  Reinicie o Claude Desktop para ativar o todos.\n")
    sys.stdout.write("\n")
    sys.stdout.write("  Para testar, pergunte ao Claude:\n")
    sys.stdout.write('    "Liste as unidades do SEI"\n')
    sys.stdout.write("\n")
    sys.stdout.write("  Para reconfigurar:\n")
    sys.stdout.write("    python3 setup_claude.py\n")
    sys.stdout.write("\n")
    sys.stdout.write("  Para remover:\n")
    sys.stdout.write(f'    Apague a entrada "todos" de {config_path}\n')
    sys.stdout.write(f"    E delete a pasta {VENV_HOME}\n")
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Fases auxiliares de main()
# ---------------------------------------------------------------------------


def _collect_credentials() -> tuple[str, str, str, str, str]:
    """Coleta as credenciais do SEI via prompts interativos."""
    sei_url = prompt_url()
    derived_web = sei_url.split("/sei/", 1)[0] if sei_url and "/sei/" in sei_url else ""
    sei_web_url = prompt_web_url(default=derived_web)
    sei_usuario = prompt_usuario()
    sei_senha = prompt_senha()
    sei_orgao = prompt_orgao()
    sei_ssl = prompt_ssl()
    return sei_url, sei_web_url, sei_usuario, sei_senha, sei_orgao, sei_ssl  # type: ignore[return-value]


def _build_env(
    creds: tuple[str, str, str, str, str, str],
    *,
    usar_keyring: bool,
) -> dict:
    """Constroi o dicionario de variaveis de ambiente para o servidor MCP."""
    sei_url, sei_web_url, sei_usuario, sei_senha, sei_orgao, sei_ssl = creds
    env: dict = {
        "SEI_USUARIO": sei_usuario,
        "SEI_ORGAO": sei_orgao,
        "SEI_VERIFY_SSL": sei_ssl,
    }
    if sei_url:
        env["SEI_URL"] = sei_url
    if sei_web_url:
        env["SEI_WEB_URL"] = sei_web_url
    if not usar_keyring:
        env["SEI_SENHA"] = sei_senha
    return env


def _save_keyring_password(
    sei_url: str,
    sei_web_url: str,
    sei_usuario: str,
    sei_senha: str,
    config: dict,
) -> None:
    """Salva a senha no cofre de credenciais do sistema via keyring."""
    info("Salvando senha com segurança no chaveiro do sistema...")
    try:
        if sei_web_url:
            sei_root = sei_web_url.rstrip("/")
        elif sei_url and "/sei/" in sei_url:
            sei_root = sei_url.split("/sei/", 1)[0]
        elif sei_url:
            sei_root = sei_url.rstrip("/")
        else:
            sei_root = ""

        instance_url = (
            sei_root.replace("https://", "").replace("http://", "").strip().rstrip("/").lower()
        )
        keyring_user = f"{sei_usuario}@{instance_url}" if instance_url else sei_usuario

        # Chama o python do venv para registrar a senha no keyring do sistema.
        # Passa a senha via stdin para evitar expor a credencial em processos/logs.
        # S603: executable is str(venv_python()) — an absolute Path constant derived from VENV_DIR,
        # not user input. The -c snippet and keyring_user args are controlled by this script;
        # the password is passed through stdin (not argv) to avoid shell/log exposure.
        subprocess.run(
            [
                str(venv_python()),
                "-c",
                "import sys, keyring; keyring.set_password('todos-mcp', sys.argv[1], sys.stdin.read())",
                keyring_user,
            ],
            input=sei_senha,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        info("Senha salva com sucesso no cofre do sistema.")
    except subprocess.TimeoutExpired:
        error(
            "Timeout ao salvar no chaveiro do sistema (>30s); verifique se o daemon está disponível."
        )
        warn("A senha será armazenada em texto plano no arquivo de configuração como fallback.")
        config["mcpServers"][MCP_SERVER_NAME]["env"]["SEI_SENHA"] = sei_senha
    except (subprocess.CalledProcessError, OSError) as e:
        error(
            f"Erro ao salvar senha no cofre do sistema: {getattr(e, 'stderr', None) or getattr(e, 'stdout', None) or str(e)}"
        )
        warn("A senha será armazenada em texto plano no arquivo de configuração como fallback.")
        config["mcpServers"][MCP_SERVER_NAME]["env"]["SEI_SENHA"] = sei_senha


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Executa o fluxo completo de configuracao do MCP do SEI no Claude Desktop."""
    banner()

    # Fase 0
    check_python()
    config_path = get_config_path()
    uv_path = detect_uv()
    repo_root = detect_repo_root()
    sys.stdout.write("\n")

    # Fase 1
    creds = _collect_credentials()
    sei_url, sei_web_url, sei_usuario, sei_senha = creds[0], creds[1], creds[2], creds[3]

    sys.stdout.write("\n")
    sys.stdout.write("  [5b/5] Guardar senha de forma segura no cofre do sistema (Keyring)?\n")
    sys.stdout.write(
        "         Se ativado, a senha não será salva em texto plano no arquivo do Claude.\n"
    )
    sys.stdout.write("\n")
    usar_keyring = confirm("Usar Keyring seguro do sistema?", default_yes=True)

    env = _build_env(creds, usar_keyring=usar_keyring)

    # Fase 2
    sys.stdout.write("\n")
    create_venv(uv_path)
    install_package(repo_root, uv_path)

    command = str(todos_command())

    # Fase 3
    print_summary(config_path, command, env, usar_keyring=usar_keyring)

    if not confirm("Confirmar e salvar?"):
        sys.stdout.write("  Cancelado.\n")
        sys.exit(0)

    # Fase 4 — decide overwrite antes de gravar qualquer credencial
    sys.stdout.write("\n")
    config = read_config(config_path)
    backup_config(config_path)
    config = merge_sei_server(config, command, env)  # pode sys.exit se usuário recusar sobrescrever

    if usar_keyring:
        _save_keyring_password(sei_url, sei_web_url, sei_usuario, sei_senha, config)

    write_config(config_path, config)

    # Fase 5
    print_success(config_path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stdout.write("\n\n  Cancelado pelo usuario.\n")
        sys.exit(1)
