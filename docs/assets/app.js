
(function () {
  document.querySelectorAll('[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var block = btn.closest('.codeblock');
      var code = block ? block.querySelector('code') : null;
      if (!code) return;
      var text = code.innerText;
      var done = function () {
        btn.textContent = 'Copied';
        btn.classList.add('done');
        setTimeout(function () { btn.textContent = 'Copy'; btn.classList.remove('done'); }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(function () { fallback(text, done); });
      } else {
        fallback(text, done);
      }
    });
  });

  function fallback(text, done) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (e) {}
    document.body.removeChild(ta);
  }

  // Scroll-spy for the docs sidebar
  var links = Array.prototype.slice.call(document.querySelectorAll('.side a[href^="#"]'));
  if (!links.length) return;
  var targets = links.map(function (a) { return document.getElementById(a.getAttribute('href').slice(1)); });
  function onScroll() {
    var pos = window.scrollY + 110, active = 0;
    for (var i = 0; i < targets.length; i++) {
      if (targets[i] && targets[i].offsetTop <= pos) active = i;
    }
    links.forEach(function (a, i) {
      a.style.color = i === active ? 'var(--text)' : '';
      a.style.borderLeftColor = i === active ? 'var(--accent)' : 'transparent';
      a.style.background = i === active ? 'var(--panel)' : '';
    });
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
})();
