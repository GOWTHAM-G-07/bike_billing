from flask import Flask, render_template, request, redirect, session, send_file, jsonify
from flask_mysqldb import MySQL
from werkzeug.security import check_password_hash
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from datetime import datetime
import os
import config

# -------------------------------------------------
# APP CONFIG
# -------------------------------------------------
app = Flask(__name__)
app.secret_key = "secret123"

# -------------------------------------------------
# MYSQL CONFIG
# -------------------------------------------------
app.config['MYSQL_HOST'] = config.MYSQL_HOST
app.config['MYSQL_USER'] = config.MYSQL_USER
app.config['MYSQL_PASSWORD'] = config.MYSQL_PASSWORD
app.config['MYSQL_DB'] = config.MYSQL_DB

mysql = MySQL(app)

# -------------------------------------------------
# HELPER: DB CONNECTION (DICT CURSOR)
# -------------------------------------------------
def get_db():
    conn = mysql.connection
    return conn

# -------------------------------------------------
# INVOICE NUMBER GENERATOR
# -------------------------------------------------
def generate_invoice_no():
    year = datetime.now().year

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT invoice_no
        FROM invoices
        WHERE invoice_no LIKE %s
        ORDER BY id DESC
        LIMIT 1
    """, (f"SV-{year}-%",))

    last = cur.fetchone()
    cur.close()

    next_no = int(last[0].split('-')[-1]) + 1 if last else 1
    return f"SV-{year}-{str(next_no).zfill(4)}"

# -------------------------------------------------
# LOGIN
# -------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cur = mysql.connection.cursor()
        cur.execute("SELECT password_hash FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        cur.close()

        if user and check_password_hash(user[0], password):
            session['user'] = username
            return redirect('/')
        else:
            error = "Invalid username or password"

    return render_template('login.html', error=error)

# -------------------------------------------------
# LOGOUT
# -------------------------------------------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# -------------------------------------------------
# DASHBOARD
# -------------------------------------------------
@app.route('/')
def dashboard():
    if 'user' not in session:
        return redirect('/login')
    return render_template('navigation.html')

# -------------------------------------------------
# PRODUCTS
# -------------------------------------------------
@app.route('/products', methods=['GET', 'POST'])
def products():
    if 'user' not in session:
        return redirect('/login')

    cur = mysql.connection.cursor()

    if request.method == 'POST':
        cur.execute("""
            INSERT INTO products
            (part_no, barcode, part_name, mrp, sell_price, stock_qty, min_stock, gst_percent)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            request.form['part_no'],
            request.form.get('barcode'),
            request.form['part_name'],
            request.form['mrp'],
            request.form['sell_price'],
            request.form['stock_qty'],
            request.form.get('min_stock', 0),
            request.form.get('gst_percent', 0)
        ))
        mysql.connection.commit()

    cur.execute("""
        SELECT id, part_no, part_name, mrp, sell_price, stock_qty, min_stock
        FROM products
        ORDER BY part_name
    """)
    products = cur.fetchall()
    cur.close()

    return render_template('products.html', products=products)

# -------------------------------------------------
# SEARCH PRODUCTS (AJAX)
# -------------------------------------------------
@app.route('/search-products')
def search_products():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])

    cur = mysql.connection.cursor(dictionary=True)
    cur.execute("""
        SELECT id, part_no, part_name, sell_price, stock_qty
        FROM products
        WHERE part_no LIKE %s OR part_name LIKE %s
        LIMIT 10
    """, (f"%{q}%", f"%{q}%"))

    rows = cur.fetchall()
    cur.close()

    return jsonify(rows)

# -------------------------------------------------
# BILLING PAGE
# -------------------------------------------------
@app.route('/billing')
def billing():
    if 'user' not in session:
        return redirect('/login')

    return render_template(
        'billing.html',
        bill_items=session.get('bill_items', []),
        grand_total=session.get('grand_total', 0)
    )


# -------------------------------------------------
# ADD ITEM TO BILL
# -------------------------------------------------
@app.route('/billing/add', methods=['POST'])
def billing_add():
    product_id = request.form['product_id']
    qty = int(request.form['quantity'])

    cur = mysql.connection.cursor(dictionary=True)
    cur.execute("""
        SELECT part_name, sell_price
        FROM products WHERE id=%s
    """, (product_id,))
    p = cur.fetchone()

    total = qty * float(p['sell_price'])

    bill = session.get('bill_items', [])
    bill.append({
        "product_id": product_id,
        "part_name": p['part_name'],
        "qty": qty,
        "price": float(p['sell_price']),
        "total": total
    })

    session['bill_items'] = bill
    session['grand_total'] = session.get('grand_total', 0) + total
    cur.close()

    return redirect('/billing')

