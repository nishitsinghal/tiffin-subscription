import os
import calendar
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    flash,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-in-codespaces"
)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tiffin.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# =========================================================
# DATABASE MODELS
# =========================================================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(
        db.String(80),
        unique=True,
        nullable=False,
        index=True
    )
    password = db.Column(db.String(255), nullable=False)


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    phone = db.Column(
        db.String(20),
        unique=True,
        nullable=False,
        index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    ownerships = db.relationship(
        "Ownership",
        backref="customer",
        cascade="all, delete-orphan"
    )

    notifications = db.relationship(
        "Notification",
        backref="customer",
        cascade="all, delete-orphan"
    )

    def ownership_on(self, day):
        """
        Return the subscription ownership valid on a particular date.
        """
        matches = [
            ownership
            for ownership in self.ownerships
            if ownership.start_date <= day
            and (
                ownership.end_date is None
                or ownership.end_date >= day
            )
        ]

        if not matches:
            return None

        return max(matches, key=lambda x: x.start_date)

    def subscription_on(self, day):
        ownership = self.ownership_on(day)
        return ownership.subscription if ownership else None

    @property
    def current_ownership(self):
        return self.ownership_on(get_today())

    @property
    def current_subscription(self):
        ownership = self.current_ownership
        return ownership.subscription if ownership else None

    def status_on(self, day):
        subscription = self.subscription_on(day)

        if subscription is None:
            return "No Plan"

        if subscription.is_paused_on(day):
            return "Paused"

        return "Active"

    @property
    def status(self):
        return self.status_on(get_today())

    def bill_for_month(self, year=None, month=None):
        """
        Calculate only the amount belonging to this customer.

        Billing is split according to:
        1. Days the customer owned the subscription.
        2. Days the subscription was not paused.

        For the current simulated month, future days are not billed yet.
        """
        simulated_today = get_today()

        if year is None or month is None:
            year = simulated_today.year
            month = simulated_today.month

        days_in_month = calendar.monthrange(year, month)[1]
        month_start = date(year, month, 1)
        month_end = date(year, month, days_in_month)

        if year == simulated_today.year and month == simulated_today.month:
            billing_end = min(month_end, simulated_today)
        else:
            billing_end = month_end

        if billing_end < month_start:
            return {
                "bill": 0.0,
                "served_days": 0,
                "total_days": days_in_month,
            }

        bill = 0.0
        served_days = 0

        for ownership in self.ownerships:
            ownership_start = max(
                ownership.start_date,
                month_start
            )

            ownership_end = min(
                ownership.end_date or billing_end,
                billing_end
            )

            if ownership_start > ownership_end:
                continue

            subscription = ownership.subscription

            paused_dates = subscription.paused_dates_in_range(
                ownership_start,
                ownership_end,
                as_of=billing_end
            )

            current_day = ownership_start
            local_served_days = 0

            while current_day <= ownership_end:
                if current_day not in paused_dates:
                    local_served_days += 1

                current_day += timedelta(days=1)

            daily_rate = subscription.plan_price / days_in_month
            bill += daily_rate * local_served_days
            served_days += local_served_days

        return {
            "bill": round(bill, 2),
            "served_days": served_days,
            "total_days": days_in_month,
        }


class Subscription(db.Model):
    """
    The plan itself.

    A transfer does NOT create a new subscription.
    The same subscription keeps:
    - plan price
    - pause history

    Only ownership changes.
    """
    id = db.Column(db.Integer, primary_key=True)
    plan_price = db.Column(
        db.Float,
        nullable=False,
        default=3000.0
    )
    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    ownerships = db.relationship(
        "Ownership",
        backref="subscription",
        cascade="all, delete-orphan",
        order_by="Ownership.start_date"
    )

    pauses = db.relationship(
        "Pause",
        backref="subscription",
        cascade="all, delete-orphan",
        order_by="Pause.start_date"
    )

    @property
    def current_owner(self):
        today = get_today()

        ownership = next(
            (
                item
                for item in self.ownerships
                if item.start_date <= today
                and (
                    item.end_date is None
                    or item.end_date >= today
                )
            ),
            None,
        )

        return ownership.customer if ownership else None

    @property
    def open_pause(self):
        return next(
            (
                pause
                for pause in self.pauses
                if pause.end_date is None
            ),
            None,
        )

    def is_paused_on(self, day):
        for pause in self.pauses:
            pause_end = pause.end_date

            if pause_end is None:
                pause_end = get_today()

            if pause.start_date <= day <= pause_end:
                return True

        return False

    def paused_dates_in_range(
        self,
        start,
        end,
        as_of=None
    ):
        """
        Return all paused dates overlapping [start, end].
        Open pauses are treated as running until as_of.
        """
        paused = set()

        if as_of is None:
            as_of = get_today()

        for pause in self.pauses:
            pause_start = max(
                pause.start_date,
                start
            )

            pause_end = min(
                pause.end_date or as_of,
                end
            )

            if pause_start > pause_end:
                continue

            current_day = pause_start

            while current_day <= pause_end:
                paused.add(current_day)
                current_day += timedelta(days=1)

        return paused


class Ownership(db.Model):
    """
    Records which customer owned a subscription and for what dates.

    Inclusive date range:
    start_date <= day <= end_date

    end_date = NULL means current ownership.
    """
    id = db.Column(db.Integer, primary_key=True)

    subscription_id = db.Column(
        db.Integer,
        db.ForeignKey("subscription.id"),
        nullable=False,
        index=True
    )

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customer.id"),
        nullable=False,
        index=True
    )

    start_date = db.Column(
        db.Date,
        nullable=False,
        default=date.today
    )

    end_date = db.Column(
        db.Date,
        nullable=True
    )


