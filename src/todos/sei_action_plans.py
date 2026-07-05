"""Declarative, no-JS action plans for pages served by the SEI frontend.

This module intentionally implements only a small, statically verifiable
subset of SEI's JavaScript idioms. It never evaluates JavaScript supplied by
a caller.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from todos.exceptions import (
    SEIConnectionError,
    SEINotFoundError,
    SEIParseError,
    SEIValidationError,
)
from todos.sei_web_client import (
    SEIWebClient,
    _check,
    _decode_response,
    _extrair_erro_sei,
    _is_login_page,
)

_PAGE_TTL_SECONDS = 120.0
_MAX_REDIRECTS = 5

# GET is not a synonym for read in SEI. Inspection follows only these page-like
# routes. Unknown routes must be reached through a typed tool or a plan that
# was discovered on an inspected page.
_READ_ACTION_EXACT = frozenset(
    {
        "arvore_visualizar",
        "arvore_montar",
        "documento_consultar",
        "documento_visualizar",
        "editor_montar",
        "documento_escolher_tipo",
        "procedimento_consultar",
        "procedimento_consultar_historico",
        "andamento_marcador_gerenciar",
        "acompanhamento_gerenciar",
        "rel_bloco_protocolo_listar",
        "bloco_assinatura_listar",
        "bloco_interno_listar",
        "procedimento_sobrestado_listar",
    }
)
_READ_ACTION_SUFFIXES = ("_listar", "_consultar", "_visualizar", "_montar", "_gerenciar")
_DESTRUCTIVE_MARKERS = (
    "excluir",
    "remover",
    "retirar",
    "cancelar",
    "concluir",
    "sobrestar",
)
_WRITE_MARKERS = (
    "salvar",
    "cadastrar",
    "alterar",
    "assinar",
    "enviar",
    "tramitar",
    "reabrir",
    "registrar",
    "disponibilizar",
    "atribuir",
    "marcar",
    "desmarcar",
)


@dataclass(frozen=True)
class _Snapshot:
    url: str
    referer: str
    fingerprint: str
    created_at: float


@dataclass
class _Store:
    snapshots: dict[str, _Snapshot]
    lock: asyncio.Lock


def _store(client: SEIWebClient) -> _Store:
    store = getattr(client, "_sei_action_plan_store", None)
    if not isinstance(store, _Store):
        store = _Store(snapshots={}, lock=asyncio.Lock())
        setattr(client, "_sei_action_plan_store", store)
    return store


def _action_name(url: str) -> str:
    return parse_qs(urlparse(url).query).get("acao", [""])[0]


def _risk(action: str) -> str:
    normalized = action.casefold()
    if not normalized:
        return "write"
    if any(marker in normalized for marker in _DESTRUCTIVE_MARKERS):
        return "destructive"
    if any(marker in normalized for marker in _WRITE_MARKERS):
        return "write"
    if normalized in _READ_ACTION_EXACT or normalized.endswith(_READ_ACTION_SUFFIXES):
        return "read"
    # Unknown controller actions remain conservative until a typed tool gives
    # them a more precise classification.
    return "write"


def _is_read_action(action: str) -> bool:
    return _risk(action) == "read"


def _same_origin(client: SEIWebClient, url: str) -> bool:
    root = urlparse(str(client.sei_root))
    candidate = urlparse(url)
    return (
        candidate.scheme.casefold() == root.scheme.casefold()
        and candidate.netloc.casefold() == root.netloc.casefold()
    )


def _absolute_and_local(client: SEIWebClient, base_url: str, maybe_relative: str) -> str:
    url = urljoin(base_url, maybe_relative.replace("&amp;", "&"))
    if not _same_origin(client, url):
        raise SEIValidationError("A URL precisa pertencer à instância SEI configurada.")
    return url


def _redact(text: str) -> str:
    text = re.sub(
        r"(?i)([?&](?:infra_hash|hdnToken|token|csrf(?:_token)?)=)[^&#'\"\s]+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r'(?i)(name=["\'](?:hdnToken|token|csrf(?:_token)?)["\'][^>]*value=["\'])[^"\']*',
        r"\1<redacted>",
        text,
    )
    return text


def _parse_js_string_arguments(source: str) -> list[str] | None:
    """Parse only JS literal arguments (strings, integers, booleans, null).

    Returning None means the callback uses an expression we do not understand;
    it must remain diagnostic-only rather than executable.
    """
    values: list[str] = []
    i = 0
    length = len(source)
    while i < length:
        while i < length and source[i].isspace():
            i += 1
        if i >= length:
            break
        if source[i] in ("'", '"'):
            quote = source[i]
            i += 1
            out: list[str] = []
            escaped = False
            while i < length:
                ch = source[i]
                i += 1
                if escaped:
                    out.append({"n": "\n", "r": "\r", "t": "\t"}.get(ch, ch))
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    break
                else:
                    out.append(ch)
            else:
                return None
            values.append("".join(out))
        else:
            start = i
            while i < length and source[i] != ",":
                i += 1
            raw = source[start:i].strip()
            if raw in {"true", "false", "null"} or re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
                values.append(raw)
            else:
                return None
        while i < length and source[i].isspace():
            i += 1
        if i == length:
            break
        if source[i] != ",":
            return None
        i += 1
    return values


def _parse_js_call(source: str) -> tuple[str, list[str]] | None:
    match = re.match(r"^\s*([A-Za-z_$][\w$]*)\s*\((.*)\)\s*;?\s*$", source, re.DOTALL)
    if not match:
        return None
    values = _parse_js_string_arguments(match.group(2))
    if values is None:
        return None
    return match.group(1), values


def _function_body(html: str, name: str) -> tuple[list[str], str] | None:
    """Return function parameter names and a balanced function body.

    This is deliberately a lexer-sized parser, not an evaluator. It ignores
    braces inside quoted literals and reports None for malformed JavaScript.
    """
    match = re.search(
        rf"\bfunction\s+{re.escape(name)}\s*\(([^)]*)\)\s*\{{",
        html,
        flags=re.DOTALL,
    )
    if not match:
        return None
    params = [part.strip() for part in match.group(1).split(",") if part.strip()]
    i = match.end()
    depth = 1
    quote: str | None = None
    escaped = False
    while i < len(html):
        ch = html[i]
        i += 1
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("'", '"', "`"):
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return params, html[match.end() : i - 1]
    return None


def _form_ref(form: Tag, index: int) -> str:
    form_id = str(form.get("id", "")).strip()
    return f"form:{form_id}" if form_id else f"form:index:{index}"


def _button_key(form_ref: str, index: int) -> str:
    return f"{form_ref}:button:{index}"


def _get_form_by_ref(soup: BeautifulSoup, ref: str) -> Tag | None:
    for index, form in enumerate(soup.find_all("form")):
        if _form_ref(form, index) == ref:
            return form
    return None


def _get_buttons(form: Tag, form_ref: str) -> list[dict[str, Any]]:
    buttons: list[dict[str, Any]] = []
    for index, element in enumerate(form.find_all(["button", "input"])):
        if element.name == "input":
            button_type = str(element.get("type", "")).casefold()
            if button_type not in {"submit", "button"}:
                continue
        else:
            button_type = str(element.get("type", "submit")).casefold() or "submit"
        onclick = str(element.get("onclick", "")).strip()
        call = _parse_js_call(onclick) if onclick else None
        buttons.append(
            {
                "button_key": _button_key(form_ref, index),
                "name": str(element.get("name", "")),
                "value": str(element.get("value", "")) or element.get_text(" ", strip=True),
                "type": button_type,
                "onclick_function": call[0] if call else None,
                "onclick_args": call[1] if call else None,
            }
        )
    return buttons


def _field_public_info(element: Tag) -> dict[str, Any] | None:
    name = str(element.get("name", "")).strip()
    if not name:
        return None
    tag = element.name
    input_type = str(element.get("type", "text")).casefold() if tag == "input" else tag
    data: dict[str, Any] = {
        "name": name,
        "type": input_type,
        "disabled": element.has_attr("disabled"),
        "readonly": element.has_attr("readonly"),
        "required": element.has_attr("required"),
    }
    if input_type == "hidden":
        data["hidden"] = True
        data["value_redacted"] = True
        return data
    if tag == "select":
        selected = element.find_all("option", selected=True)
        if not selected:
            first = element.find("option")
            selected = [first] if isinstance(first, Tag) else []
        data["multiple"] = element.has_attr("multiple")
        data["values"] = [str(option.get("value", "")) for option in selected]
        data["options"] = [
            {
                "value": str(option.get("value", "")),
                "text": option.get_text(" ", strip=True),
                "selected": option.has_attr("selected"),
                "disabled": option.has_attr("disabled"),
            }
            for option in element.find_all("option")
        ]
    elif tag == "textarea":
        data["value"] = element.get_text()
    else:
        data["value"] = str(element.get("value", ""))
        if input_type in {"checkbox", "radio"}:
            data["checked"] = element.has_attr("checked")
    return data


def _collect_form_pairs(form: Tag) -> list[tuple[str, str]]:
    """Return browser-like successful controls without collapsing duplicate names."""
    pairs: list[tuple[str, str]] = []
    for element in form.find_all(["input", "select", "textarea"]):
        name = str(element.get("name", "")).strip()
        if not name or element.has_attr("disabled"):
            continue
        tag = element.name
        input_type = str(element.get("type", "text")).casefold() if tag == "input" else tag
        if input_type in {"submit", "button", "reset", "file", "image"}:
            continue
        if input_type in {"checkbox", "radio"}:
            if not element.has_attr("checked"):
                continue
            pairs.append((name, str(element.get("value", "on"))))
        elif tag == "select":
            selected = element.find_all("option", selected=True)
            if not selected:
                first = element.find("option")
                selected = [first] if isinstance(first, Tag) else []
            for option in selected:
                if option is not None and not option.has_attr("disabled"):
                    pairs.append((name, str(option.get("value", ""))))
        elif tag == "textarea":
            pairs.append((name, element.get_text()))
        else:
            pairs.append((name, str(element.get("value", ""))))
    return pairs


def _with_overrides(
    pairs: list[tuple[str, str]],
    overrides: list[dict[str, str]] | None,
    mutations: list[dict[str, str]],
) -> list[tuple[str, str]]:
    replace: dict[str, list[str]] = {}
    for mutation in mutations:
        name = mutation.get("field", "")
        value = mutation.get("value", "")
        if name:
            replace[name] = [value]
    for override in overrides or []:
        name = str(override.get("name", "")).strip()
        if not name:
            raise SEIValidationError("Cada override precisa ter o campo 'name'.")
        replace.setdefault(name, []).append(str(override.get("value", "")))
    result = [(name, value) for name, value in pairs if name not in replace]
    for name, values in replace.items():
        result.extend((name, value) for value in values)
    return result


def _controller_action_from_text(text: str) -> tuple[str, str]:
    raw = text.replace("&amp;", "&")
    return raw, _action_name(raw)


def _callback_plan(
    html: str,
    function_name: str,
    args: list[str],
    base_url: str,
) -> dict[str, Any] | None:
    parsed = _function_body(html, function_name)
    if parsed is None:
        return None
    params, body = parsed
    param_values = {param: args[index] for index, param in enumerate(params) if index < len(args)}

    # A callback may simply navigate to a literal signed controller URL.
    location = re.search(
        r"(?:window\.)?location\.href\s*=\s*['\"]([^'\"]+)['\"]",
        body,
        flags=re.DOTALL,
    )
    if location:
        raw, action = _controller_action_from_text(location.group(1))
        return {
            "kind": "direct_get",
            "action_name": action,
            "risk": _risk(action),
            "target_url": urljoin(base_url, raw),
            "mutations": [],
            "form_ref": None,
        }

    form_match = re.search(
        r"document\.getElementById\(['\"]([^'\"]+)['\"]\)",
        body,
        flags=re.DOTALL,
    )
    form_id = form_match.group(1) if form_match else ""
    action_match = re.search(
        r"\.action\s*=\s*['\"]([^'\"]*controlador\.php\?acao=[^'\"]+)['\"]",
        body,
        flags=re.DOTALL,
    )
    submit_match = re.search(r"\.submit\s*\(\s*\)", body)
    if not form_id or not action_match or not submit_match:
        return None

    target_raw, action = _controller_action_from_text(action_match.group(1))
    mutations: list[dict[str, str]] = []
    # The common SEI pattern is document.getElementById('hidden').value = id;
    for assignment in re.finditer(
        r"document\.getElementById\(['\"]([^'\"]+)['\"]\)\.value\s*=\s*([A-Za-z_$][\w$]*)",
        body,
        flags=re.DOTALL,
    ):
        field, variable = assignment.groups()
        if variable in param_values:
            mutations.append({"field": field, "value": param_values[variable]})

    return {
        "kind": "form_submit",
        "action_name": action,
        "risk": _risk(action),
        "target_url": urljoin(base_url, target_raw),
        "mutations": mutations,
        "form_ref": f"form:{form_id}",
    }


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "trigger_id": plan["trigger_id"],
        "label": plan["label"],
        "kind": plan["kind"],
        "risk": plan["risk"],
        "action_name": plan["action_name"],
        "form_ref": plan.get("form_ref"),
        "supported": plan["supported"],
        "reason": plan.get("reason"),
    }


def _inspect_html(html: str, base_url: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    soup = BeautifulSoup(html, "html.parser")
    forms: list[dict[str, Any]] = []
    plans: dict[str, dict[str, Any]] = {}
    counter = 0

    for index, form in enumerate(soup.find_all("form")):
        ref = _form_ref(form, index)
        action_url = urljoin(base_url, str(form.get("action", "")).replace("&amp;", "&"))
        action = _action_name(action_url)
        buttons = _get_buttons(form, ref)
        forms.append(
            {
                "form_ref": ref,
                "id": str(form.get("id", "")) or None,
                "method": str(form.get("method", "get")).casefold(),
                "enctype": str(form.get("enctype", "application/x-www-form-urlencoded")).casefold(),
                "action_name": action or None,
                "fields": [
                    info
                    for element in form.find_all(["input", "select", "textarea"])
                    if (info := _field_public_info(element)) is not None
                ],
                "buttons": buttons,
            }
        )
        for button in buttons:
            if button["type"] != "submit":
                continue
            counter += 1
            trigger_id = f"submit:{counter}"
            plan = {
                "trigger_id": trigger_id,
                "label": button["value"] or "Enviar",
                "kind": "form_submit",
                "risk": _risk(action),
                "action_name": action,
                "target_url": action_url or base_url,
                "form_ref": ref,
                "mutations": [],
                "button": button,
                "supported": bool(action),
                "reason": None if action else "form_without_action",
            }
            plans[trigger_id] = plan

    # Direct links and linkX variables are discovered without exposing signed URLs.
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        if "acao=" not in href:
            continue
        raw, action = _controller_action_from_text(href)
        counter += 1
        trigger_id = f"href:{counter}"
        plans[trigger_id] = {
            "trigger_id": trigger_id,
            "label": anchor.get_text(" ", strip=True) or anchor.get("title", "") or action,
            "kind": "direct_get",
            "risk": _risk(action),
            "action_name": action,
            "target_url": urljoin(base_url, raw),
            "form_ref": None,
            "mutations": [],
            "button": None,
            "supported": bool(action),
            "reason": None if action else "missing_action",
        }

    for index, match in enumerate(
        re.finditer(r"\b(?:var\s+)?([A-Za-z_$][\w$]*[Ll]ink[\w$]*)\s*=\s*(['\"])(.*?)\2", html, re.DOTALL)
    ):
        variable, _, raw = match.groups()
        if "acao=" not in raw:
            continue
        raw, action = _controller_action_from_text(raw)
        counter += 1
        trigger_id = f"jsvar:{variable}:{index}"
        plans[trigger_id] = {
            "trigger_id": trigger_id,
            "label": variable,
            "kind": "direct_get",
            "risk": _risk(action),
            "action_name": action,
            "target_url": urljoin(base_url, raw),
            "form_ref": None,
            "mutations": [],
            "button": None,
            "supported": bool(action),
            "reason": None if action else "missing_action",
        }

    # Map callbacks on actual clickable elements to a static plan, preserving
    # literal arguments. Unsupported callbacks are intentionally diagnostic.
    for element_index, element in enumerate(soup.find_all(["a", "button", "input"])):
        onclick = str(element.get("onclick", "")).strip()
        if not onclick:
            continue
        call = _parse_js_call(onclick)
        if call is None:
            continue
        function_name, args = call
        counter += 1
        trigger_id = f"callback:{element_index}:{function_name}"
        callback = _callback_plan(html, function_name, args, base_url)
        if callback is None:
            plans[trigger_id] = {
                "trigger_id": trigger_id,
                "label": element.get_text(" ", strip=True) or str(element.get("title", "")) or function_name,
                "kind": "unsupported_callback",
                "risk": "write",
                "action_name": "",
                "target_url": "",
                "form_ref": None,
                "mutations": [],
                "button": None,
                "supported": False,
                "reason": "callback_outside_static_subset",
            }
            continue
        callback.update(
            {
                "trigger_id": trigger_id,
                "label": element.get_text(" ", strip=True) or str(element.get("title", "")) or function_name,
                "button": None,
                "supported": True,
                "reason": None,
            }
        )
        plans[trigger_id] = callback

    public = {
        "forms": forms,
        "actions": [_public_plan(plan) for plan in plans.values()],
    }
    return public, plans


def _fingerprint(public: dict[str, Any]) -> str:
    serialized = json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def _fetch_read_page(client: SEIWebClient, url: str, referer: str = "") -> tuple[str, str]:
    """Fetch a page without following an unclassified redirect."""
    await client.ensure_authenticated()
    current = _absolute_and_local(client, str(client.sei_root), url)
    for _ in range(_MAX_REDIRECTS + 1):
        action = _action_name(current)
        if not _is_read_action(action):
            raise SEIValidationError(
                f"A inspeção só abre páginas de leitura conhecidas; '{action or 'sem acao'}' "
                "é uma ação ou rota não classificada."
            )
        response = await client._http.get(
            current,
            headers={"Referer": referer or str(client._inbox_url or "")},
            follow_redirects=False,
        )
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise SEIConnectionError("Redirecionamento do SEI sem cabeçalho Location.")
            referer, current = current, _absolute_and_local(client, current, location)
            continue
        _check(response)
        body = _decode_response(response.content, response.headers.get("content-type", ""))
        if _is_login_page(body):
            raise SEIConnectionError("O SEI devolveu a página de login durante a inspeção.")
        return body, str(response.url)
    raise SEIConnectionError("Número máximo de redirecionamentos excedido na inspeção.")


async def inspect_page(
    client: SEIWebClient,
    url: str,
    *,
    incluir_raw: bool = False,
) -> dict[str, Any]:
    """Inspect a known-safe SEI page and retain an opaque action-plan snapshot."""
    body, final_url = await _fetch_read_page(client, url)
    public, _ = _inspect_html(body, final_url)
    fingerprint = _fingerprint(public)
    page_ref = f"sei-page:{secrets.token_urlsafe(18)}"
    snapshot = _Snapshot(
        url=final_url,
        referer=str(client._inbox_url or ""),
        fingerprint=fingerprint,
        created_at=time.monotonic(),
    )
    store = _store(client)
    async with store.lock:
        now = time.monotonic()
        store.snapshots = {
            key: value
            for key, value in store.snapshots.items()
            if now - value.created_at <= _PAGE_TTL_SECONDS
        }
        store.snapshots[page_ref] = snapshot

    result: dict[str, Any] = {
        "page_ref": page_ref,
        "expires_in_seconds": _PAGE_TTL_SECONDS,
        "url_kind": _action_name(final_url) or None,
        **public,
    }
    if incluir_raw:
        result["raw_redacted"] = _redact(body)
    return result


def _selected_button(form: Tag, form_ref: str, requested: dict[str, str] | None) -> dict[str, Any] | None:
    buttons = [button for button in _get_buttons(form, form_ref) if button["type"] == "submit"]
    if not buttons:
        return None
    if requested:
        key = requested.get("button_key")
        if key:
            match = next((button for button in buttons if button["button_key"] == key), None)
        else:
            match = next(
                (
                    button
                    for button in buttons
                    if button["name"] == requested.get("name", "")
                    and button["value"] == requested.get("value", "")
                ),
                None,
            )
        if match is None:
            raise SEIValidationError("O botão submit informado não pertence ao formulário atual.")
        return match
    if len(buttons) > 1:
        raise SEIValidationError(
            "O formulário possui mais de um botão submit. Informe submit_button pelo button_key."
        )
    return buttons[0]


async def _verify(
    client: SEIWebClient,
    snapshot: _Snapshot,
    expect: dict[str, str] | None,
) -> dict[str, Any]:
    if not expect:
        return {"status": "not_requested"}
    kind = str(expect.get("kind", ""))
    body, _ = await _fetch_read_page(client, snapshot.url, snapshot.referer)
    soup = BeautifulSoup(body, "html.parser")
    if kind in {"text_absent", "text_present"}:
        text = str(expect.get("text", ""))
        if not text:
            raise SEIValidationError("A verificação textual exige expect.text.")
        present = text in soup.get_text(" ", strip=True)
        wanted = kind == "text_present"
        return {"status": "passed" if present == wanted else "failed", "kind": kind}
    if kind in {"selector_absent", "selector_present"}:
        selector = str(expect.get("selector", ""))
        if not selector:
            raise SEIValidationError("A verificação por seletor exige expect.selector.")
        present = soup.select_one(selector) is not None
        wanted = kind == "selector_present"
        return {"status": "passed" if present == wanted else "failed", "kind": kind}
    raise SEIValidationError(f"Tipo de verificação não suportado: {kind!r}.")


async def execute_page_plan(
    client: SEIWebClient,
    page_ref: str,
    trigger_id: str,
    *,
    overrides: list[dict[str, str]] | None = None,
    submit_button: dict[str, str] | None = None,
    confirmar: bool = False,
    expect: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute a previously inspected static plan; never execute arbitrary JS."""
    store = _store(client)
    async with store.lock:
        snapshot = store.snapshots.get(page_ref)
    if snapshot is None:
        raise SEINotFoundError("page_ref desconhecida ou já expirada.")
    if time.monotonic() - snapshot.created_at > _PAGE_TTL_SECONDS:
        async with store.lock:
            store.snapshots.pop(page_ref, None)
        raise SEINotFoundError("page_ref expirada; inspecione a página novamente.")

    body, final_url = await _fetch_read_page(client, snapshot.url, snapshot.referer)
    public, plans = _inspect_html(body, final_url)
    if _fingerprint(public) != snapshot.fingerprint:
        raise SEIValidationError(
            "A página mudou desde a inspeção; gere uma nova page_ref antes de executar a ação."
        )
    plan = plans.get(trigger_id)
    if plan is None:
        raise SEINotFoundError("O trigger_id não existe mais na página atual.")
    if not plan["supported"]:
        raise SEIValidationError(
            "Este callback está fora do subconjunto estático suportado e não pode ser executado."
        )
    if plan["risk"] != "read" and not confirmar:
        raise SEIValidationError(
            "A ação pode alterar o SEI. Reenvie com confirmar=True após conferir o plano."
        )

    if plan["kind"] == "direct_get":
        target = _absolute_and_local(client, final_url, plan["target_url"])
        response = await client._http.get(target, headers={"Referer": final_url})
    elif plan["kind"] == "form_submit":
        soup = BeautifulSoup(body, "html.parser")
        form = _get_form_by_ref(soup, str(plan["form_ref"]))
        if form is None:
            raise SEIParseError("O formulário do plano não foi localizado após releitura.")
        method = str(form.get("method", "get")).casefold()
        if method != "post":
            raise SEIValidationError("A execução genérica só suporta formulários POST.")
        enctype = str(form.get("enctype", "application/x-www-form-urlencoded")).casefold()
        if enctype not in {"", "application/x-www-form-urlencoded"}:
            raise SEIValidationError(
                "A execução genérica não suporta este enctype; use uma ferramenta tipada."
            )
        pairs = _collect_form_pairs(form)
        pairs = _with_overrides(pairs, overrides, list(plan.get("mutations", [])))
        button = _selected_button(form, str(plan["form_ref"]), submit_button)
        if button and button["name"]:
            pairs.append((str(button["name"]), str(button["value"])))
        target = _absolute_and_local(client, final_url, str(plan["target_url"]))
        response = await client._http.post(
            target,
            content=urlencode(pairs, encoding="iso-8859-1", errors="replace").encode("ascii"),
            headers={"Referer": final_url, "Content-Type": "application/x-www-form-urlencoded"},
        )
    else:
        raise SEIValidationError(f"Tipo de plano não executável: {plan['kind']!r}.")

    _check(response)
    response_body = _decode_response(response.content, response.headers.get("content-type", ""))
    if error := _extrair_erro_sei(response_body):
        raise SEIConnectionError(error)

    # The generic executor cannot always infer the affected process; dropping
    # the short-lived tree cache is safer than returning stale state.
    client._arvore_cache.clear()
    verification = await _verify(client, snapshot, expect)
    if verification["status"] == "failed":
        raise SEIConnectionError("O SEI respondeu sem erro, mas a pós-condição não foi satisfeita.")

    return {
        "submitted": True,
        "trigger_id": trigger_id,
        "action_name": plan["action_name"],
        "risk": plan["risk"],
        "verification": verification,
    }
