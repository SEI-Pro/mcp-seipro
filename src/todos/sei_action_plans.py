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
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup, Tag

from todos.exceptions import (
    SEIConnectionError,
    SEINotFoundError,
    SEIParseError,
    SEIValidationError,
)
from todos.html_utils import action_name as _action_name
from todos.html_utils import is_read_action as _is_read_action
from todos.html_utils import redact_signed_capabilities as _redact
from todos.html_utils import risk_of_action as _risk
from todos.sei_web_client import (
    SEIWebClient,
    _check,
    _decode_response,
    _extrair_erro_sei,
    _is_login_page,
)

if TYPE_CHECKING:
    import httpx

_PAGE_TTL_SECONDS = 120.0
_MAX_REDIRECTS = 5


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


def _local_url(client: SEIWebClient, base_url: str, raw_url: str) -> str:
    """Resolve a link and reject destinations outside the configured instance.

    Delegates to `client.validar_mesma_origem` rather than re-deriving the
    scheme/netloc comparison here: an independent reimplementation previously
    casefolded both sides while the client's own check does not, so the two
    "same origin" gates in this codebase could disagree on a mixed-case host
    (e.g. via a redirect Location header) — one accepting a URL the other
    would reject. A single check means they can no longer drift apart.
    """
    return client.validar_mesma_origem(raw_url.replace("&amp;", "&"), base=base_url)


def _parse_quoted_literal(source: str, index: int, quote: str) -> tuple[str, int] | None:
    """Parse a quoted JS string literal, starting just after the opening quote.

    Returns ``(decoded_value, index_after_closing_quote)``, or ``None`` if the
    quote is never closed (a malformed/truncated literal — treated by the
    caller as an unsupported/dynamic expression, never evaluated).
    """
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
            return "".join(buffer), index
        else:
            buffer.append(character)
    return None


def _parse_bare_token(source: str, index: int) -> tuple[str, int] | None:
    """Parse a bare (unquoted) JS literal argument: a number, ``true``/``false``/``null``."""
    start = index
    while index < len(source) and source[index] != ",":
        index += 1
    token = source[start:index].strip()
    if token in {"true", "false", "null"} or re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return token, index
    return None


def _parse_one_argument(source: str, index: int) -> tuple[str, int] | None:
    """Parse a single JS call argument (quoted string or bare literal) at *index*."""
    if source[index] in {"'", '"'}:
        return _parse_quoted_literal(source, index + 1, source[index])
    return _parse_bare_token(source, index)


def _literal_arguments(source: str) -> list[str] | None:
    """Parse literals in a JavaScript call without evaluating expressions."""
    values: list[str] = []
    index = 0
    while index < len(source):
        while index < len(source) and source[index].isspace():
            index += 1
        if index >= len(source):
            break
        parsed = _parse_one_argument(source, index)
        if parsed is None:
            return None
        value, index = parsed
        values.append(value)
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


def _find_matching_brace(html: str, start_index: int) -> int | None:
    """Find the index just after the ``}`` that closes the ``{`` before *start_index*.

    Tracks string/template-literal quoting so a brace inside a JS string
    literal isn't mistaken for a block boundary.
    """
    depth = 1
    index = start_index
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
                return index
    return None


def _function_body(html: str, function_name: str) -> tuple[list[str], str] | None:
    """Extract one balanced JavaScript function body without executing it."""
    match = re.search(
        rf"\bfunction\s+{re.escape(function_name)}\s*\(([^)]*)\)\s*\{{",
        html,
        re.DOTALL,
    )
    if match is None:
        return None
    end_index = _find_matching_brace(html, match.end())
    if end_index is None:
        return None
    parameters = [parameter.strip() for parameter in match.group(1).split(",") if parameter.strip()]
    return parameters, html[match.end() : end_index - 1]


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


def _collect_form_pairs(form: Tag) -> list[tuple[str, str]]:
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


