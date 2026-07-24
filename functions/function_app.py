"""functions/function_app.py — host-entrypoint (Fase 1-scaffold).

Registreert alle Azure Functions-triggers voor deze app, naar het model van
travel-experts-backend/function_app.py + apps/syncs/functions.py (elke
sub-app levert een `func.Blueprint` genaamd `bp`, hier geregistreerd).

Vandaag: alleen `health/functions.py` (triviale ping). Fase 2/3 breiden dit
uit met eigen Blueprints, bv.:

    from syncs.functions import bp as syncs_bp
    from features.vat_return.functions import bp as vat_return_bp
    from features.sbmov.functions import bp as sbmov_bp
    from features.translation_check.functions import bp as translation_check_bp
    from import_processor import bp as import_processor_bp

    app.register_functions(syncs_bp)
    app.register_functions(vat_return_bp)
    ...

BELANGRIJK (fase 1-scaffold-regel, blijft gelden totdat fase 2 start): dit
bestand en alles wat het (transitief) importeert bevat GEEN
`from odoo import ...` en GEEN `import xmlrpc`. Het private `odoo`-pakket kan
in de scaffold-omgeving niet geïnstalleerd worden (geen GH_TOKEN beschikbaar
buiten CI) — `func start` moet hier "leeg-maar-gezond" draaien met alleen de
publieke deps (`azure-functions`). Odoo-afhankelijke code komt pas in fase 2
(functions/odoo_conn.py + shared/ + plugins/ + features/).
"""

from __future__ import annotations

import azure.functions as func

from health.functions import bp as health_bp

app = func.FunctionApp()

app.register_functions(health_bp)  # GET /ping — zie docs/contracts.md §2.9
