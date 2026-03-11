# test_library_app.py
import pytest
import json
from library_app import app, init_db, DB_PATH
import os


# -----------------------------
# Fixture for Flask test client
# -----------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr("library_app.DB_PATH", str(test_db))
    init_db()  # create tables in temp DB
    with app.test_client() as client:
        yield client


# -----------------------------
# Parametrized book data
# -----------------------------
books_to_add = [
    ("1984", "Orwell"),
    ("Dune", "Herbert"),
    ("Clean Code", "Martin"),
    ("The Pragmatic Programmer", "Hunt"),
    ("Python Tricks", "Bader"),
]

invalid_books = [
    ({}, 400),
    ({"title": "No Author"}, 400),
    ({"author": "No Title"}, 400),
]

borrow_cases = [
    ("Alice", "1984", 200),
    ("Bob", "1984", 400),  # already borrowed
    ("Charlie", "Dune", 200),
    ("Dana", "Unknown", 400),
]


# -----------------------------
# Test add books
# -----------------------------
@pytest.mark.parametrize("title,author", books_to_add)
def test_add_book(client, title, author):
    rv = client.post("/books", json={"title": title, "author": author})
    assert rv.status_code == 201
    data = rv.get_json()
    assert data["book"]["title"] == title
    assert data["book"]["author"] == author
    assert data["book"]["available"] == 1


@pytest.mark.parametrize("payload,status", invalid_books)
def test_add_book_invalid(client, payload, status):
    rv = client.post("/books", json=payload)
    assert rv.status_code == status


# -----------------------------
# Test borrowing books
# -----------------------------
@pytest.mark.parametrize("name,title,expected_status", borrow_cases)
def test_borrow_book(client, name, title, expected_status):
    # Add book only if it exists in DB
    if title != "Unknown":
        rv_add = client.post("/books", json={"title": title, "author": "Author"})
        book_id = rv_add.get_json()["book"]["id"]
        # Borrow once for Alice to simulate "already borrowed" scenario
        if name == "Bob":
            client.post(f"/books/{book_id}/borrow", json={"name": "Alice"})
    else:
        book_id = 999  # simulate unknown book
    rv = client.post(f"/books/{book_id}/borrow", json={"name": name})
    assert rv.status_code == expected_status


# -----------------------------
# Borrow twice test
# -----------------------------
@pytest.mark.parametrize("first_borrower,second_borrower", [("Alice", "Bob")])
def test_borrow_twice(client, first_borrower, second_borrower):
    rv = client.post("/books", json={"title": "Book", "author": "Author"})
    book_id = rv.get_json()["book"]["id"]

    # First borrow should succeed
    rv1 = client.post(f"/books/{book_id}/borrow", json={"name": first_borrower})
    assert rv1.status_code == 200

    # Second borrow should fail
    rv2 = client.post(f"/books/{book_id}/borrow", json={"name": second_borrower})
    assert rv2.status_code == 400


# -----------------------------
# Test returning books
# -----------------------------
@pytest.mark.parametrize("borrow_first", [True, False])
def test_return_book(client, borrow_first):
    rv = client.post("/books", json={"title": "Book", "author": "Author"})
    book_id = rv.get_json()["book"]["id"]
    if borrow_first:
        client.post(f"/books/{book_id}/borrow", json={"name": "Alice"})
    rv2 = client.post(f"/books/{book_id}/return")
    assert rv2.status_code == 200


# -----------------------------
# List books tests
# -----------------------------
def test_list_books_empty(client):
    rv = client.get("/books")
    assert rv.status_code == 200
    assert rv.get_json()["books"] == []


def test_list_books_with_items(client):
    client.post("/books", json={"title": "Book1", "author": "Author1"})
    client.post("/books", json={"title": "Book2", "author": "Author2"})
    rv = client.get("/books")
    assert len(rv.get_json()["books"]) == 2


# -----------------------------
# Delete book tests
# -----------------------------
def test_delete_book(client):
    rv = client.post("/books", json={"title": "Book", "author": "Author"})
    book_id = rv.get_json()["book"]["id"]
    rv2 = client.delete(f"/books/{book_id}")
    assert rv2.status_code == 200


@pytest.mark.parametrize("book_id", [999, 12345])
def test_delete_nonexistent(client, book_id):
    rv = client.delete(f"/books/{book_id}")
    assert rv.status_code == 200


# -----------------------------
# Full workflow test
# -----------------------------
@pytest.mark.parametrize(
    "title,author,borrower",
    [("Book1", "Author1", "Alice"), ("Book2", "Author2", "Bob")],
)
def test_full_workflow(client, title, author, borrower):
    rv = client.post("/books", json={"title": title, "author": author})
    book_id = rv.get_json()["book"]["id"]
    rv2 = client.post(f"/books/{book_id}/borrow", json={"name": borrower})
    assert rv2.status_code == 200
    rv3 = client.post(f"/books/{book_id}/return")
    assert rv3.status_code == 200
    rv4 = client.delete(f"/books/{book_id}")
    assert rv4.status_code == 200
