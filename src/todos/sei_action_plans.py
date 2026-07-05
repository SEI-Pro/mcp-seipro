"""Declarative action plans for safe exploration of SEI frontend pages.

The interpreter in this module intentionally accepts only a small, static subset
of the JavaScript patterns emitted by SEI. It never evaluates JavaScript
received from a caller.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
import weakref
from dataclasses import dataclass, field
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

# A request method alone is not a safety classification: SEI uses GET for
# several state-changing actions. Inspection follows only known page routes.
_READ_ACTIONS = frozenset(
    {
        "arvore_montar",
        "arvore_visualizar",
        "documento_consultar",
        "documento_visualizar",
        "editor_montar",
        "documento_escolher_tipo",
        "procedimento_consultar",
        "procedimento_consultar_historico",
        "procedimento_sobrestado_listar",
        "andamento_marcador_gerenciar",
        "acompanhamento_gerenciar",
        "rel_bloco_protocolo_listar",
        "bloco_assinatura_listar",
        "bloco_interno_listar",
    }
)
_READ_SUFFIXES = ("_listar", "_consultar", "_visualizar", "_montar", "_gerenciar")
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
)


@dataclass(frozen=True)
class _PageSnapshot:
    url: str
    referer: str
    fingerprint: str
    created_at: float


@dataclass
class _PageStore:
    snapshots: dict[str, _PageSnapshot] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_PAGE_STORES: weakref.WeakKeyDictionary[SEIWebClient, _PageStore] = weakref.WeakKeyDictionary()
_PAGE_STORES_LOCK = asyncio.Lock()


async def _page_store(client: SEIWebClient) -> _PageStore:
    """Return the page-ref store scoped to one authenticated web client."""
    async with _PAGE_STORES_LOCK:
        store = _PAGE_STORES.get(client)
        if store is None:
            store = _PageStore()
            _PAGE_STORES[client] = store
        return store


def _action_name(url: str) -> str:
    return parse_qs(urlparse(url).query).get("acao", [""])[0]


def _risk(action_name: str) -> str:
    """Classify a controller action conservatively."""
    action = action_name.casefold()
    if not action:
        return "write"
    if any(marker in action for marker in _DESTRUCTIVE_MARKERS):
        return "destructive"
    if any(marker in action for marker in _WRITE_MARKERS):
        return "write"
    if action in _READ_ACTIONS or action.endswith(_READ_SUFFIXES):
        return "read"
    return "write"


def _is_read_action(action_name: str) -> bool:
    return _risk(action_name) == "read"


def _local_url(client: SEIWebClient, base_url: str, raw_url: str) -> str:
    """Resolve a link and reject destinations outside the configured instance."""
    url = urljoin(base_url, raw_url.replace("&amp;", "&"))
    root = urlparse(str(client.sei_root))
    target = urlparse(url)
    if (root.scheme.casefold(), root.netloc.casefold()) != (
        target.scheme.casefold(),
        target.netloc.casefold(),
    ):
        message = "A URL precisa pertencer à instância SEI configurada."
        raise SEIValidationError(message)
    return url


def _redact(text: str) -> str:
    """Redact common SEI capabilities before returning diagnostic HTML."""
    redacted = re.sub(
        r"(?i)([?&](?:infra_hash|hdnToken|token|csrf(?:_token)?)=)[^&#'\"\s]+",
        r"\1<redacted>",
        text,
    )
    return re.sub(
        r'(?i)(name=["\'](?:hdnToken|token|csrf(?:_token)?)["\'][^>]*value=["\'])[^"\']*',
        r"\1<redacted>",
        redacted,
    )


def _literal_arguments(source: str) -> list[str] | None:  # noqa: C901, PLR0912
    """Parse literals in a JavaScript call without evaluating expressions."""
    values: list[str] = []
    index = 0
    while index < len(source):
        while index < len(source) and source[index].isspace():
            index += 1
        if index >= len(source):
            break
        if source[index] in {"'", '"'}:
            quote = source[index]
            index += 1
            buffer: list[str] = []
            escaped = False
            while index < len(source):
                character = source[index]
                index += 1
                if escaped:
                    buffer.append({"n": "\n", "r": "\r", "t": "\t"}.get(character, character))
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    break
                else:
                    buffer.append(character)
            else:
                return None
            values.append("".join(buffer))
        else:
            start = index
            while index < len(source) and source[index] != ",":
                index += 1
            token = source[start:index].strip()
            if token in {"true", "false", "null"} or re.fullmatch(r"-?\d+(?:\.\d+)?", token):
                values.append(token)
            else:
                return None
        while index < len(source) and source[index].isspace():
            index += 1
        if index == len(source):
            break
        if source[index] != ",":
            return None
        index += 1
    return values


def _parse_js_call(source: str) -> tuple[str, list[str]] | None:
    """Return a callback name and literal arguments, or None for dynamic JS."""
    match = re.match(
        r"^\s*(?:return\s+)?([A-Za-z_$][\w$]*)\s*\((.*)\)\s*;?\s*$",
        source,
        re.DOTALL,
    )
    if match is None:
        return None
    arguments = _literal_arguments(match.group(2))
    if arguments is None:
        return None
    return match.group(1), arguments


def _function_body(  # noqa: C901
    html: str, function_name: str
) -> tuple[list[str], str] | None:
    """Extract one balanced JavaScript function body without executing it."""
    match = re.search(
        rf"\bfunction\s+{re.escape(function_name)}\s*\(([^)]*)\)\s*\{{",
        html,
        re.DOTALL,
    )
    if match is None:
        return None

    depth = 1
    index = match.end()
    quote = ""
    escaped = False
    while index < len(html):
        character = html[index]
        index += 1
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if character in {"'", '"', "`"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                parameters = [
                    parameter.strip()
                    for parameter in match.group(1).split(",")
                    if parameter.strip()
                ]
                return parameters, html[match.end() : index - 1]
    return None


def _form_ref(form: Tag, index: int) -> str:
    form_id = str(form.get("id", "")).strip()
    return f"form:{form_id}" if form_id else f"form:index:{index}"


def _find_form(soup: BeautifulSoup, form_ref: str) -> Tag | None:
    for index, form in enumerate(soup.find_all("form")):
        if _form_ref(form, index) == form_ref:
            return form
    return None


def _button_specs(form: Tag, form_ref: str) -> list[dict[str, Any]]:
    """Describe clickable form controls and preserve parsed literal callbacks."""
    buttons: list[dict[str, Any]] = []
    for index, element in enumerate(form.find_all(["button", "input"])):
        if element.name == "input":
            button_type = str(element.get("type", "")).casefold()
            if button_type not in {"button", "submit"}:
                continue
        else:
            button_type = str(element.get("type", "submit")).casefold() or "submit"
        onclick = str(element.get("onclick", "")).strip()
        callback = _parse_js_call(onclick) if onclick else None
        buttons.append(
            {
                "button_key": f"{form_ref}:button:{index}",
                "name": str(element.get("name", "")),
                "value": str(element.get("value", "")) or element.get_text(" ", strip=True),
                "type": button_type,
                "onclick_function": callback[0] if callback else None,
                "onclick_args": callback[1] if callback else None,
            }
        )
    return buttons


def _field_spec(element: Tag) -> dict[str, Any] | None:
    name = str(element.get("name", "")).strip()
    if not name:
        return None
    tag = element.name
    input_type = str(element.get("type", "text")).casefold() if tag == "input" else tag
    result: dict[str, Any] = {
        "name": name,
        "type": input_type,
        "disabled": element.has_attr("disabled"),
        "readonly": element.has_attr("readonly"),
        "required": element.has_attr("required"),
    }
    if input_type == "hidden":
        result["hidden"] = True
        result["value_redacted"] = True
        return result
    if tag == "select":
        selected = element.find_all("option", selected=True)
        if not selected:
            first = element.find("option")
            selected = [first] if isinstance(first, Tag) else []
        result["multiple"] = element.has_attr("multiple")
        result["values"] = [str(option.get("value", "")) for option in selected]
        result["options"] = [
            {
                "value": str(option.get("value", "")),
                "text": option.get_text(" ", strip=True),
                "selected": option.has_attr("selected"),
                "disabled": option.has_attr("disabled"),
            }
            for option in element.find_all("option")
        ]
    elif tag == "textarea":
        result["value"] = element.get_text()
    else:
        result["value"] = str(element.get("value", ""))
        if input_type in {"checkbox", "radio"}:
            result["checked"] = element.has_attr("checked")
    return result


def _collect_form_pairs(form: Tag) -> list[tuple[str, str]]:  # noqa: C901
    """Collect successful controls, keeping duplicated names in their DOM order."""
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
            if element.has_attr("checked"):
                pairs.append((name, str(element.get("value", "on"))))
        elif tag == "select":
            selected = element.find_all("option", selected=True)
            if not selected:
                first = element.find("option")
                selected = [first] if isinstance(first, Tag) else []
            pairs.extend(
                (name, str(option.get("value", "")))
                for option in selected
                if isinstance(option, Tag) and not option.has_attr("disabled")
            )
        elif tag == "textarea":
            pairs.append((name, element.get_text()))
        else:
            pairs.append((name, str(element.get("value", ""))))
    return pairs


def _apply_values(
    pairs: list[tuple[str, str]],
    mutations: list[dict[str, str]],
    overrides: list[dict[str, str]] | None,
) -> list[tuple[str, str]]:
    """Apply plan mutations then caller overrides without losing repeated fields."""
    replacements: dict[str, list[str]] = {}
    for mutation in mutations:
        name = str(mutation.get("field", "")).strip()
        if name:
            replacements[name] = [str(mutation.get("value", ""))]

    override_names: set[str] = set()
    for override in overrides or []:
        name = str(override.get("name", "")).strip()
        if not name:
            message = "Cada override precisa ter o campo 'name'."
            raise SEIValidationError(message)
        if name not in override_names:
            replacements[name] = []
            override_names.add(name)
        replacements[name].append(str(override.get("value", "")))

    result = [(name, value) for name, value in pairs if name not in replacements]
    for name, values in replacements.items():
        result.extend((name, value) for value in values)
    return result


def _callback_plan(
    html: str,
    function_name: str,
    arguments: list[str],
    base_url: str,
) -> dict[str, Any] | None:
    """Translate a small allowlisted callback subset into a declarative plan."""
    extracted = _function_body(html, function_name)
    if extracted is None:
        return None
    parameters, body = extracted
    parameter_values = {
        parameter: arguments[index]
        for index, parameter in enumerate(parameters)
        if index < len(arguments)
    }

    location = re.search(
        r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
        body,
        re.DOTALL,
    )
    if location is not None:
        target = location.group(1).replace("&amp;", "&")
        action_name = _action_name(target)
        return {
            "kind": "direct_get",
            "action_name": action_name,
            "risk": _risk(action_name),
            "target_url": urljoin(base_url, target),
            "form_ref": None,
            "mutations": [],
        }

    form_match = re.search(
        r"(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*"
        r"document\.getElementById\(['\"]([^'\"]+)['\"]\)",
        body,
        re.DOTALL,
    )
    if form_match is None:
        return None
    variable, form_id = form_match.groups()
    action_match = re.search(
        rf"\b{re.escape(variable)}\.action\s*=\s*['\"]"
        r"([^'\"]*controlador\.php\?acao=[^'\"]+)['\"]",
        body,
        re.DOTALL,
    )
    submits = re.search(rf"\b{re.escape(variable)}\.submit\s*\(\s*\)", body)
    if action_match is None or submits is None:
        return None

    mutations: list[dict[str, str]] = []
    assignment_pattern = (
        r"document\.getElementById\(['\"]([^'\"]+)['\"]\)\.value\s*=\s*"
        r"([A-Za-z_$][\w$]*)"
    )
    for field_name, parameter in re.findall(assignment_pattern, body, re.DOTALL):
        if parameter in parameter_values:
            mutations.append({"field": field_name, "value": parameter_values[parameter]})

    target = action_match.group(1).replace("&amp;", "&")
    action_name = _action_name(target)
    return {
        "kind": "form_submit",
        "action_name": action_name,
        "risk": _risk(action_name),
        "target_url": urljoin(base_url, target),
        "form_ref": f"form:{form_id}",
        "mutations": mutations,
    }


def _unsupported_plan(trigger_id: str, label: str) -> dict[str, Any]:
    return {
        "trigger_id": trigger_id,
        "label": label,
        "kind": "unsupported_callback",
        "action_name": "",
        "risk": "write",
        "target_url": "",
        "form_ref": None,
        "mutations": [],
        "button": None,
        "supported": False,
        "reason": "callback_outside_static_subset",
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


def _inspect_html(  # noqa: C901, PLR0912, PLR0915
    html: str,
    base_url: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Build public page metadata and private executable action plans."""
    soup = BeautifulSoup(html, "html.parser")
    forms: list[dict[str, Any]] = []
    plans: dict[str, dict[str, Any]] = {}
    sequence = 0

    for index, form in enumerate(soup.find_all("form")):
        form_ref = _form_ref(form, index)
        raw_action = str(form.get("action", "")).replace("&amp;", "&")
        target_url = urljoin(base_url, raw_action) if raw_action else base_url
        action_name = _action_name(target_url)
        buttons = _button_specs(form, form_ref)
        forms.append(
            {
                "form_ref": form_ref,
                "id": str(form.get("id", "")) or None,
                "method": str(form.get("method", "get")).casefold(),
                "enctype": str(form.get("enctype", "application/x-www-form-urlencoded")).casefold(),
                "action_name": action_name or None,
                "fields": [
                    item
                    for element in form.find_all(["input", "select", "textarea"])
                    if (item := _field_spec(element)) is not None
                ],
                "buttons": buttons,
            }
        )
        for button in buttons:
            if button["type"] != "submit":
                continue
            sequence += 1
            trigger_id = f"submit:{sequence}"
            plans[trigger_id] = {
                "trigger_id": trigger_id,
                "label": button["value"] or "Enviar",
                "kind": "form_submit",
                "action_name": action_name,
                "risk": _risk(action_name),
                "target_url": target_url,
                "form_ref": form_ref,
                "mutations": [],
                "button": button,
                "supported": bool(action_name),
                "reason": None if action_name else "form_without_action",
            }

    for anchor in soup.find_all("a", href=True):
        raw_url = str(anchor["href"]).replace("&amp;", "&")
        if "acao=" not in raw_url:
            continue
        action_name = _action_name(raw_url)
        sequence += 1
        trigger_id = f"href:{sequence}"
        plans[trigger_id] = {
            "trigger_id": trigger_id,
            "label": anchor.get_text(" ", strip=True) or str(anchor.get("title", "")) or action_name,
            "kind": "direct_get",
            "action_name": action_name,
            "risk": _risk(action_name),
            "target_url": urljoin(base_url, raw_url),
            "form_ref": None,
            "mutations": [],
            "button": None,
            "supported": bool(action_name),
            "reason": None if action_name else "missing_action",
        }

    for variable, _, raw_url in re.findall(
        r"\b(?:var\s+)?(\w*[Ll]ink\w*)\s*=\s*(['\"])(.*?)\2",
        html,
        re.DOTALL,
    ):
        if "acao=" not in raw_url:
            continue
        action_name = _action_name(raw_url)
        sequence += 1
        trigger_id = f"jsvar:{variable}:{sequence}"
        plans[trigger_id] = {
            "trigger_id": trigger_id,
            "label": variable,
            "kind": "direct_get",
            "action_name": action_name,
            "risk": _risk(action_name),
            "target_url": urljoin(base_url, raw_url.replace("&amp;", "&")),
            "form_ref": None,
            "mutations": [],
            "button": None,
            "supported": bool(action_name),
            "reason": None if action_name else "missing_action",
        }

    for index, element in enumerate(soup.find_all(["a", "button", "input"])):
        onclick = str(element.get("onclick", "")).strip()
        if not onclick:
            continue
        callback = _parse_js_call(onclick)
        name_match = re.match(r"^\s*([A-Za-z_$][\w$]*)\s*\(", onclick)
        if callback is None:
            if name_match is None:
                continue
            function_name = name_match.group(1)
            arguments: list[str] = []
        else:
            function_name, arguments = callback
        sequence += 1
        trigger_id = f"callback:{index}:{function_name}"
        label = element.get_text(" ", strip=True) or str(element.get("title", "")) or function_name
        plan = _callback_plan(html, function_name, arguments, base_url) if callback else None
        if plan is None:
            plans[trigger_id] = _unsupported_plan(trigger_id, label)
            continue
        plan.update(
            {
                "trigger_id": trigger_id,
                "label": label,
                "button": None,
                "supported": True,
                "reason": None,
            }
        )
        plans[trigger_id] = plan

    public = {
        "forms": forms,
        "actions": [_public_plan(plan) for plan in plans.values()],
    }
    return public, plans


