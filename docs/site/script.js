// Footer year
const yearEl = document.getElementById('year');
if (yearEl) {
  yearEl.textContent = String(new Date().getFullYear());
}

// Copy-to-clipboard for the quick-start snippet
document.querySelectorAll('[data-copy]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const code = document.getElementById('snippet');
    if (!code) return;
    const text = code.innerText;
    try {
      await navigator.clipboard.writeText(text);
      const original = btn.textContent;
      btn.textContent = 'Copied!';
      btn.disabled = true;
      setTimeout(() => {
        btn.textContent = original;
        btn.disabled = false;
      }, 1600);
    } catch {
      btn.textContent = 'Press Ctrl+C';
      setTimeout(() => {
        btn.textContent = 'Copy';
      }, 1600);
    }
  });
});

// Subtle reveal-on-scroll for cards and steps
const io = 'IntersectionObserver' in window
  ? new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.style.opacity = '1';
            entry.target.style.transform = 'translateY(0)';
            io.unobserve(entry.target);
          }
        }
      },
      { threshold: 0.12 }
    )
  : null;

if (io) {
  document.querySelectorAll('.card, .step, .pipeline, .code, .qs-list li').forEach((el) => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(12px)';
    el.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
    io.observe(el);
  });
}
