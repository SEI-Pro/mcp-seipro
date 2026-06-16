"""Wizard de configuração interativo para o MCP SEI (todos)."""

import asyncio
import concurrent.futures
import contextlib
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

from todos.exceptions import SEIAuthError, SEICredenciaisError
from todos.sei_web_client import SEIWebClient

# Named constants for magic values
_MAX_ACRONYM_LEN: int = 6
_MAX_ANCESTOR_SEARCH: int = 5
_MIN_HOSTNAME_PARTS: int = 2
# mod-wssei can be installed as "wssei/" or "mod-wssei/" depending on how the admin
# extracted the release archive (see github.com/pengovbr/mod-wssei/issues/46).
_WSSEI_API_PATHS: tuple[str, ...] = (
    "/sei/modulos/wssei/controlador_ws.php/api/v2",
    "/sei/modulos/mod-wssei/controlador_ws.php/api/v2",
)
_WSSEI_PROBE_TIMEOUT: float = 8.0


@dataclass
class _SEIConnConfig:
    """Holds the SEI connection parameters used during credential validation."""

    sei_root: str
    usuario: str
    senha: str
    sigla_orgao: str
    sigla_orgao_sistema: str
    sigla_sistema: str
    verify_ssl_disabled: bool


# Suporte a cores no terminal
def print_cyan(text: str) -> None:
    """Print text in cyan color to stdout."""
    sys.stdout.write(f"\033[96m{text}\033[0m\n")


def print_green(text: str) -> None:
    """Print text in green color to stdout."""
    sys.stdout.write(f"\033[92m{text}\033[0m\n")


def print_yellow(text: str) -> None:
    """Print text in yellow color to stdout."""
    sys.stdout.write(f"\033[93m{text}\033[0m\n")


def print_red(text: str) -> None:
    """Print text in red color to stdout."""
    sys.stdout.write(f"\033[91m{text}\033[0m\n")


def _detect_organs(
    login_url: str,
    sigla_orgao_sistema: str,
    sigla_sistema: str,
) -> tuple[list[tuple[str, str]], bool, str, str]:
    """Try to auto-detect available organs from the SEI login page.

    Returns (organs, verify_ssl_disabled, sigla_orgao_sistema, sigla_sistema).
    """
    organs: list[tuple[str, str]] = []
    verify_ssl_disabled = False

    resp = None
    try:
        with httpx.Client(verify=True, follow_redirects=True, timeout=10.0) as client:
            resp = client.get(login_url)
            resp.raise_for_status()
    except httpx.RequestError:
        print_yellow("[!] Falha ao estabelecer conexão SSL segura com o SEI.")
        print_yellow(
            "    Isso ocorre comumente em redes governamentais com proxies ou certificados internos."
        )
        print_red("=" * 70)
        print_red("AVISO DE SEGURANÇA — RISCO DE INTERCEPTAÇÃO DE CREDENCIAIS")
        print_red("=" * 70)
        print_red("A verificação SSL protege contra ataques man-in-the-middle (MITM).")
        print_red("Desabilitá-la permite que um atacante intercepte suas credenciais")
        print_red("(usuário e senha) em trânsito, sem que você perceba.")
        print_red("Use esta opção SOMENTE em redes internas onde você controla a CA")
        print_red("(ex: rede corporativa com certificado auto-assinado próprio).")
        print_red("=" * 70)
        confirm_ssl = (
            input("Deseja tentar a conexão desativando a verificação de certificado SSL? (s/n): ")
            .strip()
            .lower()
        )
        if confirm_ssl == "s":
            verify_ssl_disabled = True
            ssl_verify: bool = False  # user explicitly disabled SSL verification
            with httpx.Client(verify=ssl_verify, follow_redirects=True, timeout=10.0) as client:
                resp = client.get(login_url)
                resp.raise_for_status()
        else:
            raise
    except httpx.HTTPStatusError as e:
        print_yellow(
            f"[!] SEI retornou HTTP {e.response.status_code} na página de login. "
            "Continuando sem detecção automática de órgãos."
        )

    if resp is not None:
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
                if (
                    isinstance(val, str)
                    and val != "null"
                    and not text.startswith(("-", "Selecione"))
                ):
                    organs.append((val, text))

    return organs, verify_ssl_disabled, sigla_orgao_sistema, sigla_sistema


