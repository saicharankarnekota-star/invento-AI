/* INVENTO AI client scripts: tabs, drag-drop, webcam, flash auto-dismiss */
let webcamStream = null;
const $ = (id) => document.getElementById(id);

function switchScanTab(tab) {
  ['upload', 'webcam', 'sample'].forEach((t) => {
    const sec = $(t + 'Section'), btn = $('tab' + t[0].toUpperCase() + t.slice(1) + 'Btn');
    if (sec) sec.style.display = t === tab ? 'block' : 'none';
    if (btn) btn.classList.toggle('active', t === tab);
  });
  if (tab !== 'webcam') stopWebcam();
}

function previewSelectedFile(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (ev) => {
    $('filePreviewImg').src = ev.target.result;
    $('fileNameBadge').textContent = file.name;
    $('filePreviewWrap').style.display = 'block';
  };
  reader.readAsDataURL(file);
}

async function startWebcam() {
  try {
    webcamStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    const v = $('webcamVideo');
    v.srcObject = webcamStream; v.style.display = 'block';
    $('webcamPlaceholder').style.display = 'none';
    $('webcamControls').style.display = 'flex';
  } catch (err) {
    alert('Camera unavailable. Allow camera access in your browser, or upload an image instead.');
  }
}

function stopWebcam() {
  if (webcamStream) webcamStream.getTracks().forEach((t) => t.stop());
  webcamStream = null;
  const v = $('webcamVideo');
  if (v) { v.srcObject = null; v.style.display = 'none'; }
  if ($('webcamPlaceholder')) $('webcamPlaceholder').style.display = 'block';
  if ($('webcamControls')) $('webcamControls').style.display = 'none';
}

function captureWebcamSnapshot() {
  const v = $('webcamVideo'), c = $('webcamCanvas');
  c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext('2d').drawImage(v, 0, 0);
  $('webcamImageData').value = c.toDataURL('image/jpeg', 0.9);
  stopWebcam();
  $('webcamForm').submit();
}

document.addEventListener('DOMContentLoaded', () => {
  const dz = $('dropZone'), fi = $('fileInput');
  if (dz && fi) {
    dz.addEventListener('click', () => fi.click());
    ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('dragover'); }));
    ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('dragover'); }));
    dz.addEventListener('drop', (e) => {
      if (e.dataTransfer.files.length) { fi.files = e.dataTransfer.files; previewSelectedFile({ target: fi }); }
    });
  }
  const sub = $('btnSubmitUpload');
  if ($('uploadForm') && sub) $('uploadForm').addEventListener('submit', () => {
    sub.disabled = true; sub.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Detecting products…';
  });
  document.querySelectorAll('.alert-success,.alert-info').forEach((a) => setTimeout(() => a.remove(), 6000));
});

/* Product form image preview */
function previewProductImage(e) {
  const file = e.target.files && e.target.files[0];
  const img = document.getElementById('productImagePreview');
  const ph = document.getElementById('productImagePlaceholder');
  if (!file || !img) return;
  const reader = new FileReader();
  reader.onload = (ev) => { img.src = ev.target.result; img.style.display = 'block'; if (ph) ph.style.display = 'none'; };
  reader.readAsDataURL(file);
}

