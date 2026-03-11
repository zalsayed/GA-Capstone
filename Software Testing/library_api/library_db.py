import sqlite3

# library_app.py
DB_PATH = "library.db"  # SQLite will create this file automatically


def init_db(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Create books table
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

    # Create borrowers table
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS borrowers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            book_id INTEGER,
            FOREIGN KEY(book_id) REFERENCES books(id)
        )
    """
    )

    conn.commit()
    conn.close()


def add_book(db_path, title, author):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("INSERT INTO books(title, author) VALUES (?, ?)", (title, author))
    book_id = c.lastrowid
    conn.commit()
    conn.close()
    return book_id


def get_book(db_path, book_id):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id, title, author, available FROM books WHERE id=?", (book_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "title": row[1], "author": row[2], "available": row[3]}
    return None


def list_books(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id, title, author, available FROM books")
    rows = c.fetchall()
    conn.close()
    return [
        {"id": r[0], "title": r[1], "author": r[2], "available": r[3]} for r in rows
    ]


def borrow_book(db_path, borrower_name, book_id):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Check if book exists and is available
    c.execute("SELECT available FROM books WHERE id=?", (book_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise ValueError("Book not found")
    if row[0] == 0:
        conn.close()
        raise ValueError("Book already borrowed")

    # Borrow the book
    c.execute(
        "INSERT INTO borrowers(name, book_id) VALUES (?, ?)", (borrower_name, book_id)
    )
    c.execute("UPDATE books SET available=0 WHERE id=?", (book_id,))
    conn.commit()
    conn.close()


def return_book(db_path, book_id):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("UPDATE books SET available=1 WHERE id=?", (book_id,))
    c.execute("DELETE FROM borrowers WHERE book_id=?", (book_id,))
    conn.commit()
    conn.close()


def delete_book(db_path, book_id):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("DELETE FROM books WHERE id=?", (book_id,))
    c.execute("DELETE FROM borrowers WHERE book_id=?", (book_id,))
    conn.commit()
    conn.close()
