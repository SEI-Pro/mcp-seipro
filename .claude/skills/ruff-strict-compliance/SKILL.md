---
name: ruff-strict-compliance
description: >-
  Enforces strict compliance with Ruff linting and formatting rules. Stops agents from using '# noqa' comments, dismissing warnings as "stylistic," or claiming rules do not apply to CLI tools like Cyclopts (or Typer).
---

# Ruff Strict Compliance

## Overview
This skill enforces **strict, zero-warning compliance** with the Ruff linter and formatter. Coding agents often vibe-code around linter errors by dismissing them as "stylistic/optional" or lazy-patching them with `# noqa` comments. This skill bans those practices and mandates writing clean, idiomatic Python that naturally passes Ruff.

---

## Core Mandates

### 1. No Excuses, No Dismissals
* You are **never** allowed to dismiss a Ruff warning as "purely stylistic," "optional," or "non-critical." 
* You must fix the underlying code pattern. If Ruff flags it, it is a code smell.

### 2. Zero-Tolerance for `# noqa`
* Do **NOT** add `# noqa` or `# type: ignore` comments to bypass Ruff alerts.
* You may only use `# noqa` if there is a documented, unavoidable library conflict (e.g., importing a module to trigger side effects in a legacy framework where there is no entrypoint). Even then, you must seek explicit user approval first.

### 3. Verification
* After editing any Python code, you must run `ruff check` and `ruff format` to verify compliance. Do not wait for the user to report lint failures.

---

## Refactoring Recipes for Common Rules

### 1. Cyclopts / Typer CLI (Resolving B008, F841, etc.)

**The Problem:** In Cyclopts (and Typer before it), developers often write CLI options using `cyclopts.Parameter(...)` / `typer.Option(...)` directly in function signatures as default values. This triggers Ruff rule **B008** (Do not perform function call in argument defaults).

To bypass this, lazy agents write `# noqa: B008` or `# noqa: F841`.

**The Right Way (Annotated):**
Use Python's `typing.Annotated` to define options and arguments. This completely avoids B008 and is the modern, type-safe standard recommended by both frameworks. Note that `@app.command(name=...)` needs `name=` as a keyword — passing the name positionally (`@app.command("setup")`) is interpreted as the object to decorate, not the command name, and breaks at runtime.

```python
# ❌ INCORRECT (Triggers B008 and F841)
import cyclopts

app = cyclopts.App()

@app.command
def main(
    name: str = cyclopts.Parameter("World", help="Who to greet"), # B008 triggered here
    verbose: bool = cyclopts.Parameter(False, "--verbose", "-v") # B008 triggered here
):
    print(f"Hello {name}")
```

```python
#  CORRECT (Modern Type-Safe Pattern - 0 Ruff Warnings)
import cyclopts
from typing import Annotated

app = cyclopts.App()

@app.command
def main(
    name: Annotated[str, cyclopts.Parameter(help="Who to greet")] = "World",
    verbose: Annotated[bool, cyclopts.Parameter(name="--verbose")] = False
):
    print(f"Hello {name}")
```

---

### 2. Mutable Default Arguments (B006 / B008)

**The Problem:** Using mutable structures like empty lists `[]` or dicts `{}` or function calls as default arguments.

```python
# ❌ INCORRECT (B006/B008)
def process_items(items: list = [], config: dict = dict()):
    items.append("new_item")
    return items
```

```python
#  CORRECT
from typing import Optional

def process_items(items: Optional[list] = None, config: Optional[dir] = None):
    if items is None:
        items = []
    if config is None:
        config = {}
    items.append("new_item")
    return items
```

---

### 3. Unused Imports & Variables (F401, F841)

**The Problem:** Leaving debug imports or assigning variables that are never read.

```python
# ❌ INCORRECT (F401/F841)
import os  # F401: unused import
import sys # noqa: F401 (Banned bypass!)

def compute(x):
    result = x * 2  # F841: local variable is assigned but never used
    return x
```

```python
#  CORRECT
def compute(x):
    return x
```

---

### 4. Star Imports (F403 / F405)

**The Problem:** Importing everything from a module (`from module import *`), which pollutes the namespace and breaks static analysis.

```python
# ❌ INCORRECT (F403)
from math import * # noqa: F403
```

```python
#  CORRECT
from math import pi, sin, cos
```

---

### 5. Blind Exceptions (BLE) & Tryceratops (TRY)