# -------------------------------------------------
# FINALIZE BILL
# -------------------------------------------------
@app.route('/finalize', methods=['POST'])
def finalize_bill():
    if 'user' not in session or not session.get('bill_items'):
        return redirect('/billing')

    invoice_no = generate_invoice_no()
    total_amount = session['grand_total']

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO invoices (invoice_no, total_amount, created_at)
        VALUES (%s,%s,%s)
    """, (invoice_no, total_amount, datetime.now()))

    invoice_id = cur.lastrowid

    for i in session['bill_items']:
        cur.execute("""
            INSERT INTO invoice_items
            (invoice_id, product_id, quantity, price, total)
            VALUES (%s,%s,%s,%s,%s)
        """, (invoice_id, i['product_id'], i['qty'], i['price'], i['total']))

        cur.execute("""
            UPDATE products
            SET stock_qty = stock_qty - %s
            WHERE id=%s
        """, (i['qty'], i['product_id']))

    mysql.connection.commit()
    cur.close()

    session.pop('bill_items')
    session.pop('grand_total')

    return redirect(f"/invoice/{invoice_no}")

# -------------------------------------------------
# INVOICE PDF
# -------------------------------------------------
@app.route('/invoice/<invoice_no>')
def invoice_pdf(invoice_no):
    if 'user' not in session:
        return redirect('/login')

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT id, total_amount, created_at
        FROM invoices WHERE invoice_no=%s
    """, (invoice_no,))
    invoice = cur.fetchone()
    invoice_id = invoice[0]

    cur.execute("""
        SELECT p.part_no, p.part_name, ii.quantity, ii.price, ii.total
        FROM invoice_items ii
        JOIN products p ON ii.product_id = p.id
        WHERE ii.invoice_id=%s
    """, (invoice_id,))
    items = cur.fetchall()
    cur.close()

    os.makedirs("invoices", exist_ok=True)
    path = f"invoices/{invoice_no}.pdf"

    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, height-40, "SRI VINAYAGA AUTO PARTS")

    c.setFont("Helvetica", 10)
    c.drawString(40, height-80, f"Invoice No : {invoice_no}")
    c.drawString(40, height-95, f"Date : {invoice[2].strftime('%d-%m-%Y')}")

    y = height-140
    for part_no, name, qty, rate, total in items:
        c.drawString(40, y, part_no)
        c.drawString(120, y, name)
        c.drawRightString(380, y, str(qty))
        c.drawRightString(450, y, f"{rate:.2f}")
        c.drawRightString(550, y, f"{total:.2f}")
        y -= 18

    c.drawRightString(550, y-20, f"Total ₹ {invoice[1]:.2f}")
    c.save()

    return send_file(path, mimetype="application/pdf")
#--------------------------------------------------
#api/products
#--------------------------------------------------
@app.route('/api/product')
def api_product():
    q = request.args.get('query')

    cur = mysql.connection.cursor(dictionary=True)
    cur.execute("""
        SELECT id, part_no, part_name, sell_price, stock_qty
        FROM products
        WHERE part_no = %s OR barcode = %s
        LIMIT 1
    """, (q, q))

    product = cur.fetchone()
    cur.close()

    if not product:
        return jsonify({}), 404

    return jsonify({
        "id": product["id"],
        "part_no": product["part_no"],
        "name": product["part_name"],
        "price": float(product["sell_price"]),
        "stock": product["stock_qty"]
    })
#--------------------------------------------------
#-------------------------------------------------
@app.route('/sales-display')
def sales_display():
    return render_template('sales_display.html')

@app.route('/invoice-print')
def invoice_print():
    return render_template('invoice_print.html')

@app.route('/purchase-create')
def purchase_create():
    return render_template('purchase_create.html')

@app.route('/stock-summary')
def stock_summary():
    return render_template('stock_summary.html')

@app.route('/voucher')
def voucher():
    return render_template('voucher.html')
@app.route('/reports')
def reports():
    if 'user' not in session:
        return redirect('/login')
    return render_template('reports.html')


# -------------------------------------------------
# RUN
# -------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True)