/* ===== v4: AJAX cart, toasts, assistant ===== */
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function md(s) { return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'); }

function toast(message, category) {
  const host = $('toastHost'); if (!host) return;
  const el = document.createElement('div');
  el.className = 'toast toast-' + (category || 'info'); el.textContent = message;
  host.appendChild(el); setTimeout(() => el.remove(), 3500);
}

function setBadge(n) {
  document.querySelectorAll('.nav-link[href$="/billing"]').forEach((a) => {
    let b = a.querySelector('.cart-count');
    if (n > 0) { if (!b) { b = document.createElement('span'); b.className = 'cart-count'; a.appendChild(b); } b.textContent = n; }
    else if (b) b.remove();
  });
  const cb = $('cartCountBadge'); if (cb) cb.textContent = n + (n === 1 ? ' item' : ' items');
}

function ctlHtml(tile, n) {
  const max = +tile.dataset.max, name = esc(tile.dataset.name);
  if (!n) return `<form method="POST" action="${tile.dataset.add}" class="ajax-form"><button type="submit" class="qc-add" aria-label="Add ${name} to cart">ADD</button></form>`;
  return `<div class="qc-stepper"><form method="POST" action="${tile.dataset.update}" class="ajax-form"><input type="hidden" name="qty" value="${n - 1}"><button type="submit" aria-label="Remove one ${name}">&minus;</button></form><span>${n}</span><form method="POST" action="${tile.dataset.add}" class="ajax-form"><button type="submit" aria-label="Add one more ${name}" ${n >= max ? 'disabled' : ''}>+</button></form></div>`;
}

function applyCart(data) {
  (data.messages || []).forEach((m) => toast(m.message, m.category));
  const dyn = $('cartDynamic'); if (dyn && data.html) dyn.innerHTML = data.html;
  document.querySelectorAll('.qc-tile').forEach((t) => {
    const n = (data.cart || {})[t.dataset.pid] || 0;
    t.classList.toggle('in-cart', n > 0);
    t.querySelector('.qc-ctl').innerHTML = ctlHtml(t, n);
  });
  setBadge(data.count || 0);
  const cf = $('checkoutForm');
  if (cf) {
    cf.hidden = data.empty; $('clearForm').hidden = data.empty;
    $('payAmount').textContent = Number(data.total).toFixed(2);
    if (typeof updatePayUI === 'function') updatePayUI();
    $('addressWrap').hidden = data.order_type !== 'delivery';
    $('address').required = data.order_type === 'delivery';
  }
}

document.addEventListener('submit', async (e) => {
  const f = e.target;
  if (!f.classList || !f.classList.contains('ajax-form')) return;
  e.preventDefault();
  try {
    const res = await fetch(f.action, { method: 'POST', body: new FormData(f), headers: { 'X-Requested-With': 'fetch' } });
    if (!res.ok) throw new Error('bad response');
    applyCart(await res.json());
  } catch (err) { f.submit(); }
});

document.addEventListener('click', (e) => {
  const b = e.target.closest('.step-btn');
  if (!b) return;
  const form = b.closest('form'), inp = form.querySelector('input[name=qty]');
  inp.value = Math.max(0, (+inp.value || 0) + (+b.dataset.step));
  form.requestSubmit();
});

/* Assistant */
document.addEventListener('DOMContentLoaded', () => {
  const fab = $('botFab'), panel = $('botPanel');
  if (!fab || !panel) return;
  const log = $('botLog'), input = $('botInput'), chips = $('botChips');
  const DEFAULT_CHIPS = ['Low stock', 'Expiring soon', "Today's sales", 'Top selling', 'Recent bills', 'Show snacks'];
  const add = (role, html) => { const d = document.createElement('div'); d.className = 'bot-msg bot-' + role; d.innerHTML = html; log.appendChild(d); log.scrollTop = log.scrollHeight; return d; };
  const setChips = (list) => { chips.innerHTML = ''; list.forEach((c) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = c; b.onclick = () => send(c); chips.appendChild(b); }); };

  function itemsHtml(items) {
    return '<div class="bot-items">' + items.map((it) => {
      const img = it.image ? `<img class="pthumb" src="${esc(it.image)}" alt="">` : `<span class="pthumb pthumb-fallback" style="--h:${(it.name.length * 47) % 360}">${esc(it.name[0].toUpperCase())}</span>`;
      const btn = it.sellable ? `<button type="button" class="bot-add" data-id="${it.id}" aria-label="Add ${esc(it.name)} to cart">ADD</button>` : '';
      return `<div class="bot-item">${img}<div class="bot-item-info"><strong>${esc(it.name)}</strong><span>&#8377;${Number(it.price).toFixed(2)} &middot; ${it.stock} in stock &middot; ${esc(it.status)}</span></div>${btn}</div>`;
    }).join('') + '</div>';
  }

  async function send(text) {
    text = text.trim(); if (!text) return;
    add('user', esc(text)); input.value = '';
    const typing = add('bot', '<span class="typing">&bull;&bull;&bull;</span>');
    try {
      const res = await fetch('/api/assistant', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }) });
      if (!res.ok) throw new Error('bad response');
      const d = await res.json(); typing.remove();
      let html = md(d.reply || '');
      if (d.items && d.items.length) html += itemsHtml(d.items);
      if (d.links && d.links.length) html += '<div class="bot-links">' + d.links.map((l) => `<a href="${esc(l.url)}">${esc(l.label)}</a>`).join('') + '</div>';
      add('bot', html);
      setChips(d.chips && d.chips.length ? d.chips : DEFAULT_CHIPS);
      if (typeof d.cart_count === 'number') setBadge(d.cart_count);
    } catch (err) { typing.remove(); add('bot', 'Sorry, I could not reach the server. Please try again.'); }
  }

  log.addEventListener('click', async (e) => {
    const b = e.target.closest('.bot-add'); if (!b) return;
    try {
      const res = await fetch('/cart/add/' + b.dataset.id, { method: 'POST', headers: { 'X-Requested-With': 'fetch' } });
      const d = await res.json(); applyCart(d);
      if (!(d.messages || []).length) toast('Added to cart', 'success');
    } catch (err) { toast('Could not add to cart', 'danger'); }
  });

  function toggle(open) {
    panel.hidden = !open; fab.setAttribute('aria-expanded', String(open));
    if (open) {
      if (!log.children.length) { add('bot', "Hi! I'm the Invento assistant. Ask me about <strong>stock, prices, expiry, sales or bills</strong>, or say <strong>add 2 milk</strong> to put items in the cart."); setChips(DEFAULT_CHIPS); }
      input.focus();
    }
  }
  fab.addEventListener('click', () => toggle(panel.hidden));
  $('botClose').addEventListener('click', () => { toggle(false); fab.focus(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panel.hidden) toggle(false); });
  $('botForm').addEventListener('submit', (e) => { e.preventDefault(); send(input.value); });
});

