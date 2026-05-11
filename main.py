from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt
from flask import Flask, jsonify, request


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR.parent / "cpp-backend" / "cppstudrecord_db.sqlite"
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", DEFAULT_DB_PATH))

app = Flask(__name__)

from routes_part2 import part2
app.register_blueprint(part2)


def get_connection() -> sqlite3.Connection:
    if not DATABASE_PATH.exists():
        raise FileNotFoundError(f"Database file not found at {DATABASE_PATH}")

    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def get_payload() -> dict:
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload

    return request.form.to_dict(flat=True)


def normalize_text(value: object) -> str:
    return str(value or "").strip()


def normalize_int(value: object) -> int | None:
    text = normalize_text(value)
    if not text:
        return None

    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def student_exists(connection: sqlite3.Connection, stud_id: str) -> bool:
    row = connection.execute("SELECT 1 FROM users WHERE stud_id = ? LIMIT 1", (stud_id,)).fetchone()
    return row is not None


def email_exists(connection: sqlite3.Connection, email: str) -> bool:
    row = connection.execute("SELECT 1 FROM users WHERE email = ? LIMIT 1", (email,)).fetchone()
    return row is not None


def generate_teacher_id(connection: sqlite3.Connection, full_name: str) -> str:
    parts = full_name.split()
    prefix = (parts[0][:1] + (parts[-1][:1] if len(parts) > 1 else "T")).upper()
    prefix = "".join(c for c in prefix if c.isalnum()) or "T"

    while True:
        candidate = f"TCH-{prefix}-{secrets.token_hex(3).upper()}"
        if not student_exists(connection, candidate):
            return candidate


def build_user_response(user_row) -> dict:
    """Build a consistent user dict from a DB row, handling both old and new schema."""
    full_name = normalize_text(user_row["full_name"]) if "full_name" in user_row.keys() and user_row["full_name"] else ""
    first_name = normalize_text(user_row["first_name"]) if "first_name" in user_row.keys() and user_row["first_name"] else ""
    last_name = normalize_text(user_row["last_name"]) if "last_name" in user_row.keys() and user_row["last_name"] else ""
    email = normalize_text(user_row["email"]) if "email" in user_row.keys() and user_row["email"] else ""

    # Derive full_name from first+last if not set
    if not full_name and (first_name or last_name):
        full_name = f"{first_name} {last_name}".strip()

    return {
        "id": user_row["id"],
        "stud_id": user_row["stud_id"],
        "studId": user_row["stud_id"],
        "full_name": full_name,
        "fullName": full_name,
        "first_name": first_name or full_name.split()[0] if full_name else "",
        "firstName": first_name or full_name.split()[0] if full_name else "",
        "last_name": last_name or (full_name.split()[-1] if full_name and len(full_name.split()) > 1 else ""),
        "lastName": last_name or (full_name.split()[-1] if full_name and len(full_name.split()) > 1 else ""),
        "email": email,
        "is_teacher": bool(user_row["is_teacher"]),
        "isTeacher": bool(user_row["is_teacher"]),
    }


@app.get("/api/v1/health")
def health_check():
    return jsonify({"success": True, "status": "ok"})


