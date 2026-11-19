# Bank of Dad

A family savings dashboard to help kids see how saving and compounding work. Track allowances, spending, savings interest, and fixed-term investments. It's an educational simulation—not a real bank, and it never connects to bank accounts or moves money.

Try the [hosted app at thebankofdad.app](https://thebankofdad.app). Self-hosting is free, with no account or subscription required.

## Run it yourself

You'll need Git, Bash, and Python 3.10 or newer with `venv` and pip available. On Linux, your distribution may package `venv` separately.

```sh
git clone https://github.com/hsearcy/bank_of_dad.git
cd bank_of_dad
./run.sh
```

The script creates a `.venv`, installs the Python dependencies, and starts the app. Open **[http://127.0.0.1:5001](http://127.0.0.1:5001)** on the same computer. Press `Ctrl+C` in the terminal to stop it; run `./run.sh` again to return to your saved bank.

1. Open **Dad** and set and confirm a **4–8 digit parent PIN**. There is no default PIN.
2. Add a child with a name and emoji. A new install starts empty; each child gets the starting wallet amount from `config.py` ($5 by default).
3. Set allowances and savings/investment rates in the parent controls. You can also change **Parent name or label** to Mom, Papa, or whatever your family uses.

Kids can collect rewards, move money between their wallet and savings, invest for a fixed term, and record spending. **Wallet** is available to spend; **Total** also includes savings and investments. The default rates are deliberately high to make growth visible to kids—adjust them to suit your family.

A shared tablet can stay on the dashboard, but isn't required. Multiple open dashboards on the same server receive live updates, with periodic refresh as a fallback. Access from another device needs network configuration and the precautions below; the default is accessible only on the computer running the app.

## Settings

Most day-to-day changes live in the parent controls: allowances, rates and periods, giving money, correcting transactions, and child names. `config.py` contains the starting defaults.

**Automatic savings interest is optional and off by default.** Normally, kids collect interest themselves. Enabling it credits pending interest immediately, then compounds once per configured savings period. Self-hosting catches up when the dashboard reads or changes the bank; it does not run an offline background scheduler. See [automatic interest](docs/automatic-interest.md) for details.

You can override the server settings when starting it:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BANK_OF_DAD_HOST` | `127.0.0.1` | Address to listen on; local access only by default |
| `BANK_OF_DAD_PORT` | `5001` | Port to listen on |
| `BANK_OF_DAD_DB` | `data/bank.db` inside the project directory | SQLite database file; its directory is created automatically |

For example, to use a different port:

```sh
BANK_OF_DAD_PORT=5005 ./run.sh
```

## Your data and backups

Everything is saved in the SQLite database: children's names, balances, transaction history, settings, and the parent PIN hash. Keep it and any backups private; don't commit them or local environment files to Git.

For a simple backup, stop the app with `Ctrl+C`, copy `data/bank.db` somewhere safe, then restart it. If you set `BANK_OF_DAD_DB`, back up that file instead. To restore, stop the app and replace the database with your backup before restarting. Back up before updating the app, too.

Renaming a child preserves their money and history. **Removing a child permanently deletes their balances, rewards, investments, and history** after confirmation; it doesn't affect other children. Keep a backup if you might want to undo it.

## Keep it local

**Don't expose the default self-hosted app to the public internet or an untrusted network.** It uses Flask's local development server, not a production server. The PIN is a child deterrent, not secure authentication: child-facing actions are intentionally unauthenticated, and anyone with access can view or change some data. Plain HTTP does not encrypt the PIN or other requests.

Set up the initial PIN on a trusted computer. Access beyond localhost requires your own strong authentication, HTTPS, network isolation, and security review—not just changing `BANK_OF_DAD_HOST`. A parent PIN alone does not make a home-network installation secure.

## Screenshots

These examples use fictional names and sample balances. Your self-hosted bank starts empty.

![Bank of Dad dashboard with fictional children's savings and investments](docs/screenshots/dashboard.png)

[See the expanded money-over-time graph](docs/screenshots/money-over-time.png).

## Development

The optional [hosted deployment guide](cloudflare/README.md) is separate from the free local setup above; no Cloudflare services or payment-provider credentials are needed to self-host.

## License

[MIT](LICENSE).
