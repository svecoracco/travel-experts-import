"""ImportPlugin-ABC + dataclasses — poort van
`travel-experts-backend/apps/main/app/plugins/base.py` (1-op-1, zuivere
dataclasses, geen Odoo-toegang, geen wijziging).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# Optionele progress-callback: (phase, current, total, message) -> None
ProgressCallback = Optional[Callable[[str, int, int, str], None]]


@dataclass
class PluginMeta:
    name: str
    display_name: str
    accepted_extensions: list[str]
    description: str = ""


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    row_count: int = 0


@dataclass
class ParsedData:
    items: list[Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MovePayload:
    payload: dict[str, Any]
    move_type: str
    ref: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    created: int = 0
    skipped: int = 0
    errors: int = 0
    items_processed: int = 0
    skip_report_rows: list[dict] = field(default_factory=list)
    log_messages: list[str] = field(default_factory=list)
    extra_report_data: dict[str, Any] = field(default_factory=dict)


class ImportPlugin(ABC):
    """Abstracte basisklasse voor alle import-plugins."""

    @abstractmethod
    def get_meta(self) -> PluginMeta: ...

    @abstractmethod
    def validate_file(self, file_path: Path) -> ValidationResult: ...

    @abstractmethod
    def parse(self, file_path: Path, config: dict[str, Any]) -> ParsedData: ...

    @abstractmethod
    def build_moves(
        self,
        parsed: ParsedData,
        odoo_client: Any,
        config: dict[str, Any],
        company_id: int,
        on_progress: ProgressCallback = None,
    ) -> list[MovePayload]: ...

    @abstractmethod
    def execute(
        self,
        moves: list[MovePayload],
        odoo_client: Any,
        company_id: int,
        dry_run: bool = False,
        auto_post: bool = True,
        auto_reconcile: bool = True,
        on_progress: ProgressCallback = None,
    ) -> ExecutionResult: ...