class Pause(db.Model):
    """
    A real pause interval rather than a counter.
    """
    id = db.Column(db.Integer, primary_key=True)

    subscription_id = db.Column(
        db.Integer,
        db.ForeignKey("subscription.id"),
        nullable=False,
        index=True
    )

    start_date = db.Column(
        db.Date,
        nullable=False,
        default=date.today
    )

    end_date = db.Column(
        db.Date,
        nullable=True
    )


class Notification(db.Model):
    """
    Notification outbox.
    """
    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customer.id"),
        nullable=False,
        index=True
    )

    message = db.Column(
        db.String(300),
        nullable=False
    )

    for_date = db.Column(
        db.Date,
        nullable=False,
        index=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


class SystemClock(db.Model):
    """
    One-row simulated system date.

    This makes the morning notification twist testable
    without waiting for an actual day to pass.
    """
    id = db.Column(db.Integer, primary_key=True)

    current_date = db.Column(
        db.Date,
        nullable=False,
        default=date.today
    )


# =========================================================
# SYSTEM CLOCK
# =========================================================

def get_today():
    clock = SystemClock.query.get(1)

    if clock is None:
        clock = SystemClock(
            id=1,
            current_date=date.today()
        )
        db.session.add(clock)
        db.session.commit()

    return clock.current_date


# =========================================================
# AUTH HELPERS
# =========================================================

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))

        return view(*args, **kwargs)

    return wrapped


def api_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({
                "error": "Login required"
            }), 401

        return view(*args, **kwargs)

    return wrapped


# =========================================================
# BASIC PAGES
# =========================================================