**The Problem:** Coding agents often catch broad, generic exceptions (`Exception`) to suppress errors, triggering rule **BLE001** (Do not catch blind `Exception`). Furthermore, they use generic `Exception` types or embed verbose strings directly in raised errors, triggering Tryceratops (**TRY**) rules (e.g., `TRY002` for custom exceptions, `TRY003` for avoiding long exception messages).

```python
# ❌ INCORRECT (Triggers BLE001, TRY002, TRY003)
def process_data(data):
    try:
        return data["value"] * 10
    except Exception: # BLE001 (blind exception caught)
        raise Exception("We failed to process the data because the value key was missing or malformed") # TRY002 (raw Exception), TRY003 (long string)
```

```python
#  CORRECT (Modern Standard - 0 Ruff Warnings)
class DataProcessingError(Exception):
    """Custom exception for domain-specific errors."""
    pass

def process_data(data: dict) -> int:
    try:
        return data["value"] * 10
    except KeyError as e: # Specific Exception
        raise DataProcessingError("Missing 'value' key") from e
    except TypeError as e: # Specific Exception
        raise DataProcessingError("Value is not a number") from e
```

---

### 6. Legacy `os.path` vs Modern `pathlib` (PTH)

**The Problem:** Using legacy string-based path manipulations (`os.path.join`, `os.path.exists`, `os.path.abspath`) instead of Python's modern object-oriented `pathlib.Path`. This is a very common legacy habit that Ruff flags under **PTH** rules.

```python
# ❌ INCORRECT (Triggers PTH118, PTH110, etc.)
import os

def get_config_content(filename):
    full_path = os.path.join(os.getcwd(), "config", filename)
    if os.path.exists(full_path):
        with open(full_path, "r") as f:
            return f.read()
    return ""
```

```python
#  CORRECT (Modern Pathlib - 0 Ruff Warnings)
from pathlib import Path

def get_config_content(filename: str) -> str:
    full_path = Path.cwd() / "config" / filename
    if full_path.exists():
        return full_path.read_text(encoding="utf-8")
    return ""
```

---

### 7. Mutable Class Attributes (RUF012)

**The Problem:** Declaring a mutable collection (like a list or dictionary) as a class attribute without explicitly annotating it as a `ClassVar` or defining it inside `__init__`. In standard classes and dataclasses, this results in shared state among all instances, causing subtle bugs. Ruff flags this under **RUF012**.

```python
# ❌ INCORRECT (Triggers RUF012)
class ProjectManager:
    active_tasks: list[str] = [] # RUF012 (mutable class attribute)
    default_config: dict[str, str] = {} # RUF012
```

```python
#  CORRECT (For static class variables)
from typing import ClassVar

class ProjectManager:
    active_tasks: ClassVar[list[str]] = [] # Explicitly marked as ClassVar
    default_config: ClassVar[dict[str, str]] = {}
```

```python
#  CORRECT (For instance-specific attributes)
class ProjectManager:
    def __init__(self) -> None:
        self.active_tasks: list[str] = []
        self.default_config: dict[str, str] = {}
```

---

### 8. Unnecessary Comprehensions & Functional Wrappers (C4)

**The Problem:** Writing unnecessarily complex or redundant structures like `list([x for x in data])` or functional wrappers instead of clean native syntax. Ruff flags these under **C4** (flake8-comprehensions) rules.

```python
# ❌ INCORRECT (Triggers C408, C416)
def get_names(users):
    names_list = list([u.name for u in users]) # C416 (unnecessary list comprehension inside list())
    empty_dict = dict() # C408 (unnecessary dict() call, use {} literal instead)
    return names_list
```

```python
#  CORRECT (0 Ruff Warnings)
def get_names(users) -> list[str]:
    names_list = [u.name for u in users] # Simple list comprehension
    empty_dict = {} # Dictionary literal
    return names_list
```

---

### 9. Unnecessary Elif/Else and Returns (RET)

**The Problem:** Storing a return value in a temporary variable only to return it on the next line (**RET504**), or using `else` / `elif` branches after a preceding block has already returned or raised (**RET505** / **RET506**).

```python
# ❌ INCORRECT (Triggers RET504, RET505)
def check_value(x):
    if x > 10:
        result = "large"
        return result # RET504 (unnecessary variable assignment before return)
    else: # RET505 (unnecessary else after return statement)
        return "small"
```

```python
#  CORRECT (0 Ruff Warnings)
def check_value(x) -> str:
    if x > 10:
        return "large"
    return "small"
```