def _resolve_organ_from_list(
    organs: list[tuple[str, str]],
) -> tuple[str, str]:
    """Prompt user to select an organ from the detected list. Returns (orgao_id, sigla_orgao)."""
    print_green("[+] Órgãos detectados com sucesso no seu SEI:")
    for idx, (val, name) in enumerate(organs, 1):
        sys.stdout.write(f"  [{idx}] {name} (ID: {val})\n")

    selection = input(f"Selecione o seu órgão [1-{len(organs)}] (padrão: 1): ").strip()
    if selection.isdigit() and 1 <= int(selection) <= len(organs):
        selected_idx = int(selection) - 1
    else:
        selected_idx = 0

    orgao_id, sigla_orgao = organs[selected_idx]

    # Limpar espaços ou traços do nome para obter apenas a sigla limpa (ex: "PGE-RO" -> "PGE")
    parts = [p.strip() for p in re.split(r"\s*-\s*", sigla_orgao) if p.strip()]

    ufs = {
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
    if len(parts) > 1:
        parts_without_uf = [p for p in parts if p.upper() not in ufs]
        if parts_without_uf:
            parts = parts_without_uf

    if len(parts) > 1:
        acronyms = [p for p in parts if p.isupper() and len(p) <= _MAX_ACRONYM_LEN]
        sigla_orgao = acronyms[0] if acronyms else parts[0]
    else:
        sigla_orgao = parts[0] if parts else sigla_orgao

    return orgao_id, sigla_orgao


def _resolve_organ_manual(
    sigla_orgao_sistema: str,
) -> tuple[str, str, str, str]:
    """Prompt user to enter organ details manually.

    Returns (sigla_orgao, sigla_orgao_sistema, orgao_id, default_sigla_sistema).
    """
    default_sigla = sigla_orgao_sistema or "PGE"
    sigla_orgao = (
        input(f"Digite a sigla do seu órgão no SEI (padrão: {default_sigla}): ").strip()
        or default_sigla
    )
    default_sigla_sistema = sigla_orgao_sistema or "RO"
    sigla_orgao_sistema = (
        input(f"Digite a sigla do órgão no sistema (padrão: {default_sigla_sistema}): ").strip()
        or default_sigla_sistema
    )
    default_id = "9" if default_sigla_sistema == "RO" else "0"
    orgao_id = input(f"Digite o ID do órgão (padrão: {default_id}): ").strip() or default_id
    return sigla_orgao, sigla_orgao_sistema, orgao_id, default_sigla_sistema


def _compute_keyring_user(usuario: str, sei_root: str) -> str:
    """Monta a chave do keyring igual ao client: '<usuario>@<host>' (host sem scheme)."""
    instance_url = (
        sei_root.replace("https://", "").replace("http://", "").strip().rstrip("/").lower()
    )
    return f"{usuario}@{instance_url}" if instance_url else usuario


def _read_existing_todos_env() -> dict[str, str] | None:
    """Retorna o `env` do mcpServers.todos em ~/.claude.json, ou None se não configurado."""
    config_path = Path.home() / ".claude.json"
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
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
    """Store password in system keyring and return (senha_for_config, senha_validacao).

    Returns (senha_config, senha_validacao) where senha_config is empty string
    if keyring succeeded (password lives in keyring), or the original password
    if keyring failed and user opted to use plaintext.
    """
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_keyring.set_password, "todos-mcp", keyring_user, senha)
            try:
                future.result(timeout=10)
            except concurrent.futures.TimeoutError as exc:
                msg = (
                    "Keyring bloqueou por mais de 10 segundos. "
                    "Tente: PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring todos setup"
                )
                raise RuntimeError(msg) from exc
        print_green("[+] Senha armazenada com sucesso no Keyring do Sistema!")
    except (RuntimeError, OSError, ValueError) as e:
        print_red(f"[ERRO] Falha ao acessar o Keyring do Sistema: {e}")
        print_yellow("[!] A senha não pôde ser salva de forma segura no Keyring nativo.")
        confirm = (
            input("Deseja salvar a senha em texto limpo nas configurações? (s/n): ").strip().lower()
        )
        if confirm != "s":
            print_red("[ERRO] Cancelado pelo usuário.")
            sys.exit(1)
        return senha, senha  # plaintext fallback
    else:
        # set_password worked — try to read back to confirm
        lida: str | None = None
        try:
            lida = _keyring.get_password("todos-mcp", keyring_user)
        except (OSError, ValueError):
            print_yellow(
                "[!] Não foi possível ler de volta do Keyring. Usando senha local para validação."
            )
        if lida:
            print_green("[+] Validação do Keyring concluída com sucesso (leitura OK)!")
            return "", lida  # keyring has it; empty in config
        print_yellow(
            "[!] Alerta: O Keyring confirmou a gravação, mas retornou vazio na leitura de teste."
        )
        print_yellow("    Usando senha local para a validação.")
        return "", senha  # keyring has it; use local for validation only