@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (
            request.form.get("username") or ""
        ).strip()

        password = request.form.get("password") or ""

        if not username or not password:
            flash(
                "Username and password are required.",
                "warning"
            )
            return redirect(url_for("register"))

        if len(password) < 6:
            flash(
                "Password must contain at least 6 characters.",
                "warning"
            )
            return redirect(url_for("register"))

        existing_user = User.query.filter_by(
            username=username
        ).first()

        if existing_user:
            flash(
                "That username is already registered.",
                "warning"
            )
            return redirect(url_for("register"))

        user = User(
            username=username,
            password=generate_password_hash(
                password,
                method="pbkdf2:sha256"
            )
        )

        db.session.add(user)
        db.session.commit()

        flash(
            "Account created. Please log in.",
            "success"
        )

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (
            request.form.get("username") or ""
        ).strip()

        password = request.form.get("password") or ""

        user = User.query.filter_by(
            username=username
        ).first()

        if user and check_password_hash(
            user.password,
            password
        ):
            session["user_id"] = user.id
            session["username"] = user.username

            return redirect(url_for("dashboard"))

        flash(
            "Invalid username or password.",
            "danger"
        )

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/dashboard")
@login_required
def dashboard():
    search = (
        request.args.get("search") or ""
    ).strip()

    sort = request.args.get(
        "sort",
        "name"
    )

    page = request.args.get(
        "page",
        1,
        type=int
    )

    if page < 1:
        page = 1

    per_page = 6

    query = Customer.query

    if search:
        like = f"%{search}%"

        query = query.filter(
            db.or_(
                Customer.name.ilike(like),
                Customer.phone.ilike(like)
            )
        )

    # Database-level sorting where possible.
    if sort == "name":
        query = query.order_by(
            Customer.name.asc()
        )

        pagination = query.paginate(
            page=page,
            per_page=per_page,
            error_out=False
        )

        customers = pagination.items

    elif sort == "phone":
        query = query.order_by(
            Customer.phone.asc()
        )

        pagination = query.paginate(
            page=page,
            per_page=per_page,
            error_out=False
        )

        customers = pagination.items

    else:
        # Status and plan price depend on calculated/current state,
        # so sort these values in Python after filtering.
        all_customers = query.all()

        if sort == "status":
            all_customers.sort(
                key=lambda c: (
                    c.status,
                    c.name.lower()
                )
            )

        elif sort == "price":
            all_customers.sort(
                key=lambda c: (
                    c.current_subscription.plan_price
                    if c.current_subscription
                    else 0
                ),
                reverse=True
            )

        total = len(all_customers)
        pages = max(
            (total + per_page - 1) // per_page,
            1
        )

        if page > pages:
            page = pages

        start = (page - 1) * per_page
        end = start + per_page

        customers = all_customers[start:end]

        class SimplePagination:
            pass

        pagination = SimplePagination()
        pagination.items = customers
        pagination.page = page
        pagination.pages = pages
        pagination.total = total
        pagination.has_prev = page > 1
        pagination.has_next = page < pages
        pagination.prev_num = page - 1 if page > 1 else None
        pagination.next_num = page + 1 if page < pages else None

    rows = []

    for customer in customers:
        subscription = customer.current_subscription
        bill_info = customer.bill_for_month()

        rows.append({
            "customer": customer,
            "subscription": subscription,
            "status": customer.status,
            "bill": bill_info["bill"],
            "served_days": bill_info["served_days"],
            "total_days": bill_info["total_days"],
        })

    return render_template(
        "dashboard.html",
        rows=rows,
        pagination=pagination,
        search=search,
        sort=sort,
        today=get_today(),
    )


# =========================================================
# CUSTOMER REST APIs
# =========================================================

@app.route("/api/customers", methods=["GET"])
@api_login_required
def api_list_customers():
    search = (
        request.args.get("search") or ""
    ).strip()

    sort = request.args.get(
        "sort",
        "name"
    )

    page = request.args.get(
        "page",
        1,
        type=int
    )

    per_page = request.args.get(
        "per_page",
        6,
        type=int
    )

    page = max(page, 1)
    per_page = max(1, min(per_page, 50))

    query = Customer.query

    if search:
        like = f"%{search}%"

        query = query.filter(
            db.or_(
                Customer.name.ilike(like),
                Customer.phone.ilike(like)
            )
        )

    if sort == "phone":
        query = query.order_by(
            Customer.phone.asc()
        )
    elif sort == "name":
        query = query.order_by(
            Customer.name.asc()
        )
    else:
        query = query.order_by(
            Customer.name.asc()
        )

    pagination = query.paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    result = []

    for customer in pagination.items:
        bill_info = customer.bill_for_month()

        result.append({
            "id": customer.id,
            "name": customer.name,
            "phone": customer.phone,
            "status": customer.status,
            "bill": bill_info["bill"],
            "served_days": bill_info["served_days"],
            "total_days": bill_info["total_days"],
        })

    return jsonify({
        "items": result,
        "page": pagination.page,
        "pages": pagination.pages,
        "total": pagination.total,
        "has_next": pagination.has_next,
        "has_prev": pagination.has_prev,
    })


