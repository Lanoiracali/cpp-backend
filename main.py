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

app = Flask(__name__)

from routes_part2 import part2
app.register_blueprint(part2)

# Use the adapter which will pick Postgres when `DATABASE_URL` is set,
# otherwise fall back to the local sqlite file.
from db_adapter import get_connection


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


def enforce_max_length(value: object, max_length: int) -> str:
    text = normalize_text(value)
    return text[:max_length]


def is_plv_email(value: object) -> bool:
    return normalize_text(value).lower().endswith('@plv.edu.ph')


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
    db_status = "unknown"
    try:
        connection = get_connection()
        try:
            connection.execute("SELECT 1")
            db_status = connection._mode
        finally:
            connection.close()
    except Exception as error:
        return jsonify({
            "success": False,
            "status": "degraded",
            "database": db_status,
            "error": str(error),
        }), 503

    return jsonify({"success": True, "status": "ok", "database": db_status})


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
                bool(is_teacher),
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
        # Debug: log the email being searched
        print(f"[DEBUG] Attempting login with email: {email}")
        
        user_row = connection.execute(
            "SELECT * FROM users WHERE email = ? AND is_teacher = TRUE",
            (email,)
        ).fetchone()

        if not user_row:
            print(f"[DEBUG] User not found for email: {email}")
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        print(f"[DEBUG] User found: {user_row.get('id')} - checking password")
        
        if not check_password(password, user_row["password"]):
            print(f"[DEBUG] Password check failed for user: {user_row.get('id')}")
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        print(f"[DEBUG] Login successful for user: {user_row.get('id')}")
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
        print(f"[DEBUG] Login error: {error}")
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

        # Join with students to get student_id if they are a student
        query = """
            SELECT u.*, st.id AS student_id
            FROM users u
            LEFT JOIN students st ON LOWER(st.email) = LOWER(u.email)
            WHERE u.id = ?
        """
        user_row = connection.execute(query, (token_row["user_id"],)).fetchone()

        if not user_row:
            return jsonify({"success": False, "error": "User not found"}), 404

        user_data = build_user_response(user_row)
        if user_row["student_id"]:
            user_data["student_id"] = user_row["student_id"]
            user_data["studentId"] = user_row["student_id"]

        return jsonify({"success": True, "user": user_data}), 200
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
        enrolled_row = connection.execute("SELECT COUNT(*) as count FROM users WHERE is_teacher = FALSE").fetchone()
        records_row = connection.execute("SELECT COUNT(*) as count FROM record").fetchone()
        teachers_row = connection.execute("SELECT COUNT(*) as count FROM users WHERE is_teacher = TRUE").fetchone()
        # Prefer an explicit sections table if present (accurate count of created sections).
        user_id_header = request.headers.get('X-User-Id')
        try:
            if user_id_header:
                try:
                    tid = int(user_id_header)
                    sections_row = connection.execute("SELECT COUNT(*) as count FROM sections WHERE teacher_id = ?", (tid,)).fetchone()
                except Exception:
                    # If conversion fails, fallback to global sections count
                    sections_row = connection.execute("SELECT COUNT(*) as count FROM sections").fetchone()
            else:
                sections_row = connection.execute("SELECT COUNT(*) as count FROM sections").fetchone()
        except Exception:
            # Fallback to legacy users.section count when sections table isn't available.
            sections_row = connection.execute("SELECT COUNT(DISTINCT section) as count FROM users WHERE is_teacher = FALSE AND section IS NOT NULL").fetchone()

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


# ── Profile Management Endpoints ─────────────────────────────────────────────
def get_user_id_from_request() -> int | None:
    """Extract user_id from request headers (passed by Node gateway)."""
    user_id_str = request.headers.get("X-User-Id")
    if user_id_str:
        try:
            return int(user_id_str)
        except (TypeError, ValueError):
            pass
    return None