def _confirmar_ou_abortar(msg_erro: str) -> None:
    """Exibe mensagem de erro e pergunta se o usuário quer continuar mesmo assim."""
    print_red(msg_erro)
    if input("Deseja gravar as configurações mesmo assim? (s/n): ").strip().lower() != "s":
        print_red("[ERRO] Configuração cancelada pelo usuário.")
        sys.exit(1)


def _validate_credentials(conn: _SEIConnConfig) -> None:
    """Perform a test login to validate SEI credentials. Prompts user on failure."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("todos").setLevel(logging.WARNING)

    web_client = SEIWebClient(
        sei_web_url=conn.sei_root,
        sei_usuario=conn.usuario,
        sei_senha=conn.senha,
        sei_sigla_orgao=conn.sigla_orgao,
        sei_sigla_orgao_sistema=conn.sigla_orgao_sistema,
        sei_sigla_sistema=conn.sigla_sistema,
        sei_verify_ssl=not conn.verify_ssl_disabled,
    )

    async def do_test_login() -> dict:
        try:
            await web_client.ensure_authenticated()
            info: dict = {
                "nome": web_client.nome_usuario,
                "id": web_client.id_usuario,
                "orgao": web_client.orgao_usuario,
                "unidade": {},
            }
            with contextlib.suppress(Exception):
                info["unidade"] = await web_client.unidade_atual()
            return info
        finally:
            await web_client.close()

    try:
        client_info = asyncio.run(do_test_login())
        web_client.limpar_senha()
        print_green("[+] Credenciais validadas com sucesso no SEI!")
        if client_info:
            nome_usuario = client_info.get("nome") or conn.usuario
            id_usuario = client_info.get("id") or "desconhecido"
            orgao_usuario = client_info.get("orgao") or conn.sigla_orgao
            unidade = client_info.get("unidade") or {}
            sigla_unid = unidade.get("sigla") or "N/A"
            nome_unid = unidade.get("nome") or "N/A"
            id_unid = unidade.get("id_unidade") or "N/A"
            print_green(f"    Usuário: {nome_usuario} (ID: {id_usuario}, Órgão: {orgao_usuario})")
            print_green(f"    Unidade Ativa: {sigla_unid} - {nome_unid} (ID: {id_unid})")
    except SEICredenciaisError as e:
        web_client.limpar_senha()
        _confirmar_ou_abortar(f"\n[ERRO] {e}")
    except SEIAuthError as e:
        web_client.limpar_senha()
        _confirmar_ou_abortar(f"\n[ERRO] Falha de autenticação no SEI: {e}")
    except (OSError, ValueError, RuntimeError) as e:
        web_client.limpar_senha()
        print_yellow(
            "[!] O login no SEI falhou. Pode ser que o usuário, senha ou órgão estejam incorretos."
        )
        _confirmar_ou_abortar(f"[ERRO] Falha na validação das credenciais no SEI: {e}")


def _mcp_add_via_cli(
    claude_cli: str,
    todos_cmd: str,
    mcp_env: dict[str, str],
    scope: str,
    cwd: Path | None = None,
) -> bool:
    """Register the MCP server via `claude mcp add`. Returns True on success."""
    env_args = [item for k, v in mcp_env.items() for item in ("-e", f"{k}={v}")]
    cmd = [claude_cli, "mcp", "add", "-s", scope, *env_args, "todos", todos_cmd]
    cwd_str = str(cwd or Path.cwd())
    try:
        # S603: claude_cli is an absolute path from shutil.which("claude") — not user input.
        # cmd elements are string literals, MCP env key=value pairs, and todos_cmd (which comes
        # from shutil.which("todos") or a hard-coded fallback "todos") — all controlled values.
        _sp.run(cmd, check=True, capture_output=True, text=True, cwd=cwd_str)
    except _sp.CalledProcessError as e:
        if "already exists" not in (e.stderr or "") and "already exists" not in (e.stdout or ""):
            return False
        with contextlib.suppress(_sp.CalledProcessError):
            # S603: same rationale as above — claude_cli is an absolute path from shutil.which.
            _sp.run(
                [claude_cli, "mcp", "remove", "-s", scope, "todos"],
                check=True,
                capture_output=True,
                text=True,
                cwd=cwd_str,
            )
            _sp.run(cmd, check=True, capture_output=True, text=True, cwd=cwd_str)
            return True
        return False
    except OSError:
        return False
    else:
        return True


def _mcp_add_via_json(config_path: Path, todos_cmd: str, mcp_env: dict[str, str]) -> bool:
    """Edit the MCP config JSON directly as a fallback. Returns True on success."""
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
    except (OSError, json.JSONDecodeError):
        return False
    else:
        return True


def _update_antigravity(todos_cmd: str, mcp_env: dict[str, str]) -> None:
    """Update Antigravity IDE MCP config files if they exist."""
    home = Path.home()
    for ag_path in [
        home / ".gemini" / "antigravity-ide" / "mcp_config.json",
        home / ".gemini" / "config" / "mcp_config.json",
    ]:
        if ag_path.exists() or ag_path.parent.exists():
            if _mcp_add_via_json(ag_path, todos_cmd, mcp_env):
                print_green(f"[+] Atualizado: {ag_path}")
            else:
                print_yellow(f"[!] Não foi possível atualizar {ag_path}")


def _claude_desktop_path() -> Path:
    """Return the platform-specific Claude Desktop config path."""
    home = Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        return appdata / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def _update_claude_desktop(todos_cmd: str, mcp_env: dict[str, str]) -> None:
    """Update Claude Desktop config file if the directory exists."""
    claude_desktop = _claude_desktop_path()
    if claude_desktop.parent.exists():
        if _mcp_add_via_json(claude_desktop, todos_cmd, mcp_env):
            print_green(f"[+] Atualizado: {claude_desktop}")
        else:
            print_yellow(f"[!] Não foi possível atualizar {claude_desktop}")


def _update_claude_code_global(
    claude_cli: str | None, todos_cmd: str, mcp_env: dict[str, str]
) -> None:
    """Update Claude Code global config via CLI or direct JSON edit."""
    home = Path.home()
    if claude_cli and _mcp_add_via_cli(claude_cli, todos_cmd, mcp_env, "user"):
        print_green("[+] Atualizado: Claude Code (global) via `claude mcp add -s user`")
    elif _mcp_add_via_json(home / ".claude.json", todos_cmd, mcp_env):
        print_green(f"[+] Atualizado: {home / '.claude.json'}")
    else:
        print_yellow(f"[!] Não foi possível atualizar {home / '.claude.json'}")


def _update_workspace_local(
    claude_cli: str | None,
    todos_cmd: str,
    mcp_env: dict[str, str],
    *,
    using_plaintext_password: bool,
) -> None:
    """Update workspace-local .mcp.json if a project root is found."""
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
        print_yellow(
            f"[!] Pulando {mcp_json}: senha em texto claro não deve "
            "ser gravada em arquivo de projeto (pode ser enviada ao controle de versão)."
        )
    elif claude_cli and _mcp_add_via_cli(
        claude_cli, todos_cmd, mcp_env, "project", cwd=workspace_dir
    ):
        print_green(f"[+] Atualizado: {mcp_json} via `claude mcp add -s project`")
    elif _mcp_add_via_json(mcp_json, todos_cmd, mcp_env):
        print_green(f"[+] Atualizado: {mcp_json}")
    else:
        print_yellow(f"[!] Não foi possível atualizar {mcp_json}")


def _update_codex_via_cli(codex_cli: str, todos_cmd: str, mcp_env: dict[str, str]) -> bool:
    """Try to add via `codex mcp add`. Returns True if successful."""
    env_args = [item for k, v in mcp_env.items() for item in ("--env", f"{k}={v}")]
    cmd_codex = [codex_cli, "mcp", "add", "todos", *env_args, "--", todos_cmd]
    try:
        # S603: codex_cli is an absolute path from shutil.which("codex") — not user input.
        # cmd_codex elements are string literals, MCP env key=value pairs, and todos_cmd
        # (shutil.which("todos") or "todos" fallback) — all controlled values.
        _sp.run(cmd_codex, check=True, capture_output=True, text=True)
    except _sp.CalledProcessError as e:
        if "already" in (e.stderr or "") or "already" in (e.stdout or ""):
            with contextlib.suppress(_sp.CalledProcessError):
                # S603: same rationale — codex_cli is an absolute path from shutil.which.
                _sp.run(
                    [codex_cli, "mcp", "remove", "todos"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                _sp.run(cmd_codex, check=True, capture_output=True, text=True)
                print_green("[+] Atualizado: Codex (global) via `codex mcp add`")
                return True
        return False
    except OSError:
        return False
    else:
        print_green("[+] Atualizado: Codex (global) via `codex mcp add`")
        return True


def _update_codex_via_toml(codex_config: Path, todos_cmd: str, mcp_env: dict[str, str]) -> None:
    """Edit ~/.codex/config.toml directly as a fallback for Codex CLI."""
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
        print_green(f"[+] Atualizado: {codex_config}")
    except OSError as e_codex:
        print_yellow(f"[!] Não foi possível atualizar {codex_config}: {e_codex}")


def _update_codex(todos_cmd: str, mcp_env: dict[str, str]) -> None:
    """Update Codex CLI config via CLI or direct TOML edit."""
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
    """Update all known MCP configuration files for Claude Code, Claude Desktop, Codex CLI."""
    _update_antigravity(todos_cmd, mcp_env)
    _update_claude_desktop(todos_cmd, mcp_env)
    _update_claude_code_global(claude_cli, todos_cmd, mcp_env)
    _update_workspace_local(
        claude_cli, todos_cmd, mcp_env, using_plaintext_password=using_plaintext_password
    )
    _update_codex(todos_cmd, mcp_env)


@dataclass
class _SEIInstanceConfig:
    """Holds the resolved SEI instance parameters gathered during setup step 1."""

    sei_root: str
    rest_url: str
    sigla_orgao: str
    sigla_orgao_sistema: str
    sigla_sistema: str
    orgao_id: str
    verify_ssl_disabled: bool


def _infer_sigla_orgao_sistema(hostname: str) -> str:
    """Infer the sigla_orgao_sistema from the SEI hostname."""
    if "ro.gov.br" in hostname:
        return "RO"
    parts = hostname.split(".")
    if len(parts) >= _MIN_HOSTNAME_PARTS:
        if parts[0] in ("sip", "sei") and parts[1] not in ("gov", "com", "org", "net", "edu"):
            return parts[1].upper()
        if parts[0] not in ("gov", "com", "org", "net", "edu"):
            return parts[0].upper()
    return "SEI"


@dataclass
class _ModseiDetection:
    """Result of probing for mod-wssei."""

    url: str
    confirmed: bool  # False when Cloudflare blocked the probe; url is a best-guess


_logger_setup = logging.getLogger(__name__)


def _is_cloudflare_response(resp: httpx.Response) -> bool:
    """Return True if the response came from an edge/CDN proxy, not from the PHP app.

    §35.3 — Uses multiple signals via any() to avoid false negatives:
    - cf-ray header: Cloudflare primary signal
    - server: cloudflare header: Cloudflare secondary signal
    - x-amzn-requestid header: AWS API Gateway / CloudFront signal
    """
    signals = {
        "cf-ray": "cf-ray" in resp.headers,
        "server:cloudflare": resp.headers.get("server", "").lower() == "cloudflare",
        "x-amzn-requestid": "x-amzn-requestid" in resp.headers,
    }
    detected = [name for name, found in signals.items() if found]
    for name in detected:
        _logger_setup.debug("Edge/CDN proxy signal detected in response: %s", name)
    return any(signals.values())


def _detect_modsei_url(sei_root: str, *, verify_ssl: bool) -> _ModseiDetection:
    """Probe both known mod-wssei paths and return the first that responds.

    Uses POST /autenticar with empty credentials as the probe: the module
    returns 400/422 (invalid credentials) when present, while Apache returns
    a plain 404 when the module is not installed at all.
    Tries "wssei/" first (most common), then "mod-wssei/" (alternative install name).
    When Cloudflare blocks the probe, returns the candidate URL as unconfirmed
    (confirmed=False) rather than a false negative or false positive.
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