@app.route("/api/customers", methods=["POST"])
@api_login_required
def api_add_customer():
    name = (
        request.form.get("name") or ""
    ).strip()

    phone = (
        request.form.get("phone") or ""
    ).strip()

    plan_price = request.form.get(
        "plan_price",
        type=float
    )

    if not name or not phone or plan_price is None:
        return jsonify({
            "error": "Name, phone and plan price are required."
        }), 400

    if plan_price <= 0:
        return jsonify({
            "error": "Plan price must be greater than zero."
        }), 400

    existing_customer = Customer.query.filter_by(
        phone=phone
    ).first()

    if (
        existing_customer
        and existing_customer.current_subscription
    ):
        return jsonify({
            "error": "This customer already has an active subscription."
        }), 409

    if existing_customer is None:
        customer = Customer(
            name=name,
            phone=phone
        )
        db.session.add(customer)
        db.session.flush()
    else:
        customer = existing_customer
        customer.name = name

    subscription = Subscription(
        plan_price=plan_price
    )

    db.session.add(subscription)
    db.session.flush()

    db.session.add(
        Ownership(
            subscription_id=subscription.id,
            customer_id=customer.id,
            start_date=get_today(),
            end_date=None,
        )
    )

    db.session.commit()

    return jsonify({
        "message": "Customer subscribed successfully.",
        "customer_id": customer.id,
        "subscription_id": subscription.id,
    }), 201


@app.route("/api/customers/<int:cid>", methods=["GET"])
@api_login_required
def api_get_customer(cid):
    customer = Customer.query.get_or_404(cid)

    bill_info = customer.bill_for_month()

    return jsonify({
        "id": customer.id,
        "name": customer.name,
        "phone": customer.phone,
        "status": customer.status,
        "bill": bill_info["bill"],
        "served_days": bill_info["served_days"],
        "total_days": bill_info["total_days"],
    })


@app.route("/api/customers/<int:cid>", methods=["DELETE"])
@api_login_required
def api_delete_customer(cid):
    customer = Customer.query.get_or_404(cid)

    db.session.delete(customer)
    db.session.commit()

    return jsonify({
        "message": "Customer deleted successfully."
    })


@app.route("/api/customers/lookup", methods=["GET"])
@api_login_required
def api_lookup_customer():
    phone = (
        request.args.get("phone") or ""
    ).strip()

    if not phone:
        return jsonify({
            "error": "phone query parameter is required"
        }), 400

    customer = Customer.query.filter_by(
        phone=phone
    ).first()

    if customer is None:
        return jsonify({
            "error": "Customer not found."
        }), 404

    bill_info = customer.bill_for_month()

    return jsonify({
        "id": customer.id,
        "name": customer.name,
        "phone": customer.phone,
        "status": customer.status,
        "bill": bill_info["bill"],
        "served_days": bill_info["served_days"],
        "total_days": bill_info["total_days"],
    })


# =========================================================
# PAUSE / RESUME
# =========================================================

@app.route(
    "/api/subscriptions/<int:sid>/pause",
    methods=["POST"]
)
@api_login_required
def api_pause_subscription(sid):
    subscription = Subscription.query.get_or_404(sid)
    today = get_today()

    if subscription.is_paused_on(today):
        return jsonify({
            "error": "Subscription is already paused."
        }), 400

    db.session.add(
        Pause(
            subscription_id=subscription.id,
            start_date=today,
            end_date=None,
        )
    )

    db.session.commit()

    return jsonify({
        "message": "Subscription paused.",
        "subscription_id": subscription.id,
        "pause_started": today.isoformat(),
    })


