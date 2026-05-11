"""
Part 2 routes: sections, groups, students, records CRUD.
Registered as a Blueprint on the main Flask app.
"""
from __future__ import annotations

import csv
import io
import random
import string
import sqlite3
from pathlib import Path
import os

import bcrypt
from flask import Blueprint, jsonify, request

part2 = Blueprint("part2", __name__)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR.parent / "cpp-backend" / "cppstudrecord_db.sqlite"
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", DEFAULT_DB_PATH))


def get_connection() -> sqlite3.Connection:
    if not DATABASE_PATH.exists():
        raise FileNotFoundError(f"Database file not found at {DATABASE_PATH}")
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def normalize_text(value: object) -> str:
    return str(value or "").strip()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def get_payload() -> dict:
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload
    return request.form.to_dict(flat=True)


def generate_temp_password(length: int = 6) -> str:
    chars = string.ascii_uppercase + string.digits
    code = "".join(random.choices(chars, k=length))
    return f"CPP-{code}"


# ── SECTIONS ─────────────────────────────────────────────────────────────────

@part2.get("/api/v1/sections")
def list_sections():
    teacher_id = request.args.get("teacher_id", type=int)
    if not teacher_id:
        return jsonify({"success": False, "error": "teacher_id is required"}), 400
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT s.id, s.name, s.school_year, s.semester, s.created_at,
                   COUNT(DISTINCT g.id) AS group_count,
                   COUNT(DISTINCT st.id) AS student_count
            FROM sections s
            LEFT JOIN groups g ON g.section_id = s.id
            LEFT JOIN students st ON st.group_id = g.id
            WHERE s.teacher_id = ?
            GROUP BY s.id
            ORDER BY s.created_at DESC
            """,
            (teacher_id,)
        ).fetchall()
        return jsonify({"success": True, "sections": [dict(r) for r in rows]}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.post("/api/v1/sections")
def create_section():
    payload = get_payload()
    teacher_id = normalize_text(payload.get("teacher_id"))
    name = normalize_text(payload.get("name"))
    school_year = normalize_text(payload.get("school_year") or "")
    semester = normalize_text(payload.get("semester") or "")

    if not teacher_id or not name:
        return jsonify({"success": False, "error": "teacher_id and name are required"}), 400

    try:
        conn = get_connection()
        cur = conn.execute(
            "INSERT INTO sections (teacher_id, name, school_year, semester) VALUES (?, ?, ?, ?)",
            (int(teacher_id), name, school_year or None, semester or None)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sections WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify({"success": True, "section": dict(row)}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.get("/api/v1/sections/<int:section_id>")
def get_section(section_id: int):
    try:
        conn = get_connection()
        section = conn.execute("SELECT * FROM sections WHERE id = ?", (section_id,)).fetchone()
        if not section:
            return jsonify({"success": False, "error": "Section not found"}), 404
        groups = conn.execute(
            """
            SELECT g.id, g.group_number, g.group_name,
                   COUNT(s.id) AS member_count
            FROM groups g
            LEFT JOIN students s ON s.group_id = g.id
            WHERE g.section_id = ?
            GROUP BY g.id
            ORDER BY g.group_number
            """,
            (section_id,)
        ).fetchall()
        return jsonify({
            "success": True,
            "section": dict(section),
            "groups": [dict(g) for g in groups]
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.post("/api/v1/sections/<int:section_id>/import")
def import_csv(section_id: int):
    """
    Import students from a CSV file.
    Expected columns (header required, any order):
      student_number, surname, first_name, middle_initial, email, group_number, group_name
    """
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No CSV file uploaded"}), 400

    file = request.files["file"]
    raw = file.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))

    def col(row: dict, *keys: str) -> str:
        for k in keys:
            for header in row:
                if header.strip().lower().replace(" ", "_") == k.lower():
                    return normalize_text(row[header])
        return ""

    try:
        conn = get_connection()
        section = conn.execute("SELECT * FROM sections WHERE id = ?", (section_id,)).fetchone()
        if not section:
            return jsonify({"success": False, "error": "Section not found"}), 404

        group_cache: dict = {}
        imported = []
        errors = []

        for i, row in enumerate(reader, start=2):
            stud_number = col(row, "student_number", "stud_number", "student_no")
            surname = col(row, "surname", "last_name")
            first_name = col(row, "first_name", "firstname")
            middle_initial = col(row, "middle_initial", "mi")
            email = col(row, "email")
            group_number_raw = col(row, "group_number", "group_no", "group_#")
            group_name = col(row, "group_name", "groupname")

            if not stud_number or not surname or not first_name or not email:
                errors.append(f"Row {i}: missing required fields (need student_number, surname, first_name, email)")
                continue

            try:
                group_number = int(group_number_raw)
            except (ValueError, TypeError):
                errors.append(f"Row {i}: invalid group_number '{group_number_raw}'")
                continue

            if group_number not in group_cache:
                existing_group = conn.execute(
                    "SELECT id FROM groups WHERE section_id = ? AND group_number = ?",
                    (section_id, group_number)
                ).fetchone()
                if existing_group:
                    group_cache[group_number] = existing_group["id"]
                else:
                    cur = conn.execute(
                        "INSERT INTO groups (section_id, group_number, group_name) VALUES (?, ?, ?)",
                        (section_id, group_number, group_name or f"Group {group_number}")
                    )
                    group_cache[group_number] = cur.lastrowid

            group_id = group_cache[group_number]

            if conn.execute("SELECT 1 FROM students WHERE stud_number = ?", (stud_number,)).fetchone():
                errors.append(f"Row {i}: student number {stud_number} already exists — skipped")
                continue
            if conn.execute("SELECT 1 FROM students WHERE email = ?", (email,)).fetchone():
                errors.append(f"Row {i}: email {email} already exists — skipped")
                continue

            temp_pw = generate_temp_password()
            hashed = hash_password(temp_pw)
            mi_dot = f"{middle_initial}. " if middle_initial else ""
            full_name_val = f"{first_name} {mi_dot}{surname}".strip()

            user_cur = conn.execute(
                """
                INSERT INTO users (is_teacher, stud_id, first_name, last_name, full_name, email,
                                   password, is_first_login)
                VALUES (0, ?, ?, ?, ?, ?, ?, 1)
                """,
                (stud_number, first_name, surname, full_name_val, email, hashed)
            )
            user_id = user_cur.lastrowid

            conn.execute(
                """
                INSERT INTO students
                    (user_id, group_id, stud_number, surname, first_name,
                     middle_initial, email, temp_password)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, group_id, stud_number, surname, first_name,
                 middle_initial or None, email, temp_pw)
            )

            imported.append({
                "stud_number": stud_number,
                "name": f"{surname}, {first_name} {middle_initial or ''}".strip(),
                "email": email,
                "group_number": group_number,
                "temp_password": temp_pw,
            })

        conn.commit()
        return jsonify({
            "success": True,
            "imported_count": len(imported),
            "group_count": len(group_cache),
            "students": imported,
            "errors": errors,
        }), 201

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