def _fingerprint(public: dict[str, Any]) -> str:
    serialized = json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def _fetch_read_page(
    client: SEIWebClient,
    url: str,
    referer: str = "",
) -> tuple[str, str]:
    """Fetch only a previously classified read page, including safe redirects."""
    await client.ensure_authenticated()
    current = _local_url(client, str(client.sei_root), url)
    for _ in range(_MAX_REDIRECTS + 1):
        action_name = _action_name(current)
        if not _is_read_action(action_name):
            message = (
                "A inspeção só abre páginas de leitura conhecidas; "
                f"{action_name or 'rota sem acao'} não foi classificada como leitura."
            )
            raise SEIValidationError(message)
        response = await client._http.get(  # noqa: SLF001
            current,
            headers={"Referer": referer or str(client._inbox_url or "")},  # noqa: SLF001
            follow_redirects=False,
        )
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                message = "Redirecionamento do SEI sem cabeçalho Location."
                raise SEIConnectionError(message)
            referer, current = current, _local_url(client, current, location)
            continue
        _check(response)
        body = _decode_response(response.content, response.headers.get("content-type", ""))
        if _is_login_page(body):
            message = "O SEI devolveu a página de login durante a inspeção."
            raise SEIConnectionError(message)
        return body, str(response.url)
    message = "Número máximo de redirecionamentos excedido na inspeção."
    raise SEIConnectionError(message)