@app.route(
    "/api/subscriptions/<int:sid>/resume",
    methods=["POST"]
)
@api_login_required
def api_resume_subscription(sid):
    subscription = Subscription.query.get_or_404(sid)

    open_pause = subscription.open_pause

    if open_pause is None:
        return jsonify({
            "error": "Subscription is not currently paused."
        }), 400

    today = get_today()

    # If pause started today and user immediately resumes,
    # remove the pause entirely.
    if open_pause.start_date == today:
        db.session.delete(open_pause)
    else:
        # Resume date is active, so pause ends yesterday.
        open_pause.end_date = today - timedelta(days=1)

    db.session.commit()

    return jsonify({
        "message": "Subscription resumed.",
        "subscription_id": subscription.id,
        "resumed_on": today.isoformat(),
    })


# =========================================================
# T6 — MID-CYCLE TRANSFER
# =========================================================

@app.route(
    "/api/subscriptions/<int:sid>/transfer",
    methods=["POST"]
)
@api_login_required
def api_transfer_subscription(sid):
    subscription = Subscription.query.get_or_404(sid)

    to_name = (
        request.form.get("to_name") or ""
    ).strip()

    to_phone = (
        request.form.get("to_phone") or ""
    ).strip()

    if not to_name or not to_phone:
        return jsonify({
            "error": "New customer name and phone are required."
        }), 400

    current_owner = subscription.current_owner

    if current_owner and current_owner.phone == to_phone:
        return jsonify({
            "error": "Subscription is already assigned to this customer."
        }), 400

    target_customer = Customer.query.filter_by(
        phone=to_phone
    ).first()

    if (
        target_customer
        and target_customer.current_subscription
    ):
        return jsonify({
            "error": "Target customer already has an active subscription."
        }), 409

    today = get_today()

    current_ownership = next(
        (
            ownership
            for ownership in subscription.ownerships
            if ownership.end_date is None
        ),
        None
    )

    if current_ownership is None:
        return jsonify({
            "error": "Subscription has no current owner."
        }), 400

    if today < current_ownership.start_date:
        return jsonify({
            "error": "Transfer date cannot be before current ownership start date."
        }), 400

    if target_customer is None:
        target_customer = Customer(
            name=to_name,
            phone=to_phone
        )

        db.session.add(target_customer)
        db.session.flush()

    else:
        target_customer.name = to_name

    # Old owner owns through yesterday.
    # New owner starts today.
    current_ownership.end_date = today - timedelta(days=1)

    db.session.add(
        Ownership(
            subscription_id=subscription.id,
            customer_id=target_customer.id,
            start_date=today,
            end_date=None,
        )
    )

    db.session.commit()

    return jsonify({
        "message": "Subscription transferred successfully.",
        "subscription_id": subscription.id,
        "old_owner_id": current_ownership.customer_id,
        "new_owner_id": target_customer.id,
        "transfer_date": today.isoformat(),
        "plan_price": subscription.plan_price,
    })


# =========================================================
# T1 — SIMULATED MORNING NOTIFICATIONS
# =========================================================

@app.route("/clock", methods=["POST"])
@api_login_required
def post_clock():
    """
    Set the simulated date.

    JSON:
        {"date": "2026-09-18"}

    Or with an empty body:
        advances one day.
    """
    clock = SystemClock.query.get(1)

    if clock is None:
        clock = SystemClock(
            id=1,
            current_date=date.today()
        )

        db.session.add(clock)
        db.session.flush()

    payload = request.get_json(
        silent=True
    ) or request.form

    date_text = (
        payload.get("date")
        if payload
        else None
    )

    if date_text:
        try:
            new_date = datetime.strptime(
                str(date_text),
                "%Y-%m-%d"
            ).date()
        except ValueError:
            return jsonify({
                "error": "Date must use YYYY-MM-DD format."
            }), 400

        clock.current_date = new_date

    else:
        clock.current_date += timedelta(days=1)

    db.session.commit()

    notifications_sent = run_morning_notifications(
        clock.current_date
    )

    return jsonify({
        "current_date": clock.current_date.isoformat(),
        "notifications_sent": notifications_sent,
    })


