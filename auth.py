"""Local account authentication and per-page access control."""

from __future__ import annotations

import hmac
import os
import re
import secrets
import sqlite3
import json
import time
from functools import wraps
from html import escape
from pathlib import Path
from datetime import timedelta
from urllib.parse import urlparse

from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
    request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

auth = Blueprint("auth", __name__)
PAGES = {"search": "جست‌وجوی آگهی", "estimate": "تخمین قیمت"}
ACTIVITY_ACTIONS = {
    "login": "ورود موفق", "logout": "خروج", "login_failed": "ورود ناموفق",
    "login_blocked": "ورود مسدودشده", "limit_reached": "پایان سهمیه",
    "page_view": "بازکردن صفحه", "ad_detail": "مشاهدهٔ آگهی",
    "search": "جست‌وجو", "csv_export": "دریافت CSV", "xlsx_export": "دریافت Excel",
    "estimate": "تخمین قیمت", "admin_created": "ساخت مدیر اصلی",
    "user_created": "ساخت کاربر", "user_updated": "تغییر تنظیمات کاربر",
    "admin_page_view": "بازکردن صفحهٔ مدیریت", "session_expired": "پایان نشست",
}
USERNAME_RE = re.compile(r"^[\w.@+-]{3,50}$", re.UNICODE)


def _database_path() -> Path:
    configured = os.environ.get("DIVAR_AUTH_DB")
    return Path(configured).expanduser() if configured else Path(current_app.root_path) / "instance" / "users.sqlite3"


