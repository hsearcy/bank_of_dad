"""
Bank of Dad — Flask app.

Run:  python app.py
Host/port are configurable via env (so we don't assume any fixed port):
  BANK_OF_DAD_HOST  (default 127.0.0.1 — local access only)
  BANK_OF_DAD_PORT  (default 5001)
"""

import os

from flask import Flask, jsonify, render_template, request

import bank
import config
import db
import hosted

app = Flask(__name__)
# Always revalidate static assets so a pushed update shows on the tablet without
# a manual hard-refresh (low-traffic local app, caching buys us nothing here).
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
if hosted.enabled():
    hosted.Hosted(app)
else:
    db.init_db()


def cfg(conn):
    """Kid-facing rules, for the UI to explain how money grows (Dad-tunable
    savings + investment rates come from the DB, else config defaults)."""
    return {
        "allowance_dollars": config.ALLOWANCE_CENTS / 100,
        "allowance_period_days": config.ALLOWANCE_PERIOD_DAYS,
        "savings_weekly_pct": round(bank.savings_weekly_rate(conn) * 100, 2),
        "min_move_cents": config.MIN_MOVE_CENTS,
        "bonds": [
            {"key": k, "label": v["label"], "days": v["days"],
             "return_pct": bank.bond_return_pct(conn, k), "emoji": v["emoji"]}
            for k, v in config.BONDS.items()
        ],
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/sw.js")
def service_worker():
    # served from root so its scope covers the whole app
    resp = app.send_static_file("hosted-sw.js" if hosted.enabled() else "sw.js")
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/state")
def api_state():
    conn = db.get_conn()
    try:
        return jsonify({
            "kids": bank.all_kids_state(conn),
            "config": cfg(conn),
            "server_now": bank.now_ts(conn),
        })
    finally:
        conn.close()


def _kid_id():
    return int(request.json["kid_id"])


def _amount():
    return int(request.json.get("amount_cents", 0))


@app.route("/api/collect", methods=["POST"])
def api_collect():
    conn = db.get_conn()
    try:
        kind = (request.json or {}).get("kind", "all")
        collected = bank.collect_rewards(conn, _kid_id(), kind)
        return jsonify({"ok": True, "collected_cents": collected})
    finally:
        conn.close()


@app.route("/api/save", methods=["POST"])
def api_save():
    conn = db.get_conn()
    try:
        ok, msg = bank.move_to_savings(conn, _kid_id(), _amount())
        return jsonify({"ok": ok, "message": msg})
    finally:
        conn.close()


@app.route("/api/withdraw", methods=["POST"])
def api_withdraw():
    conn = db.get_conn()
    try:
        ok, msg = bank.withdraw_savings(conn, _kid_id(), _amount())
        return jsonify({"ok": ok, "message": msg})
    finally:
        conn.close()


@app.route("/api/bond", methods=["POST"])
def api_bond():
    conn = db.get_conn()
    try:
        ok, msg = bank.buy_bond(conn, _kid_id(),
                                request.json["term"], _amount())
        return jsonify({"ok": ok, "message": msg})
    finally:
        conn.close()


@app.route("/api/spend", methods=["POST"])
def api_spend():
    conn = db.get_conn()
    try:
        ok, msg = bank.spend(conn, _kid_id(), _amount(),
                             request.json.get("reason", ""))
        return jsonify({"ok": ok, "message": msg})
    finally:
        conn.close()


@app.route("/api/transactions")
def api_transactions():
    conn = db.get_conn()
    try:
        kid_id = int(request.args["kid_id"])
        return jsonify({"transactions": bank.transactions(conn, kid_id),
                        "server_now": bank.now_ts(conn)})
    finally:
        conn.close()


# ----- Dad / admin -----
def _pin():
    """PIN supplied with an admin request (header preferred, body fallback)."""
    return request.headers.get("X-Dad-Pin") or (request.json or {}).get("pin")


def _pin_ok(conn):
    """403 JSON response if the Dad PIN is set and wasn't supplied/correct;
    otherwise None (allowed)."""
    if hosted.enabled():
        return None  # Hosted.guard has already authenticated and authorized the parent.
    if not bank.check_dad_pin(conn, _pin()):
        return jsonify({"ok": False, "error": "Wrong PIN"}), 403
    return None


@app.route("/api/admin/status")
def api_admin_status():
    conn = db.get_conn()
    try:
        return jsonify({"pin_set": bank.dad_pin_set(conn)})
    finally:
        conn.close()


@app.route("/api/admin/verify", methods=["POST"])
def api_admin_verify():
    conn = db.get_conn()
    try:
        return jsonify({"ok": bank.check_dad_pin(conn, _pin())})
    finally:
        conn.close()


@app.route("/api/admin/pin", methods=["POST"])
def api_admin_set_pin():
    conn = db.get_conn()
    try:
        ok, msg = bank.set_dad_pin(
            conn,
            (request.json or {}).get("new_pin"),
            (request.json or {}).get("current_pin"),
        )
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 403)
    finally:
        conn.close()


