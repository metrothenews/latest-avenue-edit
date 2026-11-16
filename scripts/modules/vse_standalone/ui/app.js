// Color tab: step through clips, preview LUTs with the number keys, space to apply + advance.
// Two passes: "look" (first-pass LUTs) and "color" (color-only LUTs, applied under the look).

// Keyboard key -> LUT slot. Left hand 1–4, right hand 7–0; LUT names stay 1–8.
const KEYMAP = { '1': 1, '2': 2, '3': 3, '4': 4, '7': 5, '8': 6, '9': 7, '0': 8 }
const POLL_MS = 400
const PASS_LABEL = { look: 'look', color: 'color' }

const $ = (id) => document.getElementById(id)
let S = null            // latest state from the engine
let gen = -1            // clip-list generation we're showing
let tab = 'color'       // which tab is showing: color | crop
let pass = 'look'       // which pass the keys drive
let i = 0               // current clip
let lut = 0             // slot being previewed in this pass (0 = none)
let chosen = { look: {}, color: {} }   // clip index -> slot applied this session, shown before the engine confirms
let done = false        // end-of-pass card showing
const loaded = new Set()    // frame URLs the browser has finished loading
const requested = new Set()

const post = (path, body) =>
  fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LA': '1' }, body: JSON.stringify(body || {}) })

const clip = () => (S && S.clips[i]) || null
const applied = (c, kind) => (c.index in chosen[kind] ? chosen[kind][c.index] : c[kind]) || 0
const lutName = (kind, slot) => (slot ? S.mode[kind][slot - 1].replace(/_\d+Pt$/, '').replace(/_/g, ' ') : '')
const show = (el, on) => { el.hidden = !on }
const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]))

// The preview for a clip: this pass's slot comes from the keys, the other pass's from what's applied.
const combo = (c, slot) => (pass === 'look' ? [applied(c, 'color'), slot] : [slot, applied(c, 'look')])
const frameUrl = (index, [color, look]) => `/frame/${gen}/${index}/${color}/${look}.jpg?v=${S.boot}`
const describe = ([color, look]) => [lutName('look', look), lutName('color', color)].filter(Boolean).join(' + ') || 'no LUT'

function want(url) {            // start loading a frame; re-render when it arrives
  if (requested.has(url)) return
  requested.add(url)
  const img = new Image()
  img.onload = () => { loaded.add(url); render() }
  img.onerror = () => { setTimeout(() => requested.delete(url), 1500) }   // not ready yet: allow a retry
  img.src = url
}

function buildKeys() {
  const btn = ([k, n]) => `<button class="glass key" data-slot="${n}">${k}<small></small></button>`
  const entries = Object.entries(KEYMAP).sort((a, b) => a[1] - b[1])
  $('keys').innerHTML = entries.slice(0, 4).map(btn).join('') + '<div class="gap"></div>' + entries.slice(4).map(btn).join('')
  $('mode').innerHTML = `<option>${esc(S.mode.name)}</option>`
}

function setPass(next) {
  if (next === pass || (next === 'color' && !S.mode.color.length)) return
  pass = next
  done = false
  const c = clip()
  lut = c ? applied(c, pass) : 0
  render()
}

function goTo(index) {
  if (!S || !S.clips.length) return
  i = Math.max(0, Math.min(S.clips.length - 1, index))
  lut = applied(S.clips[i], pass)
  done = false
  render()
}

function applyAndNext() {
  const c = clip()
  if (!c || !c.still) return
  if (lut !== applied(c, pass)) {
    chosen[pass][c.index] = lut
    post('/api/apply', { index: c.index, pass, slot: lut, generation: gen })
  }
  if (i === S.clips.length - 1) { done = true; render() } else goTo(i + 1)
}

function notice(title, text, buttons) {
  $('noticetitle').textContent = title
  $('noticetext').textContent = text
  $('noticebtns').innerHTML = ''
  for (const [label, fn, primary] of buttons || []) {
    const b = document.createElement('button')
    b.className = 'btn' + (primary ? ' primary' : '')
    b.textContent = label
    b.onclick = fn
    $('noticebtns').appendChild(b)
  }
  show($('noticebtns'), buttons && buttons.length)
  show($('notice'), true)
}

const loadClips = () => { post('/api/load'); done = false }