def _connect() -> sqlite3.Connection:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            max_reports INTEGER NOT NULL DEFAULT 0,
            reports_used INTEGER NOT NULL DEFAULT 0,
            max_logins INTEGER NOT NULL DEFAULT 0,
            logins_used INTEGER NOT NULL DEFAULT 0,
            must_reset_password INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    existing_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    for name, declaration in (
        ("max_reports", "INTEGER NOT NULL DEFAULT 0"),
        ("reports_used", "INTEGER NOT NULL DEFAULT 0"),
        ("max_logins", "INTEGER NOT NULL DEFAULT 0"),
        ("logins_used", "INTEGER NOT NULL DEFAULT 0"),
        ("must_reset_password", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in existing_columns:
            connection.execute(f"ALTER TABLE users ADD COLUMN {name} {declaration}")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS user_pages (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            page TEXT NOT NULL,
            PRIMARY KEY (user_id, page)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            description TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_activity_user_time ON activity_log(user_id, id DESC)")
    connection.commit()
    return connection


def _ensure_secret(app) -> str:
    configured = os.environ.get("SECRET_KEY")
    if configured:
        return configured
    path = Path(app.root_path) / "instance" / "session_secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = secrets.token_urlsafe(48)
        try:
            with path.open("x", encoding="utf-8") as secret_file:
                secret_file.write(value)
            return value
        except FileExistsError:
            return path.read_text(encoding="utf-8").strip()


def init_auth(app) -> None:
    app.secret_key = _ensure_secret(app)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=10),
    )
    app.before_request(_check_csrf)
    app.before_request(_enforce_session_age)
    app.after_request(_private_response_headers)


def _private_response_headers(response):
    if request.endpoint and not request.endpoint.startswith("static"):
        response.headers["Cache-Control"] = "no-store, private"
    return response


def _check_csrf():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    expected = session.get("_csrf")
    supplied = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        if request.is_json:
            return jsonify({"detail": "درخواست معتبر نیست؛ صفحه را تازه‌سازی کن."}), 400
        abort(400, description="درخواست معتبر نیست؛ صفحه را تازه‌سازی کن.")
    return None


def _enforce_session_age():
    started_at = session.get("_auth_started_at")
    if not started_at or time.time() - started_at < 10 * 60:
        return None
    identity = session.get("user_id")
    if identity:
        record_activity(identity, "session_expired", "نشست پس از ۱۰ دقیقه پایان یافت.")
    session.clear()
    return None


def csrf_token() -> str:
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def _get_user(user_id=None):
    identity = user_id if user_id is not None else session.get("user_id")
    if not identity:
        return None
    connection = _connect()
    try:
        user = connection.execute(
            "SELECT id, username, is_admin, is_active, must_reset_password FROM users WHERE id = ?", (identity,)
        ).fetchone()
        if not user or not user["is_active"] or user["must_reset_password"]:
            return None
        pages = {
            row["page"] for row in connection.execute(
                "SELECT page FROM user_pages WHERE user_id = ?", (user["id"],)
            )
        }
        return {
            "id": user["id"], "username": user["username"],
            "is_admin": bool(user["is_admin"]), "pages": pages,
        }
    finally:
        connection.close()


def _insert_activity(connection, user_id, username, action, description, details=None):
    connection.execute(
        """INSERT INTO activity_log
           (user_id, username, action, description, details_json, ip_address, user_agent)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id, username or "نام‌کاربری نامشخص", action, description,
            json.dumps(details or {}, ensure_ascii=False, default=str),
            request.remote_addr,
            request.user_agent.string[:500] if request.user_agent else None,
        ),
    )


def record_activity(user_id, action, description, details=None, username=None):
    connection = _connect()
    try:
        if not username and user_id:
            row = connection.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
            username = row["username"] if row else None
        _insert_activity(connection, user_id, username, action, description, details)
        connection.commit()
    finally:
        connection.close()


def consume_report_quota(user, action, description, details=None):
    """Log one report attempt and atomically enforce the account report allowance."""
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT max_reports, reports_used, must_reset_password FROM users WHERE id = ?",
            (user["id"],),
        ).fetchone()
        if not row:
            return False
        if not user["is_admin"] and row["must_reset_password"]:
            _insert_activity(connection, user["id"], user["username"], "blocked", "حساب نیازمند بازنشانی گذرواژه است.", details)
            connection.commit()
            return False
        if not user["is_admin"] and row["max_reports"] and row["reports_used"] >= row["max_reports"]:
            connection.execute("UPDATE users SET must_reset_password = 1 WHERE id = ?", (user["id"],))
            _insert_activity(connection, user["id"], user["username"], "limit_reached", "سهمیهٔ گزارش‌گیری تمام شد؛ حساب تا بازنشانی گذرواژه قفل شد.", details)
            connection.commit()
            return False
        if not user["is_admin"]:
            connection.execute("UPDATE users SET reports_used = reports_used + 1 WHERE id = ?", (user["id"],))
        _insert_activity(connection, user["id"], user["username"], action, description, details)
        connection.commit()
        return True
    finally:
        connection.close()


def _quota_value(raw, label):
    try:
        value = int(str(raw or "0").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} باید عدد صحیح باشد.") from exc
    if not 0 <= value <= 1_000_000:
        raise ValueError(f"{label} باید بین صفر تا یک میلیون باشد؛ صفر یعنی نامحدود.")
    return value


def current_user():
    if "current_user" not in g:
        g.current_user = _get_user()
    return g.current_user


def _can_access(user, page: str) -> bool:
    return bool(user and (user["is_admin"] or page in user["pages"]))


def require_page(page: str):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                if request.path.startswith(("/ads", "/catalog", "/estimate/property")):
                    return jsonify({"detail": "برای استفاده ابتدا وارد حساب شو."}), 401
                return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
            if not _can_access(user, page):
                if request.path.startswith(("/ads", "/catalog", "/estimate/property")):
                    return jsonify({"detail": "دسترسی به این بخش برای حساب شما فعال نیست."}), 403
                abort(403)
            if request.method == "GET" and request.path in {"/", "/estimate"}:
                record_activity(user["id"], "page_view", f"صفحهٔ «{PAGES[page]}» باز شد.", {"path": request.path})
            elif request.method == "GET" and request.path.startswith("/ads/"):
                record_activity(user["id"], "ad_detail", "مشخصات یک آگهی باز شد.", {"token": request.view_args.get("token", "")})
            return view(*args, **kwargs)
        return wrapped
    return decorate


def require_any_page(*pages: str):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                return jsonify({"detail": "برای استفاده ابتدا وارد حساب شو."}), 401
            if not user["is_admin"] and not any(page in user["pages"] for page in pages):
                return jsonify({"detail": "دسترسی به این بخش برای حساب شما فعال نیست."}), 403
            return view(*args, **kwargs)
        return wrapped
    return decorate


def user_page_response(filename: str):
    """Serve an existing app page with a session-aware menu and CSRF token."""
    page = (Path(current_app.root_path) / filename).read_text(encoding="utf-8")
    page = page.replace(
        "</head>",
        '<link rel="stylesheet" href="/static/auth.css">'
        f'<meta name="csrf-token" content="{escape(csrf_token(), quote=True)}"></head>',
        1,
    )
    # Put the navigation inside body while retaining any attributes on the body tag.
    match = re.search(r"<body\b[^>]*>", page, flags=re.IGNORECASE)
    if match:
        page = page[:match.end()] + '<nav class="account-nav" id="account-nav" aria-label="حساب کاربری"></nav>' + page[match.end():]
    page = page.replace(
        "</body>",
        '<script src="/static/auth-ui.js" defer></script></body>',
        1,
    )
    return current_app.response_class(page, mimetype="text/html")


def _safe_next(candidate: str | None) -> str:
    if not candidate:
        return url_for("home")
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate:
        return url_for("home")
    return candidate


def _landing_for_user(user) -> str:
    if user["is_admin"] or "search" in user["pages"]:
        return url_for("home")
    if "estimate" in user["pages"]:
        return url_for("estimate_page")
    return url_for("access_denied")


@auth.route("/setup", methods=["GET", "POST"])
def setup():
    connection = _connect()
    try:
        has_users = connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
        if has_users:
            return redirect(url_for("auth.login"))
        if request.method == "POST":
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
                connection.rollback()
                return redirect(url_for("auth.login"))
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirmation = request.form.get("password_confirm", "")
            if not USERNAME_RE.fullmatch(username):
                flash("نام کاربری باید ۳ تا ۵۰ نویسه باشد و فقط حروف، عدد و . _ + - @ داشته باشد.", "error")
            elif len(password) < 6:
                flash("گذرواژهٔ مدیر باید دست‌کم ۶ نویسه باشد.", "error")
            elif password != confirmation:
                flash("تکرار گذرواژه با آن یکسان نیست.", "error")
            else:
                try:
                    cursor = connection.execute(
                        "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
                        (username, generate_password_hash(password)),
                    )
                    _insert_activity(connection, cursor.lastrowid, username, "admin_created", "حساب مدیر اصلی ساخته شد.")
                    connection.commit()
                    session.clear()
                    session["user_id"] = cursor.lastrowid
                    session["_auth_started_at"] = time.time()
                    csrf_token()
                    session.permanent = True
                    return redirect(url_for("auth.users"))
                except sqlite3.IntegrityError:
                    connection.rollback()
                    flash("ساخت مدیر انجام نشد؛ صفحه را تازه کن و دوباره تلاش کن.", "error")
        return render_template("setup.html", csrf=csrf_token())
    finally:
        connection.close()


@auth.route("/login", methods=["GET", "POST"])
def login():
    connection = _connect()
    try:
        if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None:
            return redirect(url_for("auth.setup"))
        next_url = _safe_next(request.args.get("next") or request.form.get("next"))
        if request.method == "POST":
            connection.execute("BEGIN IMMEDIATE")
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = connection.execute(
                """SELECT id, username, password_hash, is_active, is_admin, must_reset_password,
                          max_logins, logins_used
                   FROM users WHERE username = ? COLLATE NOCASE""",
                (username,),
            ).fetchone()
            if not user:
                _insert_activity(connection, None, username, "login_failed", "ورود ناموفق؛ حسابی با این نام‌کاربری پیدا نشد.")
                connection.commit()
                flash("نام کاربری یا گذرواژه درست نیست، یا حساب غیرفعال شده است.", "error")
            elif user["must_reset_password"]:
                _insert_activity(connection, user["id"], user["username"], "login_blocked", "ورود رد شد؛ مدیر باید گذرواژه را بازنشانی کند.")
                connection.commit()
                flash("حساب به‌دلیل پایان سهمیه قفل است. از مدیر بخواه گذرواژه را بازنشانی کند.", "error")
            elif not user["is_active"]:
                _insert_activity(connection, user["id"], user["username"], "login_blocked", "ورود رد شد؛ حساب غیرفعال است.")
                connection.commit()
                flash("این حساب غیرفعال است؛ با مدیر تماس بگیر.", "error")
            elif not check_password_hash(user["password_hash"], password):
                _insert_activity(connection, user["id"], user["username"], "login_failed", "گذرواژهٔ واردشده نادرست بود.")
                connection.commit()
                flash("نام کاربری یا گذرواژه درست نیست، یا حساب غیرفعال شده است.", "error")
            elif not user["is_admin"] and user["max_logins"] and user["logins_used"] >= user["max_logins"]:
                connection.execute("UPDATE users SET must_reset_password = 1 WHERE id = ?", (user["id"],))
                _insert_activity(connection, user["id"], user["username"], "limit_reached", "سهمیهٔ ورود تمام شد؛ حساب تا بازنشانی گذرواژه قفل شد.")
                connection.commit()
                flash("سهمیهٔ ورود این حساب تمام شده است. از مدیر بخواه گذرواژه را بازنشانی کند.", "error")
            else:
                if not user["is_admin"]:
                    connection.execute("UPDATE users SET logins_used = logins_used + 1 WHERE id = ?", (user["id"],))
                _insert_activity(connection, user["id"], user["username"], "login", "ورود موفق به سیستم.")
                connection.commit()
                session.clear()
                session["user_id"] = user["id"]
                session["_auth_started_at"] = time.time()
                csrf_token()
                session.permanent = True
                account = {
                    "id": user["id"], "is_admin": bool(user["is_admin"]),
                    "pages": {row["page"] for row in connection.execute(
                        "SELECT page FROM user_pages WHERE user_id = ?", (user["id"],)
                    )},
                }
                return redirect(_landing_for_user(account))
        return render_template("login.html", csrf=csrf_token(), next_url=next_url)
    finally:
        connection.close()


@auth.post("/logout")
def logout():
    identity = session.get("user_id")
    if identity:
        record_activity(identity, "logout", "خروج از سیستم.")
    session.clear()
    return redirect(url_for("auth.login"))


@auth.get("/auth/status")
def status():
    user = current_user()
    if not user:
        return jsonify({"authenticated": False}), 401
    return jsonify({
        "authenticated": True, "username": user["username"],
        "is_admin": user["is_admin"], "pages": sorted(user["pages"]),
        "csrf": csrf_token(),
    })


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        if not user["is_admin"]:
            abort(403)
        if request.method == "GET":
            record_activity(user["id"], "admin_page_view", "مدیر یک صفحهٔ مدیریتی را باز کرد.", {
                "path": request.path, "query": request.args.to_dict(flat=True),
            })
        return view(*args, **kwargs)
    return wrapped


@auth.route("/admin/users", methods=["GET", "POST"])
@require_admin
def users():
    connection = _connect()
    try:
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            is_admin = request.form.get("is_admin") == "on"
            pages = set(request.form.getlist("pages")) & PAGES.keys()
            if not USERNAME_RE.fullmatch(username):
                flash("نام کاربری باید ۳ تا ۵۰ نویسه و بدون فاصله باشد.", "error")
            elif len(password) < 6:
                flash("گذرواژه باید دست‌کم ۶ نویسه باشد.", "error")
            else:
                try:
                    max_reports = _quota_value(request.form.get("max_reports"), "سقف گزارش‌گیری")
                    max_logins = _quota_value(request.form.get("max_logins"), "سقف ورود")
                    cursor = connection.execute(
                        """INSERT INTO users
                           (username, password_hash, is_admin, max_reports, max_logins)
                           VALUES (?, ?, ?, ?, ?)""",
                        (username, generate_password_hash(password), int(is_admin), max_reports, max_logins),
                    )
                    if not is_admin:
                        connection.executemany(
                            "INSERT INTO user_pages (user_id, page) VALUES (?, ?)",
                            [(cursor.lastrowid, page) for page in pages],
                        )
                    _insert_activity(connection, cursor.lastrowid, username, "user_created", "حساب کاربری ساخته شد.", {
                        "max_reports": max_reports, "max_logins": max_logins,
                        "pages": sorted(pages), "is_admin": is_admin,
                    })
                    connection.commit()
                    flash(f"حساب «{username}» ساخته شد.", "success")
                except ValueError as error:
                    connection.rollback()
                    flash(str(error), "error")
                except sqlite3.IntegrityError:
                    connection.rollback()
                    flash("این نام کاربری قبلاً ثبت شده است.", "error")
        rows = connection.execute(
            """SELECT id, username, is_admin, is_active, max_reports, reports_used,
                      max_logins, logins_used, must_reset_password, created_at
               FROM users ORDER BY is_admin DESC, username COLLATE NOCASE"""
        ).fetchall()
        records = []
        for row in rows:
            item = dict(row)
            item["pages"] = {
                record["page"] for record in connection.execute(
                    "SELECT page FROM user_pages WHERE user_id = ?", (row["id"],)
                )
            }
            records.append(item)
        return render_template(
            "admin_users.html", users=records, pages=PAGES, csrf=csrf_token(),
            admin_id=current_user()["id"],
        )
    finally:
        connection.close()


@auth.post("/admin/users/<int:user_id>/update")
@require_admin
def update_user(user_id: int):
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        target = connection.execute(
            """SELECT id, username, is_admin, is_active, max_reports, max_logins,
                      reports_used, logins_used, must_reset_password
               FROM users WHERE id = ?""", (user_id,)
        ).fetchone()
        if not target:
            abort(404)
        actor = current_user()
        is_admin = bool(target["is_admin"]) if user_id == actor["id"] else request.form.get("is_admin") == "on"
        is_active = True if user_id == actor["id"] else request.form.get("is_active") == "on"
        pages = set(request.form.getlist("pages")) & PAGES.keys()
        new_password = request.form.get("new_password", "")
        if new_password and len(new_password) < 6:
            flash("گذرواژهٔ تازه باید دست‌کم ۶ نویسه باشد.", "error")
            return redirect(url_for("auth.users"))
        try:
            max_reports = _quota_value(request.form.get("max_reports"), "سقف گزارش‌گیری")
            max_logins = _quota_value(request.form.get("max_logins"), "سقف ورود")
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("auth.users"))
        if target["is_admin"] and target["id"] != actor["id"] and (not is_admin or not is_active):
            remaining = connection.execute(
                "SELECT COUNT(*) AS total FROM users WHERE is_admin = 1 AND is_active = 1 AND id != ?",
                (user_id,),
            ).fetchone()["total"]
            if not remaining:
                flash("باید دست‌کم یک مدیر فعال باقی بماند.", "error")
                return redirect(url_for("auth.users"))

        is_active = is_active or bool(new_password)
        connection.execute(
            """UPDATE users SET is_admin = ?, is_active = ?, max_reports = ?, max_logins = ?,
                      must_reset_password = CASE WHEN ? THEN 0 ELSE must_reset_password END,
                      reports_used = CASE WHEN ? THEN 0 ELSE reports_used END,
                      logins_used = CASE WHEN ? THEN 0 ELSE logins_used END
               WHERE id = ?""",
            (
                int(is_admin), int(is_active), max_reports, max_logins,
                int(bool(new_password)), int(bool(new_password)), int(bool(new_password)), user_id,
            ),
        )
        connection.execute("DELETE FROM user_pages WHERE user_id = ?", (user_id,))
        if not is_admin:
            connection.executemany(
                "INSERT INTO user_pages (user_id, page) VALUES (?, ?)",
                [(user_id, page) for page in pages],
            )
        if new_password:
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new_password), user_id),
            )
        _insert_activity(connection, user_id, target["username"], "user_updated",
                         "مدیر تنظیمات و دسترسی‌های حساب را تغییر داد.", {
                             "is_admin": is_admin, "is_active": is_active,
                             "max_reports": max_reports, "max_logins": max_logins,
                             "reset_password_and_quotas": bool(new_password),
                         })
        connection.commit()
        flash("تنظیمات، سهمیه‌ها و دسترسی‌های حساب ذخیره شد.", "success")
        return redirect(url_for("auth.users"))
    finally:
        connection.close()


@auth.get("/admin/users/<int:user_id>/activity")
@require_admin
def user_activity(user_id: int):
    connection = _connect()
    try:
        user = connection.execute(
            """SELECT id, username, is_active, is_admin, max_reports, reports_used,
                      max_logins, logins_used, must_reset_password
               FROM users WHERE id = ?""", (user_id,)
        ).fetchone()
        if not user:
            abort(404)
        try:
            page_number = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page_number = 1
        page_size = 50
        total = connection.execute(
            "SELECT COUNT(*) AS total FROM activity_log WHERE user_id = ? OR (user_id IS NULL AND username = ? COLLATE NOCASE)",
            (user_id, user["username"]),
        ).fetchone()["total"]
        rows = connection.execute(
            """SELECT id, action, description, details_json, ip_address, user_agent, created_at
               FROM activity_log
               WHERE user_id = ? OR (user_id IS NULL AND username = ? COLLATE NOCASE)
               ORDER BY id DESC LIMIT ? OFFSET ?""",
            (user_id, user["username"], page_size, (page_number - 1) * page_size),
        ).fetchall()
        entries = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except (TypeError, ValueError):
                item["details"] = {}
            entries.append(item)
        return render_template(
            "user_activity.html", user=dict(user), entries=entries, total=total,
            page=page_number, page_size=page_size, csrf=csrf_token(), action_labels=ACTIVITY_ACTIONS,
        )
    finally:
        connection.close()
