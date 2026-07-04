"""Wizard de configuração interativo para o MCP SEI (todos)."""

import asyncio
import concurrent.futures
import getpass
import json
import logging
import os
import re
import shutil
import subprocess as _sp
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import keyring as _keyring
from bs4 import BeautifulSoup
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from todos import output
from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIAuthError, SEICredenciaisError, SEIError
from todos.sei_web_client import SEIWebClient

# ── Constantes ──────────────────────────────────────────────────────────────
_MAX_ACRONYM_LEN: int = 6
_MAX_ANCESTOR_SEARCH: int = 5
_MIN_HOSTNAME_PARTS: int = 2
_WSSEI_API_PATHS: tuple[str, ...] = (
    "/sei/modulos/wssei/controlador_ws.php/api/v2",
    "/sei/modulos/mod-wssei/controlador_ws.php/api/v2",
)
_WSSEI_PROBE_TIMEOUT: float = 8.0
_TOTAL_STEPS: int = 5
_UFS: frozenset[str] = frozenset(
    {
        "AC",
        "AL",
        "AP",
        "AM",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MT",
        "MS",
        "MG",
        "PA",
        "PB",
        "PR",
        "PE",
        "PI",
        "RJ",
        "RN",
        "RS",
        "RO",
        "RR",
        "SC",
        "SP",
        "SE",
        "TO",
    }
)

_console = Console()
_logger_setup = logging.getLogger(__name__)

_BANNER_ART: str = """\
  ████████╗  ██████╗  ██████╗   ██████╗  ███████╗
     ██╔══╝ ██╔═══██╗ ██╔══██╗ ██╔═══██╗ ██╔════╝
     ██║    ██║   ██║ ██║  ██║ ██║   ██║ ███████╗
     ██║    ██║   ██║ ██║  ██║ ██║   ██║ ╚════██║
     ██║    ╚██████╔╝ ██████╔╝ ╚██████╔╝ ███████║
     ╚═╝     ╚═════╝  ╚═════╝   ╚═════╝  ╚══════╝"""


# ── Dataclasses ──────────────────────────────────────────────────────────────
@dataclass
class _SEIConnConfig:
    """Parâmetros de conexão usados durante a validação de credenciais."""

    sei_root: str
    usuario: str
    senha: str
    sigla_orgao: str
    sigla_orgao_sistema: str
    sigla_sistema: str
    verify_ssl_disabled: bool


@dataclass
class _SEIInstanceConfig:
    """Parâmetros da instância SEI resolvidos na etapa 1 do wizard."""

    sei_root: str
    rest_url: str
    sigla_orgao: str
    sigla_orgao_sistema: str
    sigla_sistema: str
    orgao_id: str
    verify_ssl_disabled: bool


@dataclass
class _ModseiDetection:
    """Resultado da sondagem do mod-wssei."""

    url: str
    confirmed: bool


# ── UI helpers ───────────────────────────────────────────────────────────────
def _show_banner() -> None:
    """Exibe o banner ASCII art do TOdos em um painel estilizado."""
    content = (
        f"[bold cyan]{_BANNER_ART}[/]\n\n"
        "[bold white]TOdos Domina O Sei[/]  [dim]·[/]  "
        "[cyan]MCP Server[/]  [dim]·[/]  [cyan]126 tools[/]"
    )
    _console.print(Panel(content, border_style="cyan", padding=(1, 4)))
    _console.print()


def _step(n: int, title: str) -> None:
    """Imprime um separador de etapa numerado e estilizado."""
    _console.print()
    _console.rule(f"[bold cyan]Passo {n}/{_TOTAL_STEPS}  ·  {title}[/]")
    _console.print()


def _ok(msg: str) -> None:
    """Imprime uma linha de sucesso."""
    _console.print(f"  [bold green]✓[/]  {msg}")


def _warn(msg: str) -> None:
    """Imprime uma linha de aviso."""
    _console.print(f"  [bold yellow]⚠[/]  {msg}")


def _err(msg: str) -> None:
    """Imprime uma linha de erro."""
    _console.print(f"  [bold red]✗[/]  {msg}")


# ── Detecção de órgãos ───────────────────────────────────────────────────────
def _detect_organs(
    login_url: str,
    sigla_orgao_sistema: str,
    sigla_sistema: str,
    *,
    verify_ssl: bool,
) -> tuple[list[tuple[str, str]], str, str]:
    """Detecta os órgãos disponíveis na página de login do SEI.

    Retorna (organs, sigla_orgao_sistema, sigla_sistema).
    Lança httpx.RequestError ou httpx.HTTPStatusError em falha de conexão.
    """
    organs: list[tuple[str, str]] = []

    with httpx.Client(verify=verify_ssl, follow_redirects=True, timeout=10.0) as client:
        resp = client.get(login_url)
        resp.raise_for_status()

    parsed_final = urlparse(str(resp.url))
    query_final = parse_qs(parsed_final.query)
    sigla_orgao_sistema = query_final.get("sigla_orgao_sistema", [sigla_orgao_sistema])[0]
    sigla_sistema = query_final.get("sigla_sistema", [sigla_sistema])[0]

    soup = BeautifulSoup(resp.text, "html.parser")
    sel = soup.find("select", attrs={"name": "selOrgao"}) or soup.find("select", id="selOrgao")
    if sel:
        for opt in sel.find_all("option"):
            val = opt.get("value")
            text = opt.get_text(strip=True)
            if isinstance(val, str) and val != "null" and not text.startswith(("-", "Selecione")):
                organs.append((val, text))

    return organs, sigla_orgao_sistema, sigla_sistema


