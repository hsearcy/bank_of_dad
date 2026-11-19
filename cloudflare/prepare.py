"""Bundle only the actual app modules and its existing Jinja templates.

Never recursively bundle a checkout: it can contain virtualenvs or private state.
"""
from pathlib import Path
import os
import tempfile

here = Path(__file__).resolve().parent
root = here.parent
out = here / 'build'
out.mkdir(exist_ok=True)
modules = ('app.py', 'bank.py', 'config.py', 'db.py', 'hosted.py', 'hosted_access.py',
           'guest_bank.py', 'owner_panel.py', 'cloudflare_owner.py', 'cloudflare_auth.py', 'cloudflare_billing.py', 'cloudflare_hosted.py', 'cloudflare_sql.py', 'cloudflare_worker.py', 'cloudflare_security.py')
def stage(path, data):
    # Concurrent local runtime builds must never expose an empty/truncated module.
    # Avoid unchanged rewrites that can also trigger unnecessary runtime reloads.
    if path.exists() and path.read_bytes() == data:
        return
    with tempfile.NamedTemporaryFile(dir=out, prefix='.stage-', delete=False) as file:
        temporary = Path(file.name)
        file.write(data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


for name in modules:
    stage(out / name, (root / name).read_bytes())
templates = {p.name: p.read_text() for p in sorted((root / 'templates').glob('*.html'))}
stage(out / 'cloudflare_templates.py', ('# Generated; do not edit.\nTEMPLATES = ' + repr(templates) + '\n').encode())
