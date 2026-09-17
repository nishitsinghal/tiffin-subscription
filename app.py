import os
import calendar
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tiffin.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ---------------------------------------------------------------
# MODELS
# ---------------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password = db.Column(db.String(200), nullable=False)


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    phone = db.Column(db.String(15), unique=True, nullable=False, index=True)
    plan_price = db.Column(db.Float, nullable=False, default=3000.0)  # monthly plan cost
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    pauses = db.relationship('Pause', backref='customer', cascade='all, delete-orphan',
                              order_by='Pause.start_date.desc()')

    @property
    def open_pause(self):
        """The pause record with no end_date yet, if the customer is currently paused."""
        return next((p for p in self.pauses if p.end_date is None), None)

    @property
    def is_paused_today(self):
        return self.open_pause is not None

    @property
    def status(self):
        return 'Paused' if self.is_paused_today else 'Active'

    def paused_days_in_month(self, year, month):
        """Count the distinct calendar days this customer was paused within the given month."""
        days_in_month = calendar.monthrange(year, month)[1]
        month_start = date(year, month, 1)
        month_end = date(year, month, days_in_month)
        paused_dates = set()
        for p in self.pauses:
            p_end = p.end_date or date.today()
            start = max(p.start_date, month_start)
            end = min(p_end, month_end)
            if start > end:
                continue
            d = start
            while d <= end:
                paused_dates.add(d)
                d = date.fromordinal(d.toordinal() + 1)
        return len(paused_dates)

    def calculate_bill(self, year=None, month=None):
        """Pro-rated bill = plan_price * (days actually served / days in that month)."""
        today = date.today()
        year = year or today.year
        month = month or today.month
        days_in_month = calendar.monthrange(year, month)[1]
        paused = self.paused_days_in_month(year, month)
        served_days = max(days_in_month - paused, 0)
        per_day_rate = self.plan_price / days_in_month
        bill = round(per_day_rate * served_days, 2)
        return {'bill': bill, 'served_days': served_days, 'total_days': days_in_month, 'paused_days': paused}


class Pause(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False, default=date.today)
    end_date = db.Column(db.Date, nullable=True)  # NULL => still paused / ongoing


# ---------------------------------------------------------------
# AUTH HELPERS
# ---------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------
# PAGES
# ---------------------------------------------------------------

@app.route('/')
def landing():
    return render_template('landing.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        if not username or not password:
            flash('Username and password are both required.')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('That username is already taken.')
            return redirect(url_for('register'))
        user = User(username=username, password=generate_password_hash(password, method='pbkdf2:sha256'))
        db.session.add(user)
        db.session.commit()
        flash('Account created — please log in.')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['username'] = user.username
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('landing'))


@app.route('/dashboard')
@login_required
def dashboard():
    search = request.args.get('search', '').strip()
    sort = request.args.get('sort', 'name')
    page = request.args.get('page', 1, type=int)
    per_page = 6

    query = Customer.query
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(Customer.name.ilike(like), Customer.phone.ilike(like)))

    if sort == 'phone':
        query = query.order_by(Customer.phone.asc())
    elif sort == 'price':
        query = query.order_by(Customer.plan_price.desc())
    elif sort == 'status':
        # Active first then Paused, computed in Python since status isn't a DB column
        pass
    else:
        query = query.order_by(Customer.name.asc())

    if sort == 'status':
        all_customers = query.all()
        all_customers.sort(key=lambda c: c.status)
        start = (page - 1) * per_page
        page_items = all_customers[start:start + per_page]
        total = len(all_customers)
        pages = max((total + per_page - 1) // per_page, 1)
        pagination = {
            'items': page_items, 'page': page, 'pages': pages,
            'has_prev': page > 1, 'has_next': page < pages,
            'prev_num': page - 1, 'next_num': page + 1,
        }
        rows = [{'c': c, **c.calculate_bill()} for c in page_items]
        return render_template('dashboard.html', rows=rows, pagination=pagination, search=search, sort=sort, manual=True)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    rows = [{'c': c, **c.calculate_bill()} for c in pagination.items]
    return render_template('dashboard.html', rows=rows, pagination=pagination, search=search, sort=sort, manual=False)


# ---------------------------------------------------------------
# REST API
# ---------------------------------------------------------------

@app.route('/api/customers', methods=['GET'])
@login_required
def api_list_customers():
    customers = Customer.query.order_by(Customer.name.asc()).all()
    return jsonify([{
        'id': c.id, 'name': c.name, 'phone': c.phone, 'plan_price': c.plan_price,
        'status': c.status, **c.calculate_bill()
    } for c in customers])


@app.route('/api/customers', methods=['POST'])
@login_required
def api_add_customer():
    name = (request.form.get('name') or '').strip()
    phone = (request.form.get('phone') or '').strip()
    plan_price = request.form.get('plan_price', type=float)

    if not name or not phone or not plan_price:
        flash('Name, phone and plan price are all required.')
        return redirect(url_for('dashboard'))
    if Customer.query.filter_by(phone=phone).first():
        flash('A customer with this phone number already exists.')
        return redirect(url_for('dashboard'))

    db.session.add(Customer(name=name, phone=phone, plan_price=plan_price))
    db.session.commit()
    flash(f'{name} added.')
    return redirect(url_for('dashboard'))


@app.route('/api/customers/<int:cid>', methods=['GET'])
@login_required
def api_get_customer(cid):
    c = Customer.query.get_or_404(cid)
    return jsonify({'id': c.id, 'name': c.name, 'phone': c.phone, 'plan_price': c.plan_price,
                     'status': c.status, **c.calculate_bill()})


@app.route('/api/customers/<int:cid>', methods=['DELETE'])
@login_required
def api_delete_customer(cid):
    c = Customer.query.get_or_404(cid)
    db.session.delete(c)
    db.session.commit()
    return jsonify({'status': 'deleted'})


@app.route('/api/customers/<int:cid>/pause', methods=['POST'])
@login_required
def api_pause_customer(cid):
    customer = Customer.query.get_or_404(cid)
    if customer.is_paused_today:
        flash(f'{customer.name} is already paused.')
        return redirect(url_for('dashboard'))
    db.session.add(Pause(customer_id=cid, start_date=date.today(), end_date=None))
    db.session.commit()
    flash(f'{customer.name} paused starting today.')
    return redirect(url_for('dashboard'))


@app.route('/api/customers/<int:cid>/resume', methods=['POST'])
@login_required
def api_resume_customer(cid):
    customer = Customer.query.get_or_404(cid)
    open_pause = customer.open_pause
    if not open_pause:
        flash(f'{customer.name} is not currently paused.')
        return redirect(url_for('dashboard'))
    open_pause.end_date = date.today()
    db.session.commit()
    flash(f'{customer.name} resumed.')
    return redirect(url_for('dashboard'))


# ---------------------------------------------------------------
# BOOTSTRAP / SEED
# ---------------------------------------------------------------

def seed_demo_data():
    if Customer.query.first():
        return
    demo = [
        Customer(name="Aarav Mehta", phone="9898989898", plan_price=3000),
        Customer(name="Isha Sharma", phone="9797979797", plan_price=3000),
        Customer(name="Kabir Singh", phone="9696969696", plan_price=4000),
    ]
    db.session.add_all(demo)
    db.session.commit()

    isha = Customer.query.filter_by(phone="9797979797").first()
    today = date.today()
    db.session.add(Pause(
        customer_id=isha.id,
        start_date=today.replace(day=1),
        end_date=today.replace(day=min(5, today.day))
    ))
    db.session.commit()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_demo_data()
    app.run(host='0.0.0.0', port=5000, debug=True)