@app.route("/api/admin/gift", methods=["POST"])
def api_gift():
    conn = db.get_conn()
    try:
        denied = _pin_ok(conn)
        if denied:
            return denied
        body = request.json or {}
        # "reason" is what the Spend flow calls it; accept either spelling so the
        # add-money and spend APIs feel the same.
        note = body.get("note", body.get("reason", "")) or ""
        ok = bank.gift(conn, _kid_id(), _amount(), note)
        return jsonify({"ok": ok})
    finally:
        conn.close()


@app.route("/api/admin/kid", methods=["POST"])
def api_add_kid():
    conn = db.get_conn()
    try:
        denied = _pin_ok(conn)
        if denied:
            return denied
        bank.add_kid(conn, request.json["name"],
                     request.json.get("emoji", "🐣"),
                     request.json.get("color", "#4f9dde"))
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/admin/kid/update", methods=["POST"])
def api_update_kid():
    conn = db.get_conn()
    try:
        denied = _pin_ok(conn)
        if denied:
            return denied
        bank.update_kid(conn, _kid_id(), request.json.get("name"),
                        request.json.get("emoji"), request.json.get("color"))
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/admin/kid/allowance", methods=["POST"])
def api_set_allowance():
    conn = db.get_conn()
    try:
        denied = _pin_ok(conn)
        if denied:
            return denied
        body = request.json or {}
        ok = bank.set_allowance(conn, _kid_id(),
                                int(body.get("amount_cents", 0)),
                                int(body.get("period_days", 14)),
                                body.get("enabled", True))
        return jsonify({"ok": ok})
    finally:
        conn.close()


@app.route("/api/admin/tx/delete", methods=["POST"])
def api_tx_delete():
    conn = db.get_conn()
    try:
        denied = _pin_ok(conn)
        if denied:
            return denied
        ok, msg = bank.delete_transaction(conn, int(request.json["tx_id"]))
        return jsonify({"ok": ok, "message": msg})
    finally:
        conn.close()


@app.route("/api/admin/tx/edit", methods=["POST"])
def api_tx_edit():
    conn = db.get_conn()
    try:
        denied = _pin_ok(conn)
        if denied:
            return denied
        amt = request.json.get("amount_cents")
        ok, msg = bank.edit_transaction(conn, int(request.json["tx_id"]),
                                        None if amt is None else int(amt),
                                        request.json.get("note"))
        return jsonify({"ok": ok, "message": msg})
    finally:
        conn.close()


@app.route("/api/admin/rates", methods=["POST"])
def api_set_rates():
    conn = db.get_conn()
    try:
        denied = _pin_ok(conn)
        if denied:
            return denied
        body = request.json or {}
        bank.set_rates(conn, body.get("savings_weekly_pct"), body.get("returns") or {})
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/admin/fastforward", methods=["POST"])
def api_fastforward():
    conn = db.get_conn()
    try:
        denied = _pin_ok(conn)
        if denied:
            return denied
        bank.fast_forward(conn, float(request.json.get("days", 7)))
        return jsonify({"ok": True})
    finally:
        conn.close()


if __name__ == "__main__":
    host = os.environ.get("BANK_OF_DAD_HOST", "127.0.0.1")
    port = int(os.environ.get("BANK_OF_DAD_PORT", "5001"))
    app.run(host=host, port=port, debug=False)
