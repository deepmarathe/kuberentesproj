// Shop: categories, sorting, product grid with sale prices and ratings.
import { $, api, emit, esc, priceHtml, setTitle, stars, toast } from '../core.js';

const state = { category: null, q: '', sort: 'featured', root: null };

export function setQuery(q) { state.q = q; if (state.root) load(); }

export async function mount(root) {
  state.root = root;
  setTitle('');
  root.innerHTML = `
    <section class="hero">
      <div><h1>Everything you need, shipped by Kubernetes.</h1>
      <p class="muted">Every click below is served by pods you can watch, scale and break. Open "Under the hood" at the bottom.</p></div>
    </section>
    <div class="toolbar">
      <div id="categories" class="chips" role="toolbar" aria-label="Categories"></div>
      <label class="muted small">Sort
        <select id="sort">
          <option value="featured">Featured</option><option value="price-asc">Price: low → high</option>
          <option value="price-desc">Price: high → low</option><option value="rating">Top rated</option>
          <option value="deals">Biggest deals</option>
        </select></label>
    </div>
    <div id="products" class="grid" aria-live="polite"></div>`;
  $('#sort', root).value = state.sort;
  $('#sort', root).onchange = (e) => { state.sort = e.target.value; load(); };
  $('#products', root).addEventListener('click', onGridClick);
  await Promise.all([loadCategories(), load()]);
}

export function unmount() { state.root = null; }

async function loadCategories() {
  const wrap = $('#categories', state.root);
  try {
    const { items } = await api('GET', '/api/catalog/categories');
    const all = [{ category: null, label: 'All' }, ...items.map((c) => ({ category: c.category, label: `${c.category} (${c.products})` }))];
    wrap.innerHTML = all.map((c) => `<button class="chip ${state.category === c.category ? 'active' : ''}" data-cat="${esc(c.category ?? '')}">${esc(c.label)}</button>`).join('');
    wrap.onclick = (e) => {
      if (!e.target.matches('.chip')) return;
      state.category = e.target.dataset.cat || null;
      [...wrap.children].forEach((b) => b.classList.toggle('active', b === e.target));
      load();
    };
  } catch (e) {
    wrap.innerHTML = `<span class="muted small">Categories unavailable (${esc(e.message)})</span>`;
  }
}

const sorters = {
  featured: () => 0,
  'price-asc': (a, b) => a.sale_price_cents - b.sale_price_cents,
  'price-desc': (a, b) => b.sale_price_cents - a.sale_price_cents,
  rating: (a, b) => b.rating - a.rating || b.review_count - a.review_count,
  deals: (a, b) => (b.discount_percent || 0) - (a.discount_percent || 0),
};

async function load() {
  if (!state.root) return;
  const grid = $('#products', state.root);
  const params = new URLSearchParams();
  if (state.q) params.set('q', state.q);
  if (state.category) params.set('category', state.category);
  try {
    const { items } = await api('GET', `/api/catalog/products?${params}`);
    items.sort(sorters[state.sort]);
    grid.innerHTML = items.length ? items.map(card).join('') : '<p class="muted">No products match.</p>';
  } catch (e) {
    grid.innerHTML = `<div class="panel error">Could not load products: ${esc(e.message)}.<br><span class="muted small">Is the catalog Deployment ready? <code>kubectl -n shopfleet get pods -l app=catalog</code></span></div>`;
  }
}

function card(p) {
  const stock = p.stock === 0 ? '<span class="st-bad small">Sold out</span>'
    : p.stock < 25 ? `<span class="st-warn small">Only ${p.stock} left</span>` : '<span class="muted small">In stock</span>';
  return `<article class="card" data-id="${p.id}">
    ${p.discount_percent ? `<span class="ribbon">-${p.discount_percent}%</span>` : ''}
    <a class="card-link" href="#/product/${p.id}">
      <div class="emoji" aria-hidden="true">${esc(p.emoji)}</div>
      <h3>${esc(p.name)}</h3>
    </a>
    <div class="muted small">${esc(p.category)}</div>
    <div>${stars(p.rating, p.review_count)}</div>
    <div class="row"><div>${priceHtml(p)}</div>${stock}</div>
    <button class="btn primary block" data-add="${p.id}" ${p.stock === 0 ? 'disabled' : ''}>Add to cart</button>
  </article>`;
}

async function onGridClick(e) {
  const id = e.target.dataset?.add;
  if (!id) return;
  e.target.disabled = true;
  try {
    emit('cart', await api('POST', '/api/cart/items', { product_id: Number(id), qty: 1 }));
    toast('Added to cart', 'good');
  } catch (err) {
    toast(`Could not add: ${err.message}`, 'bad');
  } finally {
    e.target.disabled = false;
  }
}