def _hidden_field_names(form: Tag) -> frozenset[str]:
    """Return the names of every (non-disabled) hidden input in *form*.

    Per RFC 0025 §4, hidden fields may appear in inspection output "pelo nome
    e por um indicador de presença, sem vazar seu valor" — the caller never
    sees a hidden field's value, only that it exists. It follows that
    `overrides` must never be allowed to set one either: the caller can't
    know what a legitimate value would even be, so any override targeting a
    hidden field is either a mistake or an attempt to redirect the action to
    an untinspected target via the same mechanism `mutations` protects.
    """
    return frozenset(
        str(element.get("name", "")).strip()
        for element in form.find_all("input", type="hidden")
        if str(element.get("name", "")).strip() and not element.has_attr("disabled")
    )


def _apply_values(
    pairs: list[tuple[str, str]],
    mutations: list[dict[str, str]],
    overrides: list[dict[str, str]] | None,
    *,
    protected_fields: frozenset[str] = frozenset(),
) -> list[tuple[str, str]]:
    """Apply plan mutations, then caller overrides, without losing repeated fields.

    `mutations` are parsed from the SEI page's own onclick/callback JS — e.g.
    which record id a destructive action targets — and are NOT caller-controlled.
    `protected_fields` (typically every hidden field on the form — see
    `_hidden_field_names`) are likewise not caller-controlled: the caller
    never even sees their values (RFC 0025 §4). `overrides` may add or change
    any other field, but must never replace one of these: doing so would let
    a caller invoke a discovered "delete this record" trigger and silently
    redirect it to a different, never-inspected record, defeating the entire
    point of restricting execution to page_ref + trigger_id.
    """
    replacements: dict[str, list[str]] = {}
    locked_fields: set[str] = set(protected_fields)
    for mutation in mutations:
        name = str(mutation.get("field", "")).strip()
        if name:
            replacements[name] = [str(mutation.get("value", ""))]
            locked_fields.add(name)

    override_names: set[str] = set()
    for override in overrides or []:
        name = str(override.get("name", "")).strip()
        if not name:
            message = "Cada override precisa ter o campo 'name'."
            raise SEIValidationError(message)
        if name in locked_fields:
            message = (
                f"Campo '{name}' é fixado pelo formulário/callback da página "
                "(identifica o alvo da ação) e não pode ser sobrescrito por override."
            )
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