async def inspect_page(
    client: SEIWebClient,
    url: str,
    *,
    incluir_raw: bool = False,
) -> dict[str, Any]:
    """Inspect a read page and retain an opaque reference to its static plans."""
    body, final_url = await _fetch_read_page(client, url)
    public, _ = _inspect_html(body, final_url)
    now = time.monotonic()
    reference = f"sei-page:{secrets.token_urlsafe(18)}"
    snapshot = _PageSnapshot(
        url=final_url,
        referer=str(client._inbox_url or ""),  # noqa: SLF001
        fingerprint=_fingerprint(public),
        created_at=now,
    )
    store = await _page_store(client)
    async with store.lock:
        store.snapshots = {
            key: value
            for key, value in store.snapshots.items()
            if now - value.created_at <= _PAGE_TTL_SECONDS
        }
        store.snapshots[reference] = snapshot

    result: dict[str, Any] = {
        "page_ref": reference,
        "expires_in_seconds": _PAGE_TTL_SECONDS,
        "url_kind": _action_name(final_url) or None,
        **public,
    }
    if incluir_raw:
        result["raw_redacted"] = _redact(body)
    return result


def _button_for_plan(
    form: Tag,
    form_ref: str,
    planned_button: dict[str, Any] | None,
    requested_button: dict[str, str] | None,
) -> dict[str, Any] | None:
    buttons = [button for button in _button_specs(form, form_ref) if button["type"] == "submit"]
    if not buttons:
        return None
    request = requested_button or planned_button
    if request is not None:
        button_key = str(request.get("button_key", ""))
        button = next((item for item in buttons if item["button_key"] == button_key), None)
        if button is None:
            message = "O botão submit informado não pertence ao formulário atual."
            raise SEIValidationError(message)
        return button
    if len(buttons) == 1:
        return buttons[0]
    message = "O formulário possui mais de um botão submit; informe submit_button.button_key."
    raise SEIValidationError(message)