@app.get("/api/profile")
def get_profile():
    """Get the current user's profile."""
    user_id = get_user_id_from_request()
    if not user_id:
        return jsonify({"success": False, "error": "User ID required"}), 401

    try:
        connection = get_connection()
        user = connection.execute("SELECT id, stud_id, first_name, last_name, email, is_teacher, profile_pic FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        return jsonify({
            "success": True,
            "profile": {
                "id": user["id"],
                "stud_id": user["stud_id"],
                "first_name": user["first_name"],
                "last_name": user["last_name"],
                "email": user["email"],
                "is_teacher": user["is_teacher"],
                "profile_pic": user["profile_pic"],
            }
        }), 200
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        connection.close()


@app.put("/api/profile")
def update_profile():
    """Update user profile (first name, last name)."""
    user_id = get_user_id_from_request()
    if not user_id:
        return jsonify({"success": False, "error": "User ID required"}), 401

    payload = get_payload()
    first_name = enforce_max_length(payload.get("first_name"), 25)
    last_name = enforce_max_length(payload.get("last_name"), 20)
    email = enforce_max_length(payload.get("email") or "", 50)

    if not first_name or not last_name:
        return jsonify({"success": False, "error": "First name and last name are required"}), 400

    if email and not is_plv_email(email):
        return jsonify({"success": False, "error": "Email must end with @plv.edu.ph"}), 400

    try:
        connection = get_connection()
        connection.execute(
            "UPDATE users SET first_name = ?, last_name = ?, email = ? WHERE id = ?",
            (first_name, last_name, email or None, user_id),
        )
        connection.commit()

        user = connection.execute("SELECT id, stud_id, first_name, last_name, email, is_teacher, profile_pic FROM users WHERE id = ?", (user_id,)).fetchone()
        return jsonify({
            "success": True,
            "message": "Profile updated successfully",
            "profile": {
                "id": user["id"],
                "stud_id": user["stud_id"],
                "first_name": user["first_name"],
                "last_name": user["last_name"],
                "email": user["email"],
                "is_teacher": user["is_teacher"],
                "profile_pic": user["profile_pic"],
            }
        }), 200
    except Exception as error:
        connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        connection.close()