/* ===== v5: payments (Cash / UPI / Card) ===== */
let qrLoading = null, lastQr = '';

function loadQrLib() {
  if (window.QRCode) return Promise.resolve();
  if (!qrLoading) qrLoading = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js';   // loaded only when UPI is chosen
    s.onload = resolve; s.onerror = reject; document.head.appendChild(s);
  });
  return qrLoading;
}

function payTotal() { const el = $('payAmount'); return el ? parseFloat(el.textContent) || 0 : 0; }

function luhn(num) {
  let sum = 0, flip = false;
  for (let i = num.length - 1; i >= 0; i--) { let d = +num[i]; if (flip) { d *= 2; if (d > 9) d -= 9; } sum += d; flip = !flip; }
  return sum % 10 === 0;
}

function cardBrand(n) {
  if (/^4/.test(n)) return 'Visa';
  if (/^(5[1-5]|222[1-9]|22[3-9]|2[3-6]|27[01]|2720)/.test(n)) return 'Mastercard';
  if (/^3[47]/.test(n)) return 'Amex';
  if (/^(60|65|81|82|508)/.test(n)) return 'RuPay';
  return '';
}

function renderUpi() {
  const form = $('checkoutForm'); if (!form) return;
  const amt = payTotal().toFixed(2);
  $('upiAmt').textContent = amt;
  const link = `upi://pay?pa=${encodeURIComponent(form.dataset.upi)}&pn=${encodeURIComponent(form.dataset.merchant)}&am=${amt}&cu=INR`;
  $('upiLink').href = link;
  if (link === lastQr) return;
  lastQr = link;
  loadQrLib().then(() => {
    const box = $('upiQr'); box.innerHTML = '';
    new QRCode(box, { text: link, width: 120, height: 120, correctLevel: QRCode.CorrectLevel.M });
  }).catch(() => { $('upiQr').textContent = 'QR unavailable offline. Use the UPI ID.'; });
}

