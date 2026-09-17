# TiffinTrack — Tiffin Subscription Billing

A web app for a home-style tiffin service owner to manage customer subscriptions and
automatically bill each customer only for the days they were actually served —
pausing a plan (travel, festivals, etc.) excludes those days from the bill.

## Features
- Owner registration & login (hashed passwords)
- Add customers with a monthly plan price
- Pause / Resume a customer's plan with real dates (not just a counter)
- Automatic pro-rated bill for the current month, based on actual paused days
- Search customers by name or phone
- Sort by name, phone, plan price, or status
- Paginated customer list

## Tech Stack
Python, Flask, Flask-SQLAlchemy, SQLite, Bootstrap 5 (CDN)

## Setup & Run (GitHub Codespaces or local)

1. Install dependencies:
   ```bash
   pip install flask flask-sqlalchemy
   ```
2. Run the app:
   ```bash
   python app.py
   ```
3. Open the forwarded port 5000 (Codespaces will show a popup — click **Open in Browser**),
   or visit `http://localhost:5000` locally.
4. Register an owner account, log in, and start adding customers from the dashboard.

The database (`tiffin.db`) is created automatically on first run, along with 3 demo
customers so the dashboard isn't empty.

## Debugging notes
- If you see `ModuleNotFoundError: No module named 'flask'`, run the pip install command above.
- If port 5000 shows "address already in use", stop the previous run with **Ctrl+C** in its
  terminal, or run `fuser -k 5000/tcp` and start again.
- Delete `tiffin.db` and re-run `python app.py` to reset the database.

## REST API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/customers` | List all customers with computed status & bill |
| POST | `/api/customers` | Add a new customer (`name`, `phone`, `plan_price`) |
| GET | `/api/customers/<id>` | Get one customer's details & current bill |
| DELETE | `/api/customers/<id>` | Delete a customer |
| POST | `/api/customers/<id>/pause` | Pause the customer's plan starting today |
| POST | `/api/customers/<id>/resume` | Resume (close) the customer's active pause |

Dashboard page itself (`/dashboard`) also accepts `search`, `sort`, and `page` query
parameters for search, sorting, and pagination.