def _setup_sei_instance() -> _SEIInstanceConfig:
    """Prompt the user for the SEI URL and organ, returning a resolved instance config."""
    print_yellow("[*] Configuração da URL e Instância do SEI")
    web_url_input = (
        input("Digite ou cole a URL do seu SEI (ex: https://sei.sistemas.ro.gov.br): ").strip()
        or "https://sei.sistemas.ro.gov.br"
    )

    if not web_url_input.startswith(("http://", "https://")):
        web_url_input = "https://" + web_url_input

    parsed = urlparse(web_url_input)
    if not parsed.netloc or ":" in parsed.netloc:
        print_red("[ERRO] URL inválida. Use o formato: https://sei.exemplo.gov.br")
        sys.exit(1)
    sei_root = f"{parsed.scheme}://{parsed.netloc}"
    query = parse_qs(parsed.query)

    if "sigla_orgao_sistema" in query:
        sigla_orgao_sistema: str = query["sigla_orgao_sistema"][0]
    else:
        sigla_orgao_sistema = _infer_sigla_orgao_sistema(parsed.netloc.lower())

    sigla_sistema = query.get("sigla_sistema", ["SEI"])[0]
    login_url = (
        f"{sei_root}/sip/login.php"
        f"?sigla_orgao_sistema={sigla_orgao_sistema}&sigla_sistema={sigla_sistema}"
    )

    organs: list[tuple[str, str]] = []
    verify_ssl_disabled = False
    print_yellow("[*] Tentando detectar os órgãos disponíveis no SEI...")
    try:
        organs, verify_ssl_disabled, sigla_orgao_sistema, sigla_sistema = _detect_organs(
            login_url, sigla_orgao_sistema, sigla_sistema
        )
    except (httpx.RequestError, httpx.HTTPStatusError) as e:
        print_yellow(f"[!] Não foi possível conectar ao login do SEI para listar órgãos: {e}")

    if organs:
        orgao_id, sigla_orgao = _resolve_organ_from_list(organs)
    else:
        sigla_orgao, sigla_orgao_sistema, orgao_id, _ = _resolve_organ_manual(sigla_orgao_sistema)

    print_green(f"[+] Configurado para o órgão: {sigla_orgao} (ID: {orgao_id})")

    sys.stdout.write("\n")
    print_yellow("[*] Detectando mod-wssei...")
    detection = _detect_modsei_url(sei_root, verify_ssl=not verify_ssl_disabled)
    if detection.url and detection.confirmed:
        print_green(f"[+] mod-wssei detectado em: {detection.url}")
        confirm = input("Usar esta URL REST? (s/n, padrão: s): ").strip().lower() or "s"
        rest_url = (
            detection.url
            if confirm == "s"
            else input(
                "Digite a URL REST do mod-wssei (ou deixe em branco para desativar): "
            ).strip()
        )
    elif detection.url:
        print_yellow(f"[!] Possível mod-wssei em: {detection.url}")
        print_yellow(
            "    (Cloudflare bloqueou a verificação automática — confirme se mod-wssei está instalado.)"
        )
        confirm = input("Usar esta URL REST? (s/n, padrão: s): ").strip().lower() or "s"
        rest_url = (
            detection.url
            if confirm == "s"
            else input(
                "Digite a URL REST do mod-wssei (ou deixe em branco para desativar): "
            ).strip()
        )
    else:
        print_yellow("[!] mod-wssei não detectado automaticamente nesta instância.")
        rest_url = input(
            "Digite a URL REST do mod-wssei manualmente (ou deixe em branco se não instalado): "
        ).strip()

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
    """Prompt for SEI user/password and store in keyring.

    Returns (usuario, senha_for_config, senha_validacao).
    """
    sys.stdout.write("\n")
    print_yellow("[*] Configuração de Usuário e Senha")
    usuario = input("Digite seu usuário do SEI (geralmente CPF ou iniciais): ").strip()
    if not usuario:
        print_red("[ERRO] Usuário é obrigatório.")
        sys.exit(1)

    senha = getpass.getpass("Digite sua senha do SEI (entrada oculta): ")
    if not senha:
        print_red("[ERRO] Senha é obrigatória.")
        sys.exit(1)

    sys.stdout.write("\n")
    print_yellow("[*] Gravando senha com segurança no Keyring do Sistema...")
    keyring_user = _compute_keyring_user(usuario, sei_root)
    senha_config, senha_validacao = _save_password_to_keyring(keyring_user, senha)
    return usuario, senha_config, senha_validacao


