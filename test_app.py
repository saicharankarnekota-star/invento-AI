import unittest
import json
import os
import io
import tempfile
os.environ['INVENTO_DB'] = os.path.join(tempfile.mkdtemp(), 'test.db')   # tests never touch your real database
from datetime import date, timedelta
from app import app, init_db, get_db

class InventoAITestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        self.client = app.test_client()
        init_db()

    def login(self, username, password):
        return self.client.post('/login', data=dict(
            username=username,
            password=password
        ), follow_redirects=True)

    def test_01_login_logout(self):
        # Invalid login
        res = self.login('wrong', 'creds')
        self.assertIn(b'Invalid email or password', res.data)

        # Valid login
        res = self.login('admin', 'admin123')
        self.assertIn(b'Inventory Overview', res.data)

        # Logout
        res = self.client.get('/logout', follow_redirects=True)
        self.assertIn(b'Welcome to', res.data)

    def test_02_dashboard(self):
        self.login('admin', 'admin123')
        res = self.client.get('/dashboard')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Total Products', res.data)
        self.assertIn(b'Inventory Valuation', res.data)
        # Expiry widget verification
        self.assertIn(b'Expiry Alerts', res.data)
        self.assertIn(b'Product Expiry Watchlist', res.data)

    def test_03_product_crud_with_expiry(self):
        self.login('admin', 'admin123')
        
        # Add Product with Expiry Date
        test_exp = (date.today() + timedelta(days=20)).strftime("%Y-%m-%d")
        res = self.client.post('/products/add', data=dict(
            name='Test Yogurt Cup',
            category='Dairy',
            quantity='15',
            reorder_level='5',
            price='25.0',
            description='Fresh strawberry yogurt',
            expiry_date=test_exp
        ), follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Test Yogurt Cup', res.data)
        self.assertIn(test_exp.encode('utf-8'), res.data)

        # Filter by expiry
        res = self.client.get('/products?expiry_status=good')
        self.assertIn(b'Test Yogurt Cup', res.data)

        # Edit Product Expiry Date
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM products WHERE name = 'Test Yogurt Cup'")
        p_id = cursor.fetchone()[0]
        conn.close()

        new_exp = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        res = self.client.post(f'/products/edit/{p_id}', data=dict(
            name='Test Yogurt Cup Updated',
            category='Dairy',
            quantity='20',
            reorder_level='5',
            price='28.0',
            description='Updated yogurt pack',
            expiry_date=new_exp
        ), follow_redirects=True)
        self.assertIn(b'Test Yogurt Cup Updated', res.data)
        self.assertIn(new_exp.encode('utf-8'), res.data)

        # Delete Product
        res = self.client.post(f'/products/delete/{p_id}', follow_redirects=True)
        self.assertNotIn(b'Test Yogurt Cup Updated', res.data)

    def test_04_alerts_and_expiry(self):
        self.login('admin', 'admin123')
        res = self.client.get('/alerts')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Inventory & Expiry Alerts', res.data)
        self.assertIn(b'Expired Products', res.data)
        self.assertIn(b'Products Expiring Soon', res.data)

        # Test disposing of an expired item (e.g. Milk)
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM products WHERE name = 'Milk'")
        milk_id = cursor.fetchone()[0]
        conn.close()

        res = self.client.post(f'/alerts/dispose/{milk_id}', follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'disposed and cleared', res.data)

        # Verify Milk quantity became 0
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT quantity FROM products WHERE id = ?", (milk_id,))
        qty = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(qty, 0)

    def test_05_reports_and_csv_with_expiry(self):
        self.login('admin', 'admin123')
        res = self.client.get('/reports')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Inventory Asset Valuation Report', res.data)
        self.assertIn(b'Expiry & Spoilage Risk Report', res.data)
        self.assertIn(b'Value at Expiry Risk', res.data)

        # Test CSV export
        res = self.client.get('/reports/export-csv')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Product ID,Product Name,Category', res.data)
        self.assertIn(b'Expiry Date,Shelf Status', res.data)

    def test_06_ai_shelf_scan(self):
        """Full scan flow with a stand-in YOLO engine (so the test is fast and needs no torch)."""
        import app as app_module
        class _T:
            def __init__(s, v): s.v = v
            def tolist(s): return s.v
        class _Box:
            def __init__(s, box, cls, conf): s.xyxy, s.cls, s.conf = [_T(box)], [cls], [conf]
        class _Res:
            names = {0: 'bottle'}
            boxes = [_Box([130, 100, 350, 430], 0, 0.91)]
        class _Model:
            def predict(self, *a, **k): return [_Res()]
        det = app_module.detector
        det.model, det.custom, det.model_label = _Model(), False, 'test engine'
        det._load = lambda: True
        self.login('admin', 'admin123')
        with open(os.path.join('static', 'img', 'products', 'pepsi.png'), 'rb') as img_file:
            data = {'image': (io.BytesIO(img_file.read()), 'shelf.png')}
        res = self.client.post('/scan', data=data, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'objects matched to your products', res.data)
        self.assertIn(b'Pepsi', res.data)                 # recognised (or suggested) from the product image

        sync_payload = {'detected_counts': json.dumps({'Coca Cola': 8, 'Soap': 4, 'Shampoo': 6}), 'sync_mode': 'update'}
        res = self.client.post('/scan/sync', data=sync_payload, follow_redirects=True)
        self.assertIn(b'Inventory synchronized successfully', res.data)

    def test_06b_scan_reports_missing_engine_honestly(self):
        import app as app_module
        det = app_module.detector
        det.model = None
        det._load = lambda: False
        det.load_error = 'No module named torch'
        self.login('admin', 'admin123')
        with open(os.path.join('static', 'img', 'products', 'pepsi.png'), 'rb') as f:
            res = self.client.post('/scan', data={'image': (io.BytesIO(f.read()), 'x.png')}, content_type='multipart/form-data', follow_redirects=True)
        self.assertIn(b'AI engine could not start', res.data)   # an honest error, never invented counts

    # ---------- Accounts, images, billing ----------
    def test_09_register_and_email_login(self):
        import secrets
        email = f"cashier_{secrets.token_hex(3)}@example.com"
        res = self.client.post('/register', data=dict(name='Priya Sharma', email=email, password='secret1', confirm_password='secret1'), follow_redirects=True)
        self.assertIn(b'Account created', res.data)
        res = self.client.post('/login', data=dict(email=email, password='secret1'), follow_redirects=True)
        self.assertIn(b'Priya Sharma', res.data)       # shows the person's name, not "admin"
        conn = get_db(); conn.execute("DELETE FROM users WHERE email = ?", (email,)); conn.commit(); conn.close()

    def test_10_admin_email_login(self):
        res = self.client.post('/login', data=dict(email='admin@invento.ai', password='admin123'), follow_redirects=True)
        self.assertIn(b'Inventory Overview', res.data)

    def test_11_product_image_upload(self):
        from PIL import Image
        self.login('admin', 'admin123')
        buf = io.BytesIO(); Image.new('RGB', (50, 50), 'red').save(buf, 'PNG'); buf.seek(0)
        res = self.client.post('/products/add', data={'name': 'Image Test Item', 'category': 'Snacks', 'quantity': '5',
                               'reorder_level': '2', 'price': '10', 'image': (buf, 'item.png')},
                               content_type='multipart/form-data', follow_redirects=True)
        self.assertIn(b'uploads/products/product_', res.data)
        conn = get_db(); conn.execute("DELETE FROM products WHERE name = 'Image Test Item'"); conn.commit(); conn.close()

    def test_12_cart_and_billing_flow(self):
        self.login('admin', 'admin123')
        conn = get_db()
        conn.execute("UPDATE products SET quantity = 50 WHERE name = 'Coca Cola'"); conn.commit()
        row = conn.execute("SELECT id, quantity FROM products WHERE name = 'Coca Cola'").fetchone()
        pid, before = row['id'], row['quantity']
        conn.close()
        self.client.post(f'/cart/add/{pid}'); self.client.post(f'/cart/add/{pid}')
        res = self.client.get('/billing')
        self.assertIn(b'My cart', res.data)
        res = self.client.post('/checkout', data=dict(customer_name='Test Buyer', payment_method='UPI', upi_txn_id='UTR123456789'), follow_redirects=True)
        self.assertIn(b'INV-', res.data)
        self.assertIn(b'Test Buyer', res.data)
        conn = get_db()
        after = conn.execute("SELECT quantity FROM products WHERE id = ?", (pid,)).fetchone()['quantity']
        conn.close()
        self.assertEqual(after, before - 2)            # stock reduced by 2
        res = self.client.get('/bills')
        self.assertIn(b'Coca Cola x 2', res.data)      # items bought shown in history

    def test_13_cannot_oversell(self):
        self.login('admin', 'admin123')
        conn = get_db(); pid = conn.execute("SELECT id FROM products WHERE name = 'Soap'").fetchone()['id']; conn.close()
        for _ in range(50):
            self.client.post(f'/cart/add/{pid}')
        conn = get_db(); stock = conn.execute("SELECT quantity FROM products WHERE id = ?", (pid,)).fetchone()['quantity']; conn.close()
        with self.client.session_transaction() as sess:
            self.assertLessEqual(sess['cart'].get(str(pid), 0), stock)
        self.client.post('/cart/clear')


    # ---------- GST, discount, delivery, assistant, images ----------
    def _fresh_cola(self):
        conn = get_db()
        conn.execute("UPDATE products SET quantity = 50 WHERE name = 'Coca Cola'"); conn.commit()
        pid = conn.execute("SELECT id FROM products WHERE name = 'Coca Cola'").fetchone()['id']
        conn.close()
        return pid

    def test_14_gst_and_flat_discount(self):
        self.login('admin', 'admin123')
        pid = self._fresh_cola()
        self.client.post('/cart/clear')
        for _ in range(2):
            self.client.post(f'/cart/add/{pid}')          # 2 x 40 = 80, Beverages GST 12%
        self.client.post('/cart/options', data=dict(discount_type='flat', discount_value='10'))
        self.client.post('/checkout', data=dict(customer_name='GST Buyer', payment_method='Cash'), follow_redirects=True)
        conn = get_db(); b = conn.execute("SELECT * FROM bills WHERE customer_name = 'GST Buyer'").fetchone(); conn.close()
        self.assertEqual(b['subtotal'], 80.0)
        self.assertEqual(b['discount_amount'], 10.0)
        self.assertAlmostEqual(b['gst_amount'], 8.4, places=2)       # (80 - 10) x 12%
        self.assertAlmostEqual(b['total'], 78.4, places=2)

    def test_15_percent_discount_and_delivery_fee(self):
        self.login('admin', 'admin123')
        pid = self._fresh_cola()
        self.client.post('/cart/clear')
        self.client.post(f'/cart/add/{pid}')              # 40 only, below free-delivery limit
        self.client.post('/cart/options', data=dict(order_type='delivery', discount_type='percent', discount_value='50'))
        res = self.client.post('/checkout', data=dict(customer_name='Dlv', payment_method='UPI', upi_txn_id='UTR123456789'), follow_redirects=True)
        self.assertIn(b'delivery address', res.data)      # address required for delivery
        self.client.post('/checkout', data=dict(customer_name='Dlv', payment_method='UPI', upi_txn_id='UTR123456789', address='12 MG Road'), follow_redirects=True)
        conn = get_db(); b = conn.execute("SELECT * FROM bills WHERE customer_name = 'Dlv'").fetchone(); conn.close()
        self.assertEqual(b['discount_amount'], 20.0)      # 50% of 40
        self.assertEqual(b['delivery_fee'], 25.0)
        self.assertAlmostEqual(b['total'], 20 + 2.4 + 25, places=2)

    def test_16_ajax_cart_returns_json(self):
        self.login('admin', 'admin123')
        pid = self._fresh_cola()
        self.client.post('/cart/clear')
        res = self.client.post(f'/cart/add/{pid}', headers={'X-Requested-With': 'fetch'})
        data = res.get_json()
        self.assertEqual(data['count'], 1)
        self.assertIn('Bill details', data['html'])

    def test_17_assistant(self):
        self.login('admin', 'admin123')
        r = self.client.post('/api/assistant', json={'message': 'price of shampoo'}).get_json()
        self.assertIn('Shampoo', r['reply'])
        r = self.client.post('/api/assistant', json={'message': 'what is low on stock'}).get_json()
        self.assertTrue(r['items'])
        r = self.client.post('/api/assistant', json={'message': 'add 2 soap to cart'}).get_json()
        self.assertEqual(r['cart_count'], 2)
        r = self.client.post('/api/assistant', json={'message': "today's sales"}).get_json()
        self.assertIn('Today', r['reply'])

    def test_18_seed_products_have_images_and_bills_page(self):
        self.login('admin', 'admin123')
        res = self.client.get('/billing')
        self.assertIn(b'img/products/coca-cola.svg', res.data)
        self.assertEqual(self.client.get('/bills').status_code, 200)


    # ---------- payments + scanner ----------
    def _cart_one_cola(self):
        pid = self._fresh_cola(); self.client.post('/cart/clear'); self.client.post(f'/cart/add/{pid}'); return pid

    def test_19_upi_needs_transaction_id(self):
        self.login('admin', 'admin123'); self._cart_one_cola()
        res = self.client.post('/checkout', data=dict(customer_name='U1', payment_method='UPI'), follow_redirects=True)
        self.assertIn(b'UPI transaction ID', res.data)
        res = self.client.post('/checkout', data=dict(customer_name='U1', payment_method='UPI', upi_txn_id='412345678901', customer_upi='ravi@okaxis'), follow_redirects=True)
        self.assertIn(b'UPI Txn 412345678901', res.data)

    def test_20_card_validation_and_no_card_data_stored(self):
        self.login('admin', 'admin123'); self._cart_one_cola()
        bad = dict(customer_name='C1', payment_method='Card', card_number='4242 4242 4242 4241', card_name='Ravi', card_expiry='12/39', card_cvv='123')
        self.assertIn(b'not valid', self.client.post('/checkout', data=bad, follow_redirects=True).data)
        old = dict(bad, card_number='4242 4242 4242 4242', card_expiry='01/20')
        self.assertIn(b'expired', self.client.post('/checkout', data=old, follow_redirects=True).data)
        ok = dict(bad, card_number='4242 4242 4242 4242')
        res = self.client.post('/checkout', data=ok, follow_redirects=True)
        self.assertIn(b'Visa', res.data); self.assertIn(b'4242', res.data)
        conn = get_db(); row = conn.execute("SELECT * FROM bills WHERE customer_name = 'C1'").fetchone(); conn.close()
        self.assertNotIn('4242424242424242', ' '.join(str(v) for v in dict(row).values()).replace(' ', ''))   # full number never stored
        self.assertNotIn('123', str(row['payment_ref']).replace('4242', ''))

    def test_21_cash_shortfall_is_rejected(self):
        self.login('admin', 'admin123'); self._cart_one_cola()
        res = self.client.post('/checkout', data=dict(customer_name='K1', payment_method='Cash', cash_received='10'), follow_redirects=True)
        self.assertIn(b'less than the total', res.data)

    def test_22_scan_never_creates_junk_products(self):
        self.login('admin', 'admin123')
        conn = get_db(); before = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]; conn.close()
        self.client.post('/scan/sync', data={'detected_counts': json.dumps({'bottle': 3}), 'unmatched_counts': json.dumps({'cup': 2}), 'sync_mode': 'add'}, follow_redirects=True)
        conn = get_db(); after = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]; conn.close()
        self.assertEqual(before, after)

    def test_23_assigning_unmatched_objects_updates_stock(self):
        self.login('admin', 'admin123')
        conn = get_db(); row = conn.execute("SELECT id, quantity FROM products WHERE name = 'Pepsi'").fetchone(); conn.close()
        res = self.client.post('/scan/sync', data={'detected_counts': '{}', 'unmatched_counts': json.dumps({'bottle': 4}),
                               'assign::bottle': str(row['id']), 'sync_mode': 'update'}, follow_redirects=True)
        self.assertIn(b'Inventory synchronized successfully', res.data)
        conn = get_db(); q = conn.execute("SELECT quantity FROM products WHERE id = ?", (row['id'],)).fetchone()[0]; conn.close()
        self.assertEqual(q, 4)

    def test_24_matcher_recognises_catalog_products(self):
        import cv2
        from ai.detector import ProductDetector
        from app import product_catalog
        conn = get_db(); catalog = product_catalog(conn); conn.close()
        det = ProductDetector(); refs = []
        for p in catalog:
            if p['reference_path']:
                d = det._ref_descriptor(p['reference_path']); refs.append({**p, 'desc': d})
        self.assertGreaterEqual(len(refs), 8)
        img = cv2.imread([p['reference_path'] for p in catalog if p['name'] == 'Pepsi'][0])
        h, w = img.shape[:2]
        crop = img[int(h*.22):int(h*.88), int(w*.28):int(w*.72)]       # a tight "shelf box" around the product
        best, score, runner = det.identify(crop, 'bottle', refs)
        self.assertEqual(best['name'], 'Pepsi')


    def test_25_blank_background_is_never_matched_to_a_product(self):
        import numpy as np
        from ai.detector import ProductDetector, MATCH_MIN, MATCH_MARGIN, descriptor, similarity
        from app import product_catalog
        conn = get_db(); catalog = product_catalog(conn); conn.close()
        det = ProductDetector(); refs = [{**p, 'desc': det._ref_descriptor(p['reference_path'])} for p in catalog if p['reference_path']]
        for colour in ([225, 235, 250], [245, 245, 245], [128, 128, 128], [20, 20, 20]):
            crop = np.full((120, 70, 3), colour, np.uint8)
            scores = sorted((similarity(descriptor(crop), r['desc']) for r in refs), reverse=True)
            self.assertFalse(scores[0] >= MATCH_MIN and scores[0] - scores[1] >= MATCH_MARGIN, f'blank {colour} was matched')

    # ---------- Barcodes, food catalogue, accurate scanning ----------
    def test_26_every_product_has_valid_unique_barcode_and_foods_seeded(self):
        from ai.barcode import normalize
        conn = get_db()
        rows = conn.execute("SELECT name, barcode FROM products").fetchall(); conn.close()
        self.assertGreaterEqual(len(rows), 130)
        codes = [r['barcode'] for r in rows]
        self.assertTrue(all(normalize(c) for c in codes))
        self.assertEqual(len(codes), len(set(codes)))
        names = {r['name'] for r in rows}
        for n in ('Sprite', 'Amul Butter', 'Parle-G Biscuits', 'Maggi Masala Noodles', 'Basmati Rice', 'Banana'):
            self.assertIn(n, names)

    def test_27_barcode_add_edit_lookup_and_duplicates(self):
        self.login('admin', 'admin123')
        self.client.post('/products/add', data=dict(name='Test Chips', category='Snacks', price='10', quantity='5', reorder_level='2', barcode='8901234567890'), follow_redirects=True)
        res = self.client.get('/api/barcode/8901234567890')
        self.assertEqual(res.get_json()['name'], 'Test Chips')
        dup = self.client.post('/products/add', data=dict(name='Other', category='Snacks', price='10', quantity='5', reorder_level='2', barcode='8901234567890'), follow_redirects=True)
        self.assertIn(b'already belongs to another product', dup.data)
        self.assertEqual(self.client.get('/api/barcode/1234').status_code, 404)
        self.assertEqual(self.client.get('/barcode/8901234567890.svg').mimetype, 'image/svg+xml')

    def test_28_billing_barcode_adds_to_cart(self):
        self.login('admin', 'admin123')
        conn = get_db(); row = conn.execute("SELECT barcode FROM products WHERE name = 'Sprite'").fetchone(); conn.close()
        res = self.client.get('/billing?q=' + row['barcode'], follow_redirects=True)
        self.assertIn(b'Sprite', res.data)
        with self.client.session_transaction() as sess:
            self.assertEqual(sum(sess.get('cart', {}).values()), 1)

    def test_29_barcode_decoder_reads_rendered_barcode(self):
        import numpy as np, cv2
        from ai.barcode import ean13_bits, decode_barcodes, make_internal_ean13
        code = make_internal_ean13(42)
        row = np.array([20 if b == '1' else 240 for b in ean13_bits(code)], np.uint8).repeat(3)
        row = np.pad(row, (27, 27), constant_values=240)
        canvas = np.full((300, 500), 235, np.uint8); canvas[80:200, 30:30 + row.size] = row
        found = decode_barcodes(cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR))
        self.assertEqual([c for c, _ in found], [code])

    def test_30_barcode_in_photo_gives_exact_product_not_a_colour_guess(self):
        import numpy as np, cv2
        from ai.detector import ProductDetector
        from ai.barcode import ean13_bits
        from app import product_catalog
        conn = get_db(); catalog = product_catalog(conn); conn.close()
        sprite = [p for p in catalog if p['name'] == 'Sprite'][0]
        canvas = np.full((520, 800, 3), 235, np.uint8)
        row = np.array([20 if b == '1' else 240 for b in ean13_bits(sprite['barcode'])], np.uint8).repeat(3)
        row = np.pad(row, (27, 27), constant_values=240)
        canvas[300:420, 100:100 + row.size] = row[None, :, None]
        out = tempfile.mkdtemp()
        det = ProductDetector()
        res = det._build_result(canvas, [(60, 150, 460, 480, 'bottle', 0.9)], [], catalog, out, __import__('ai.barcode', fromlist=['x']).decode_barcodes(canvas))
        self.assertEqual(list(res['matched']), ['Sprite'])
        self.assertEqual(res['matched']['Sprite']['method'], 'barcode')
        self.assertEqual(res['unmatched'], {})


if __name__ == '__main__':
    unittest.main()
