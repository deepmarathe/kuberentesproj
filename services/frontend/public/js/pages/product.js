// Product detail: price, stock, quantity, reviews (write one when logged in), recommendations.
import { $, api, auth, emit, esc, priceHtml, setTitle, stars, toast } from '../core.js';

export async function mount(root, id) {
  const p = await api('GET', `/api/catalog/products/${id}`);
  setTitle(p.name);
  root.innerHTML = `
    <a href="#/" class="muted small">← Back to shop</a>
    <section class="product">
      <div class="product-emoji" aria-hidden="true">${esc(p.emoji)}</div>
      <div class="product-info">
        <div class="muted small">${esc(p.category)} · SKU ${esc(p.sku)}</div>
        <h1>${esc(p.name)}</h1>
        <div>${stars(p.rating, `${p.review_count} reviews`)}</div>
        <p>${esc(p.description)}</p>
        ${p.promotion ? `<p class="promo-note">⚡ ${esc(p.promotion)}</p>` : ''}
        <div class="big-price">${priceHtml(p)}</div>
        <p class="${p.stock === 0 ? 'st-bad' : p.stock < 25 ? 'st-warn' : 'muted'}">${p.stock === 0 ? 'Sold out' : `${p.stock} in stock`}</p>
        <div class="buy">
          <label class="muted small">Qty <input id="qty" type="number" min="1" max="20" value="1"></label>
          <button id="add" class="btn primary" ${p.stock === 0 ? 'disabled' : ''}>Add to cart</button>
        </div>
      </div>
    </section>
    <section class="panel"><h2>Customers also bought <span id="recoSource" class="muted small"></span></h2><div id="recos" class="grid small-grid"></div></section>
    <section class="panel"><h2>Reviews</h2><div id="reviewForm"></div><div id="reviews"></div></section>`;

  $('#add', root).onclick = async () => {
    const qty = Math.max(1, Math.min(20, Number($('#qty', root).value) || 1));
    try { emit('cart', await api('POST', '/api/cart/items', { product_id: p.id, qty })); toast(`Added ${qty} to cart`, 'good'); emit('open-cart'); } catch (e) { toast(e.message, 'bad'); }
  };
  renderReviewForm(root, p);
  await Promise.all([loadReviews(root, p.id), loadRecos(root, p.id)]);
}

async function loadRecos(root, id) {
  try {
    const { items, source } = await api('GET', `/api/catalog/products/${id}/recommendations`);
    $('#recoSource', root).textContent = source === 'co-purchase' ? '· computed by the recommender CronJob' : '· (fallback: same category — recommender has not run yet)';
    $('#recos', root).innerHTML = items.map((r) => `<a class="card mini" href="#/product/${r.id}"><div class="emoji">${esc(r.emoji)}</div><div>${esc(r.name)}</div><div>${priceHtml(r)}</div></a>`).join('') || '<p class="muted">Nothing yet.</p>';
  } catch (e) { $('#recos', root).innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

async function loadReviews(root, id) {
  try {
    const { items } = await api('GET', `/api/catalog/products/${id}/reviews`);
    $('#reviews', root).innerHTML = items.length ? items.map((r) => `
      <article class="review">
        <div>${stars(r.rating)} <strong>${esc(r.title)}</strong> ${r.verified ? '<span class="badge good">Verified purchase</span>' : ''}</div>
        <div class="muted small">${esc(r.author)} · ${new Date(r.created_at).toLocaleDateString()}</div>
        ${r.body ? `<p>${esc(r.body)}</p>` : ''}
      </article>`).join('') : '<p class="muted">No reviews yet. Be the first!</p>';
  } catch (e) { $('#reviews', root).innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

function renderReviewForm(root, p) {
  const box = $('#reviewForm', root);
  if (!auth.user) { box.innerHTML = '<p class="muted"><a href="#/login">Log in</a> to write a review.</p>'; return; }
  box.innerHTML = `
    <form id="rf" class="form inline-form">
      <label>Rating <select name="rating">${[5, 4, 3, 2, 1].map((n) => `<option value="${n}">${'★'.repeat(n)}</option>`).join('')}</select></label>
      <label class="grow">Title <input name="title" required maxlength="120"></label>
      <label class="full">Review <textarea name="body" rows="2" maxlength="2000"></textarea></label>
      <button class="btn primary">Post review</button>
    </form>`;
  $('#rf', root).onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const r = await api('POST', `/api/catalog/products/${p.id}/reviews`, { rating: Number(f.get('rating')), title: f.get('title'), body: f.get('body') });
      toast(r.verified ? 'Review posted (verified purchase)' : 'Review posted', 'good');
      e.target.reset();
      loadReviews(root, p.id);
    } catch (err) { toast(err.message, 'bad'); }
  };
}