def run_setup_wizard(*, force: bool = False) -> None:
    """Run the interactive setup wizard to configure the MCP SEI server.

    Idempotente: se o MCP `todos` já está configurado e `force` é False, mostra
    o estado atual e sai SEM sobrescrever (evita clobbar a config do MCP). Use
    `--force` para reconfigurar do zero, ou `todos set-password` para trocar só
    a senha.
    """
    if not sys.stdin.isatty():
        print_red("[ERRO] 'todos setup' requer um terminal interativo (stdin não é um TTY).")
        sys.exit(1)
    if not force:
        env = _read_existing_todos_env()
        if env is not None:
            print_yellow("[!] MCP 'todos' já está configurado em ~/.claude.json:")
            sys.stdout.write(f"    usuário: {env.get('SEI_USUARIO', '?')}\n")
            sys.stdout.write(f"    SEI:     {env.get('SEI_WEB_URL') or env.get('SEI_URL', '?')}\n")
            sys.stdout.write(f"    órgão:   {env.get('SEI_SIGLA_ORGAO', '?')}\n")
            sys.stdout.write("\n")
            print_cyan("    Trocar SÓ a senha:   todos set-password")
            print_cyan("    Reconfigurar tudo:   todos setup --force")
            return
    print_cyan("=====================================================")
    print_cyan("  Configurador do MCP SEI (todos)")
    print_cyan("=====================================================")
    sys.stdout.write("\n")

    # 1. Configurar URL, instância e órgão do SEI
    inst = _setup_sei_instance()

    # 2. Obter usuário e senha + Keyring
    usuario, senha, senha_validacao = _setup_credentials(inst.sei_root)

    # 3. Validar as credenciais efetuando um login de teste
    sys.stdout.write("\n")
    print_yellow("[*] Validando credenciais com o SEI...")
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

    # 4. Preparar variáveis de ambiente MCP
    mcp_env: dict[str, str] = {
        "SEI_URL": inst.rest_url,
        "SEI_WEB_URL": inst.sei_root,
        "SEI_SIGLA_ORGAO": inst.sigla_orgao,
        "SEI_SIGLA_ORGAO_SISTEMA": inst.sigla_orgao_sistema,
        "SEI_SIGLA_SISTEMA": inst.sigla_sistema,
        "SEI_USUARIO": usuario,
        "SEI_SENHA": senha,  # Vazio se keyring foi usado
        "SEI_ORGAO": inst.orgao_id,
    }
    if inst.verify_ssl_disabled:
        mcp_env["SEI_VERIFY_SSL"] = "false"
    using_plaintext_password = bool(mcp_env["SEI_SENHA"])
    senha = ""
    del senha

    # 5. Atualizar as configurações
    sys.stdout.write("\n")
    print_yellow("[*] Atualizando arquivos de configuração MCP...")
    claude_cli = shutil.which("claude")
    todos_cmd = shutil.which("todos") or "todos"
    _update_mcp_configs(
        claude_cli, todos_cmd, mcp_env, using_plaintext_password=using_plaintext_password
    )
    mcp_env["SEI_SENHA"] = ""

    sys.stdout.write("\n")
    print_cyan("=====================================================")
    print_green("  Configuração concluída com sucesso!")
    print_green("  Agora você já pode iniciar o Antigravity, Claude ou Codex.")
    print_cyan("=====================================================")


