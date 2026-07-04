'use strict';

const UNCHANGED = '__UNCHANGED__';

let CSRF = '';
let loginValidated = false; // true after a successful login test (or pre-saved creds)
let orgaoId = ''; // numeric org id resolved by /api/detect-instance (select) — sent to /api/save
let restUrl = ''; // mod-wssei URL resolved by /api/detect-instance

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', init);

let pendingConfig = null;

async function init() {
  try {
    const [csrfData, config] = await Promise.all([
      fetch('/api/csrf').then(r => r.json()),
      fetch('/api/current-config').then(r => r.json()),
    ]);
    CSRF = csrfData.csrf;
    pendingConfig = config;
    if (config.already_configured) {
      showAlreadyConfigured(config);
    } else {
      populateConfig(config);
      document.getElementById('form-section').hidden = false;
    }
  } catch (e) {
    console.error('Wizard init failed:', e);
  }

  // Timeout bar (600s)
  let remaining = 600;
  const bar = document.getElementById('timeout-bar');
  setInterval(() => {
    remaining = Math.max(0, remaining - 1);
    bar.style.width = (remaining / 600 * 100) + '%';
    if (remaining === 60) bar.style.background = '#f59e0b';
    if (remaining === 0) bar.style.background = '#ef4444';
  }, 1000);
}

// ── Already-configured guard (mirrors run_setup_wizard's --force check) ───────

function showAlreadyConfigured(cfg) {
  const list = document.getElementById('already_configured_list');
  list.innerHTML = '';
  for (const [label, val] of [
    ['Usuário', cfg.sei_usuario],
    ['SEI URL', cfg.sei_web_url || cfg.sei_url],
    ['Órgão', cfg.sei_sigla_orgao],
  ]) {
    const li = document.createElement('li');
    li.textContent = `${label}: ${val || '—'}`;
    list.appendChild(li);
  }
  document.getElementById('already-configured-section').hidden = false;
}

function reconfigure() {
  document.getElementById('already-configured-section').hidden = true;
  populateConfig(pendingConfig);
  document.getElementById('form-section').hidden = false;
}

function keepAsIs() {
  const section = document.getElementById('already-configured-section');
  section.innerHTML = '<p style="text-align:center">Configuração mantida. Esta janela pode ser fechada.</p>';
  finish();
}

// ── Config pre-populate ───────────────────────────────────────────────────────

function populateConfig(cfg) {
  setVal('sei_url', cfg.sei_web_url || cfg.sei_url);
  setVal('rest_url', cfg.sei_url && cfg.sei_url !== cfg.sei_web_url ? cfg.sei_url : '');
  setVal('sei_sigla_orgao', cfg.sei_sigla_orgao);
  setVal('sei_usuario', cfg.sei_usuario);

  if (cfg.sei_senha === UNCHANGED) {
    markSaved('sei_senha');
    if (cfg.sei_usuario) {
      loginValidated = true;
      showBadge('login_badge', 'ok', 'Configurado');
    }
  }
}

function setVal(id, val) {
  const el = document.getElementById(id);
  if (el && val) el.value = val;
}

function markSaved(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.dataset.hasSaved = 'true';
  el.placeholder = '(senha salva — deixe em branco para manter)';
}

/** Returns the field value, or UNCHANGED if user left a saved-field blank. */
function getField(id) {
  const el = document.getElementById(id);
  if (!el) return '';
  if (!el.value && el.dataset.hasSaved === 'true') return UNCHANGED;
  return el.value;
}

// ── HTTP helpers ──────────────────────────────────────────────────────────────

async function post(url, data) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
    body: JSON.stringify(data),
  });
  return r.json();
}

// ── Error / badge helpers ─────────────────────────────────────────────────────

function showFieldErr(id, msg) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
  const inputId = id.replace('_err', '');
  const input = document.getElementById(inputId);
  if (input) input.setAttribute('aria-invalid', 'true');
}

function clearFieldErr(id) {
  const el = document.getElementById(id);
  if (el) { el.textContent = ''; el.classList.add('hidden'); }
  const inputId = id.replace('_err', '');
  const input = document.getElementById(inputId);
  if (input) input.removeAttribute('aria-invalid');
}

function showBadge(id, kind, msg) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = 'badge badge-' + kind;
  el.textContent = (kind === 'ok' ? '✓ ' : kind === 'err' ? '✗ ' : '⚠ ') + msg;
}

function showSpinner(id, on) {
  const el = document.getElementById(id);
  if (el) el.className = on ? 'spinner' : 'spinner hidden';
}

// ── Instance detection (organs + mod-wssei) ───────────────────────────────────