function updateChange() {
  const out = $('cashChange'), got = parseFloat(($('cash_received') || {}).value);
  if (!out) return;
  if (!got) { out.textContent = ''; out.className = 'pay-change'; return; }
  const diff = got - payTotal();
  out.textContent = diff >= 0 ? `Change to return: ₹${diff.toFixed(2)}` : `Short by ₹${(-diff).toFixed(2)}`;
  out.className = 'pay-change ' + (diff >= 0 ? 'ok' : 'bad');
}

function updatePayUI() {
  const checked = document.querySelector('input[name=payment_method]:checked');
  if (!checked) return;
  const m = checked.value;
  ['Cash', 'UPI', 'Card'].forEach((k) => { const p = $('pay-' + k); if (p) p.hidden = k !== m; });
  const need = { UPI: ['upi_txn_id'], Card: ['card_number', 'card_name', 'card_expiry', 'card_cvv'] }[m] || [];
  ['upi_txn_id', 'card_number', 'card_name', 'card_expiry', 'card_cvv'].forEach((id) => { const el = $(id); if (el) el.required = need.includes(id); });
  if (m === 'Cash') updateChange();
  if (m === 'UPI') renderUpi();
}

document.addEventListener('DOMContentLoaded', () => {
  const form = $('checkoutForm'); if (!form) return;
  document.querySelectorAll('input[name=payment_method]').forEach((r) => r.addEventListener('change', updatePayUI));
  $('cash_received').addEventListener('input', updateChange);

  const num = $('card_number'), exp = $('card_expiry'), cvv = $('card_cvv'), err = $('cardError');
  num.addEventListener('input', () => {
    const d = num.value.replace(/\D/g, '').slice(0, 19);
    num.value = d.replace(/(.{4})/g, '$1 ').trim();
    $('cardBrand').textContent = cardBrand(d) ? '· ' + cardBrand(d) : '';
    num.setCustomValidity(''); err.textContent = '';
  });
  num.addEventListener('blur', () => {
    const d = num.value.replace(/\D/g, '');
    if (d && !(d.length >= 13 && luhn(d))) { num.setCustomValidity('Invalid card number'); err.textContent = 'That card number does not look valid.'; }
  });
  exp.addEventListener('input', () => {
    let d = exp.value.replace(/\D/g, '').slice(0, 4);
    if (d.length >= 3) d = d.slice(0, 2) + '/' + d.slice(2);
    exp.value = d;
    const m = /^(\d{2})\/(\d{2})$/.exec(d);
    const now = new Date(), ym = now.getFullYear() * 12 + now.getMonth();
    const bad = m && (+m[1] < 1 || +m[1] > 12 || (2000 + +m[2]) * 12 + (+m[1] - 1) < ym);
    exp.setCustomValidity(d.length === 5 && !m ? 'Use MM/YY' : (bad ? 'Card expired or invalid month' : ''));
  });
  cvv.addEventListener('input', () => { cvv.value = cvv.value.replace(/\D/g, '').slice(0, 4); });

  form.addEventListener('submit', () => {          // stop double-clicks creating two bills
    const b = form.querySelector('.btn-pay'); if (b) { b.disabled = true; b.innerHTML = '<span>Processing…</span>'; }
  });
  updatePayUI();
});

/* Switch the camera off whenever the page is hidden (saves CPU and battery) */
document.addEventListener('visibilitychange', () => { if (document.hidden && typeof stopWebcam === 'function') stopWebcam(); });
window.addEventListener('pagehide', () => { if (typeof stopWebcam === 'function') stopWebcam(); });
