"""Validação de destino para o modo HTTP (deploy remoto multiusuário).

No modo stdio a URL do SEI vem do ambiente de quem roda o MCP. No modo HTTP ela
vem de um formulário público — e o servidor faz requisições para ela e devolve
trechos da resposta nos erros. Sem validação isso é SSRF: um atacante aponta a
URL para a rede interna, o endpoint de metadados da nuvem (169.254.169.254) ou
um servidor dele, e lê o que voltar.

Duas defesas moram aqui:

1. `validar_url_sei` — só aceita https, host permitido (`SEI_ALLOWED_HOSTS`)
   e que resolva exclusivamente para IPs públicos.
2. `host_recebe_segredos` — os segredos do OPERADOR (`SEI_EXTRA_HEADERS`,
   `SEI_CF_CLEARANCE`) só acompanham requisições ao host para o qual foram
   criados. Antes iam para qualquer URL informada pelo usuário.

Limitação conhecida: a checagem de IP é feita na validação, não a cada conexão.
Um DNS com TTL curto que troque de IP entre a validação e o uso (DNS rebinding)
escapa dela. `SEI_ALLOWED_HOSTS` fecha essa porta — use-o em produção.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


def _lista_env(nome: str) -> list[str]:
    return [p.strip().lower() for p in os.environ.get(nome, "").split(",") if p.strip()]


def _casa_host(host: str, padroes: list[str]) -> bool:
    """`sei.orgao.gov.br` exato, ou `*.gov.br` / `.gov.br` para sufixo."""
    host = host.lower().rstrip(".")
    for p in padroes:
        if p in ("*", "*.*"):
            return True
        if p.startswith("*."):
            p = p[1:]
        if p.startswith("."):
            if host.endswith(p):
                return True
        elif host == p:
            return True
    return False


def hosts_permitidos() -> list[str]:
    """`SEI_ALLOWED_HOSTS` (vírgula). Vazio = qualquer host público."""
    return _lista_env("SEI_ALLOWED_HOSTS")


def _ip_publico(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def _checar_forma(url: str) -> tuple[str | None, str]:
    """Checagens sem rede. Retorna (erro, host)."""
    try:
        u = urlparse(url)
    except ValueError:
        return "URL inválida.", ""
    if u.scheme != "https":
        return "A URL do SEI precisa usar https://.", ""
    host = (u.hostname or "").lower()
    if not host:
        return "A URL do SEI não tem host.", ""
    if u.username or u.password:
        return "A URL do SEI não pode conter usuário/senha embutidos.", ""
    permitidos = hosts_permitidos()
    if permitidos and not _casa_host(host, permitidos):
        return (
            f"O host {host} não está entre os SEI aceitos por este servidor. "
            "Peça ao administrador para incluí-lo em SEI_ALLOWED_HOSTS.",
            host,
        )
    return None, host


def validar_url_sei_sem_rede(url: str) -> str | None:
    """Esquema + allowlist. Barato; usado a cada criação de cliente."""
    erro, _ = _checar_forma(url)
    return erro


async def validar_url_sei(url: str) -> str | None:
    """Validação completa (inclui DNS). Retorna mensagem de erro ou None."""
    erro, host = _checar_forma(url)
    if erro:
        return erro
    import anyio

    try:
        infos = await anyio.to_thread.run_sync(
            lambda: socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        )
    except OSError:
        return f"Não foi possível resolver o host {host}."
    ips = {info[4][0] for info in infos}
    if not ips or not all(_ip_publico(ip) for ip in ips):
        return f"O host {host} resolve para um endereço não público — recusado."
    return None


def host_recebe_segredos(url: str) -> bool:
    """Os segredos do operador podem ir para este host?

    Aceita os hosts de `SEI_SECRET_HOSTS` (vírgula, mesmo formato de
    SEI_ALLOWED_HOSTS) ou, na falta dele, o host de `SEI_URL` — que é para
    onde o operador configurou o bypass.
    """
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    padroes = _lista_env("SEI_SECRET_HOSTS")
    if not padroes:
        host_env = (urlparse(os.environ.get("SEI_URL", "")).hostname or "").lower()
        padroes = [host_env] if host_env else []
    return _casa_host(host, padroes)