async def _verify(
    client: SEIWebClient,
    snapshot: _PageSnapshot,
    expectation: dict[str, str] | None,
) -> dict[str, Any]:
    if not expectation:
        return {"status": "not_requested"}
    kind = str(expectation.get("kind", ""))
    body, _ = await _fetch_read_page(client, snapshot.url, snapshot.referer)
    soup = BeautifulSoup(body, "html.parser")
    if kind in {"text_present", "text_absent"}:
        text = str(expectation.get("text", ""))
        if not text:
            message = "A verificação textual exige expect.text."
            raise SEIValidationError(message)
        present = text in soup.get_text(" ", strip=True)
        expected = kind == "text_present"
    elif kind in {"selector_present", "selector_absent"}:
        selector = str(expectation.get("selector", ""))
        if not selector:
            message = "A verificação por seletor exige expect.selector."
            raise SEIValidationError(message)
        present = soup.select_one(selector) is not None
        expected = kind == "selector_present"
    else:
        message = f"Tipo de verificação não suportado: {kind!r}."
        raise SEIValidationError(message)
    return {"status": "passed" if present == expected else "failed", "kind": kind}


async def execute_page_plan(  # noqa: C901, PLR0912, PLR0913, PLR0915
    client: SEIWebClient,
    page_ref: str,
    trigger_id: str,
    *,
    overrides: list[dict[str, str]] | None = None,
    submit_button: dict[str, str] | None = None,
    confirmar: bool = False,
    expect: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute a previously inspected plan without JS input or arbitrary URLs."""
    store = await _page_store(client)
    async with store.lock:
        snapshot = store.snapshots.get(page_ref)
    if snapshot is None:
        message = "page_ref desconhecida ou expirada; inspecione a página novamente."
        raise SEINotFoundError(message)
    if time.monotonic() - snapshot.created_at > _PAGE_TTL_SECONDS:
        async with store.lock:
            store.snapshots.pop(page_ref, None)
        message = "page_ref expirada; inspecione a página novamente."
        raise SEINotFoundError(message)

    body, final_url = await _fetch_read_page(client, snapshot.url, snapshot.referer)
    public, plans = _inspect_html(body, final_url)
    if _fingerprint(public) != snapshot.fingerprint:
        message = "A página mudou desde a inspeção; gere uma nova page_ref antes de executar."
        raise SEIValidationError(message)
    plan = plans.get(trigger_id)
    if plan is None:
        message = "O trigger_id não existe mais na página atual."
        raise SEINotFoundError(message)
    if not plan["supported"]:
        message = "O callback está fora do subconjunto estático suportado."
        raise SEIValidationError(message)
    if plan["risk"] != "read" and not confirmar:
        message = "A ação pode alterar o SEI. Reenvie com confirmar=True."
        raise SEIValidationError(message)

    if plan["kind"] == "direct_get":
        response = await client._http.get(  # noqa: SLF001
            _local_url(client, final_url, str(plan["target_url"])),
            headers={"Referer": final_url},
            follow_redirects=False,
        )
    elif plan["kind"] == "form_submit":
        soup = BeautifulSoup(body, "html.parser")
        form = _find_form(soup, str(plan["form_ref"]))
        if form is None:
            message = "O formulário do plano não foi localizado após releitura."
            raise SEIParseError(message)
        if str(form.get("method", "get")).casefold() != "post":
            message = "A execução genérica só suporta formulários POST."
            raise SEIValidationError(message)
        enctype = str(form.get("enctype", "application/x-www-form-urlencoded")).casefold()
        if enctype not in {"", "application/x-www-form-urlencoded"}:
            message = "A execução genérica não suporta este enctype; use uma ferramenta tipada."
            raise SEIValidationError(message)
        pairs = _apply_values(
            _collect_form_pairs(form),
            list(plan.get("mutations", [])),
            overrides,
        )
        button = _button_for_plan(
            form,
            str(plan["form_ref"]),
            plan.get("button"),
            submit_button,
        )
        if button and button["name"]:
            pairs.append((str(button["name"]), str(button["value"])))
        response = await client._http.post(  # noqa: SLF001
            _local_url(client, final_url, str(plan["target_url"])),
            content=urlencode(pairs, encoding="iso-8859-1", errors="replace").encode("ascii"),
            headers={"Referer": final_url, "Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
    else:
        message = f"Plano não executável: {plan['kind']!r}."
        raise SEIValidationError(message)

    _check(response)
    response_body = _decode_response(response.content, response.headers.get("content-type", ""))
    if error := _extrair_erro_sei(response_body):
        raise SEIConnectionError(error)

    client._arvore_cache.clear()  # noqa: SLF001
    verification = await _verify(client, snapshot, expect)
    if verification["status"] == "failed":
        message = "O SEI respondeu sem erro, mas a pós-condição não foi satisfeita."
        raise SEIConnectionError(message)

    return {
        "submitted": True,
        "trigger_id": trigger_id,
        "action_name": plan["action_name"],
        "risk": plan["risk"],
        "verification": verification,
    }