---

### 10. Simplify Rules (SIM)

**The Problem:** Writing verbose nested `if` statements or manually returning `True`/`False` based on boolean evaluations, which violates **SIM** (flake8-simplify) rules.

```python
# ❌ INCORRECT (Triggers SIM102, SIM103)
def is_valid_user(user):
    if user.is_active:
        if user.has_permission: # SIM102 (nested ifs can be combined into a single if)
            return True
    return False # SIM103 (unnecessary if-else statement returning boolean)
```

```python
#  CORRECT (0 Ruff Warnings)
def is_valid_user(user) -> bool:
    return bool(user.is_active and user.has_permission)
```

---

### 11. Legacy Type Annotations & Syntax (UP)

**The Problem:** Using deprecated structures or importing typing wrappers like `List`, `Dict`, `Tuple`, or `Set` under Python 3.9+ instead of using lowercase builtins (`list`, `dict`, `tuple`, `set`). These are flagged under **UP** (pyupgrade) rules.

```python
# ❌ INCORRECT (Triggers UP006, UP035)
from typing import List, Dict, Tuple

def process_data(items: List[str]) -> Dict[str, Tuple[int, int]]:
    pass
```

```python
#  CORRECT (Modern Python 3.9+ - 0 Ruff Warnings)
def process_data(items: list[str]) -> dict[str, tuple[int, int]]:
    pass
```

---

### 12. Builtin Shadowing (A)

**The Problem:** Naming variables, function arguments, or class attributes after builtins (like `id`, `type`, `list`, `dict`, `dir`, `input`, `min`, `max`). This is flagged under **A001** / **A002** / **A003**.

```python
# ❌ INCORRECT (Triggers A001, A002)
def get_user_by_id(id: int): # A002 (shadowing builtin 'id')
    list = ["active", "inactive"] # A001 (shadowing builtin 'list')
    return list
```

```python
#  CORRECT (0 Ruff Warnings)
def get_user_by_id(user_id: int) -> list[str]:
    statuses = ["active", "inactive"]
    return statuses
```

---

### 13. Logging Format and F-Strings (G)

**The Problem:** Using f-strings, `.format()`, or string concatenation inside logging statements (e.g. `logger.info(f"User {name} logged in")`). Doing this forces string interpolation immediately, even if the log level is disabled. Ruff flags this under **G004** (logging-f-string).

```python
# ❌ INCORRECT (Triggers G004)
import logging
logger = logging.getLogger(__name__)

def log_event(name):
    logger.info(f"Processing event: {name}") # G004 (logging statement uses f-string)
```

```python
#  CORRECT (Modern Standard - 0 Ruff Warnings)
def log_event(name: str):
    logger.info("Processing event: %s", name) # Lazy evaluation (highly performant)
```

---

### 14. Insecure Assertions & Silently Swallowed Exceptions (S)

**The Problem:** Using `assert` statements to validate program inputs or critical runtime flow in production code (**S101**). When Python is run in optimized mode (`-O`), all assert statements are stripped out, rendering validation useless. Also, catching exceptions and swallowing them using `pass` or `continue` without logging is flagged under **S110** / **S112**.

```python
# ❌ INCORRECT (Triggers S101, S110)
def process_age(age: int):
    assert age >= 0, "Age cannot be negative" # S101 (assert used in production)
    try:
        do_something()
    except ValueError:
        pass # S110 (try-except-pass detected, exception silently swallowed)
```

```python
#  CORRECT (0 Ruff Warnings)
def process_age(age: int):
    if age < 0:
        raise ValueError("Age cannot be negative")
    try:
        do_something()
    except ValueError as e:
        logger.warning("Swallowing expected error: %s", e)
```

---

### 15. Commented-Out Code (ERA)

**The Problem:** Leaving commented-out chunks of code in files, which clutters files. This is flagged under **ERA001**.

```python
# ❌ INCORRECT (Triggers ERA001)
def calculate(x):
    # print(f"debugging x: {x}") # ERA001 (Found commented-out code)
    return x * 2
```

```python
#  CORRECT (0 Ruff Warnings)
def calculate(x: int) -> int:
    return x * 2
```

---

## Checklist Before Ending Your Turn

1. **Format:** Run `ruff format .` to make sure all code matches the project styling.
2. **Lint:** Run `ruff check .` to inspect all warnings.
3. **Fix:** Refactor any violations using the clean patterns described above. Do **NOT** use `# noqa`.