def run_set_password() -> None:
    """Atualiza SOMENTE a senha no keyring — não toca na config do MCP.

    Lê usuário/URL da config existente (~/.claude.json), grava a nova senha no
    keyring e valida com um login real. Use quando só quer trocar a senha sem
    risco de reconfigurar o MCP.
    """
    if not sys.stdin.isatty():
        print_red("[ERRO] 'todos set-password' requer um terminal interativo (stdin não é um TTY).")
        sys.exit(1)
    env = _read_existing_todos_env()
    if env is None:
        print_red(
            "[ERRO] MCP 'todos' não está configurado em ~/.claude.json. Rode 'todos setup' primeiro."
        )
        sys.exit(1)
    usuario = (env.get("SEI_USUARIO") or "").strip()
    sei_root = (env.get("SEI_WEB_URL") or env.get("SEI_URL") or "").strip()
    if not usuario or not sei_root:
        print_red("[ERRO] SEI_USUARIO/SEI_WEB_URL ausentes na config. Rode 'todos setup'.")
        sys.exit(1)
    keyring_user = _compute_keyring_user(usuario, sei_root)

    print_cyan("=====================================================")
    print_cyan(f"  Atualizar senha do SEI ({keyring_user})")
    print_cyan("=====================================================")
    senha = getpass.getpass("Nova senha do SEI: ").strip()
    if not senha:
        print_red("[ERRO] Senha vazia.")
        sys.exit(1)

    print_yellow("[*] Gravando no Keyring do Sistema...")
    try:
        _keyring.set_password("todos-mcp", keyring_user, senha)
        lida = _keyring.get_password("todos-mcp", keyring_user)
    except (RuntimeError, OSError, ValueError, ImportError, AttributeError) as exc:
        print_red(f"[ERRO] Não foi possível gravar no keyring: {exc}")
        sys.exit(1)
    if lida != senha:
        print_red("[ERRO] Falha ao confirmar a senha no keyring (readback divergente).")
        sys.exit(1)

    print_yellow("[*] Validando com login real no SEI...")
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
    print_green(f"[+] Senha atualizada no keyring ({keyring_user}). Config do MCP intacta.")
