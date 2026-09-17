# TiffinTrack — Tiffin Subscription Billing


TiffinTrack is a full-stack web application for a home-style tiffin service owner to manage subscriptions and calculate fair monthly bills.


A customer is charged only for the days they actually received service.


## Features


- Owner registration and login

- Password hashing

- Customer subscription management

- Pause / Resume subscription

- Automatic pro-rated billing

- Search by name or phone

- Sorting and pagination


## Twists Implemented


### T1 — Morning Notifications


`POST /clock` changes the simulated system date and runs the morning notification service.


Notifications are generated only for customers who:

- have an active subscription

- are not paused

- are scheduled on a weekday


Notifications are stored in the database and can be viewed using:


`GET /outbox`


Repeated execution for the same date does not create duplicate notifications.


### T6 — Mid-Cycle Transfer


A subscription is separate from its customer.


During transfer:

- the same subscription is kept

- plan price remains unchanged

- pause history remains unchanged

- old ownership ends before the transfer date

- new ownership starts on the transfer date


Billing is automatically split according to ownership days.


### T4 — Messy Data Import


`POST /api/import` accepts customer records containing messy or invalid data.


It handles:

- duplicate phones

- spaces / hyphens in phone numbers

- multiple date formats

- blank dates

- missing fields

- invalid prices

- invalid dates


The result is divided into:


`imported`, `deduped`, `rejected`


## Tech Stack


- Python

- Flask

- Flask-SQLAlchemy

- SQLite

- HTML/CSS/JavaScript

- Bootstrap 5


## Database Models


- `User` — owner authentication

- `Customer` — customer details

- `Subscription` — plan and price

- `Ownership` — subscription ownership history

- `Pause` — pause date ranges

- `Notification` — notification outbox

- `SystemClock` — simulated current date


## Setup


Install dependencies:


```bash

pip install flask flask-sqlalchemy

```


Run:


```bash

python app.py

```


Open forwarded port `5000` in GitHub Codespaces.


## REST API Endpoints


| Method | Endpoint | Purpose |

|---|---|---|

| GET | `/api/customers` | List customers |

| POST | `/api/customers` | Create subscription |

| GET | `/api/customers/<id>` | Get customer |

| DELETE | `/api/customers/<id>` | Delete customer |

| GET | `/api/customers/lookup?phone=` | Find customer by phone |

| POST | `/api/subscriptions/<id>/pause` | Pause subscription |

| POST | `/api/subscriptions/<id>/resume` | Resume subscription |

| POST | `/api/subscriptions/<id>/transfer` | Transfer subscription |

| POST | `/clock` | Change simulated date and run notifications |

| GET | `/outbox` | View notifications |

| POST | `/api/import` | Import and clean messy data |


The dashboard also supports:


`/dashboard?search=<term>&sort=<value>&page=<number>`


## Debugging


If Flask is missing:


```bash

pip install flask flask-sqlalchemy

```


To reset the SQLite database:


```bash

rm -rf instance __pycache__

```


Then run:


```bash

python app.py

```