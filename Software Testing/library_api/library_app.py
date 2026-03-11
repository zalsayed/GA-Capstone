# library_app.py
import sqlite3
from flask import Flask, request, jsonify

app = Flask(__name__)

DB_PATH = "library.db"  # default SQLite DB file


# -----------------------------
# DB helpers
# -----------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS books(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            available INTEGER DEFAULT 1
        )
    """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS borrowers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            book_id INTEGER
        )
    """
    )
    conn.commit()
    conn.close()


def add_book_db(title, author):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO books(title, author) VALUES (?, ?)", (title, author))
    conn.commit()
    book_id = c.lastrowid
    conn.close()
    return book_id


def list_books_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, author, available FROM books")
    rows = c.fetchall()
    conn.close()
    return rows


def borrow_book_db(book_id, borrower_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT available FROM books WHERE id=?", (book_id,))
    book = c.fetchone()
    if not book:
        conn.close()
        return False, "Book not found"
    if book[0] == 0:
        conn.close()
        return False, "Book already borrowed"
    c.execute(
        "INSERT INTO borrowers(name, book_id) VALUES (?, ?)", (borrower_name, book_id)
    )
    c.execute("UPDATE books SET available=0 WHERE id=?", (book_id,))
    conn.commit()
    conn.close()
    return True, None


def return_book_db(book_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE books SET available=1 WHERE id=?", (book_id,))
    conn.commit()
    conn.close()


def delete_book_db(book_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM books WHERE id=?", (book_id,))
    conn.commit()
    conn.close()


# -----------------------------
# Flask routes
# -----------------------------
@app.route("/books", methods=["POST"])
def add_book():
    data = request.json
    if not data.get("title") or not data.get("author"):
        return {"error": "Missing title or author"}, 400
    book_id = add_book_db(data["title"], data["author"])
    return {
        "book": {
            "id": book_id,
            "title": data["title"],
            "author": data["author"],
            "available": 1,
        }
    }, 201


@app.route("/books", methods=["GET"])
def list_books():
    rows = list_books_db()
    books = [
        {"id": r[0], "title": r[1], "author": r[2], "available": r[3]} for r in rows
    ]
    return {"books": books}, 200


@app.route("/books/<int:book_id>/borrow", methods=["POST"])
def borrow_book_route(book_id):
    data = request.json
    if not data.get("name"):
        return {"error": "Missing borrower name"}, 400
    success, msg = borrow_book_db(book_id, data["name"])
    if not success:
        return {"error": msg}, 400
    return {"book": {"id": book_id}}, 200


@app.route("/books/<int:book_id>/return", methods=["POST"])
def return_book_route(book_id):
    return_book_db(book_id)
    return {"book": {"id": book_id}}, 200


@app.route("/books/<int:book_id>", methods=["DELETE"])
def delete_book_route(book_id):
    delete_book_db(book_id)
    return {}, 200


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
    print("999")
