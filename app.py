from flask import Flask, render_template, request, redirect, session, send_file, jsonify
from flask_mysqldb import MySQL
from werkzeug.security import check_password_hash
from reportlab.pdfgen import canvas
import uuid
import config

# -------------------------------------------------
# APP CONFIG
# -------------------------------------------------
app = Flask(__name__)
app.secret_key = 'secret123'

# -------------------------------------------------
# MYSQL CONFIG
# -------------------------------------------------
app.config['MYSQL_HOST'] = config.MYSQL_HOST
app.config['MYSQL_USER'] = config.MYSQL_USER
app.config['MYSQL_PASSWORD'] = config.MYSQL_PASSWORD
app.config['MYSQL_DB'] = config.MYSQL_DB

mysql = MySQL(app)

# -------------------------------------------------
# HOME / NAVIGATION
# -------------------------------------------------
@app.route('/')
def dashboard():
    if 'user' not in session:
        return redirect('/login')
    return render_template('navigation.html')

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
        cur.execute(
            "SELECT password_hash FROM users WHERE username=%s",
            (username,)
        )
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
# PRODUCTS (CREATE NEW)
# -------------------------------------------------
@app.route('/products', methods=['GET', 'POST'])
def products():
    if 'user' not in session:
        return redirect('/login')

    cur = mysql.connection.cursor()

    if request.method == 'POST':
        gst_percent = request.form.get('gst_percent') or 0

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
    request.form['min_stock'],
    request.form.get('gst_percent', 0)
))



        mysql.connection.commit()

    cur.execute("""
        SELECT id, part_no, part_name, mrp, sell_price, stock_qty, min_stock
        FROM products
        ORDER BY part_name""")
    products = cur.fetchall()

    cur.close()

    return render_template('products.html', products=products)

# -------------------------------------------------
# UPDATE EXISTING PRODUCT
# -------------------------------------------------
@app.route('/update-product', methods=['POST'])
def update_product():
    if 'user' not in session:
        return redirect('/login')

    product_id = request.form['product_id']
    mrp = request.form.get('mrp')
    sell_price = request.form.get('sell_price')
    stock_qty = request.form.get('stock_qty')
    gst = request.form.get('gst_percent')

    cur = mysql.connection.cursor()

    if mrp:
        cur.execute("UPDATE products SET mrp=%s WHERE id=%s", (mrp, product_id))

    if sell_price:
        cur.execute("UPDATE products SET sell_price=%s WHERE id=%s", (sell_price, product_id))

    if stock_qty:
        cur.execute(
            "UPDATE products SET stock_qty = stock_qty + %s WHERE id=%s",
            (stock_qty, product_id)
        )

    if gst:
        cur.execute("UPDATE products SET gst_percent=%s WHERE id=%s", (gst, product_id))

    mysql.connection.commit()
    cur.close()

    return redirect('/products')

    # ADD stock to existing stock
    if stock_qty:
        cur.execute(
            "UPDATE products SET stock_qty = stock_qty + %s WHERE id=%s",
            (stock_qty, product_id)
        )

    if gst_percent:
        cur.execute(
            "UPDATE products SET gst_percent=%s WHERE id=%s",
            (gst_percent, product_id)
        )

    mysql.connection.commit()
    cur.close()

    return redirect('/products')


