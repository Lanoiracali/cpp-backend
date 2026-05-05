# CPP Backend Service

This is the Python/Flask backend service for the CPP application.

## Requirements
- Python 3.8+
- SQLite3

## Installation

1. Create a virtual environment:
   ```bash
   python -m venv venv
   ```

2. Activate the virtual environment:
   - On Windows:
     ```bash
     venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```bash
     source venv/bin/activate
     ```

3. Install dependencies:
   ```bash
   pip install flask bcrypt
   ```

## Running the Server

Start the backend server by running:
```bash
python main.py
```
The server will start on `http://127.0.0.1:8000`.

## Database
The database uses SQLite and is stored in `cppstudrecord_db.sqlite`. It handles the storage of users, records, and session tokens.