@app.post("/api/profile/change-password")
def change_password():
    """Change user password."""
    user_id = get_user_id_from_request()
    if not user_id:
        return jsonify({"success": False, "error": "User ID required"}), 401

    payload = get_payload()
    current_password = normalize_text(payload.get("current_password"))
    new_password = normalize_text(payload.get("new_password"))
    confirm_password = normalize_text(payload.get("confirm_password"))

    if not current_password or not new_password or not confirm_password:
        return jsonify({"success": False, "error": "All password fields are required"}), 400

    if new_password != confirm_password:
        return jsonify({"success": False, "error": "New passwords do not match"}), 400

    if len(new_password) < 6:
        return jsonify({"success": False, "error": "Password must be at least 6 characters"}), 400

    try:
        connection = get_connection()
        user = connection.execute("SELECT password FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        if not check_password(current_password, user["password"]):
            return jsonify({"success": False, "error": "Current password is incorrect"}), 401

        hashed_password = hash_password(new_password)
        connection.execute("UPDATE users SET password = ? WHERE id = ?", (hashed_password, user_id))
        connection.commit()

        return jsonify({"success": True, "message": "Password changed successfully"}), 200
    except Exception as error:
        connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        connection.close()


@app.post("/api/profile/upload-pic")
def upload_profile_pic():
    """Upload profile picture (base64 image)."""
    user_id = get_user_id_from_request()
    if not user_id:
        return jsonify({"success": False, "error": "User ID required"}), 401

    payload = get_payload()
    profile_pic = normalize_text(payload.get("profile_pic"))

    if not profile_pic:
        return jsonify({"success": False, "error": "Profile picture data is required"}), 400

    # Validate base64 image (should start with data:image/)
    if not profile_pic.startswith("data:image/"):
        return jsonify({"success": False, "error": "Invalid image format"}), 400

    try:
        connection = get_connection()
        connection.execute("UPDATE users SET profile_pic = ? WHERE id = ?", (profile_pic, user_id))
        connection.commit()

        return jsonify({"success": True, "message": "Profile picture updated successfully"}), 200
    except Exception as error:
        connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        connection.close()


@app.delete("/api/profile")
def delete_account():
    """Delete user account and all their associated records."""
    user_id = get_user_id_from_request()
    if not user_id:
        return jsonify({"success": False, "error": "User ID required"}), 401

    try:
        connection = get_connection()
        user = connection.execute("SELECT id, is_teacher FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        # Delete records created by this user (if teacher)
        if user["is_teacher"]:
            connection.execute("DELETE FROM record WHERE created_by = ?", (user_id,))

        # Delete user
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        connection.commit()

        return jsonify({"success": True, "message": "Account deleted successfully"}), 200
    except Exception as error:
        connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        connection.close()


@app.put("/api/v2/profile")
def update_profile_v2():
    """Update user profile (first name, last name, email) for v2."""
    user_id = get_user_id_from_request()
    if not user_id:
        return jsonify({"success": False, "error": "User ID required"}), 401

    payload = get_payload()
    first_name = normalize_text(payload.get("first_name"))
    last_name = normalize_text(payload.get("last_name"))
    email = normalize_text(payload.get("email"))

    if not first_name or not last_name or not email:
        return jsonify({"success": False, "error": "First name, last name, and email are required"}), 400

    try:
        connection = get_connection()
        
        # Check if the email is already registered by another user
        existing_email_row = connection.execute(
            "SELECT id FROM users WHERE LOWER(email) = LOWER(?) AND id != ? LIMIT 1",
            (email, user_id)
        ).fetchone()
        
        if existing_email_row:
            connection.close()
            return jsonify({"success": False, "error": "Email is already taken by another account"}), 409

        # Fetch current user to determine role
        user = connection.execute("SELECT id, is_teacher FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            connection.close()
            return jsonify({"success": False, "error": "User not found"}), 404

        full_name = f"{first_name} {last_name}".strip()
        is_teacher = bool(user["is_teacher"])

        # Update users table
        connection.execute(
            "UPDATE users SET first_name = ?, last_name = ?, full_name = ?, email = ? WHERE id = ?",
            (first_name, last_name, full_name, email, user_id)
        )

        # If user is a student (is_teacher = False), update the students table as well
        if not is_teacher:
            connection.execute(
                "UPDATE students SET first_name = ?, surname = ?, email = ? WHERE user_id = ?",
                (first_name, last_name, email, user_id)
            )

        connection.commit()

        updated_user = connection.execute(
            "SELECT id, stud_id, first_name, last_name, email, is_teacher, profile_pic FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

        return jsonify({
            "success": True,
            "message": "Profile updated successfully",
            "profile": {
                "id": updated_user["id"],
                "stud_id": updated_user["stud_id"],
                "first_name": updated_user["first_name"],
                "last_name": updated_user["last_name"],
                "email": updated_user["email"],
                "is_teacher": updated_user["is_teacher"],
                "profile_pic": updated_user["profile_pic"],
            }
        }), 200
    except Exception as error:
        try:
            connection.rollback()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        try:
            connection.close()
        except Exception:
            pass


@app.delete("/api/v2/profile")
def delete_account_v2():
    """Delete user account and all their associated records (v2)."""
    user_id = get_user_id_from_request()
    if not user_id:
        return jsonify({"success": False, "error": "User ID required"}), 401

    try:
        connection = get_connection()
        user = connection.execute("SELECT id, is_teacher FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            connection.close()
            return jsonify({"success": False, "error": "User not found"}), 404

        is_teacher = bool(user["is_teacher"])

        if is_teacher:
            # Delete records created by this teacher
            connection.execute("DELETE FROM record WHERE created_by = ?", (user_id,))
        else:
            # It's a student user. Let's find their student_id
            student_row = connection.execute("SELECT id FROM students WHERE user_id = ?", (user_id,)).fetchone()
            if student_row:
                student_id = student_row["id"]
                # Delete records of this student
                connection.execute("DELETE FROM record WHERE student_id = ?", (student_id,))
                # Delete student record
                connection.execute("DELETE FROM students WHERE id = ?", (student_id,))

        # Delete user tokens
        connection.execute("DELETE FROM user_tokens WHERE user_id = ?", (user_id,))

        # Delete user
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        connection.commit()

        return jsonify({"success": True, "message": "Account deleted successfully"}), 200
    except Exception as error:
        try:
            connection.rollback()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        try:
            connection.close()
        except Exception:
            pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)