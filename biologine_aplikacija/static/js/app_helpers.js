
function rrToggleCard(btn) {
  const card = btn.closest('.results-card');
  if (card) card.classList.toggle('collapsed');
}

function rrConfirmDelete(form, name) {
  return confirm('Delete "' + (name || 'this project') + '"? This action cannot be undone.');
}

function setupUploadZone(zoneId, inputId, filenameId) {
  const zone = document.getElementById(zoneId);
  const input = document.getElementById(inputId);
  const filenameEl = filenameId ? document.getElementById(filenameId) : null;
  if (!zone || !input) return;

  function setFilename(name) {
    if (filenameEl) filenameEl.textContent = name;
    zone.classList.toggle('has-file', !!name);
  }

  input.addEventListener('change', function () {
    setFilename(this.files[0]?.name || '');
  });

  zone.addEventListener('dragover', e => {
    e.preventDefault();
    e.stopPropagation();
    zone.classList.add('drag-over');
  });
  zone.addEventListener('dragleave', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
  });
  zone.addEventListener('drop', e => {
    e.preventDefault();
    e.stopPropagation();
    zone.classList.remove('drag-over');
    const files = e.dataTransfer.files;
    if (!files.length) return;
    const dt = new DataTransfer();
    dt.items.add(files[0]);
    input.files = dt.files;
    setFilename(files[0].name);
  });
}

(function () {
  function syncSeg(seg) {
    seg.querySelectorAll('label').forEach(lbl => {
      const input = lbl.querySelector('input[type="radio"]');
      lbl.classList.toggle('checked', !!(input && input.checked));
    });
  }

  function bind(seg) {
    seg.addEventListener('change', e => {
      if (e.target.matches('input[type="radio"]')) syncSeg(seg);
    });
    syncSeg(seg);
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.hf-radio-seg, .app-page .radio-seg').forEach(bind);
  });
})();

function setupSimpleInputTabs(opts) {
  const cfg = Object.assign({
    paneUploadId: 'pane-upload',
    panePasteId: 'pane-paste',
    tabUploadId: 'tab-upload',
    tabPasteId: 'tab-paste',
    radioName: 'input_source',
  }, opts || {});

  const paneUpload = document.getElementById(cfg.paneUploadId);
  const panePaste  = document.getElementById(cfg.panePasteId);
  const tabUpload  = document.getElementById(cfg.tabUploadId);
  const tabPaste   = document.getElementById(cfg.tabPasteId);
  if (!paneUpload || !panePaste) return;

  window.appSwitchTab = function (tab) {
    const isUpload = tab === 'upload';
    paneUpload.style.display = isUpload ? 'block' : 'none';
    panePaste.style.display  = isUpload ? 'none'  : 'block';
    if (tabUpload) tabUpload.classList.toggle('active', isUpload);
    if (tabPaste)  tabPaste.classList.toggle('active', !isUpload);
    document.querySelectorAll('input[type="radio"][name="' + cfg.radioName + '"]').forEach(r => {
      r.checked = (r.value === tab);
    });
    if (!isUpload) {
      panePaste.querySelectorAll('textarea').forEach(function(ta) { ta.scrollTop = 0; });
    }
  };

  const checked = document.querySelector('input[type="radio"][name="' + cfg.radioName + '"]:checked');
  window.appSwitchTab(checked && checked.value === 'paste' ? 'paste' : 'upload');
}