function renderCrop() {
  const cr = S.crop, r = cr.result
  $('zoombtn').disabled = cr.active || !S.status.project
  $('zoombtn').textContent = cr.active ? 'Zooming…' : 'Zoom all clips'
  let text = ''
  if (cr.error) text = `Couldn't zoom: ${cr.error}`
  else if (!S.status.project) text = S.status.connected ? S.status.message : "The editor isn't running."
  else if (r) {
    const parts = [`${r.changed} changed`]
    if (r.unchanged) parts.push(`${r.unchanged} already at ${r.zoom}`)
    if (r.failed) parts.push(`${r.failed} failed`)
    text = `${r.total} clips set to ${r.zoom}: ${parts.join(', ')}.`
  }
  $('cropresult').textContent = text
  show($('cropresult'), !!text)
}

function setTab(next) {
  tab = next
  render()
}

function render() {
  if (!S) return
  const st = S.status, ld = S.loading

  // Tabs
  document.querySelectorAll('#tabs .tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab))
  show($('colorstage'), tab === 'color')
  show($('cropstage'), tab === 'crop')
  document.querySelectorAll('.coloronly').forEach((el) => show(el, tab === 'color'))
  renderCrop()

  // Connection pill
  $('dot').classList.toggle('on', !!st.connected)
  $('conn').innerHTML = st.project
    ? `${esc(st.project)} <span class="muted">›</span> <b>${esc(st.timeline)}</b>`
    : (st.connected ? 'Editor connected' : 'Editor not connected')

  // Toolbar: pass switch, progress, load button
  document.querySelectorAll('.segbtn').forEach((b) => {
    b.classList.toggle('active', b.dataset.pass === pass)
    b.disabled = b.dataset.pass === 'color' && !S.mode.color.length
  })
  $('hintmode').textContent = pass === 'look' ? S.mode.name.toUpperCase() : `${S.mode.name.toUpperCase()} COLOR`
  $('load').disabled = ld.active || !st.project
  const progress = ld.active ? `Grabbing frames ${ld.done} / ${ld.total || '…'}` : (st.stale ? 'Timeline changed. Load clips to refresh.' : '')
  $('progress').textContent = progress
  show($('progress'), !!progress)

  const c = clip()
  show($('clipinfo'), !!c && tab === 'color')
  show($('notice'), false)

  // Blocking states
  if (!st.project) {
    showFrame(null, st.connected ? st.message : 'Waiting for the editor…')
    notice(st.connected ? 'Nothing to color yet' : "The editor isn't running",
      st.connected ? st.message
        : 'Start latest avenue video. This will connect on its own.')
    return
  }
  if (ld.error) { showFrame(null, ''); notice("Couldn't load clips", ld.error, [['Load clips', loadClips, true]]); return }
  if (!c) { showFrame(null, ld.active ? 'Reading the timeline…' : 'Press Load clips to start.'); return }

  // Toolbar clip info
  $('pos').textContent = `${i + 1} / ${S.clips.length}`
  $('name').textContent = c.name
  show($('graded'), c.graded)
  $('otherlut').textContent = c.other_lut ? `has LUT: ${c.other_lut}` : ''
  show($('otherlut'), !!c.other_lut)

  // Preview: the chosen combination if loaded; otherwise keep the clip's last loaded frame while it renders
  const now = combo(c, lut), base = combo(c, applied(c, pass))
  let shown = null
  if (c.still) {
    const url = frameUrl(c.index, now)
    want(url)
    if (loaded.has(url)) shown = url
    else {
      const fallback = [frameUrl(c.index, base), frameUrl(c.index, [0, 0])].find((u) => loaded.has(u))
      if (fallback) shown = fallback
      else want(frameUrl(c.index, [0, 0]))
    }
    $('lutlabel').textContent = shown === url ? describe(now) : `preparing ${describe(now)}…`
  }
  showFrame(shown, c.error || (c.still ? 'Preparing preview…' : 'Grabbing frame…'))
  show($('lutlabel'), !!shown)
  show($('hint1s'), !!shown)
  const a = [applied(c, 'color'), applied(c, 'look')]
  $('applied').textContent = c.pending ? 'applying…' : `applied: ${describe(a)}`
  show($('applied'), !!shown && (a[0] || a[1] || c.pending))
  $('cliperror').textContent = c.error || ''
  show($('cliperror'), !!c.error && !!shown)

  document.querySelectorAll('.key').forEach((k) => {
    const slot = +k.dataset.slot
    k.classList.toggle('on', slot === lut)
    k.classList.toggle('applied', slot === applied(c, pass))
    k.querySelector('small').textContent = `${pass === 'look' ? S.mode.name : 'color'} ${slot}`
  })

  // Warm this clip's and the next clip's previews for the current pass
  for (const n of [c, S.clips[i + 1]]) {
    if (n && n.still) for (let slot = 0; slot <= 8; slot++) want(frameUrl(n.index, combo(n, slot)))
  }

  if (done) {
    const total = S.clips.length
    const count = S.clips.filter((x) => applied(x, pass)).length
    if (pass === 'look') {
      const buttons = [['Back to first clip', () => goTo(0)], ['Load clips', loadClips]]
      if (S.mode.color.length) buttons.unshift(['Start color pass', () => { setPass('color'); goTo(0) }, true])
      notice('First pass done', `${count} of ${total} clips have a ${S.mode.name.toUpperCase()} look LUT applied.`, buttons)
    } else {
      notice('Color pass done', `${count} of ${total} clips have a color LUT layered into their grade. Carry on with manual adjustments in the editor.`,
        [['Back to first clip', () => goTo(0)], ['Load clips', loadClips]])
    }
  }
}

function showFrame(url, placeholder) {
  const img = $('frame')
  if (url && img.getAttribute('src') !== url) img.src = url
  show(img, !!url)
  $('placeholder').textContent = placeholder || ''
  show($('placeholder'), !url)
  if (!url) { show($('lutlabel'), false); show($('hint1s'), false); show($('applied'), false); show($('cliperror'), false) }
}

async function poll() {
  try {
    const next = await (await fetch('/api/state')).json()
    const first = !S
    S = next
    if (first) buildKeys()
    if (S.generation !== gen) {           // a new clip list was loaded
      gen = S.generation; chosen = { look: {}, color: {} }; loaded.clear(); requested.clear(); done = false; i = 0
      lut = S.clips[0] ? applied(S.clips[0], pass) : 0
    }
    // Once the engine has confirmed (or failed) an apply, trust its answer
    for (const kind of ['look', 'color']) {
      for (const c of S.clips) {
        if (!(c.index in chosen[kind]) || c.pending) continue
        if (c.error || (c[kind] || 0) === chosen[kind][c.index]) {
          const showing = c.index === i && kind === pass && lut === chosen[kind][c.index]
          delete chosen[kind][c.index]
          if (showing && c.error) lut = c[kind] || 0
        }
      }
    }
    render()
  } catch (e) {
    $('dot').classList.remove('on')
    $('conn').textContent = 'App engine not responding'
  }
  setTimeout(poll, POLL_MS)
}

document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return
  if (tab !== 'color') {                                   // the LUT keys only work on the Color tab
    if (e.key === 'Enter' && e.target.id === 'zoom') $('zoombtn').click()
    return
  }
  const blocked = !clip() || (!$('notice').hidden && !done)
  if (e.key === ' ') { e.preventDefault(); if (!e.repeat && !blocked && !done) applyAndNext(); return }
  if (e.key === 'Tab') { e.preventDefault(); if (!blocked && !e.repeat) setPass(pass === 'look' ? 'color' : 'look'); return }
  if (blocked) return
  if (e.key in KEYMAP) { if (!done) { lut = KEYMAP[e.key]; render() } }
  else if (e.key === 'Backspace') { e.preventDefault(); if (!done) { lut = 0; render() } }
  else if (e.key === 'ArrowRight') { e.preventDefault(); if (!done) goTo(i + 1) }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); done ? goTo(i) : goTo(i - 1) }
})

$('keys').addEventListener('click', (e) => {
  const b = e.target.closest('.key')
  if (b && clip() && !done) { lut = +b.dataset.slot; render() }
  b && b.blur()
})
$('passes').addEventListener('click', (e) => {
  const b = e.target.closest('.segbtn')
  if (b && clip()) setPass(b.dataset.pass)
  b && b.blur()
})
$('tabs').addEventListener('click', (e) => {
  const b = e.target.closest('.tab')
  if (b) { setTab(b.dataset.tab); b.blur() }
})
$('zoombtn').addEventListener('click', (e) => {
  const zoom = parseFloat($('zoom').value)
  if (!(zoom >= 0.1 && zoom <= 10)) { $('cropresult').textContent = 'Enter a zoom between 0.1 and 10.'; show($('cropresult'), true); return }
  post('/api/zoom', { zoom })
  e.target.blur()
})
$('load').addEventListener('click', (e) => { loadClips(); e.target.blur() })

poll()