@app.post("/api/v1/register")
def register_user():
    payload = get_payload()
    role = normalize_text(payload.get("role") or payload.get("accountType") or "student").lower()
    is_teacher = role == "teacher"

    # Support both full_name (new) and first_name/last_name (legacy)
    full_name = normalize_text(payload.get("full_name") or payload.get("fullName"))
    first_name = normalize_text(payload.get("first_name") or payload.get("firstName"))
    last_name = normalize_text(payload.get("last_name") or payload.get("lastName"))
    email = normalize_text(payload.get("email") or "")
    password = normalize_text(payload.get("password"))

    # Reconcile names: prefer full_name; fall back to first+last
    if not full_name and (first_name or last_name):
        full_name = f"{first_name} {last_name}".strip()
    if not first_name and full_name:
        parts = full_name.split()
        first_name = parts[0]
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    if not full_name or not password:
        return jsonify({"success": False, "error": "Please complete all required fields"}), 400

    if is_teacher and not email:
        return jsonify({"success": False, "error": "Email is required for teacher registration"}), 400

    password_confirm = normalize_text(payload.get("password_confirm") or payload.get("passwordConfirm"))
    if password_confirm and password_confirm != password:
        return jsonify({"success": False, "error": "Passwords do not match"}), 400

    try:
        connection = get_connection()
    except FileNotFoundError as error:
        return jsonify({"success": False, "error": str(error)}), 503

    try:
        stud_id = normalize_text(payload.get("stud_id") or payload.get("studId"))
        year = normalize_int(payload.get("year"))
        section = normalize_int(payload.get("section"))
        group_name = normalize_text(payload.get("group_name") or payload.get("groupName")) or None

        if is_teacher and not stud_id:
            stud_id = generate_teacher_id(connection, full_name)

        if not stud_id:
            return jsonify({"success": False, "error": "Student ID is required"}), 400

        if student_exists(connection, stud_id):
            return jsonify({"success": False, "error": "Student ID already registered"}), 409

        if is_teacher and email and email_exists(connection, email):
            return jsonify({"success": False, "error": "Email is already registered"}), 409

        if not is_teacher and (year is None or section is None or not group_name):
            return jsonify({"success": False, "error": "Please complete all required fields"}), 400

        hashed_password = hash_password(password)

        cursor = connection.execute(
            """
            INSERT INTO users (is_teacher, stud_id, first_name, last_name, full_name, email, year, section, group_name, password)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1 if is_teacher else 0,
                stud_id,
                first_name,
                last_name,
                full_name,
                email if email else None,
                year,
                section,
                group_name,
                hashed_password,
            ),
        )
        connection.commit()

        return jsonify(
            {
                "success": True,
                "message": "Registration saved successfully",
                "user": {
                    "id": cursor.lastrowid,
                    "studId": stud_id,
                    "stud_id": stud_id,
                    "fullName": full_name,
                    "full_name": full_name,
                    "firstName": first_name,
                    "first_name": first_name,
                    "lastName": last_name,
                    "last_name": last_name,
                    "email": email,
                    "isTeacher": is_teacher,
                    "is_teacher": is_teacher,
                },
            }
        ), 201
    except sqlite3.IntegrityError as error:
        connection.rollback()
        return jsonify({"success": False, "error": "Student ID already registered", "details": str(error)}), 409
    except Exception as error:
        connection.rollback()
        return jsonify({"success": False, "error": "Registration failed", "details": str(error)}), 500
    finally:
        connection.close()


@app.post("/api/v1/login")
def login_user_v1():
    """Legacy login: first_name + last_name + password. Kept for backward compatibility."""
    payload = get_payload()
    first_name = normalize_text(payload.get("first_name") or payload.get("firstName"))
    last_name = normalize_text(payload.get("last_name") or payload.get("lastName"))
    password = normalize_text(payload.get("password"))

    if not first_name or not last_name or not password:
        return jsonify({"success": False, "error": "First name, last name, and password are required"}), 400

    try:
        connection = get_connection()
    except FileNotFoundError as error:
        return jsonify({"success": False, "error": str(error)}), 503

    try:
        user_row = connection.execute(
            "SELECT * FROM users WHERE first_name = ? AND last_name = ?",
            (first_name, last_name)
        ).fetchone()

        if not user_row:
            return jsonify({"success": False, "error": "Invalid credentials"}), 401

        if not check_password(password, user_row["password"]):
            return jsonify({"success": False, "error": "Invalid credentials"}), 401

        response_data = {"success": True, "user": build_user_response(user_row)}

        if str(payload.get("remember")).lower() in ["1", "true", "on", "yes"]:
            token = secrets.token_hex(32)
            expires_at = datetime.now() + timedelta(days=30)
            connection.execute(
                "INSERT INTO user_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
                (user_row["id"], token, expires_at.strftime("%Y-%m-%d %H:%M:%S"))
            )
            connection.commit()
            response_data["token"] = token

        return jsonify(response_data), 200
    except Exception as error:
        return jsonify({"success": False, "error": "Login failed", "details": str(error)}), 500
    finally:
        connection.close()


@app.post("/api/v2/login")
def login_user_v2():
    """Teacher login: email + password."""
    payload = get_payload()
    email = normalize_text(payload.get("email") or "")
    password = normalize_text(payload.get("password"))

    if not email or not password:
        return jsonify({"success": False, "error": "Email and password are required"}), 400

    try:
        connection = get_connection()
    except FileNotFoundError as error:
        return jsonify({"success": False, "error": str(error)}), 503

    try:
        user_row = connection.execute(
            "SELECT * FROM users WHERE email = ? AND is_teacher = 1",
            (email,)
        ).fetchone()

        if not user_row:
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        if not check_password(password, user_row["password"]):
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        response_data = {"success": True, "user": build_user_response(user_row)}

        if str(payload.get("remember")).lower() in ["1", "true", "on", "yes"]:
            token = secrets.token_hex(32)
            expires_at = datetime.now() + timedelta(days=30)
            connection.execute(
                "INSERT INTO user_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
                (user_row["id"], token, expires_at.strftime("%Y-%m-%d %H:%M:%S"))
            )
            connection.commit()
            response_data["token"] = token

        return jsonify(response_data), 200
    except Exception as error:
        return jsonify({"success": False, "error": "Login failed", "details": str(error)}), 500
    finally:
        connection.close()


@app.post("/api/v1/verify_token")
def verify_token():
    payload = get_payload()
    token = normalize_text(payload.get("token"))

    if not token:
        return jsonify({"success": False, "error": "Token is required"}), 400

    try:
        connection = get_connection()
    except FileNotFoundError as error:
        return jsonify({"success": False, "error": str(error)}), 503

    try:
        token_row = connection.execute(
            "SELECT user_id, expires_at FROM user_tokens WHERE token = ?",
            (token,)
        ).fetchone()

        if not token_row:
            return jsonify({"success": False, "error": "Invalid token"}), 401

        expires_at = datetime.strptime(token_row["expires_at"], "%Y-%m-%d %H:%M:%S")
        if expires_at < datetime.now():
            connection.execute("DELETE FROM user_tokens WHERE token = ?", (token,))
            connection.commit()
            return jsonify({"success": False, "error": "Token expired"}), 401

        user_row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (token_row["user_id"],)
        ).fetchone()

        if not user_row:
            return jsonify({"success": False, "error": "User not found"}), 404

        return jsonify({"success": True, "user": build_user_response(user_row)}), 200
    except Exception as error:
        return jsonify({"success": False, "error": "Verification failed", "details": str(error)}), 500
    finally:
        connection.close()


@app.post("/api/v1/logout")
def logout_user():
    payload = get_payload()
    token = normalize_text(payload.get("token"))

    if not token:
        return jsonify({"success": True}), 200

    try:
        connection = get_connection()
        connection.execute("DELETE FROM user_tokens WHERE token = ?", (token,))
        connection.commit()
    except Exception:
        pass
    finally:
        try:
            connection.close()
        except Exception:
            pass

    return jsonify({"success": True}), 200


@app.get("/api/v1/stats")
def get_stats():
    try:
        connection = get_connection()
        enrolled_row = connection.execute("SELECT COUNT(*) as count FROM users WHERE is_teacher = 0").fetchone()
        records_row = connection.execute("SELECT COUNT(*) as count FROM record").fetchone()
        teachers_row = connection.execute("SELECT COUNT(*) as count FROM users WHERE is_teacher = 1").fetchone()
        sections_row = connection.execute("SELECT COUNT(DISTINCT section) as count FROM users WHERE is_teacher = 0 AND section IS NOT NULL").fetchone()

        return jsonify({
            "success": True,
            "totalEnrolled": enrolled_row["count"] if enrolled_row else 0,
            "processedRecords": records_row["count"] if records_row else 0,
            "totalTeachers": teachers_row["count"] if teachers_row else 0,
            "totalSections": sections_row["count"] if sections_row else 0,
        }), 200
    except Exception as error:
        return jsonify({"success": False, "error": "Failed to fetch stats", "details": str(error)}), 500
    finally:
        try:
            connection.close()
        except Exception:
            pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)