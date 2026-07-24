"""functions/function_app.py — host-entrypoint.

Registreert alle Azure Functions-triggers voor deze app, naar het model van
travel-experts-backend/apps/syncs/functions.py + apps/main (Flask-via-WSGI).
Elke sub-app levert een `func.Blueprint` genaamd `bp`, hier geregistreerd.

Fase 2 (Odoo-consolidatie + plugin/feature-port): registreert
`features/{vat_return,sbmov,translation_check}` (HTTP, `auth_level=FUNCTION`,
zie docs/contracts.md §2) en `syncs/` (timer + HTTP, al pakket-gebaseerd).
De 8 import-plugins (`plugins/`) hebben GEEN eigen HTTP-trigger — import loopt
via de queue (fase 3, `import_processor.py`), zie docs/contracts.md §1/§3.

Fase 3 (queue-based import): registreert `import_processor` (queue-trigger,
GEEN `auth_level` — dit is geen HTTP-trigger, zie docs/contracts.md §1: web
schrijft de queue-message rechtstreeks met de Azure Storage Queue SDK, geen
function-key nodig voor dit pad).

BELANGRIJK (fase-1-scaffold-invariant, nu bewust doorbroken door fase 2): dit
bestand importeert nu wel degelijk Odoo-/SQL-/Blob-afhankelijke code
(`features/`, `syncs/`) - vanaf fase 2 vereist `func start` dus een volledige
Track-A-env (zie `env.py` + `docs/contracts.md` §6). Dat is de bedoelde
verstrakking van harde projectregel #7/#8 ("crash voor het eerste gebruik,
geen stille fallback") en dus infra-gated: lokaal draaien zonder de echte
ODOO_*/DB_SCHEMA/SQL_CONNECTION_STRING/AZURE_*-vars in `local.settings.json`
(mens-only, zie de harde projectregels) laat de host bewust falen bij het
opstarten. `health/functions.py` blijft zelf odoo-vrij (geen wijziging), maar
de host als geheel is dat niet meer.
"""

from __future__ import annotations

import azure.functions as func

from features.sbmov.functions import bp as sbmov_bp
from features.translation_check.functions import bp as translation_check_bp
from features.vat_return.functions import bp as vat_return_bp
from health.functions import bp as health_bp
from import_processor import bp as import_processor_bp
from syncs.functions import bp as syncs_bp

app = func.FunctionApp()

app.register_functions(health_bp)  # GET /ping - zie docs/contracts.md §2.9
app.register_functions(vat_return_bp)  # /vat-return/* - zie docs/contracts.md §2.1-2.4
app.register_functions(sbmov_bp)  # /sbmov/* - zie docs/contracts.md §2.5-2.6
app.register_functions(translation_check_bp)  # /translation-check/* - §2.7-2.8
app.register_functions(syncs_bp)  # timer-syncs + /sync/csv-blob (al pakket-gebaseerd)
app.register_functions(import_processor_bp)  # queue-trigger - zie docs/contracts.md §1/§3
