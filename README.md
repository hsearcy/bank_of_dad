# Bank of Dad

A local, kid-friendly savings dashboard for tracking imaginary family balances, allowances, savings interest, and fixed-term investments. It does not connect to a bank or move real money.

## Start

Requires Python 3 and `venv`. From this directory:

```sh
./run.sh
```

The script creates a local virtual environment, installs Flask, and starts the app. Open **http://127.0.0.1:5001** on the same machine. On first visit, set a 4–8 digit Dad PIN, open **Dad**, and add a kid by name and emoji. A fresh database starts empty; no example family or transaction history is included. Newly added kids receive the starting wallet amount configured in `config.py`.

Use Dad controls to adjust allowances and rates, give money, edit transactions, or advance the clock for demonstrations. Kids can collect rewards, save, withdraw, invest, and record spending. Changes persist in SQLite. This is an educational simulation; rates and balances are not financial advice or actual accounts.

## Configuration and data

| Variable | Default | Meaning |
| --- | --- | --- |
| `BANK_OF_DAD_HOST` | `127.0.0.1` | Listen address |
| `BANK_OF_DAD_PORT` | `5001` | Listen port |
| `BANK_OF_DAD_DB` | `data/bank.db` in this directory | SQLite database path |

For example, `BANK_OF_DAD_PORT=5005 ./run.sh`. Back up your database separately; it contains children's names, balances, history and the PIN hash. Never commit it, local environment files, or backups to a public repository. The app creates the database directory when needed. Configure rules in `config.py` or use the Dad panel for per-child allowances and rates.

## Security limitations

**Localhost is the safe default. Do not expose this app to the public internet or an untrusted network.** The built-in Flask server is for local use, not a production deployment. The PIN is a child deterrent, not secure authentication: it has a small search space, no rate limiting, and is sent in requests without encryption over HTTP. Before the first PIN is set, administrative API access is not protected; set it immediately on a trusted machine. The kid-facing API is intentionally unauthenticated, and anyone with access can view or modify some data. Network access requires your own strong authentication, transport encryption, network isolation, and security review. No payment or banking integration is provided.

## Tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Tests use temporary databases; set `BANK_OF_DAD_DB` to a disposable path when running any additional experiments because importing `app` initializes the configured database.

## Licensing

No license was included in the source provided for this release candidate. No license is granted by this README; obtain the rights holder's permission and resolve licensing before distributing or publishing.