# -------------------------------------------------
# BILLING
# -------------------------------------------------
@app.route('/billing', methods=['GET', 'POST'])
def billing():
    if 'user' not in session:
        return redirect('/login')

    if 'bill_items' not in session:
        session['bill_items'] = []

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT id, part_no, part_name, sell_price, stock_qty
        FROM products
        ORDER BY part_name
        """)
    products = cur.fetchall()



    if request.method == 'POST':
        product_id = request.form.get('product_id')
        quantity = request.form.get('quantity')

        if not product_id or not quantity:
            cur.close()
            return redirect('/billing')

        product_id = int(product_id)
        quantity = int(quantity)

        cur.execute("""
            SELECT part_name, sell_price, stock_qty, gst_percent
            FROM products WHERE id=%s
        """, (product_id,))
        product = cur.fetchone()

        if not product:
            cur.close()
            return "Product not found", 404

        part_name, sell_price, stock_qty, gst_percent = product

        if quantity > stock_qty:
            cur.close()
            return "Insufficient stock", 400

        subtotal = quantity * float(sell_price)
        gst_amount = subtotal * (float(gst_percent) / 100)
        total = subtotal + gst_amount

        session['bill_items'].append({
            'product_id': product_id,
            'part_name': part_name,
            'qty': quantity,
            'price': float(sell_price),
            'total': round(total, 2)
        })

        session.modified = True

    cur.close()

    grand_total = sum(item['total'] for item in session['bill_items'])

    return render_template(
        'billing.html',
        products=products,
        bill_items=session['bill_items'],
        grand_total=round(grand_total, 2)
    )

# -------------------------------------------------
# QUICK ADD PRODUCT
# -------------------------------------------------
@app.route('/quick-add-product', methods=['POST'])
def quick_add_product():
    if 'user' not in session:
        return redirect('/login')

    gst_percent = request.form.get('gst_percent') or 0

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO products
        (part_name, sell_price, stock_qty, gst_percent)
        VALUES (%s,%s,%s,%s)
    """, (
        request.form['part_name'],
        request.form['sell_price'],
        request.form['stock_qty'],
        gst_percent
    ))

    mysql.connection.commit()
    cur.close()
    return redirect('/billing')