def _resolve_organ_from_list(organs: list[tuple[str, str]]) -> tuple[str, str]:
    """Exibe tabela de órgãos e solicita seleção. Retorna (orgao_id, sigla_orgao)."""
    table = Table(
        title="Órgãos disponíveis no SEI",
        box=box.ROUNDED,
        border_style="cyan",
        header_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Órgão", style="bold white", ratio=1)
    table.add_column("ID", style="cyan", width=8)
    for idx, (val, name) in enumerate(organs, 1):
        table.add_row(str(idx), name, val)
    _console.print(table)
    _console.print()

    while True:
        raw = Prompt.ask("  Número do seu órgão", default="1", console=_console)
        if raw.isdigit() and 1 <= int(raw) <= len(organs):
            selected_idx = int(raw) - 1
            break
        _err(f"Opção inválida — informe um número entre 1 e {len(organs)}.")

    orgao_id, sigla_orgao = organs[selected_idx]

    parts = [p.strip() for p in re.split(r"\s*-\s*", sigla_orgao) if p.strip()]
    if len(parts) > 1:
        parts_without_uf = [p for p in parts if p.upper() not in _UFS]
        if parts_without_uf:
            parts = parts_without_uf
    if len(parts) > 1:
        acronyms = [p for p in parts if p.isupper() and len(p) <= _MAX_ACRONYM_LEN]
        sigla_orgao = acronyms[0] if acronyms else parts[0]
    else:
        sigla_orgao = parts[0] if parts else sigla_orgao

    return orgao_id, sigla_orgao


def _resolve_organ_manual(sigla_orgao_sistema: str) -> tuple[str, str, str, str]:
    """Solicita dados do órgão manualmente.

    Retorna (sigla_orgao, sigla_orgao_sistema, orgao_id, default_sigla_sistema).
    """
    default_sigla = sigla_orgao_sistema or "PGE"
    sigla_orgao = Prompt.ask("  Sigla do órgão no SEI", default=default_sigla, console=_console)
    default_sigla_sistema = sigla_orgao_sistema or "RO"
    sigla_orgao_sistema = Prompt.ask(
        "  Sigla do órgão no sistema", default=default_sigla_sistema, console=_console
    )
    default_id = "9" if default_sigla_sistema == "RO" else "0"
    orgao_id = Prompt.ask("  ID do órgão", default=default_id, console=_console)
    return sigla_orgao, sigla_orgao_sistema, orgao_id, default_sigla_sistema


# ── Keyring ──────────────────────────────────────────────────────────────────
def _compute_keyring_user(usuario: str, sei_root: str) -> str:
    """Monta a chave do keyring: '<usuario>@<host>' (sem scheme)."""
    instance_url = (
        sei_root.replace("https://", "").replace("http://", "").strip().rstrip("/").lower()
    )
    return f"{usuario}@{instance_url}" if instance_url else usuario


def _read_existing_todos_env() -> dict[str, str] | None:
    """Retorna o `env` de mcpServers.todos em ~/.claude.json, ou None se não configurado."""
    config_path = Path.home() / ".claude.json"
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger_setup.warning("Não foi possível ler ~/.claude.json: %s", exc)
        return None
    servers = data.get("mcpServers")
    todos = servers.get("todos") if isinstance(servers, dict) else None
    if not isinstance(todos, dict):
        return None
    env = todos.get("env")
    return env if isinstance(env, dict) else {}


def _save_password_to_keyring(
    keyring_user: str,
    senha: str,
) -> tuple[str, str]:
    """Armazena a senha no keyring do sistema.

    Retorna (senha_config, senha_validacao): senha_config é vazia se keyring OK,
    ou a senha original se o keyring falhou e o usuário aceitou texto claro.
    """
    try:
        with (
            _console.status("  [dim]Gravando no Keyring do Sistema...[/]", spinner="dots"),
            concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool,
        ):
            future = pool.submit(_keyring.set_password, "todos-mcp", keyring_user, senha)
            try:
                future.result(timeout=10)
            except concurrent.futures.TimeoutError as exc:
                msg = (
                    "Keyring bloqueou por mais de 10 segundos. "
                    "Tente: PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring todos setup"
                )
                raise RuntimeError(msg) from exc
        _ok("Senha armazenada no Keyring do Sistema!")
    except (RuntimeError, OSError, ValueError) as e:
        _err(f"Falha ao acessar o Keyring: {e}")
        _warn("A senha não pôde ser salva de forma segura no Keyring.")
        if not Confirm.ask("  Salvar senha em texto claro nas configurações?", console=_console):
            _err("Configuração cancelada.")
            sys.exit(1)
        return senha, senha
    else:
        lida: str | None = None
        try:
            lida = _keyring.get_password("todos-mcp", keyring_user)
        except (OSError, ValueError) as exc:
            _warn(f"Leitura de verificação do Keyring falhou: {exc}")
            return "", senha
        if lida:
            _ok("Leitura de verificação do Keyring OK!")
            return "", lida
        _warn("Keyring confirmou gravação mas retornou vazio na leitura de teste.")
        return "", senha


def _confirmar_ou_abortar(msg_erro: str) -> None:
    """Exibe erro e pergunta se o usuário quer continuar mesmo assim."""
    _err(msg_erro)
    if not Confirm.ask("  Gravar configurações mesmo assim?", console=_console):
        _err("Configuração cancelada.")
        sys.exit(1)


# ── Validação de credenciais ─────────────────────────────────────────────────
def _validate_credentials(conn: _SEIConnConfig) -> None:
    """Efetua login de teste no SEI para validar as credenciais."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("todos").setLevel(logging.WARNING)

    web_client = SEIWebClient(
        SEIWebClientConfig(
            sei_web_url=conn.sei_root,
            sei_usuario=conn.usuario,
            sei_senha=conn.senha,
            sei_sigla_orgao=conn.sigla_orgao,
            sei_sigla_orgao_sistema=conn.sigla_orgao_sistema,
            sei_sigla_sistema=conn.sigla_sistema,
            sei_verify_ssl=not conn.verify_ssl_disabled,
        )
    )

    async def _do_login() -> dict:
        """Executa o login assíncrono e coleta informações do usuário."""
        try:
            await web_client.ensure_authenticated()
            info: dict = {
                "nome": web_client.nome_usuario,
                "id": web_client.id_usuario,
                "orgao": web_client.orgao_usuario,
                "unidade": {},
            }
            try:
                info["unidade"] = await web_client.unidade_atual()
            except (SEIError, httpx.HTTPError, OSError) as exc:
                _logger_setup.warning("setup_wizard: não foi possível obter unidade atual: %s", exc)
            return info
        finally:
            await web_client.close()

    try:
        with _console.status("  [dim]Efetuando login de teste no SEI...[/]", spinner="dots"):
            client_info = asyncio.run(_do_login())
        web_client.limpar_senha()
        _ok("Credenciais validadas com sucesso!")
        if client_info:
            nome = client_info.get("nome") or conn.usuario
            uid = client_info.get("id") or "—"
            orgao = client_info.get("orgao") or conn.sigla_orgao
            unid: dict = client_info.get("unidade") or {}
            sigla_u = unid.get("sigla") or "N/A"
            nome_u = unid.get("nome") or "N/A"
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="dim", width=10)
            grid.add_column(style="white")
            grid.add_row("Usuário", f"{nome}  [dim]ID {uid} · {orgao}[/]")
            grid.add_row("Unidade", f"{sigla_u}  [dim]{nome_u}[/]")
            _console.print(Panel(grid, border_style="green", padding=(0, 3)))
    except SEICredenciaisError as e:
        web_client.limpar_senha()
        _confirmar_ou_abortar(f"Credenciais rejeitadas pelo SEI: {e}")
    except SEIAuthError as e:
        web_client.limpar_senha()
        _confirmar_ou_abortar(f"Falha de autenticação: {e}")
    except (OSError, ValueError, RuntimeError) as e:
        web_client.limpar_senha()
        _warn("Login no SEI falhou. Verifique usuário, senha e órgão.")
        _confirmar_ou_abortar(f"Erro na validação: {e}")


# ── Atualização de configs MCP ───────────────────────────────────────────────
def _mcp_add_via_cli(
    claude_cli: str,
    todos_cmd: str,
    mcp_env: dict[str, str],
    scope: str,
    cwd: Path | None = None,
) -> bool:
    """Registra o servidor MCP via `claude mcp add`. Retorna True em sucesso."""
    env_args = [item for k, v in mcp_env.items() for item in ("-e", f"{k}={v}")]
    cmd = [claude_cli, "mcp", "add", "-s", scope, *env_args, "todos", todos_cmd]
    cwd_str = str(cwd or Path.cwd())
    try:
        # S603: claude_cli é um caminho absoluto de shutil.which — não é entrada do usuário.
        _sp.run(cmd, check=True, capture_output=True, text=True, cwd=cwd_str)
    except _sp.CalledProcessError as e:
        if "already exists" not in (e.stderr or "") and "already exists" not in (e.stdout or ""):
            return False
        try:
            _sp.run(
                [claude_cli, "mcp", "remove", "-s", scope, "todos"],
                check=True,
                capture_output=True,
                text=True,
                cwd=cwd_str,
            )
            _sp.run(cmd, check=True, capture_output=True, text=True, cwd=cwd_str)
        except _sp.CalledProcessError as re_exc:
            _logger_setup.debug("mcp add re-tentativa falhou: %s", re_exc)
            return False
        else:
            return True
    except OSError as exc:
        _logger_setup.warning("mcp add falhou: %s", exc)
        return False
    else:
        return True


def _mcp_add_via_json(config_path: Path, todos_cmd: str, mcp_env: dict[str, str]) -> bool:
    """Edita o JSON de config MCP diretamente como fallback. Retorna True em sucesso."""
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_data: dict = {"mcpServers": {}}
        if config_path.exists():
            content = config_path.read_text(encoding="utf-8").strip()
            if content:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    config_data = parsed
                if not isinstance(config_data.get("mcpServers"), dict):
                    config_data["mcpServers"] = {}
        config_data["mcpServers"]["todos"] = {
            "command": todos_cmd,
            "args": [],
            "env": dict(mcp_env),
        }
        with config_path.open("w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
    except (OSError, json.JSONDecodeError) as exc:
        _logger_setup.warning("_mcp_add_via_json: falha ao ler/escrever arquivo de config: %s", exc)
        return False
    else:
        return True


def _update_antigravity(todos_cmd: str, mcp_env: dict[str, str]) -> None:
    """Atualiza os arquivos de config do Antigravity IDE, se existirem."""
    home = Path.home()
    for ag_path in [
        home / ".gemini" / "antigravity-ide" / "mcp_config.json",
        home / ".gemini" / "config" / "mcp_config.json",
    ]:
        if ag_path.exists() or ag_path.parent.exists():
            if _mcp_add_via_json(ag_path, todos_cmd, mcp_env):
                _ok(f"Atualizado: {ag_path}")
            else:
                _warn(f"Não foi possível atualizar {ag_path}")


def _claude_desktop_path() -> Path:
    """Retorna o caminho do arquivo de config do Claude Desktop para a plataforma atual."""
    home = Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        return appdata / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def _update_claude_desktop(todos_cmd: str, mcp_env: dict[str, str]) -> None:
    """Atualiza o arquivo de config do Claude Desktop, se o diretório existir."""
    claude_desktop = _claude_desktop_path()
    if claude_desktop.parent.exists():
        if _mcp_add_via_json(claude_desktop, todos_cmd, mcp_env):
            _ok(f"Atualizado: {claude_desktop}")
        else:
            _warn(f"Não foi possível atualizar {claude_desktop}")


def _update_claude_code_global(
    claude_cli: str | None, todos_cmd: str, mcp_env: dict[str, str]
) -> None:
    """Atualiza a config global do Claude Code via CLI ou edição direta do JSON."""
    home = Path.home()
    if claude_cli and _mcp_add_via_cli(claude_cli, todos_cmd, mcp_env, "user"):
        _ok("Atualizado: Claude Code (global) via `claude mcp add -s user`")
    elif _mcp_add_via_json(home / ".claude.json", todos_cmd, mcp_env):
        _ok(f"Atualizado: {home / '.claude.json'}")
    else:
        _warn(f"Não foi possível atualizar {home / '.claude.json'}")


def _update_workspace_local(
    claude_cli: str | None,
    todos_cmd: str,
    mcp_env: dict[str, str],
    *,
    using_plaintext_password: bool,
) -> None:
    """Atualiza o .mcp.json local do workspace, se encontrado nos diretórios ancestrais."""
    mcp_search = Path.cwd()
    workspace_dir: Path | None = None
    for _ in range(_MAX_ANCESTOR_SEARCH):
        if (mcp_search / ".mcp.json").exists():
            workspace_dir = mcp_search
            break
        parent = mcp_search.parent
        if parent == mcp_search:
            break
        mcp_search = parent

    if workspace_dir is None:
        return

    mcp_json = workspace_dir / ".mcp.json"
    if using_plaintext_password:
        _warn(
            f"Pulando {mcp_json}: senha em texto claro não deve ser "
            "gravada em arquivo de projeto (risco de VCS)."
        )
    elif claude_cli and _mcp_add_via_cli(
        claude_cli, todos_cmd, mcp_env, "project", cwd=workspace_dir
    ):
        _ok(f"Atualizado: {mcp_json} via `claude mcp add -s project`")
    elif _mcp_add_via_json(mcp_json, todos_cmd, mcp_env):
        _ok(f"Atualizado: {mcp_json}")
    else:
        _warn(f"Não foi possível atualizar {mcp_json}")


def _update_codex_via_cli(codex_cli: str, todos_cmd: str, mcp_env: dict[str, str]) -> bool:
    """Tenta adicionar via `codex mcp add`. Retorna True em sucesso."""
    env_args = [item for k, v in mcp_env.items() for item in ("--env", f"{k}={v}")]
    cmd_codex = [codex_cli, "mcp", "add", "todos", *env_args, "--", todos_cmd]
    try:
        # S603: codex_cli é caminho absoluto de shutil.which — não é entrada do usuário.
        _sp.run(cmd_codex, check=True, capture_output=True, text=True)
    except _sp.CalledProcessError as e:
        if "already" in (e.stderr or "") or "already" in (e.stdout or ""):
            try:
                _sp.run(
                    [codex_cli, "mcp", "remove", "todos"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                _sp.run(cmd_codex, check=True, capture_output=True, text=True)
            except _sp.CalledProcessError as re_exc:
                _logger_setup.debug("codex mcp add re-tentativa falhou: %s", re_exc)
            else:
                _ok("Atualizado: Codex (global) via `codex mcp add`")
                return True
        return False
    except OSError as exc:
        _logger_setup.warning("codex mcp add falhou: %s", exc)
        return False
    else:
        _ok("Atualizado: Codex (global) via `codex mcp add`")
        return True


def _update_codex_via_toml(codex_config: Path, todos_cmd: str, mcp_env: dict[str, str]) -> None:
    """Edita ~/.codex/config.toml diretamente como fallback para o Codex CLI."""
    try:
        codex_config.parent.mkdir(parents=True, exist_ok=True)
        existing = codex_config.read_text(encoding="utf-8") if codex_config.exists() else ""
        existing = re.sub(
            r"\[mcp_servers\.todos\][^\[]*",
            "",
            existing,
            flags=re.DOTALL,
        ).rstrip()
        env_lines = "\n".join(f"  {k} = {json.dumps(v)}" for k, v in mcp_env.items())
        block = (
            f"\n\n[mcp_servers.todos]\ncommand = {json.dumps(todos_cmd)}\nargs = []\n"
            f"[mcp_servers.todos.env]\n{env_lines}\n"
        )
        codex_config.write_text(existing + block, encoding="utf-8")
        _ok(f"Atualizado: {codex_config}")
    except OSError as e_codex:
        _warn(f"Não foi possível atualizar {codex_config}: {e_codex}")


def _update_codex(todos_cmd: str, mcp_env: dict[str, str]) -> None:
    """Atualiza a config do Codex CLI via CLI ou edição direta do TOML."""
    home = Path.home()
    codex_cli = shutil.which("codex")
    codex_config = home / ".codex" / "config.toml"
    if not (codex_cli or codex_config.exists()):
        return
    if codex_cli and _update_codex_via_cli(codex_cli, todos_cmd, mcp_env):
        return
    _update_codex_via_toml(codex_config, todos_cmd, mcp_env)


def _update_mcp_configs(
    claude_cli: str | None,
    todos_cmd: str,
    mcp_env: dict[str, str],
    *,
    using_plaintext_password: bool,
) -> None:
    """Atualiza todos os arquivos de config MCP conhecidos."""
    _update_antigravity(todos_cmd, mcp_env)
    _update_claude_desktop(todos_cmd, mcp_env)
    _update_claude_code_global(claude_cli, todos_cmd, mcp_env)
    _update_workspace_local(
        claude_cli, todos_cmd, mcp_env, using_plaintext_password=using_plaintext_password
    )
    _update_codex(todos_cmd, mcp_env)


# ── Detecção de mod-wssei ────────────────────────────────────────────────────
def _infer_sigla_orgao_sistema(hostname: str) -> str:
    """Infere a sigla_orgao_sistema a partir do hostname do SEI."""
    if "ro.gov.br" in hostname:
        return "RO"
    parts = hostname.split(".")
    if len(parts) >= _MIN_HOSTNAME_PARTS:
        if parts[0] in ("sip", "sei") and parts[1] not in ("gov", "com", "org", "net", "edu"):
            return parts[1].upper()
        if parts[0] not in ("gov", "com", "org", "net", "edu"):
            return parts[0].upper()
    return "SEI"


def _is_cloudflare_response(resp: httpx.Response) -> bool:
    """Retorna True se a resposta veio de um edge/CDN proxy, não do app PHP.

    Usa múltiplos sinais: cf-ray, server:cloudflare, x-amzn-requestid.
    """
    signals = {
        "cf-ray": "cf-ray" in resp.headers,
        "server:cloudflare": resp.headers.get("server", "").lower() == "cloudflare",
        "x-amzn-requestid": "x-amzn-requestid" in resp.headers,
    }
    detected = [name for name, found in signals.items() if found]
    for name in detected:
        _logger_setup.debug("Edge/CDN proxy signal: %s", name)
    return any(signals.values())


def _detect_modsei_url(sei_root: str, *, verify_ssl: bool) -> _ModseiDetection:
    """Sonda os dois caminhos conhecidos do mod-wssei e retorna o primeiro que responde.

    Usa POST /autenticar como sonda: módulo retorna 400/422 quando presente,
    Apache retorna 404 quando não instalado. Quando Cloudflare bloqueia,
    retorna a URL candidata como não-confirmada (confirmed=False).
    """
    first_cloudflare: str = ""
    try:
        with httpx.Client(
            verify=verify_ssl, follow_redirects=False, timeout=_WSSEI_PROBE_TIMEOUT
        ) as client:
            for api_path in _WSSEI_API_PATHS:
                candidate = f"{sei_root}{api_path}"
                try:
                    resp = client.post(
                        f"{candidate}/autenticar",
                        data={"usuario": "", "senha": "", "orgao": "0"},
                    )
                except httpx.RequestError:
                    continue
                if resp.status_code in (404, 501):
                    continue
                if _is_cloudflare_response(resp):
                    if not first_cloudflare:
                        first_cloudflare = candidate
                    continue
                return _ModseiDetection(url=candidate, confirmed=True)
    except httpx.RequestError:
        pass
    if first_cloudflare:
        return _ModseiDetection(url=first_cloudflare, confirmed=False)
    return _ModseiDetection(url="", confirmed=False)


# ── Etapas do wizard ─────────────────────────────────────────────────────────


def _detect_organs_with_ssl_fallback(
    login_url: str,
    sigla_orgao_sistema: str,
    sigla_sistema: str,
) -> tuple[list[tuple[str, str]], str, str, bool]:
    """Detecta órgãos com fallback para SSL desativado se solicitado pelo usuário.

    Retorna (organs, sigla_orgao_sistema, sigla_sistema, verify_ssl_disabled).
    """
    try:
        with _console.status("  [dim]Detectando órgãos disponíveis...[/]", spinner="dots"):
            organs, sigla_orgao_sistema, sigla_sistema = _detect_organs(
                login_url, sigla_orgao_sistema, sigla_sistema, verify_ssl=True
            )
        if organs:
            _ok(f"{len(organs)} órgão(s) detectado(s) com sucesso.")
        else:
            _warn("Nenhum órgão encontrado na página de login. Configuração manual.")
    except httpx.RequestError:
        _console.print(
            Panel(
                "[bold white]A verificação SSL protege contra ataques man-in-the-middle.[/]\n"
                "Desabilitá-la permite que um atacante intercepte suas credenciais em trânsito.\n\n"
                "[dim]Use SOMENTE em redes com CA interna controlada "
                "(ex.: rede corporativa com certificado auto-assinado).[/]",
                title="[bold red]⚠  Aviso de Segurança — SSL[/]",
                border_style="red",
                padding=(1, 2),
            )
        )
        if not Confirm.ask("  Desativar SSL e tentar novamente?", console=_console):
            _warn("Continuando sem detecção automática de órgãos. Configure o órgão manualmente.")
            return [], sigla_orgao_sistema, sigla_sistema, False
    except httpx.HTTPStatusError as e:
        _warn(
            f"SEI retornou HTTP {e.response.status_code}. "
            "Continuando sem detecção automática de órgãos."
        )
        return [], sigla_orgao_sistema, sigla_sistema, False
    else:
        return organs, sigla_orgao_sistema, sigla_sistema, False

    # Reached only when httpx.RequestError was raised and user chose to disable SSL
    try:
        with _console.status("  [dim]Detectando órgãos (sem SSL)...[/]", spinner="dots"):
            organs, sigla_orgao_sistema, sigla_sistema = _detect_organs(
                login_url, sigla_orgao_sistema, sigla_sistema, verify_ssl=False
            )
        if organs:
            _ok(f"{len(organs)} órgão(s) detectado(s).")
        else:
            _warn("Nenhum órgão encontrado. Configuração manual.")
    except (httpx.RequestError, httpx.HTTPStatusError) as e2:
        _warn(f"Não foi possível conectar: {e2}")
        organs = []
    return organs, sigla_orgao_sistema, sigla_sistema, True


def _resolve_modsei_url(detection: _ModseiDetection) -> str:
    """Confirma com o usuário qual URL REST do mod-wssei usar. Retorna a URL escolhida."""
    if detection.url and detection.confirmed:
        _ok(f"mod-wssei detectado: [cyan]{detection.url}[/]")
    elif detection.url:
        _warn(f"Possível mod-wssei em: [cyan]{detection.url}[/]")
        _warn(
            "  (Cloudflare bloqueou verificação automática — confirme se mod-wssei está instalado.)"
        )
    else:
        _warn("mod-wssei não detectado automaticamente nesta instância.")

    if detection.url and Confirm.ask("  Usar esta URL REST?", default=True, console=_console):
        return detection.url

    prompt_label = (
        "  URL REST do mod-wssei (vazio = desativar)"
        if detection.url
        else "  URL REST do mod-wssei (vazio = web-only)"
    )
    return Prompt.ask(prompt_label, default="", show_default=False, console=_console)


def _setup_sei_instance() -> _SEIInstanceConfig:
    """Solicita a URL do SEI e o órgão, retornando a config da instância resolvida."""
    sei_url_raw = Prompt.ask(
        "  URL do SEI",
        default="https://sei.sistemas.ro.gov.br",
        console=_console,
    )
    if not sei_url_raw.startswith(("http://", "https://")):
        sei_url_raw = "https://" + sei_url_raw

    parsed = urlparse(sei_url_raw)
    if not parsed.netloc or ":" in parsed.netloc:
        _err("URL inválida. Use o formato: https://sei.exemplo.gov.br")
        sys.exit(1)
    sei_root = f"{parsed.scheme}://{parsed.netloc}"
    query = parse_qs(parsed.query)

    sigla_orgao_sistema: str = (
        query["sigla_orgao_sistema"][0]
        if "sigla_orgao_sistema" in query
        else _infer_sigla_orgao_sistema(parsed.netloc.lower())
    )
    sigla_sistema = query.get("sigla_sistema", ["SEI"])[0]
    login_url = (
        f"{sei_root}/sip/login.php"
        f"?sigla_orgao_sistema={sigla_orgao_sistema}&sigla_sistema={sigla_sistema}"
    )

    organs, sigla_orgao_sistema, sigla_sistema, verify_ssl_disabled = (
        _detect_organs_with_ssl_fallback(login_url, sigla_orgao_sistema, sigla_sistema)
    )

    _console.print()
    if organs:
        orgao_id, sigla_orgao = _resolve_organ_from_list(organs)
    else:
        sigla_orgao, sigla_orgao_sistema, orgao_id, _ = _resolve_organ_manual(sigla_orgao_sistema)
    _ok(f"Órgão: [bold]{sigla_orgao}[/]  [dim]ID {orgao_id}[/]")

    _console.print()
    with _console.status("  [dim]Verificando mod-wssei...[/]", spinner="dots"):
        detection = _detect_modsei_url(sei_root, verify_ssl=not verify_ssl_disabled)
    rest_url = _resolve_modsei_url(detection)

    return _SEIInstanceConfig(
        sei_root=sei_root,
        rest_url=rest_url,
        sigla_orgao=sigla_orgao,
        sigla_orgao_sistema=sigla_orgao_sistema,
        sigla_sistema=sigla_sistema,
        orgao_id=orgao_id,
        verify_ssl_disabled=verify_ssl_disabled,
    )


def _setup_credentials(sei_root: str) -> tuple[str, str, str]:
    """Solicita usuário e senha, armazenando no keyring.

    Retorna (usuario, senha_config, senha_validacao).
    """
    _console.print()
    usuario = Prompt.ask("  Usuário do SEI (CPF ou iniciais)", console=_console)
    if not usuario:
        _err("Usuário é obrigatório.")
        sys.exit(1)

    senha = getpass.getpass("  Senha do SEI (oculta): ")
    if not senha:
        _err("Senha é obrigatória.")
        sys.exit(1)

    _console.print()
    keyring_user = _compute_keyring_user(usuario, sei_root)
    senha_config, senha_validacao = _save_password_to_keyring(keyring_user, senha)
    return usuario, senha_config, senha_validacao


# ── Helpers de build/deploy/summary ─────────────────────────────────────────
def _build_mcp_env(
    inst: _SEIInstanceConfig,
    usuario: str,
    senha: str,
) -> tuple[dict[str, str], bool]:
    """Constrói o dicionário de variáveis MCP e exibe tabela resumida.

    Retorna (mcp_env, using_plaintext_password).
    """
    mcp_env: dict[str, str] = {
        "SEI_URL": inst.rest_url,
        "SEI_WEB_URL": inst.sei_root,
        "SEI_SIGLA_ORGAO": inst.sigla_orgao,
        "SEI_SIGLA_ORGAO_SISTEMA": inst.sigla_orgao_sistema,
        "SEI_SIGLA_SISTEMA": inst.sigla_sistema,
        "SEI_USUARIO": usuario,
        "SEI_SENHA": senha,
        "SEI_ORGAO": inst.orgao_id,
    }
    if inst.verify_ssl_disabled:
        mcp_env["SEI_VERIFY_SSL"] = "false"
    using_plaintext = bool(mcp_env["SEI_SENHA"])

    vars_table = Table(box=box.SIMPLE, padding=(0, 1), show_header=False)
    vars_table.add_column(style="dim cyan", width=28)
    vars_table.add_column(style="white")
    for key, val in mcp_env.items():
        display = "[dim]•••••••[/]" if key == "SEI_SENHA" and val else val or "[dim]—[/]"
        vars_table.add_row(key, display)
    _console.print(vars_table)
    return mcp_env, using_plaintext


def _deploy_mcp_configs(
    mcp_env: dict[str, str],
    *,
    using_plaintext_password: bool,
) -> None:
    """Atualiza todos os arquivos de config MCP e apaga a senha do env em memória."""
    claude_cli = shutil.which("claude")
    todos_cmd = shutil.which("todos") or "todos"
    _update_mcp_configs(
        claude_cli, todos_cmd, mcp_env, using_plaintext_password=using_plaintext_password
    )
    mcp_env["SEI_SENHA"] = ""


def _show_config_summary(
    inst: _SEIInstanceConfig,
    usuario: str,
    *,
    using_plaintext_password: bool,
) -> None:
    """Exibe o painel de sumário final da configuração concluída."""
    _console.print()
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="dim", width=12)
    summary.add_column(style="bold white")
    summary.add_row("SEI URL", inst.sei_root)
    summary.add_row("mod-wssei", inst.rest_url or "[dim]— (web-only)[/]")
    summary.add_row("Órgão", f"{inst.sigla_orgao}  [dim]ID {inst.orgao_id}[/]")
    summary.add_row("Usuário", usuario)
    senha_label = "[green]Keyring[/]" if not using_plaintext_password else "[yellow]texto claro[/]"
    summary.add_row("Senha", senha_label)
    _console.print(
        Panel(
            summary,
            title="[bold green]✓  Configuração concluída com sucesso![/]",
            border_style="green",
            padding=(1, 3),
        )
    )
    _console.print()
    _console.print(
        "  Reinicie o [bold]Antigravity[/], [bold]Claude Desktop[/] ou [bold]Codex[/] "
        "para ativar as [cyan bold]126 ferramentas SEI[/]."
    )
    _console.print()


# ── Pontos de entrada públicos ───────────────────────────────────────────────
def run_setup_wizard(*, force: bool = False) -> None:
    """Executa o wizard interativo de configuração do MCP SEI.

    Idempotente: se o MCP 'todos' já está configurado e `force` é False, exibe
    o estado atual e sai SEM sobrescrever. Use `--force` para reconfigurar do
    zero, ou `todos set-password` para trocar só a senha.
    """
    if not sys.stdin.isatty():
        _err("'todos setup' requer um terminal interativo (stdin não é TTY).")
        sys.exit(1)

    if not force:
        env = _read_existing_todos_env()
        if env is not None:
            _show_banner()
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="dim", width=12)
            grid.add_column(style="bold white")
            grid.add_row("Usuário", env.get("SEI_USUARIO") or "—")
            grid.add_row("SEI URL", env.get("SEI_WEB_URL") or env.get("SEI_URL") or "—")
            grid.add_row("Órgão", env.get("SEI_SIGLA_ORGAO") or "—")
            _console.print(
                Panel(
                    grid,
                    title="[bold cyan]MCP 'todos' já configurado[/]",
                    border_style="cyan",
                    padding=(1, 3),
                )
            )
            _console.print()
            _console.print(
                "  [dim]Trocar só a senha:[/]   [bold cyan]todos set-password[/]\n"
                "  [dim]Reconfigurar tudo:[/]   [bold cyan]todos setup --force[/]"
            )
            _console.print()
            return

    _show_banner()

    # ── Passo 1 ─────────────────────────────────────────────────────────────
    _step(1, "URL e Instância do SEI")
    inst = _setup_sei_instance()

    # ── Passo 2 ─────────────────────────────────────────────────────────────
    _step(2, "Usuário e Senha")
    usuario, senha, senha_validacao = _setup_credentials(inst.sei_root)

    # ── Passo 3 ─────────────────────────────────────────────────────────────
    _step(3, "Validação de Credenciais")
    _validate_credentials(
        _SEIConnConfig(
            sei_root=inst.sei_root,
            usuario=usuario,
            senha=senha_validacao,
            sigla_orgao=inst.sigla_orgao,
            sigla_orgao_sistema=inst.sigla_orgao_sistema,
            sigla_sistema=inst.sigla_sistema,
            verify_ssl_disabled=inst.verify_ssl_disabled,
        )
    )
    senha_validacao = ""
    del senha_validacao

    # ── Passo 4 ─────────────────────────────────────────────────────────────
    _step(4, "Variáveis de Ambiente MCP")
    mcp_env, using_plaintext_password = _build_mcp_env(inst, usuario, senha)
    senha = ""
    del senha

    # ── Passo 5 ─────────────────────────────────────────────────────────────
    _step(5, "Arquivos de Configuração MCP")
    _deploy_mcp_configs(mcp_env, using_plaintext_password=using_plaintext_password)

    _show_config_summary(inst, usuario, using_plaintext_password=using_plaintext_password)


def run_set_password() -> None:
    """Atualiza SOMENTE a senha no keyring, sem tocar na config MCP.

    Lê usuário/URL da config existente (~/.claude.json), grava a nova senha no
    keyring e valida com um login real. Use quando quiser trocar a senha sem
    risco de reconfigurar o MCP.
    """
    if not sys.stdin.isatty():
        _err("'todos set-password' requer um terminal interativo (stdin não é TTY).")
        sys.exit(1)

    env = _read_existing_todos_env()
    if env is None:
        _err("MCP 'todos' não está configurado. Rode 'todos setup' primeiro.")
        sys.exit(1)
    usuario = (env.get("SEI_USUARIO") or "").strip()
    sei_root = (env.get("SEI_WEB_URL") or env.get("SEI_URL") or "").strip()
    if not usuario or not sei_root:
        _err("SEI_USUARIO/SEI_WEB_URL ausentes na config. Rode 'todos setup'.")
        sys.exit(1)
    keyring_user = _compute_keyring_user(usuario, sei_root)

    _show_banner()
    _console.print(
        Panel(
            f"  Conta: [bold cyan]{keyring_user}[/]",
            title="[bold cyan]Atualizar Senha do SEI[/]",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    _console.print()

    senha = getpass.getpass("  Nova senha do SEI (oculta): ")
    if not senha:
        _err("Senha vazia.")
        sys.exit(1)

    with _console.status("  [dim]Gravando no Keyring...[/]", spinner="dots"):
        try:
            _keyring.set_password("todos-mcp", keyring_user, senha)
            lida = _keyring.get_password("todos-mcp", keyring_user)
        except (RuntimeError, OSError, ValueError, ImportError, AttributeError) as exc:
            _err(f"Não foi possível gravar no keyring: {exc}")
            sys.exit(1)
    if lida != senha:
        _err("Falha ao confirmar a senha no keyring (readback divergente).")
        sys.exit(1)
    _ok("Senha gravada no Keyring com sucesso!")

    _validate_credentials(
        _SEIConnConfig(
            sei_root=sei_root,
            usuario=usuario,
            senha=senha,
            sigla_orgao=env.get("SEI_SIGLA_ORGAO", ""),
            sigla_orgao_sistema=env.get("SEI_SIGLA_ORGAO_SISTEMA", ""),
            sigla_sistema=env.get("SEI_SIGLA_SISTEMA", "SEI"),
            verify_ssl_disabled=(env.get("SEI_VERIFY_SSL", "") == "false"),
        )
    )
    senha = ""
    del senha
    _console.print()
    _ok(f"Senha atualizada com sucesso!  [dim]({keyring_user})[/]  Config MCP intacta.")
    _console.print()


# ── Provisionamento headless (RFC 0019 §2.3) ─────────────────────────────────
def _validate_credentials_headless(conn: _SEIConnConfig) -> None:
    """Login de teste sem interação: `sys.exit(1)` direto em falha, sem `Confirm.ask` de fallback.

    Equivalente não-interativo de `_validate_credentials` — usado quando não
    há TTY para perguntar "gravar mesmo assim?" (provisionamento por script/CI).
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("todos").setLevel(logging.WARNING)
    web_client = SEIWebClient(
        SEIWebClientConfig(
            sei_web_url=conn.sei_root,
            sei_usuario=conn.usuario,
            sei_senha=conn.senha,
            sei_sigla_orgao=conn.sigla_orgao,
            sei_sigla_orgao_sistema=conn.sigla_orgao_sistema,
            sei_sigla_sistema=conn.sigla_sistema,
            sei_verify_ssl=not conn.verify_ssl_disabled,
        )
    )

    async def _do_login() -> None:
        try:
            await web_client.ensure_authenticated()
        finally:
            await web_client.close()

    try:
        asyncio.run(_do_login())
    except SEICredenciaisError as exc:
        web_client.limpar_senha()
        output.emit_human(f"[bold red]Credenciais rejeitadas pelo SEI:[/] {escape(str(exc))}")
        sys.exit(1)
    except SEIAuthError as exc:
        web_client.limpar_senha()
        output.emit_human(f"[bold red]Falha de autenticação:[/] {escape(str(exc))}")
        sys.exit(1)
    except (OSError, ValueError, RuntimeError) as exc:
        web_client.limpar_senha()
        output.emit_human(f"[bold red]Erro na validação de login:[/] {escape(str(exc))}")
        sys.exit(1)
    else:
        web_client.limpar_senha()


def _save_password_headless(keyring_user: str, senha: str) -> None:
    """Grava a senha no Keyring sem interação: `sys.exit(1)` direto em falha.

    Equivalente não-interativo de `_save_password_to_keyring` — sem o
    fallback de "salvar em texto claro?" (não há TTY para perguntar).
    """
    try:
        _keyring.set_password("todos-mcp", keyring_user, senha)
    except (RuntimeError, OSError, ValueError) as exc:
        output.emit_human(f"[bold red]Falha ao gravar a senha no Keyring:[/] {escape(str(exc))}")
        sys.exit(1)


def run_setup_headless(
    inst: _SEIInstanceConfig,
    usuario: str,
    senha: str,
    *,
    force: bool = False,
) -> None:
    """Configura o MCP SEI sem interação — provisionamento por script/CI (RFC 0019 §2.3).

    Pula o wizard inteiro: valida login, grava a senha no Keyring e monta/
    aplica a config MCP direto. Sem TTY, então qualquer falha aborta com
    `sys.exit(1)` e uma mensagem clara em vez de perguntar "continuar mesmo
    assim?" — o chamador (script/CI) decide o que fazer com o exit code.

    `inst` já deve vir com os campos resolvidos e normalizados (mesmo
    dataclass que `_setup_sei_instance` monta no wizard interativo) — quem
    chama (tipicamente o parsing de flags de CLI) decide como obtê-los.
    """
    if not force:
        env = _read_existing_todos_env()
        if env is not None:
            output.emit_human(
                "[bold red]MCP 'todos' já está configurado.[/] Use --force para reconfigurar "
                "do zero, ou 'todos set-password' para trocar só a senha."
            )
            sys.exit(1)

    conn = _SEIConnConfig(
        sei_root=inst.sei_root,
        usuario=usuario,
        senha=senha,
        sigla_orgao=inst.sigla_orgao,
        sigla_orgao_sistema=inst.sigla_orgao_sistema,
        sigla_sistema=inst.sigla_sistema,
        verify_ssl_disabled=inst.verify_ssl_disabled,
    )
    _validate_credentials_headless(conn)

    keyring_user = _compute_keyring_user(usuario, inst.sei_root)
    _save_password_headless(keyring_user, senha)

    # senha="" (não o valor real): keyring já gravou a senha acima, e SEI_SENHA
    # vazio no mcp_env é o sinal (mesma convenção do wizard interativo) de que
    # o runtime deve resolver a senha via keyring, não via env var em texto claro.
    mcp_env, using_plaintext_password = _build_mcp_env(inst, usuario, "")
    _deploy_mcp_configs(mcp_env, using_plaintext_password=using_plaintext_password)
    output.emit_result(
        f"todos configurado sem interação (usuário: {usuario!r}, SEI: {inst.sei_root!r})."
    )