def _inspect_forms(
    soup: BeautifulSoup, base_url: str
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Describe every form on the page and its submit-button trigger plans."""
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
    return forms, plans


def _inspect_anchors(soup: BeautifulSoup, base_url: str) -> dict[str, dict[str, Any]]:
    """Discover direct-GET action links (``<a href="...acao=...">``)."""
    plans: dict[str, dict[str, Any]] = {}
    sequence = 0
    for anchor in soup.find_all("a", href=True):
        raw_url = str(anchor["href"]).replace("&amp;", "&")
        if "acao=" not in raw_url:
            continue
        action_name = _action_name(raw_url)
        sequence += 1
        trigger_id = f"href:{sequence}"
        plans[trigger_id] = {
            "trigger_id": trigger_id,
            "label": anchor.get_text(" ", strip=True)
            or str(anchor.get("title", ""))
            or action_name,
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
    return plans


def _inspect_js_vars(html: str, base_url: str) -> dict[str, dict[str, Any]]:
    """Discover ``var linkX = '...acao=...'`` style static action links."""
    plans: dict[str, dict[str, Any]] = {}
    sequence = 0
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
    return plans


def _inspect_onclick_callbacks(
    soup: BeautifulSoup, html: str, base_url: str
) -> dict[str, dict[str, Any]]:
    """Parse ``onclick`` callbacks into declarative plans for the supported static subset."""
    plans: dict[str, dict[str, Any]] = {}
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
    return plans


def _inspect_html(
    html: str,
    base_url: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Build public page metadata and private executable action plans."""
    soup = BeautifulSoup(html, "html.parser")
    forms, plans = _inspect_forms(soup, base_url)
    plans.update(_inspect_anchors(soup, base_url))
    plans.update(_inspect_js_vars(html, base_url))
    plans.update(_inspect_onclick_callbacks(soup, html, base_url))

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
        response = await client.http_get(
            current,
            headers={"Referer": referer or client.inbox_url},
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
        referer=client.inbox_url,
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


async def _load_snapshot(client: SEIWebClient, page_ref: str) -> _PageSnapshot:
    """Look up *page_ref*, evicting and rejecting it if past its TTL."""
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
    return snapshot


async def _resolve_plan(
    client: SEIWebClient,
    snapshot: _PageSnapshot,
    trigger_id: str,
    *,
    confirmar: bool,
) -> tuple[str, str, dict[str, Any]]:
    """Re-fetch the page, verify it is unchanged, and return its resolved plan.

    Refetching (instead of trusting the snapshot's own content) is what makes
    the freshness/fingerprint check meaningful: a page edited since inspection
    must not be actionable via a stale trigger_id.
    """
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
    return body, final_url, plan


async def _execute_direct_get(
    client: SEIWebClient, final_url: str, plan: dict[str, Any]
) -> httpx.Response:
    return await client.http_get(
        _local_url(client, final_url, str(plan["target_url"])),
        headers={"Referer": final_url},
        follow_redirects=False,
    )


async def _execute_form_submit(
    client: SEIWebClient,
    body: str,
    final_url: str,
    plan: dict[str, Any],
    overrides: list[dict[str, str]] | None,
    submit_button: dict[str, str] | None,
) -> httpx.Response:
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
        protected_fields=_hidden_field_names(form),
    )
    button = _button_for_plan(
        form,
        str(plan["form_ref"]),
        plan.get("button"),
        submit_button,
    )
    if button and button["name"]:
        pairs.append((str(button["name"]), str(button["value"])))
    return await client.http_post(
        _local_url(client, final_url, str(plan["target_url"])),
        content=urlencode(pairs, encoding="iso-8859-1", errors="replace").encode("ascii"),
        headers={"Referer": final_url, "Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )


async def _follow_redirects_same_origin(
    client: SEIWebClient, response: httpx.Response
) -> httpx.Response:
    """Follow `response.next_request` manually, validating origin per hop.

    Mirrors `SEIWebClient._enviar_mesma_origem`. SEI's write actions commonly
    answer with a redirect (POST/redirect-then-GET) whose target page carries
    the real success/error indicator — `_check`/`_extrair_erro_sei` must
    inspect that final page, not the near-empty redirect response itself, or
    a server-side failure surfaced only on the redirected page would be
    silently missed and reported as `submitted: True`.
    """
    saltos = 0
    while response.next_request is not None and saltos < _MAX_REDIRECTS:
        saltos += 1
        client.validar_mesma_origem(str(response.next_request.url))
        response = await client.http_send(response.next_request, follow_redirects=False)
    return response


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
    """Execute a previously inspected plan without JS input or arbitrary URLs."""
    snapshot = await _load_snapshot(client, page_ref)
    body, final_url, plan = await _resolve_plan(client, snapshot, trigger_id, confirmar=confirmar)

    if plan["kind"] == "direct_get":
        response = await _execute_direct_get(client, final_url, plan)
    elif plan["kind"] == "form_submit":
        response = await _execute_form_submit(
            client, body, final_url, plan, overrides, submit_button
        )
    else:
        message = f"Plano não executável: {plan['kind']!r}."
        raise SEIValidationError(message)

    response = await _follow_redirects_same_origin(client, response)
    _check(response)
    response_body = _decode_response(response.content, response.headers.get("content-type", ""))
    if error := _extrair_erro_sei(response_body):
        raise SEIConnectionError(error)

    client.invalidar_cache_arvore_completo()
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