def run_morning_notifications(for_date):
    """
    Notify customers who:
    - hold a subscription on that date
    - are not paused on that date
    - have delivery due on a weekday

    Re-running the same date does not create duplicates.
    """
    if for_date.isoweekday() > 5:
        return 0

    count = 0

    for customer in Customer.query.all():
        ownership = customer.ownership_on(for_date)

        if ownership is None:
            continue

        subscription = ownership.subscription

        if subscription.is_paused_on(for_date):
            continue

        existing_notification = Notification.query.filter_by(
            customer_id=customer.id,
            for_date=for_date
        ).first()

        if existing_notification:
            continue

        db.session.add(
            Notification(
                customer_id=customer.id,
                message=(
                    "Reminder: your tiffin will be "
                    f"delivered today ({for_date.isoformat()})."
                ),
                for_date=for_date,
            )
        )

        count += 1

    db.session.commit()

    return count


@app.route("/outbox", methods=["GET"])
@api_login_required
def get_outbox():
    date_text = (
        request.args.get("date") or ""
    ).strip()

    query = Notification.query

    if date_text:
        try:
            selected_date = datetime.strptime(
                date_text,
                "%Y-%m-%d"
            ).date()
        except ValueError:
            return jsonify({
                "error": "Date must use YYYY-MM-DD format."
            }), 400

        query = query.filter_by(
            for_date=selected_date
        )

    notifications = query.order_by(
        Notification.for_date.desc(),
        Notification.id.asc()
    ).all()

    result = []

    for notification in notifications:
        customer = notification.customer

        result.append({
            "id": notification.id,
            "customer_id": notification.customer_id,
            "customer_name": (
                customer.name
                if customer
                else None
            ),
            "customer_phone": (
                customer.phone
                if customer
                else None
            ),
            "message": notification.message,
            "for_date": notification.for_date.isoformat(),
        })

    return jsonify(result)


# =========================================================
# T4 — MESSY DATA IMPORT
# =========================================================

DATE_FORMATS = [
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%d.%m.%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
]


def parse_messy_date(raw, fallback):
    """
    Returns:
        (parsed_date, was_blank)

    Blank value:
        uses fallback

    Unknown format:
        returns (None, False)
    """
    if raw is None or str(raw).strip() == "":
        return fallback, True

    raw = str(raw).strip()

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(
                raw,
                fmt
            ).date(), False
        except ValueError:
            continue

    return None, False


