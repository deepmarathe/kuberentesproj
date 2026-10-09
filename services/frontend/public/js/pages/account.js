// Login / register. Tokens are JWTs signed by the auth service with a key from a Kubernetes Secret.
import { $, api, auth, esc, setTitle, toast } from '../core.js';

export async function mount(root, mode) {
  setTitle(mode === 'login' ? 'Log in' : 'Create account');
  const login = mode === 'login';
  root.innerHTML = `
    <section class="panel narrow">
      <h1>${login ? 'Log in' : 'Create your account'}</h1>
      <form id="af" class="form">
        ${login ? '' : '<label>Name <input name="name" required maxlength="80" autocomplete="name"></label>'}
        <label>Email <input name="email" type="email" required autocomplete="email"></label>
        <label>Password <input name="password" type="password" required minlength="${login ? 1 : 8}" autocomplete="${login ? 'current-password' : 'new-password'}"></label>
        <button class="btn primary block">${login ? 'Log in' : 'Create account'}</button>
      </form>
      <p class="muted small">${login ? 'No account? <a href="#/register">Create one</a>' : 'Have an account? <a href="#/login">Log in</a>'}</p>
      ${login ? `<details class="hint"><summary>Where's the admin password?</summary>
        <p class="small">It lives in a Kubernetes Secret. Reading it is your first exam-style task:</p>
        <pre>kubectl -n shopfleet get secret admin-credentials \\
  -o jsonpath='{.data.email}' | base64 -d; echo
kubectl -n shopfleet get secret admin-credentials \\
  -o jsonpath='{.data.password}' | base64 -d; echo</pre></details>` : ''}
    </section>`;
  $('#af', root).onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const btn = e.target.querySelector('button');
    btn.disabled = true;
    try {
      const res = await api('POST', login ? '/api/auth/login' : '/api/auth/register', f);
      auth.login(res.token);
      toast(`Welcome, ${res.user.name}!`, 'good');
      location.hash = res.user.role === 'admin' ? '#/admin' : '#/';
    } catch (err) {
      toast(err.message, 'bad');
      btn.disabled = false;
    }
  };
  if (auth.user) root.insertAdjacentHTML('afterbegin', `<p class="muted">Already signed in as ${esc(auth.user.email)}.</p>`);
}
