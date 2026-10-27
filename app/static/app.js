'use strict';
const $ = (id) => document.getElementById(id);
let selectedFile = null;
let outputUrl = null;
let currentMode = 'before';
let ruleCount = 0;

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function setMessage(message, type='error') {
  $('message').className = 'message ' + type;
  $('message').textContent = message;
}
function addRule(old = '', replacement = '') {
  ruleCount += 1;
  const row = document.createElement('div');
  row.className = 'rule-card';
  row.innerHTML = `
    <div class="rule-top"><span class="rule-label">REPLACEMENT ${String(ruleCount).padStart(2,'0')}</span><button class="remove-rule" type="button" title="Remove this edit" aria-label="Remove this edit">×</button></div>
    <div class="rule-fields"><input class="old-text" aria-label="Exact original text" placeholder="Text to find exactly" value="${escapeHtml(old)}" /><input class="new-text" aria-label="Replacement text" placeholder="Replace with (blank = remove)" value="${escapeHtml(replacement)}" /></div>
    <div class="rule-options"><label>PAGE <input class="page-number" type="number" min="1" max="40" placeholder="Any" aria-label="Page, optional" /></label><label>ALIGN <select class="alignment" aria-label="Replacement alignment"><option value="auto">Auto</option><option value="left">Left</option><option value="right">Right</option></select></label><label>MATCH <input class="occurrence" type="number" min="1" placeholder="Any" aria-label="Match occurrence, optional" /></label></div>`;
  row.querySelector('.remove-rule').addEventListener('click', () => row.remove());
  $('rules').appendChild(row);
}
addRule();
$('addRule').addEventListener('click', () => addRule());
function chooseFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) { setMessage('Only original PDF files are supported. A screenshot has no reusable PDF font objects.'); return; }
  if (file.size > 12 * 1024 * 1024) { setMessage('PDF must be smaller than 12 MB.'); return; }
  selectedFile = file;
  $('uploadTitle').textContent = file.name;
  $('uploadSubtitle').textContent = `${(file.size / 1024).toFixed(0)} KB · READY FOR INSPECTION`;
  $('message').className = 'message';
  $('previewState').textContent = 'DOCUMENT LOADED';
  if (outputUrl) { URL.revokeObjectURL(outputUrl); outputUrl = null; }
  $('results').hidden = true;
  // Native PDF preview is intentionally not sent to a third-party web service.
}
$('dropzone').addEventListener('click', () => $('fileInput').click());
$('dropzone').addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('fileInput').click(); } });
$('fileInput').addEventListener('change', e => chooseFile(e.target.files?.[0]));
for (const event of ['dragenter', 'dragover']) $('dropzone').addEventListener(event, e => { e.preventDefault(); $('dropzone').classList.add('dragover'); });
for (const event of ['dragleave', 'drop']) $('dropzone').addEventListener(event, e => { e.preventDefault(); $('dropzone').classList.remove('dragover'); });
$('dropzone').addEventListener('drop', e => chooseFile(e.dataTransfer?.files?.[0]));
function setMode(mode) {
  currentMode = mode;
  for (const [key,id] of [['before','beforeButton'],['after','afterButton'],['compare','compareButton']]) $(id).classList.toggle('active', mode === key);
  $('afterImg').style.opacity = mode === 'before' ? '0' : mode === 'after' ? '1' : String(Number($('comparison').value) / 100);
  $('compareControls').hidden = mode !== 'compare';
}
for (const [mode,id] of [['before','beforeButton'],['after','afterButton'],['compare','compareButton']]) $(id).addEventListener('click', () => setMode(mode));
$('comparison').addEventListener('input', () => { if (currentMode === 'compare') $('afterImg').style.opacity = String(Number($('comparison').value) / 100); });
function pdfToBlob(b64) {
  const chars = atob(b64);
  const chunkSize = 8192;
  const chunks = [];
  for (let i=0; i<chars.length; i+=chunkSize) {
    const slice = chars.slice(i, i+chunkSize);
    const nums = new Uint8Array(slice.length);
    for (let j=0; j<slice.length; j++) nums[j] = slice.charCodeAt(j);
    chunks.push(nums);
  }
  return new Blob(chunks, {type:'application/pdf'});
}
$('run').addEventListener('click', async () => {
  if (!selectedFile) { setMessage('Choose an original PDF first.'); return; }
  const edits = [];
  for (const row of document.querySelectorAll('.rule-card')) {
    const old = row.querySelector('.old-text').value;
    const replacement = row.querySelector('.new-text').value;
    const page = row.querySelector('.page-number').value;
    const occurrence = row.querySelector('.occurrence').value;
    if (!old && !replacement && !page && !occurrence) continue;
    if (!old) { setMessage('Every filled replacement row needs the exact original text.'); return; }
    edits.push({old, new: replacement, page: page ? Number(page) : null,
      occurrence: occurrence ? Number(occurrence) : null, align:row.querySelector('.alignment').value});
  }
  if (edits.length === 0 && !$('instructions').value.trim()) { setMessage('Add at least one edit or text instruction.'); return; }
  $('run').disabled = true;
  $('run').querySelector('span:first-child').textContent = 'Verifying document…';
  $('message').className = 'message';
  const form = new FormData();
  form.append('file', selectedFile);
  form.append('instructions', $('instructions').value.trim());
  form.append('edits_json', JSON.stringify(edits));
  try {
    const response = await fetch('/api/edit', {method:'POST', body:form});
    const data = await response.json();
    if (!response.ok) { setMessage(typeof data.detail === 'string' ? data.detail : 'Edit failed.'); return; }
    if (outputUrl) URL.revokeObjectURL(outputUrl);
    outputUrl = URL.createObjectURL(pdfToBlob(data.pdf_base64));
    const report = data.report;
    $('beforeImg').src = report.before_preview;
    $('afterImg').src = report.after_preview;
    $('emptyPreview').hidden = true;
    $('imageViewer').hidden = false;
    $('previewState').classList.add('passed');
    $('previewState').textContent = 'VALIDATION PASSED';
    $('pageLabel').textContent = 'PAGE ' + String(report.preview_page).padStart(2,'0');
    $('changeCount').textContent = String(report.changes.length).padStart(2,'0');
    const pixels = report.visual_regression.reduce((sum,r) => sum + r.outside_changed_pixels,0);
    $('pixelCount').textContent = pixels.toLocaleString() + ' px';
    $('changeList').innerHTML = report.changes.map((ch,i) => `<div class="change-item"><span class="old">${escapeHtml(ch.old)}</span> → <span class="new">${escapeHtml(ch.new || '[REMOVED]')}</span><small>PAGE ${ch.page} / ${escapeHtml(ch.font)} / ${ch.font_size} PT / ${escapeHtml(ch.alignment.toUpperCase())}</small></div>`).join('');
    $('results').hidden = false;
    setMode('compare');
    setMessage('Edits applied. Check the comparison, then download the verified PDF.', 'success');
  } catch (error) { setMessage('The server could not complete the edit: ' + error.message); }
  finally { $('run').disabled = false; $('run').querySelector('span:first-child').textContent = 'Generate precision edit'; }
});
$('download').addEventListener('click', () => {
  if (!outputUrl) return;
  const a = document.createElement('a');
  a.href = outputUrl;
  a.download = (selectedFile?.name || 'document.pdf').replace(/\.pdf$/i, '') + '_pedit.pdf';
  document.body.appendChild(a); a.click(); a.remove();
});
