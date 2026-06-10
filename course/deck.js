// DATA 2027 deck navigation
(function () {
  const slides = [...document.querySelectorAll('.slide')];
  const hud = document.createElement('div'); hud.id = 'hud';
  hud.innerHTML = `<span><a href="index.html">DATA 2027</a> · ${document.body.dataset.week || ''}</span><span id="ctr"></span>`;
  document.body.appendChild(hud);
  const bar = document.createElement('div'); bar.id = 'bar'; document.body.appendChild(bar);
  const ctr = hud.querySelector('#ctr');
  let i = Math.min(slides.length - 1, Math.max(0, (parseInt(location.hash.slice(1), 10) || 1) - 1));

  function show(n) {
    i = Math.min(slides.length - 1, Math.max(0, n));
    slides.forEach((s, k) => s.classList.toggle('current', k === i));
    ctr.textContent = `${i + 1} / ${slides.length} · ← → · p prints`;
    bar.style.width = ((i + 1) / slides.length * 100) + '%';
    history.replaceState(null, '', '#' + (i + 1));
  }
  addEventListener('keydown', e => {
    if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown') { e.preventDefault(); show(i + 1); }
    else if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); show(i - 1); }
    else if (e.key === 'Home') show(0);
    else if (e.key === 'End') show(slides.length - 1);
    else if (e.key === 'p') window.print();
  });
  addEventListener('click', e => {
    if (e.target.closest('a')) return;
    show(e.clientX > innerWidth / 2 ? i + 1 : i - 1);
  });
  addEventListener('hashchange', () => show((parseInt(location.hash.slice(1), 10) || 1) - 1));
  show(i);
})();