# -------------------------------------------------
# FINALIZE BILL
# -------------------------------------------------
@app.route('/finalize', methods=['POST'])
def finalize_bill():
    if 'user' not in session or not session.get('bill_items'):
        return redirect('/billing')

    invoice_no = str(uuid.uuid4())[:8]
    total_amount = sum(i['total'] for i in session['bill_items'])

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO invoices (invoice_no, total_amount)
        VALUES (%s,%s)
    """, (invoice_no, total_amount))

    invoice_id = cur.lastrowid

    for i in session['bill_items']:
        cur.execute("""
            INSERT INTO invoice_items
            (invoice_id, product_id, quantity, price, total)
            VALUES (%s,%s,%s,%s,%s)
        """, (
            invoice_id,
            i['product_id'],
            i['qty'],
            i['price'],
            i['total']
        ))

        cur.execute("""
            UPDATE products SET stock_qty = stock_qty - %s
            WHERE id=%s
        """, (i['qty'], i['product_id']))

    mysql.connection.commit()
    cur.close()

    session.pop('bill_items')
    return redirect(f"/invoice/{invoice_no}")

# -------------------------------------------------
# INVOICE PDF
# -------------------------------------------------
@app.route('/invoice/<invoice_no>')
def generate_invoice(invoice_no):
    if 'user' not in session:
        return redirect('/login')

    cur = mysql.connection.cursor()

    # Invoice header data
    cur.execute("""
        SELECT id, invoice_no, total_amount, created_at
        FROM invoices WHERE invoice_no=%s
    """, (invoice_no,))
    invoice = cur.fetchone()
    invoice_id = invoice[0]

    # Invoice items
    cur.execute("""
        SELECT p.part_no, p.part_name, ii.quantity, ii.price, ii.total
        FROM invoice_items ii
        JOIN products p ON ii.product_id = p.id
        WHERE ii.invoice_id=%s
    """, (invoice_id,))
    items = cur.fetchall()
    cur.close()

    file_path = f"invoice_{invoice_no}.pdf"
    c = canvas.Canvas(file_path, pagesize=(595, 842))
    width, height = 595, 842

    # ---------------- HEADER ----------------
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, height-40, "SWARNA MOTORS (SALEM)")

    c.setFont("Helvetica", 9)
    c.drawCentredString(width/2, height-55, "Door No 6, Mulluvadi, Ambedkar Street No.1")
    c.drawCentredString(width/2, height-68, "PH: 0427-4514114 | GSTIN: 33ACFSS156C1ZD")

    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width/2, height-95, "TAX INVOICE")

    # ---------------- META ----------------
    c.setFont("Helvetica", 9)
    c.drawString(40, height-125, f"Invoice No : {invoice_no}")
    c.drawString(40, height-140, f"Invoice Date : {invoice[3].strftime('%d-%m-%Y')}")
    c.drawString(320, height-125, "State : Tamil Nadu")
    c.drawString(320, height-140, "State Code : 33")

    # ---------------- BILL TO ----------------
    c.rect(35, height-230, 525, 70)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(45, height-175, "BILL TO")

    c.setFont("Helvetica", 9)
    c.drawString(45, height-190, "SRI VINAYAGA AUTO PARTS")
    c.drawString(45, height-205, "Salem")

    # ---------------- TABLE HEADER ----------------
    y = height-270
    c.line(35, y, 560, y)

    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y-15, "S.No")
    c.drawString(70, y-15, "Part No")
    c.drawString(150, y-15, "Description")
    c.drawString(360, y-15, "Qty")
    c.drawString(400, y-15, "Rate")
    c.drawString(470, y-15, "Amount")

    c.line(35, y-20, 560, y-20)

    # ---------------- ITEMS ----------------
    c.setFont("Helvetica", 9)
    y -= 35
    sno = 1

    for item in items:
        part_no, name, qty, rate, total = item
        c.drawString(40, y, str(sno))
        c.drawString(70, y, part_no)
        c.drawString(150, y, name[:30])
        c.drawRightString(385, y, str(qty))
        c.drawRightString(440, y, f"{rate:.2f}")
        c.drawRightString(550, y, f"{total:.2f}")
        y -= 18
        sno += 1

    # ---------------- TOTAL ----------------
    c.line(35, y-10, 560, y-10)
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(440, y-30, "Total Amount")
    c.drawRightString(550, y-30, f"₹ {invoice[2]:.2f}")

    # ---------------- FOOTER ----------------
    c.setFont("Helvetica", 8)
    c.drawString(40, 100, "Bank: Canara Bank | A/C No: 62911010002253")
    c.drawString(40, 85, "IFSC: CNRB0001691")

    c.drawString(350, 85, "For SWARNA MOTORS")
    c.drawString(350, 60, "Authorised Signatory")

    c.save()

    return send_file(file_path, mimetype='application/pdf')

# -------------------------------------------------
# SEARCH PRODUCTS
# -------------------------------------------------
@app.route('/search-products')
def search_products():
    if 'user' not in session:
        return jsonify([])

    q = request.args.get('q', '').strip()

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT id, part_no, part_name, sell_price, stock_qty
        FROM products
        WHERE part_no LIKE %s OR part_name LIKE %s
        ORDER BY part_name
        LIMIT 10
    """, (f"{q}%", f"{q}%"))

    rows = cur.fetchall()
    cur.close()

    return jsonify([
        {
            "id": r[0],
            "part_no": r[1],
            "part_name": r[2],
            "price": r[3],
            "stock": r[4]
        } for r in rows
    ])




# -------------------------------------------------
# PROFIT REPORT
# -------------------------------------------------
@app.route('/profit-report')
def profit_report():
    if 'user' not in session:
        return redirect('/login')

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT
            p.part_name,
            SUM(ii.quantity),
            SUM(ii.quantity * p.cost_price),
            SUM(ii.total),
            SUM(ii.total) - SUM(ii.quantity * p.cost_price)
        FROM invoice_items ii
        JOIN products p ON ii.product_id = p.id
        GROUP BY p.id
    """)
    report = cur.fetchall()
    cur.close()

    return render_template('profit_report.html', report=report)

# -------------------------------------------------
# REPORTS
# -------------------------------------------------
@app.route('/reports')
def reports():
    if 'user' not in session:
        return redirect('/login')

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT DATE(created_at), SUM(total_amount)
        FROM invoices
        GROUP BY DATE(created_at)
    """)
    daily = cur.fetchall()
    cur.close()

    return render_template('reports.html', daily=daily)

# -------------------------------------------------
# RUN
# -------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True)