async function detectInstance() {
  clearFieldErr('sei_url_err');
  const seiUrl = document.getElementById('sei_url').value.trim().replace(/\/$/, '');
  if (!seiUrl) {
    showFieldErr('sei_url_err', 'Informe a URL do SEI.');
    return;
  }

  showSpinner('detect_spin', true);
  const res = await post('/api/detect-instance', { sei_url: seiUrl });
  showSpinner('detect_spin', false);

  if (!res.ok) {
    showFieldErr('sei_url_err', res.error || 'Falha na detecção.');
    return;
  }

  restUrl = res.rest_url || '';
  document.getElementById('rest_url').value = restUrl;
  const note = document.getElementById('modsei_note');
  if (restUrl && res.rest_url_confirmed) {
    note.textContent = 'mod-wssei detectado automaticamente.';
  } else if (restUrl) {
    note.textContent = 'Possível mod-wssei — não confirmado (pode estar atrás de proteção). Confira a URL.';
  } else {
    note.textContent = 'mod-wssei não detectado — modo web-only (sem REST).';
  }
  document.getElementById('modsei_details').hidden = false;

  const select = document.getElementById('sei_sigla_orgao_select');
  const textInput = document.getElementById('sei_sigla_orgao');
  if (res.organs && res.organs.length) {
    select.innerHTML = '<option value="">— selecione o órgão —</option>';
    for (const org of res.organs) {
      const opt = document.createElement('option');
      opt.value = org.id;
      opt.textContent = org.nome;
      select.appendChild(opt);
    }
    select.classList.remove('hidden');
    textInput.classList.add('hidden');
    select.onchange = () => {
      orgaoId = select.value;
      const selected = res.organs.find(o => o.id === select.value);
      textInput.value = selected ? selected.nome.split(/\s*-\s*/)[0] : '';
    };
  } else {
    select.classList.add('hidden');
    textInput.classList.remove('hidden');
  }
}

// ── Login test ─────────────────────────────────────────────────────────────────

async function testLogin() {
  const usuario = document.getElementById('sei_usuario').value.trim();
  const senha = getField('sei_senha');
  const seiUrl = document.getElementById('sei_url').value.trim().replace(/\/$/, '');
  const siglaOrgao = document.getElementById('sei_sigla_orgao').value.trim();

  if (!seiUrl || !usuario) {
    showBadge('login_badge', 'err', 'Preencha URL, usuário e senha');
    return;
  }
  if (!senha) {
    showBadge('login_badge', 'err', 'Preencha a senha');
    return;
  }

  showSpinner('login_spin', true);
  const res = await post('/api/validate', {
    sei_root: seiUrl,
    sei_usuario: usuario,
    sei_senha: senha,
    sei_sigla_orgao: siglaOrgao,
  });
  showSpinner('login_spin', false);
  loginValidated = res.ok;
  if (res.ok) {
    const detail = res.unidade_sigla ? ` — unidade ${res.unidade_sigla}` : '';
    showBadge('login_badge', res.warn ? 'warn' : 'ok', res.warn || ('Login OK, ' + res.nome + detail));
  } else {
    showBadge('login_badge', 'err', res.error || 'Erro');
  }
}

// Invalidate on any change to login fields
['sei_url', 'sei_usuario', 'sei_senha'].forEach(id => {
  document.addEventListener('DOMContentLoaded', () => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', () => { loginValidated = false; });
  });
});

// ── Save ──────────────────────────────────────────────────────────────────────

async function doSave() {
  clearFieldErr('sei_url_err');
  clearFieldErr('sei_sigla_orgao_err');
  document.getElementById('save_err').classList.add('hidden');

  const seiUrl = document.getElementById('sei_url').value.trim().replace(/\/$/, '');
  const usuario = document.getElementById('sei_usuario').value.trim();
  const senha = getField('sei_senha');
  const siglaOrgao = document.getElementById('sei_sigla_orgao').value.trim();

  if (!seiUrl) {
    showFieldErr('sei_url_err', 'A URL do SEI é obrigatória.');
    return;
  }
  if (!siglaOrgao) {
    showFieldErr('sei_sigla_orgao_err', 'O órgão é obrigatório.');
    return;
  }
  if (!usuario) {
    showBadge('login_badge', 'err', 'Usuário é obrigatório');
    document.getElementById('sei_usuario').focus();
    return;
  }
  if (!senha) {
    showBadge('login_badge', 'err', 'Senha é obrigatória');
    document.getElementById('sei_senha').focus();
    return;
  }
  if (!loginValidated) {
    showBadge('login_badge', 'err', 'Clique "Testar" para verificar as credenciais');
    document.getElementById('sei_senha').focus();
    return;
  }

  const payload = {
    sei_web_url: seiUrl,
    rest_url: document.getElementById('rest_url').value.trim() || restUrl,
    sei_usuario: usuario,
    sei_senha: senha,
    sei_sigla_orgao: siglaOrgao,
    sei_orgao: orgaoId,
    sei_sigla_orgao_sistema: '',
    sei_sigla_sistema: '',
  };

  showSpinner('save_spin', true);
  const res = await post('/api/save', payload);
  showSpinner('save_spin', false);

  if (!res.ok) {
    const err = document.getElementById('save_err');
    err.textContent = 'Erro ao salvar: ' + (res.error || 'desconhecido');
    err.classList.remove('hidden');
    return;
  }

  const list = document.getElementById('saved_list');
  list.innerHTML = '';
  for (const item of (res.saved || [])) {
    const li = document.createElement('li');
    li.textContent = item;
    list.appendChild(li);
  }

  document.getElementById('form-section').hidden = true;
  document.getElementById('step-done').hidden = false;
  finish();
}

// ── Done ──────────────────────────────────────────────────────────────────────

function finish() {
  document.getElementById('already-configured-section').hidden = true;
  post('/done', {}).catch(() => {});
}