function setupHmmSourceAndAutocomplete() {
  const radios = document.querySelectorAll('input[name="hmm_source"]');
  const uploadSection = document.getElementById('upload-section');
  const externalSection = document.getElementById('external-section');
  const input = document.getElementById('external_hmm_id');
  const dropdown = document.getElementById('autocomplete-results');

  function toggleSections() {
    if (!uploadSection || !externalSection) return;
    const val = document.querySelector('input[name="hmm_source"]:checked')?.value;
    uploadSection.style.display = val === 'upload' ? 'block' : 'none';
    externalSection.style.display = val === 'library' ? 'block' : 'none';
  }
  radios.forEach(r => r.addEventListener('change', toggleSections));
  toggleSections();

  if (!input || !dropdown) return;

  function render(results) {
    if (!results.length) {
      dropdown.innerHTML = '<div class="autocomplete-item">No results found</div>';
      dropdown.style.display = 'block';
      return;
    }
    dropdown.innerHTML = results.map(r =>
      `<div class="autocomplete-item" data-id="${r.id}"><strong>${r.id}</strong> - ${r.name}` +
      `<div class="autocomplete-desc">${r.description || ''}</div></div>`
    ).join('');
    dropdown.style.display = 'block';
    dropdown.querySelectorAll('.autocomplete-item[data-id]').forEach(item => {
      item.addEventListener('click', () => {
        input.value = item.dataset.id;
        dropdown.style.display = 'none';
      });
    });
  }

  let timer;
  input.addEventListener('focus', e => {
    if (e.target.value.trim().length >= 3) return;
    fetch('/hmm-library/api/search/?source=pfam&q=kin&limit=5')
      .then(r => r.json())
      .then(d => { if (d.results?.length) render(d.results); })
      .catch(() => {});
  });

  input.addEventListener('input', e => {
    const q = e.target.value.trim();
    clearTimeout(timer);
    if (q.length < 3) return;
    if (/^PF\d{5}$/i.test(q) || /^IPR\d{6}$/i.test(q)) {
      dropdown.style.display = 'none';
      return;
    }
    timer = setTimeout(() => {
      const sources = q.toUpperCase().startsWith('IPR') ? ['interpro'] : ['pfam'];
      Promise.all(sources.map(s =>
        fetch(`/hmm-library/api/search/?source=${s}&q=${encodeURIComponent(q)}&limit=5`)
          .then(r => r.json()).then(d => d.results || []).catch(() => [])
      )).then(all => render(all.flat()));
    }, 200);
  });

  document.addEventListener('click', e => {
    if (e.target !== input && !dropdown.contains(e.target)) {
      dropdown.style.display = 'none';
    }
  });
}

function setupFastaTabToggle(opts) {
  const cfg = Object.assign({
    paneUploadId: 'fasta-pane-upload',
    panePasteId: 'fasta-pane-paste',
    tabUploadId: 'fasta-tab-upload',
    tabPasteId: 'fasta-tab-paste',
    pasteId: 'pasted_fasta',
    fileId: 'fasta_file',
    filenameId: 'fasta-filename',
    zoneId: 'upload-zone-fasta',
  }, opts || {});

  function switchTab(which) {
    const paneUp = document.getElementById(cfg.paneUploadId);
    const panePa = document.getElementById(cfg.panePasteId);
    const btnUp  = document.getElementById(cfg.tabUploadId);
    const btnPa  = document.getElementById(cfg.tabPasteId);
    if (!paneUp || !panePa) return;
    const isUp = which === 'upload';
    paneUp.style.display = isUp ? 'block' : 'none';
    panePa.style.display = isUp ? 'none' : 'block';
    if (btnUp) btnUp.classList.toggle('active', isUp);
    if (btnPa) btnPa.classList.toggle('active', !isUp);
    if (isUp) {
      const ta = document.getElementById(cfg.pasteId);
      if (ta) ta.value = '';
    } else {
      const fi = document.getElementById(cfg.fileId);
      if (fi) fi.value = '';
      const fn = document.getElementById(cfg.filenameId);
      if (fn) fn.textContent = '';
      const zone = document.getElementById(cfg.zoneId);
      if (zone) zone.classList.remove('has-file');
    }
  }

  window.hfSwitchFastaTab = switchTab;

  document.addEventListener('DOMContentLoaded', () => {
    const ta = document.getElementById(cfg.pasteId);
    if (ta && ta.value.trim()) switchTab('paste');
  });
}

function rcToggle(btn) {
  var id = btn.getAttribute('data-target');
  var body = document.getElementById(id);
  if (!body) return;
  var chev = btn.querySelector('.rc-chevron');
  var open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  if (chev) chev.classList.toggle('rc-open', !open);
  btn.setAttribute('aria-expanded', String(!open));
}
function rcToggleFull(btn) {
  var card = btn.getAttribute('data-card');
  var pre = document.querySelector('.rc-pre[data-card="' + card + '"]');
  if (!pre) return;
  var full = pre.classList.toggle('rc-pre-full');
  pre.classList.toggle('rc-pre-clipped', !full);
  btn.textContent = full ? 'Collapse preview' : 'Show full preview';
}
