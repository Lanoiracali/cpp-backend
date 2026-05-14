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

@part2.delete("/api/v1/sections/<int:section_id>")
def delete_section(section_id: int):
    try:
        conn = get_connection()
        section = conn.execute("SELECT id FROM sections WHERE id = ?", (section_id,)).fetchone()
        if not section:
            return jsonify({"success": False, "error": "Section not found"}), 404
        
        # ON DELETE CASCADE on groups table should handle group deletion
        conn.execute("DELETE FROM sections WHERE id = ?", (section_id,))
        conn.commit()
        return jsonify({"success": True}), 200
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

            if conn.execute("SELECT 1 FROM students WHERE stud_number = ? AND group_id = ?", (stud_number, group_id)).fetchone():
                errors.append(f"Row {i}: student number {stud_number} already exists in this group — skipped")
                continue
            if conn.execute("SELECT 1 FROM students WHERE email = ? AND group_id = ?", (email, group_id)).fetchone():
                errors.append(f"Row {i}: email {email} already exists in this group — skipped")
                continue

            existing_user = conn.execute("SELECT id, is_first_login FROM users WHERE stud_id = ?", (stud_number,)).fetchone()
            if existing_user:
                user_id = existing_user["id"]
                is_first_login = existing_user["is_first_login"]
                if is_first_login:
                    existing_student = conn.execute("SELECT temp_password FROM students WHERE user_id = ? AND temp_password IS NOT NULL", (user_id,)).fetchone()
                    temp_pw = existing_student["temp_password"] if existing_student else generate_temp_password()
                else:
                    temp_pw = None
            else:
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
                is_first_login = 1

            conn.execute(
                """
                INSERT INTO students
                    (user_id, group_id, stud_number, surname, first_name,
                     middle_initial, email, temp_password, is_first_login)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, group_id, stud_number, surname, first_name,
                 middle_initial or None, email, temp_pw, is_first_login)
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


@part2.post("/api/v1/sections/<int:section_id>/groups")
def create_group(section_id: int):
    """Create a new group inside a section."""
    payload = get_payload()
    group_number = payload.get("group_number")
    group_name   = normalize_text(payload.get("group_name") or "")
    try:
        group_number = int(group_number)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "group_number must be an integer"}), 400
    try:
        conn = get_connection()
        section = conn.execute("SELECT id FROM sections WHERE id = ?", (section_id,)).fetchone()
        if not section:
            return jsonify({"success": False, "error": "Section not found"}), 404
        existing = conn.execute(
            "SELECT id FROM groups WHERE section_id = ? AND group_number = ?",
            (section_id, group_number)
        ).fetchone()
        if existing:
            return jsonify({"success": False, "error": f"Group {group_number} already exists in this section"}), 409
        cur = conn.execute(
            "INSERT INTO groups (section_id, group_number, group_name) VALUES (?, ?, ?)",
            (section_id, group_number, group_name or f"Group {group_number}")
        )
        conn.commit()
        row = conn.execute("SELECT * FROM groups WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify({"success": True, "group": dict(row)}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.delete("/api/v1/groups/<int:group_id>")
def delete_group(group_id: int):
    """Delete a group (and cascade-removes all its students)."""
    try:
        conn = get_connection()
        group = conn.execute("SELECT id, section_id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            return jsonify({"success": False, "error": "Group not found"}), 404
        # Remove students' user accounts first
        student_ids = conn.execute("SELECT user_id FROM students WHERE group_id = ?", (group_id,)).fetchall()
        for s in student_ids:
            conn.execute("DELETE FROM users WHERE id = ?", (s["user_id"],))
        conn.execute("DELETE FROM students WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        conn.commit()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.post("/api/v1/groups/<int:group_id>/students")
def add_student_to_group(group_id: int):
    """Add a single student manually to a group."""
    payload = get_payload()
    stud_number = normalize_text(payload.get("student_number") or payload.get("stud_number") or "")
    surname     = normalize_text(payload.get("surname") or "")
    first_name  = normalize_text(payload.get("first_name") or "")
    middle_initial = normalize_text(payload.get("middle_initial") or "")
    email       = normalize_text(payload.get("email") or "")

    if not stud_number or not surname or not first_name or not email:
        return jsonify({"success": False, "error": "student_number, surname, first_name, and email are required"}), 400
    try:
        conn = get_connection()
        group = conn.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            return jsonify({"success": False, "error": "Group not found"}), 404
        if conn.execute("SELECT 1 FROM students WHERE stud_number = ? AND group_id = ?", (stud_number, group_id)).fetchone():
            return jsonify({"success": False, "error": f"Student number {stud_number} already exists in this group"}), 409
        if conn.execute("SELECT 1 FROM students WHERE email = ? AND group_id = ?", (email, group_id)).fetchone():
            return jsonify({"success": False, "error": f"Email {email} already exists in this group"}), 409

        existing_user = conn.execute("SELECT id, is_first_login FROM users WHERE stud_id = ?", (stud_number,)).fetchone()
        if existing_user:
            user_id = existing_user["id"]
            is_first_login = existing_user["is_first_login"]
            if is_first_login:
                existing_student = conn.execute("SELECT temp_password FROM students WHERE user_id = ? AND temp_password IS NOT NULL", (user_id,)).fetchone()
                temp_pw = existing_student["temp_password"] if existing_student else generate_temp_password()
            else:
                temp_pw = None
        else:
            temp_pw = generate_temp_password()
            hashed  = hash_password(temp_pw)
            mi_dot  = f"{middle_initial}. " if middle_initial else ""
            full_name = f"{first_name} {mi_dot}{surname}".strip()

            user_cur = conn.execute(
                "INSERT INTO users (is_teacher, stud_id, first_name, last_name, full_name, email, password, is_first_login) VALUES (0,?,?,?,?,?,?,1)",
                (stud_number, first_name, surname, full_name, email, hashed)
            )
            user_id = user_cur.lastrowid
            is_first_login = 1
            
        cur = conn.execute(
            "INSERT INTO students (user_id, group_id, stud_number, surname, first_name, middle_initial, email, temp_password, is_first_login) VALUES (?,?,?,?,?,?,?,?,?)",
            (user_id, group_id, stud_number, surname, first_name, middle_initial or None, email, temp_pw, is_first_login)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM students WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify({"success": True, "student": dict(row), "temp_password": temp_pw}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()


@part2.delete("/api/v1/students/<int:student_id>")
def delete_student(student_id: int):
    """Remove a student from the system (also deletes their user account)."""
    try:
        conn = get_connection()
        student = conn.execute("SELECT user_id FROM students WHERE id = ?", (student_id,)).fetchone()
        if not student:
            return jsonify({"success": False, "error": "Student not found"}), 404
        conn.execute("DELETE FROM record WHERE student_id = ?", (student_id,))
        conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
        if student["user_id"]:
            conn.execute("DELETE FROM users WHERE id = ?", (student["user_id"],))
        conn.commit()
        return jsonify({"success": True}), 200
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
            return jsonify({"success": False, "error": "Email not found in our records. Please contact your teacher to ensure you are registered."}), 404

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
            "UPDATE students SET temp_password = NULL, is_first_login = 0 WHERE LOWER(email) = LOWER(?)",
            (email,)
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

@part2.post("/api/v1/student/login")
def student_login():
    """
    Student email + password login (for already-activated accounts).
    Mirrors /api/v2/login but scoped to is_teacher = 0.
    """
    import secrets as _secrets
    from datetime import datetime as _dt, timedelta as _td

    payload = get_payload()
    email = normalize_text(payload.get("email") or "")
    password = normalize_text(payload.get("password") or "")

    if not email or not password:
        return jsonify({"success": False, "error": "Email and password are required"}), 400

    try:
        conn = get_connection()

        student = conn.execute(
            """
            SELECT st.id AS student_id, st.first_name, st.surname, st.stud_number,
                   st.email, st.group_id,
                   u.id AS user_id, u.password AS hashed_password, u.is_first_login,
                   g.group_number, g.group_name,
                   s.name AS section_name, s.id AS section_id
            FROM students st
            JOIN users u ON LOWER(u.email) = LOWER(st.email)
            JOIN groups g ON g.id = st.group_id
            JOIN sections s ON s.id = g.section_id
            WHERE LOWER(st.email) = LOWER(?)
            """,
            (email,)
        ).fetchone()

        if not student:
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        if student["is_first_login"]:
            return jsonify({"success": False, "error": "Account not yet activated. Please use the temporary code to set your password first."}), 403

        if not bcrypt.checkpw(password.encode("utf-8"), student["hashed_password"].encode("utf-8")):
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        response_data = {
            "success": True,
            "student": {
                "id": student["student_id"],
                "user_id": student["user_id"],
                "email": student["email"],
                "first_name": student["first_name"],
                "surname": student["surname"],
                "stud_number": student["stud_number"],
                "group_id": student["group_id"],
                "group_number": student["group_number"],
                "group_name": student["group_name"],
                "section_name": student["section_name"],
                "section_id": student["section_id"],
            }
        }

        if str(payload.get("remember")).lower() in ["1", "true", "on", "yes"]:
            token = _secrets.token_hex(32)
            expires_at = _dt.now() + _td(days=30)
            conn.execute(
                "INSERT INTO user_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
                (student["user_id"], token, expires_at.strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            response_data["token"] = token

        return jsonify(response_data), 200
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

@part2.get("/api/v1/users/<int:user_id>/student_dashboard")
def get_user_student_dashboard(user_id: int):
    """Get full student data + records + enrollments for the student's own view."""
    enrollment_id = request.args.get("enrollment_id", type=int)
    
    try:
        conn = get_connection()
        
        # 1. Fetch all enrollments for this user
        enrollments = conn.execute(
            """
            SELECT st.id AS student_id, st.stud_number,
                   g.group_number, g.group_name,
                   s.name AS section_name, s.id AS section_id,
                   t.full_name AS teacher_name
            FROM students st
            JOIN groups g ON g.id = st.group_id
            JOIN sections s ON s.id = g.section_id
            JOIN users t ON t.id = s.teacher_id
            WHERE st.user_id = ?
            ORDER BY s.created_at DESC
            """,
            (user_id,)
        ).fetchall()
        
        enrollments_list = [dict(e) for e in enrollments]
        if not enrollments_list:
            return jsonify({"success": False, "error": "No enrollments found for this user"}), 404
            
        # 2. Determine target student_id (enrollment_id)
        target_student_id = enrollment_id
        if not target_student_id:
            target_student_id = enrollments_list[0]["student_id"]
        else:
            # Validate that the requested enrollment belongs to this user
            if not any(e["student_id"] == target_student_id for e in enrollments_list):
                return jsonify({"success": False, "error": "Unauthorized enrollment ID"}), 403

        # 3. Fetch specific student details
        student = conn.execute(
            """
            SELECT st.*, g.group_number, g.group_name,
                   s.name AS section_name, s.id AS section_id,
                   t.full_name AS teacher_name
            FROM students st
            JOIN groups g ON g.id = st.group_id
            JOIN sections s ON s.id = g.section_id
            JOIN users t ON t.id = s.teacher_id
            WHERE st.id = ?
            """,
            (target_student_id,)
        ).fetchone()
        
        # 4. Fetch records for this student
        records = conn.execute(
            """
            SELECT id, date, type_of_undertaking, total_score, score, remarks, created_at
            FROM record WHERE student_id = ?
            ORDER BY date DESC, created_at DESC
            """,
            (target_student_id,)
        ).fetchall()

        return jsonify({
            "success": True,
            "student": dict(student),
            "records": [dict(r) for r in records],
            "enrollments": enrollments_list
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()