# ── GROUPS ────────────────────────────────────────────────────────────────────

@part2.get("/api/v1/groups/<int:group_id>")
def get_group(group_id: int):
    try:
        conn = get_connection()
        group = conn.execute(
            """
            SELECT g.*, s.name AS section_name, s.id AS section_id
            FROM groups g
            JOIN sections s ON s.id = g.section_id
            WHERE g.id = ?
            """,
            (group_id,)
        ).fetchone()
        if not group:
            return jsonify({"success": False, "error": "Group not found"}), 404

        members = conn.execute(
            """
            SELECT id, stud_number, surname, first_name, middle_initial,
                   email, temp_password, is_first_login
            FROM students
            WHERE group_id = ?
            ORDER BY surname, first_name
            """,
            (group_id,)
        ).fetchall()
        return jsonify({
            "success": True,
            "group": dict(group),
            "members": [dict(m) for m in members]
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


# ── STUDENTS ──────────────────────────────────────────────────────────────────

@part2.get("/api/v1/students/<int:student_id>")
def get_student(student_id: int):
    try:
        conn = get_connection()
        student = conn.execute(
            """
            SELECT st.*, g.group_number, g.group_name,
                   s.name AS section_name, s.id AS section_id
            FROM students st
            JOIN groups g ON g.id = st.group_id
            JOIN sections s ON s.id = g.section_id
            WHERE st.id = ?
            """,
            (student_id,)
        ).fetchone()
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404
        return jsonify({"success": True, "student": dict(student)}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.get("/api/v1/students/<int:student_id>/records")
def list_student_records(student_id: int):
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, date, type_of_undertaking, total_score, score, remarks, created_at
            FROM record
            WHERE student_id = ?
            ORDER BY date DESC, created_at DESC
            """,
            (student_id,)
        ).fetchall()
        return jsonify({"success": True, "records": [dict(r) for r in rows]}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.post("/api/v1/students/<int:student_id>/records")
def add_student_record(student_id: int):
    payload = get_payload()
    date = normalize_text(payload.get("date"))
    type_of_undertaking = normalize_text(payload.get("type_of_undertaking") or payload.get("type") or "")
    total_score_raw = payload.get("total_score")
    score_raw = payload.get("score")
    remarks = normalize_text(payload.get("remarks") or "")

    if not date or not type_of_undertaking or score_raw is None:
        return jsonify({"success": False, "error": "date, type_of_undertaking, and score are required"}), 400

    try:
        total_score = float(total_score_raw) if total_score_raw not in (None, "") else None
        score = float(score_raw)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "score and total_score must be numbers"}), 400

    try:
        conn = get_connection()
        student = conn.execute("SELECT stud_number FROM students WHERE id = ?", (student_id,)).fetchone()
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404

        cur = conn.execute(
            """
            INSERT INTO record (student_id, stud_id, date, type_of_undertaking, total_score, score, remarks)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (student_id, student["stud_number"], date, type_of_undertaking,
             total_score, score, remarks or None)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM record WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify({"success": True, "record": dict(row)}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


# ── RECORDS CRUD ──────────────────────────────────────────────────────────────

@part2.put("/api/v1/records/<int:record_id>")
def update_record_endpoint(record_id: int):
    payload = get_payload()
    date = normalize_text(payload.get("date"))
    type_of_undertaking = normalize_text(payload.get("type_of_undertaking") or payload.get("type") or "")
    total_score_raw = payload.get("total_score")
    score_raw = payload.get("score")
    remarks = normalize_text(payload.get("remarks") or "")

    if not date or not type_of_undertaking or score_raw is None:
        return jsonify({"success": False, "error": "date, type_of_undertaking, and score are required"}), 400

    try:
        total_score = float(total_score_raw) if total_score_raw not in (None, "") else None
        score = float(score_raw)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "score must be a number"}), 400

    try:
        conn = get_connection()
        existing = conn.execute("SELECT id FROM record WHERE id = ?", (record_id,)).fetchone()
        if not existing:
            return jsonify({"success": False, "error": "Record not found"}), 404
        conn.execute(
            """
            UPDATE record
            SET date = ?, type_of_undertaking = ?, total_score = ?, score = ?, remarks = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (date, type_of_undertaking, total_score, score, remarks or None, record_id)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM record WHERE id = ?", (record_id,)).fetchone()
        return jsonify({"success": True, "record": dict(row)}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.delete("/api/v1/records/<int:record_id>")
def delete_record_endpoint(record_id: int):
    try:
        conn = get_connection()
        existing = conn.execute("SELECT id FROM record WHERE id = ?", (record_id,)).fetchone()
        if not existing:
            return jsonify({"success": False, "error": "Record not found"}), 404
        conn.execute("DELETE FROM record WHERE id = ?", (record_id,))
        conn.commit()
        return jsonify({"success": True, "message": "Record deleted"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


# ── STUDENT AUTH ──────────────────────────────────────────────────────────────

@part2.post("/api/v1/student/request-temp")
def student_request_temp():
    """
    Step 1: student enters email → system returns temp_password so Node can email it.
    Only works if is_first_login = 1. Returns temp_password in plaintext (Node sends email).
    """
    payload = get_payload()
    email = normalize_text(payload.get("email"))
    if not email:
        return jsonify({"success": False, "error": "Email is required"}), 400

    try:
        conn = get_connection()
        student = conn.execute(
            """
            SELECT st.id, st.first_name, st.surname, st.temp_password, st.is_first_login,
                   st.email
            FROM students st
            WHERE LOWER(st.email) = LOWER(?)
            """,
            (email,)
        ).fetchone()

        if not student:
            # Generic message to avoid enumeration
            return jsonify({"success": True, "message": "If this email is registered, a code has been sent."}), 200

        if not student["is_first_login"]:
            return jsonify({"success": False, "error": "Account already activated. Please log in normally."}), 400

        if not student["temp_password"]:
            return jsonify({"success": False, "error": "No temporary password found. Contact your teacher."}), 400

        return jsonify({
            "success": True,
            "temp_password": student["temp_password"],
            "student_name": f"{student['first_name']} {student['surname']}",
            "email": student["email"],
            "message": "Temporary password retrieved.",
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.post("/api/v1/student/verify-temp")
def student_verify_temp():
    """
    Step 2: student submits email + temp_password → verified → returns student info for session.
    Matches against stored plaintext temp_password (not bcrypt, temp only).
    """
    payload = get_payload()
    email = normalize_text(payload.get("email"))
    temp_pw = normalize_text(payload.get("temp_password"))

    if not email or not temp_pw:
        return jsonify({"success": False, "error": "Email and temporary password are required"}), 400

    try:
        conn = get_connection()
        student = conn.execute(
            """
            SELECT st.id, st.first_name, st.surname, st.temp_password, st.is_first_login,
                   st.email, st.stud_number, st.group_id,
                   g.group_number, g.group_name,
                   s.name AS section_name, s.id AS section_id
            FROM students st
            JOIN groups g ON g.id = st.group_id
            JOIN sections s ON s.id = g.section_id
            WHERE LOWER(st.email) = LOWER(?)
            """,
            (email,)
        ).fetchone()

        if not student or not student["is_first_login"]:
            return jsonify({"success": False, "error": "Invalid email or account already activated."}), 401

        if student["temp_password"] != temp_pw:
            return jsonify({"success": False, "error": "Incorrect temporary password."}), 401

        return jsonify({
            "success": True,
            "student_id": student["id"],
            "email": student["email"],
            "first_name": student["first_name"],
            "surname": student["surname"],
            "stud_number": student["stud_number"],
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.post("/api/v1/student/set-password")
def student_set_password():
    """
    Step 3: student sets permanent password → account activated.
    Also logs in via users table (email + new hashed password).
    """
    payload = get_payload()
    email = normalize_text(payload.get("email"))
    new_password = normalize_text(payload.get("new_password"))

    if not email or not new_password:
        return jsonify({"success": False, "error": "Email and new_password are required"}), 400

    if len(new_password) < 6:
        return jsonify({"success": False, "error": "Password must be at least 6 characters"}), 400

    try:
        conn = get_connection()
        student = conn.execute(
            "SELECT id FROM students WHERE LOWER(email) = LOWER(?) AND is_first_login = 1",
            (email,)
        ).fetchone()

        if not student:
            return jsonify({"success": False, "error": "Student not found or already activated"}), 404

        hashed = hash_password(new_password)

        # Update students table
        conn.execute(
            "UPDATE students SET temp_password = NULL, is_first_login = 0 WHERE id = ?",
            (student["id"],)
        )

        # Update users table (the auth account)
        conn.execute(
            "UPDATE users SET password = ?, is_first_login = 0 WHERE LOWER(email) = LOWER(?)",
            (hashed, email)
        )

        conn.commit()

        # Fetch full student info for session
        full = conn.execute(
            """
            SELECT st.id, st.first_name, st.surname, st.stud_number, st.email,
                   st.group_id, g.group_number, g.group_name,
                   s.name AS section_name, s.id AS section_id,
                   u.id AS user_id
            FROM students st
            JOIN groups g ON g.id = st.group_id
            JOIN sections s ON s.id = g.section_id
            JOIN users u ON u.email = st.email
            WHERE st.id = ?
            """,
            (student["id"],)
        ).fetchone()

        return jsonify({
            "success": True,
            "message": "Account activated successfully.",
            "student": dict(full),
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.get("/api/v1/students/<int:student_id>/me")
def get_student_me(student_id: int):
    """Get full student data + records for the student's own view."""
    try:
        conn = get_connection()
        student = conn.execute(
            """
            SELECT st.*, g.group_number, g.group_name,
                   s.name AS section_name, s.id AS section_id
            FROM students st
            JOIN groups g ON g.id = st.group_id
            JOIN sections s ON s.id = g.section_id
            WHERE st.id = ?
            """,
            (student_id,)
        ).fetchone()
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404

        records = conn.execute(
            """
            SELECT id, date, type_of_undertaking, total_score, score, remarks, created_at
            FROM record WHERE student_id = ?
            ORDER BY date DESC, created_at DESC
            """,
            (student_id,)
        ).fetchall()

        return jsonify({
            "success": True,
            "student": dict(student),
            "records": [dict(r) for r in records],
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()