@app.route("/api/import", methods=["POST"])
@api_login_required
def api_import_customers():
    """
    Expected JSON:

    {
        "records": [
            {
                "name": "Aarav",
                "phone": "98-9898-9898",
                "plan_price": "3000",
                "start_date": "15/09/2026"
            }
        ]
    }
    """
    payload = request.get_json(
        silent=True
    ) or {}

    records = payload.get(
        "records",
        []
    )

    if not isinstance(records, list):
        return jsonify({
            "error": "records must be a list."
        }), 400

    today = get_today()

    imported = []
    deduped = []
    rejected = []

    seen_phones = set()

    for row_index, raw in enumerate(records):
        raw = raw or {}

        name = str(
            raw.get("name") or ""
        ).strip()

        phone = str(
            raw.get("phone") or ""
        ).strip()

        # Normalize phone numbers.
        phone = (
            phone
            .replace(" ", "")
            .replace("-", "")
            .replace("(", "")
            .replace(")", "")
            .replace("+91", "")
        )

        plan_price_raw = raw.get(
            "plan_price"
        )

        if not name or not phone:
            rejected.append({
                "row": row_index,
                "reason": "missing name or phone",
                "data": raw,
            })
            continue

        if len(phone) < 7 or len(phone) > 15:
            rejected.append({
                "row": row_index,
                "reason": "invalid phone number",
                "data": raw,
            })
            continue

        try:
            plan_price = float(
                plan_price_raw
            )

            if plan_price <= 0:
                raise ValueError

        except (TypeError, ValueError):
            rejected.append({
                "row": row_index,
                "reason": "missing or invalid plan_price",
                "data": raw,
            })
            continue

        parsed_date, was_blank = parse_messy_date(
            raw.get("start_date"),
            today
        )

        if parsed_date is None:
            rejected.append({
                "row": row_index,
                "reason": "unrecognized start_date format",
                "data": raw,
            })
            continue

        existing_customer = Customer.query.filter_by(
            phone=phone
        ).first()

        if (
            phone in seen_phones
            or (
                existing_customer
                and existing_customer.current_subscription
            )
        ):
            deduped.append({
                "row": row_index,
                "phone": phone,
                "reason": (
                    "duplicate phone or "
                    "already subscribed"
                ),
            })
            continue

        seen_phones.add(phone)

        if existing_customer is None:
            customer = Customer(
                name=name,
                phone=phone
            )

            db.session.add(customer)
            db.session.flush()

        else:
            customer = existing_customer
            customer.name = name

        subscription = Subscription(
            plan_price=plan_price
        )

        db.session.add(subscription)
        db.session.flush()

        db.session.add(
            Ownership(
                subscription_id=subscription.id,
                customer_id=customer.id,
                start_date=parsed_date,
                end_date=None,
            )
        )

        imported.append({
            "row": row_index,
            "customer_id": customer.id,
            "name": name,
            "phone": phone,
            "start_date": parsed_date.isoformat(),
            "date_defaulted": was_blank,
        })

    db.session.commit()

    return jsonify({
        "imported": imported,
        "deduped": deduped,
        "rejected": rejected,
        "summary": {
            "imported": len(imported),
            "deduped": len(deduped),
            "rejected": len(rejected),
        },
    })


# =========================================================
# DEMO DATA
# =========================================================

def seed_demo_data():
    if Customer.query.first():
        return

    def subscribe_customer(
        name,
        phone,
        price,
        start_date
    ):
        customer = Customer(
            name=name,
            phone=phone
        )

        db.session.add(customer)
        db.session.flush()

        subscription = Subscription(
            plan_price=price
        )

        db.session.add(subscription)
        db.session.flush()

        ownership = Ownership(
            subscription_id=subscription.id,
            customer_id=customer.id,
            start_date=start_date,
            end_date=None
        )

        db.session.add(ownership)

        return customer, subscription

    today = date.today()

    aarav, aarav_sub = subscribe_customer(
        "Aarav Mehta",
        "9898989898",
        3000,
        today.replace(day=1)
    )

    isha, isha_sub = subscribe_customer(
        "Isha Sharma",
        "9797979797",
        3000,
        today.replace(day=1)
    )

    kabir, kabir_sub = subscribe_customer(
        "Kabir Singh",
        "9696969696",
        4000,
        today.replace(day=3)
    )

    meera, meera_sub = subscribe_customer(
        "Meera Nair",
        "9595959595",
        3000,
        today.replace(day=5)
    )

    rohan, rohan_sub = subscribe_customer(
        "Rohan Verma",
        "9494949494",
        3600,
        today.replace(day=1)
    )

    # Demo pause history.
    pause_end = min(
        today.day,
        5
    )

    db.session.add(
        Pause(
            subscription_id=isha_sub.id,
            start_date=today.replace(day=1),
            end_date=today.replace(day=pause_end)
        )
    )

    # One older pause to demonstrate pause data.
    if today.day >= 10:
        db.session.add(
            Pause(
                subscription_id=rohan_sub.id,
                start_date=today.replace(day=7),
                end_date=today.replace(day=8)
            )
        )

    db.session.commit()


# =========================================================
# APPLICATION START
# =========================================================

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed_demo_data()
        get_today()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )